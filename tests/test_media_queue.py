import hashlib
import uuid
from datetime import date, time
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command
from django.test import Client, TestCase, override_settings

from pubtv.operations.automation import process_preparation_job
from pubtv.operations.media_queue import cancel_media_queue_batch, create_media_queue_batch, queue_summary, remove_cancelled_media_batch, retry_media_queue_item
from pubtv.operations.models import (Device, Episode, Occurrence, PreparationBatch,
    PreparationBatchItem, PreparationJob, RecurrenceSlot, ResearchGate, Show, Station, AuditEvent,
    TransferAttempt, UltraNexusTargetSettings, MediaBinding)
from pubtv.ultranexus.ftp import StdlibFTPAdapter, TransferResult


class MediaQueueAcceptanceTests(TestCase):
    def setUp(self):
        self.data = TemporaryDirectory(); self.addCleanup(self.data.cleanup)
        self.setting = override_settings(DATA_DIR=Path(self.data.name)); self.setting.enable(); self.addCleanup(self.setting.disable)
        self.station = Station.objects.create(name="PUB-TV", timezone="America/Detroit")
        self.show = Show.objects.create(station=self.station, title="Queue Show", code="queue")
        self.target = Device.objects.create(name="UltraNEXUS A")
        self.other_target = Device.objects.create(name="UltraNEXUS B")
        UltraNexusTargetSettings.objects.create(target=self.target, version=1, is_current=True,
            host="controller.test", media_directory="/Vol1/mpeg", secret_reference="channeldex:test",
            settings={"ftp_username": "owner"})

    def upload(self, name, body=None):
        return SimpleUploadedFile(name, body or ("bytes:" + name).encode(), content_type="video/mp4")

    def rows(self, titles, *, encode=True):
        return [{"file": self.upload(f"{i}-{title}.mp4"), "new_title": title,
                 "encode_before_transfer": encode} for i, title in enumerate(titles)]

    def compatible_probe(self):
        return {"streams": [
            {"codec_type": "video", "codec_name": "h264", "profile": "Main", "level": 41,
             "width": 1920, "height": 1080, "duration": "10.0", "r_frame_rate": "30000/1001",
             "avg_frame_rate": "30000/1001", "field_order": "progressive", "pix_fmt": "yuv420p",
             "sample_aspect_ratio": "1:1", "color_space": "bt709", "color_range": "tv"},
            {"codec_type": "audio", "codec_name": "aac", "sample_rate": "48000", "channels": 1}]}

    class MemoryFTP:
        def __init__(self):
            self.files = {}
            self.readbacks = 0

        def connect(self, *args, **kwargs): pass
        def login(self, *args, **kwargs): pass
        def set_pasv(self, *args, **kwargs): pass
        def size(self, path):
            if path not in self.files: raise FileNotFoundError(path)
            return len(self.files[path])
        def storbinary(self, command, source, blocksize=8192):
            self.files[command.removeprefix("STOR ")] = source.read()
        def retrbinary(self, command, callback, blocksize=8192):
            self.readbacks += 1
            callback(self.files[command.removeprefix("RETR ")])
        def rename(self, source, destination):
            self.files[destination] = self.files.pop(source)
        def delete(self, path):
            self.files.pop(path, None)
        def quit(self): pass

    def adapter_factory(self, connection):
        return lambda host, **kwargs: StdlibFTPAdapter(host, ftp_factory=lambda: connection, **kwargs)

    def complete_bypass(self, title="Completed"):
        ResearchGate.objects.get_or_create(target=self.target, key="nexus_mono_bypass", defaults={"status": "passed"})
        batch, _ = create_media_queue_batch(target=self.target, show=self.show,
            rows=self.rows([title], encode=False), submission_token=uuid.uuid4())
        connection = self.MemoryFTP()
        job = batch.jobs.get()
        process_preparation_job(job, credential_resolver=lambda _ref: "password",
            ftp_factory=self.adapter_factory(connection), probe_runner=lambda _path: self.compatible_probe())
        item = batch.items.select_related("resulting_binding", "resulting_inspection").get()
        self.assertEqual(item.execution_status, "ready")
        return batch, item, connection

    def test_five_file_intake_creates_explicit_episodes_true_approval_and_one_job(self):
        before = Occurrence.objects.count()
        batch, created = create_media_queue_batch(target=self.target, show=self.show,
            rows=self.rows(["One", "Two", "Three", "Four", "Five"]), submission_token=uuid.uuid4())
        batch.refresh_from_db()
        self.assertTrue(created)
        self.assertEqual((batch.approval_1_status, batch.status), ("approved", "approved"))
        self.assertEqual([item.asset.episode.title for item in batch.items.all()], ["One", "Two", "Three", "Four", "Five"])
        self.assertEqual(list(batch.items.values_list("position", flat=True)), list(range(5)))
        self.assertTrue(all(item.selected_input_hash for item in batch.items.all()))
        self.assertEqual(batch.approval_1_snapshot["target_settings"]["version"], 1)
        self.assertEqual(PreparationJob.objects.filter(batch=batch, status="queued").count(), 1)
        self.assertEqual(Occurrence.objects.count(), before)
        self.assertEqual(Episode.objects.filter(show=self.show).count(), 5)

    def test_second_batch_appends_while_first_job_is_running(self):
        first, _ = create_media_queue_batch(target=self.target, show=self.show, rows=self.rows(["One", "Two"]), submission_token=uuid.uuid4())
        first.jobs.update(status="running")
        second, created = create_media_queue_batch(target=self.target, show=self.show, rows=self.rows(["Three", "Four"]), submission_token=uuid.uuid4())
        self.assertTrue(created)
        self.assertNotEqual(first.pk, second.pk)
        self.assertEqual(first.jobs.get().status, "running")
        self.assertEqual(second.jobs.get().status, "queued")
        self.assertEqual([i.asset.episode.title for i in second.items.all()], ["Three", "Four"])

    def test_submission_token_is_idempotent_but_cannot_change_target(self):
        token = uuid.uuid4()
        first, _ = create_media_queue_batch(target=self.target, show=self.show, rows=self.rows(["One"]), submission_token=token)
        again, created = create_media_queue_batch(target=self.target, show=self.show, rows=self.rows(["One"]), submission_token=token)
        self.assertFalse(created); self.assertEqual(again.pk, first.pk)
        self.assertEqual(PreparationBatch.objects.count(), 1)
        with self.assertRaisesMessage(ValueError, "another show or target"):
            create_media_queue_batch(target=self.other_target, show=self.show, rows=self.rows(["One"]), submission_token=token)

    def test_all_rows_validate_before_any_database_or_file_write(self):
        rows = self.rows(["One", "Two", "Three", "Four"])
        rows.append({"file": self.upload("notes.txt"), "new_title": "Five"})
        with self.assertRaisesMessage(ValueError, "unsupported video file type"):
            create_media_queue_batch(target=self.target, show=self.show, rows=rows)
        self.assertFalse(PreparationBatch.objects.exists()); self.assertFalse(Episode.objects.exists())
        self.assertFalse((Path(self.data.name) / "imports").exists())

    def test_storage_failure_rolls_back_records_and_removes_prior_private_files(self):
        from pubtv.operations import media_queue
        real_store, calls = media_queue._store_upload, 0
        def fail_second(upload):
            nonlocal calls
            self.assertFalse(PreparationBatch.objects.exists(), "file copies must finish before the transaction starts")
            calls += 1
            if calls == 2: raise OSError("disk full")
            return real_store(upload)
        with patch.object(media_queue, "_store_upload", side_effect=fail_second):
            with self.assertRaises(OSError):
                create_media_queue_batch(target=self.target, show=self.show, rows=self.rows(["One", "Two"]))
        self.assertFalse(PreparationBatch.objects.exists()); self.assertFalse(Episode.objects.exists())
        imports = Path(self.data.name) / "imports"
        self.assertEqual(list(imports.iterdir()) if imports.exists() else [], [])

    def test_public_queue_summary_never_exposes_private_source_paths(self):
        batch, _ = create_media_queue_batch(target=self.target, show=self.show, rows=self.rows(["Private"]), submission_token=uuid.uuid4())
        item = batch.items.get(); rendered = repr(queue_summary())
        self.assertNotIn(item.selected_input_path, rendered)
        self.assertNotIn(str(Path(self.data.name)), rendered)

    def test_explicit_retry_processes_only_selected_item_and_preserves_siblings(self):
        ResearchGate.objects.create(target=self.target, key="nexus_mono_bypass", status="passed")
        batch, _ = create_media_queue_batch(target=self.target, show=self.show,
            rows=self.rows(["Retry me", "Leave failed", "Already ready"], encode=False), submission_token=uuid.uuid4())
        batch.jobs.update(status="failed")
        retry_item, failed_sibling, ready_sibling = list(batch.items.all())
        PreparationBatchItem.objects.filter(pk=retry_item.pk).update(execution_status="failed", blocker="network")
        PreparationBatchItem.objects.filter(pk=failed_sibling.pk).update(execution_status="failed", blocker="operator review")
        PreparationBatchItem.objects.filter(pk=ready_sibling.pk).update(execution_status="ready", blocker="")
        retry_job = retry_media_queue_item(retry_item.pk)
        probe = {"streams": [
            {"codec_type": "video", "codec_name": "h264", "profile": "Main", "level": 41,
             "width": 1920, "height": 1080, "duration": "10.0", "r_frame_rate": "30000/1001",
             "avg_frame_rate": "30000/1001", "field_order": "progressive", "pix_fmt": "yuv420p",
             "sample_aspect_ratio": "1:1", "color_space": "bt709", "color_range": "tv"},
            {"codec_type": "audio", "codec_name": "aac", "sample_rate": "48000", "channels": 1}]}
        class FTP:
            def __init__(self, *args, **kwargs): pass
            def upload(self, local_path, remote_path, overwrite=False):
                return TransferResult(remote_path, hashlib.sha256(Path(local_path).read_bytes()).hexdigest())
        process_preparation_job(retry_job, credential_resolver=lambda _ref: "password", ftp_factory=FTP,
                                probe_runner=lambda _path: probe)
        retry_item.refresh_from_db(); failed_sibling.refresh_from_db(); ready_sibling.refresh_from_db(); retry_job.refresh_from_db()
        self.assertEqual(retry_item.execution_status, "ready")
        self.assertEqual(retry_job.result["item_ids"], [retry_item.pk])
        self.assertEqual((failed_sibling.execution_status, failed_sibling.blocker), ("failed", "operator review"))
        self.assertEqual(ready_sibling.execution_status, "ready")

    def test_retry_adopts_orphan_binding_after_remote_readback_without_reencoding(self):
        batch, item, connection = self.complete_bypass("Orphan binding")
        binding = item.resulting_binding
        inspection = item.resulting_inspection
        retained = Path(inspection.local_path)
        retained_bytes = retained.read_bytes()
        media_id = binding.media_id_uint16
        first_readbacks = connection.readbacks
        PreparationBatchItem.objects.filter(pk=item.pk).update(
            resulting_binding=None, resulting_inspection=None, execution_status="blocked", blocker="uncertain link")
        batch.jobs.update(status="succeeded")
        retry = retry_media_queue_item(item.pk)

        def must_not_encode(*args, **kwargs):
            raise AssertionError("orphan binding retry must not encode")

        process_preparation_job(retry, runner=must_not_encode,
            credential_resolver=lambda _ref: "password", ftp_factory=self.adapter_factory(connection),
            probe_runner=lambda _path: self.compatible_probe())
        item.refresh_from_db()
        self.assertEqual(item.execution_status, "ready")
        self.assertEqual(item.resulting_binding_id, binding.pk)
        self.assertEqual(item.resulting_inspection_id, inspection.pk)
        self.assertEqual(MediaBinding.objects.filter(asset=item.asset, target=self.target).count(), 1)
        self.assertEqual(item.resulting_binding.media_id_uint16, media_id)
        self.assertEqual(retained.read_bytes(), retained_bytes)
        self.assertGreater(connection.readbacks, first_readbacks)
        self.assertTrue(TransferAttempt.objects.filter(item=item, status="succeeded", evidence__reused=True).exists())

    def test_retry_blocks_when_orphan_binding_retained_bytes_no_longer_match(self):
        batch, item, _connection = self.complete_bypass("Changed retained media")
        retained = Path(item.resulting_inspection.local_path)
        retained.write_bytes(b"tampered")
        PreparationBatchItem.objects.filter(pk=item.pk).update(
            resulting_binding=None, resulting_inspection=None, execution_status="blocked", blocker="uncertain link")
        retry = retry_media_queue_item(item.pk)
        with patch("pubtv.operations.automation.StdlibFTPAdapter.upload") as upload:
            process_preparation_job(retry, credential_resolver=lambda _ref: "password",
                ftp_factory=self.adapter_factory(self.MemoryFTP()), probe_runner=lambda _path: self.compatible_probe())
        item.refresh_from_db()
        # Already encoded input and retained rendition are the same bytes.
        # Detect their mutation at the source approval boundary, before any
        # orphan-binding adoption or remote transfer can occur.
        self.assertEqual(item.execution_status, "blocked")
        self.assertEqual(item.approval_1_status, "stale")
        self.assertIn("source input changed", item.blocker)
        upload.assert_not_called()
        binding = MediaBinding.objects.get(asset=item.asset, target=self.target)
        self.assertNotEqual(binding.local_inspection.media_hash, hashlib.sha256(retained.read_bytes()).hexdigest())
        with self.assertRaisesMessage(ValueError, "fresh intake review"):
            retry_media_queue_item(item.pk)
        upload.assert_not_called()
        self.assertEqual(MediaBinding.objects.filter(asset=item.asset, target=self.target).count(), 1)

    def test_terminal_item_link_failure_rolls_back_binding_and_attempt_success_then_retry_recovers(self):
        ResearchGate.objects.create(target=self.target, key="nexus_mono_bypass", status="passed")
        batch, _ = create_media_queue_batch(target=self.target, show=self.show,
            rows=self.rows(["Atomic link"], encode=False), submission_token=uuid.uuid4())
        item = batch.items.get()
        connection = self.MemoryFTP()
        original_save = PreparationBatchItem.save
        raised = False

        def fail_terminal_save(instance, *args, **kwargs):
            nonlocal raised
            fields = kwargs.get("update_fields") or []
            if not raised and "resulting_binding" in fields:
                raised = True
                raise RuntimeError("simulated item-link write failure")
            return original_save(instance, *args, **kwargs)

        with patch.object(PreparationBatchItem, "save", new=fail_terminal_save):
            process_preparation_job(batch.jobs.get(), credential_resolver=lambda _ref: "password",
                ftp_factory=self.adapter_factory(connection), probe_runner=lambda _path: self.compatible_probe())
        item.refresh_from_db()
        self.assertEqual(item.execution_status, "failed")
        self.assertFalse(MediaBinding.objects.filter(asset=item.asset, target=self.target).exists())
        self.assertEqual(list(TransferAttempt.objects.filter(item=item).values_list("status", flat=True)), ["failed"])

        retry = retry_media_queue_item(item.pk)
        process_preparation_job(retry, credential_resolver=lambda _ref: "password",
            ftp_factory=self.adapter_factory(connection), probe_runner=lambda _path: self.compatible_probe())
        item.refresh_from_db()
        self.assertEqual(item.execution_status, "ready")
        self.assertEqual(MediaBinding.objects.filter(asset=item.asset, target=self.target).count(), 1)
        self.assertEqual(list(TransferAttempt.objects.filter(item=item).order_by("pk").values_list("status", flat=True)),
                         ["failed", "succeeded"])

    def test_optimistic_token_collision_returns_existing_without_extra_records_or_files(self):
        token = uuid.uuid4()
        existing, _ = create_media_queue_batch(target=self.target, show=self.show,
            rows=self.rows(["Original"]), submission_token=token)
        before_episodes = Episode.objects.count()
        before_assets = existing.items.count()
        imports = Path(self.data.name) / "imports"
        before_files = sorted(path.name for path in imports.iterdir())
        original_filter = PreparationBatch.objects.filter
        lookups = 0

        def race_filter(*args, **kwargs):
            nonlocal lookups
            queryset = original_filter(*args, **kwargs)
            if kwargs.get("submission_token") == token:
                class Lookup:
                    def first(inner_self):
                        nonlocal lookups
                        lookups += 1
                        return None if lookups == 1 else queryset.first()
                return Lookup()
            return queryset

        with patch.object(PreparationBatch.objects, "filter", side_effect=race_filter):
            duplicate, created = create_media_queue_batch(target=self.target, show=self.show,
                rows=self.rows(["Duplicate request"]), submission_token=token)
        self.assertFalse(created)
        self.assertEqual(duplicate.pk, existing.pk)
        self.assertEqual(Episode.objects.count(), before_episodes)
        self.assertEqual(existing.items.count(), before_assets)
        self.assertEqual(sorted(path.name for path in imports.iterdir()), before_files)

    def test_optimistic_token_collision_rejects_different_target(self):
        token = uuid.uuid4()
        create_media_queue_batch(target=self.target, show=self.show,
            rows=self.rows(["Original"]), submission_token=token)
        original_filter = PreparationBatch.objects.filter
        lookups = 0

        def race_filter(*args, **kwargs):
            nonlocal lookups
            queryset = original_filter(*args, **kwargs)
            if kwargs.get("submission_token") == token:
                class Lookup:
                    def first(inner_self):
                        nonlocal lookups
                        lookups += 1
                        return None if lookups == 1 else queryset.first()
                return Lookup()
            return queryset

        with patch.object(PreparationBatch.objects, "filter", side_effect=race_filter):
            with self.assertRaisesMessage(ValueError, "another show or target"):
                create_media_queue_batch(target=self.other_target, show=self.show,
                    rows=self.rows(["Wrong target"]), submission_token=token)

    def test_worker_startup_blocks_uncertain_active_item_but_runs_untouched_pending_job(self):
        first, _ = create_media_queue_batch(target=self.target, show=self.show, rows=self.rows(["Interrupted"], encode=False), submission_token=uuid.uuid4())
        first.jobs.update(status="running"); first.items.update(execution_status="transferring")
        second, _ = create_media_queue_batch(target=self.target, show=self.show, rows=self.rows(["Pending"], encode=False), submission_token=uuid.uuid4())
        with patch("pubtv.operations.management.commands.run_ultranexus_worker.process_preparation_job") as process:
            call_command("run_ultranexus_worker", once=True, poll_seconds=0.25)
        running, active = first.jobs.get(), first.items.get()
        self.assertEqual(running.status, "failed"); self.assertEqual(active.execution_status, "blocked")
        self.assertIn("explicit item retry", active.blocker)
        process.assert_called_once(); self.assertEqual(process.call_args.args[0].batch_id, second.pk)

    def test_cancelling_batch_retains_records_and_is_idempotent(self):
        batch, _ = create_media_queue_batch(target=self.target, show=self.show, rows=self.rows(["Cancel me"]), submission_token=uuid.uuid4())
        cancelled, changed = cancel_media_queue_batch(batch.pk)
        self.assertTrue(changed)
        self.assertEqual(cancelled.status, "cancelled")
        self.assertEqual(batch.items.count(), 1)
        self.assertEqual(batch.jobs.get().status, "cancelled")
        self.assertTrue(AuditEvent.objects.filter(entity="PreparationBatch", entity_id=batch.pk, action="cancel").exists())
        _same, changed_again = cancel_media_queue_batch(batch.pk)
        self.assertFalse(changed_again)

    def test_cancelling_completed_batch_is_rejected(self):
        batch, _ = create_media_queue_batch(target=self.target, show=self.show, rows=self.rows(["Done"]), submission_token=uuid.uuid4())
        batch.status = "complete"
        batch.save(update_fields=["status"])
        with self.assertRaisesMessage(ValueError, "Only approved, blocked, or failed"):
            cancel_media_queue_batch(batch.pk)

    def test_cancel_endpoint_is_post_only_and_preserves_cancelled_history(self):
        batch, _ = create_media_queue_batch(target=self.target, show=self.show, rows=self.rows(["Endpoint cancel"]), submission_token=uuid.uuid4())
        client = Client()
        self.assertEqual(client.get(f"/media/batches/{batch.pk}/cancel/").status_code, 405)
        response = client.post(f"/media/batches/{batch.pk}/cancel/")
        self.assertRedirects(response, "/media/")
        batch.refresh_from_db()
        self.assertEqual(batch.status, "cancelled")
        summary = queue_summary()
        self.assertNotIn("cancelled", {key: value for key, value in summary["counts"].items() if value})
        self.assertTrue(any(row["batch_id"] == batch.pk and row["state"] == "cancelled" for row in summary["items"]))

    def test_cancelled_batch_is_visible_until_explicitly_removed(self):
        batch, _ = create_media_queue_batch(target=self.target, show=self.show, rows=self.rows(["Hide me"]), submission_token=uuid.uuid4())
        cancel_media_queue_batch(batch.pk)
        client = Client()
        response = client.get("/media/")
        self.assertContains(response, f"batch-{batch.pk}")
        self.assertContains(response, "Remove from list")

        response = client.post(f"/media/batches/{batch.pk}/remove/")
        self.assertRedirects(response, "/media/")
        self.assertNotContains(client.get("/media/"), f"batch-{batch.pk}")
        batch.refresh_from_db()
        self.assertTrue(batch.removed_from_list)
        self.assertEqual(batch.items.count(), 1)
        self.assertEqual(batch.jobs.count(), 1)
        self.assertTrue(AuditEvent.objects.filter(entity="PreparationBatch", entity_id=batch.pk, action="cancel").exists())
        self.assertTrue(AuditEvent.objects.filter(entity="PreparationBatch", entity_id=batch.pk, action="remove_from_list").exists())

    def test_remove_from_list_requires_cancelled_state_and_is_idempotent(self):
        batch, _ = create_media_queue_batch(target=self.target, show=self.show, rows=self.rows(["Still active"]), submission_token=uuid.uuid4())
        with self.assertRaisesMessage(ValueError, "Only cancelled media batches"):
            remove_cancelled_media_batch(batch.pk)
        batch.refresh_from_db()
        self.assertFalse(batch.removed_from_list)
        cancel_media_queue_batch(batch.pk)
        removed, changed = remove_cancelled_media_batch(batch.pk)
        self.assertTrue(changed)
        _same, changed_again = remove_cancelled_media_batch(batch.pk)
        self.assertFalse(changed_again)
        self.assertEqual(AuditEvent.objects.filter(entity="PreparationBatch", entity_id=removed.pk, action="remove_from_list").count(), 1)

    def test_remove_endpoint_is_post_only(self):
        batch, _ = create_media_queue_batch(target=self.target, show=self.show, rows=self.rows(["Endpoint remove"]), submission_token=uuid.uuid4())
        cancel_media_queue_batch(batch.pk)
        client = Client()
        self.assertEqual(client.get(f"/media/batches/{batch.pk}/remove/").status_code, 405)
        self.assertEqual(client.post(f"/media/batches/{batch.pk}/remove/").status_code, 302)

    def test_remove_endpoint_returns_json_for_async_request(self):
        batch, _ = create_media_queue_batch(target=self.target, show=self.show, rows=self.rows(["Async remove"]), submission_token=uuid.uuid4())
        cancel_media_queue_batch(batch.pk)
        response = Client().post(f"/media/batches/{batch.pk}/remove/", HTTP_X_REQUESTED_WITH="XMLHttpRequest", HTTP_ACCEPT="application/json")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"removed": True, "batch_id": batch.pk})

    def test_repeated_async_remove_is_safe_success(self):
        batch, _ = create_media_queue_batch(target=self.target, show=self.show, rows=self.rows(["Async repeat"]), submission_token=uuid.uuid4())
        cancel_media_queue_batch(batch.pk)
        client = Client()
        headers = {"HTTP_X_REQUESTED_WITH": "XMLHttpRequest", "HTTP_ACCEPT": "application/json"}
        self.assertEqual(client.post(f"/media/batches/{batch.pk}/remove/", **headers).json(), {"removed": True, "batch_id": batch.pk})
        response = client.post(f"/media/batches/{batch.pk}/remove/", **headers)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"removed": False, "batch_id": batch.pk})

    def test_async_remove_non_cancelled_returns_json_error(self):
        batch, _ = create_media_queue_batch(target=self.target, show=self.show, rows=self.rows(["Async active"]), submission_token=uuid.uuid4())
        response = Client().post(f"/media/batches/{batch.pk}/remove/", HTTP_X_REQUESTED_WITH="XMLHttpRequest", HTTP_ACCEPT="application/json")
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json(), {"error": "Only cancelled media batches can be removed from the list."})
        batch.refresh_from_db()
        self.assertFalse(batch.removed_from_list)

    def test_running_batch_cannot_be_cancelled_or_mutated(self):
        batch, _ = create_media_queue_batch(target=self.target, show=self.show, rows=self.rows(["Running"]), submission_token=uuid.uuid4())
        batch.status = "in_progress"
        batch.save(update_fields=["status"])
        job = batch.jobs.get()
        job.status = "running"
        job.save(update_fields=["status"])
        with self.assertRaisesMessage(ValueError, "In-progress work cannot be removed safely"):
            cancel_media_queue_batch(batch.pk)
        batch.refresh_from_db(); job.refresh_from_db()
        self.assertEqual(batch.status, "in_progress")
        self.assertEqual(job.status, "running")
        self.assertNotContains(Client().get("/media/"), f"/media/batches/{batch.pk}/cancel/")

    def test_cancelled_batch_item_cannot_be_retried(self):
        batch, _ = create_media_queue_batch(target=self.target, show=self.show, rows=self.rows(["Cancelled retry"]), submission_token=uuid.uuid4())
        cancel_media_queue_batch(batch.pk)
        item = batch.items.get()
        item.execution_status = "failed"
        item.save(update_fields=["execution_status"])
        with self.assertRaisesMessage(ValueError, "Cancelled media batches cannot be retried"):
            retry_media_queue_item(item.pk)
        self.assertEqual(batch.jobs.count(), 1)

    def test_episode_planning_does_not_rewrite_media_hashes_or_queue_order(self):
        from pubtv.operations.premiere_planning import build_bulk_premiere_preview, confirm_bulk_premiere_plan
        batch, _ = create_media_queue_batch(target=self.target, show=self.show, rows=self.rows(["One", "Two"]), submission_token=uuid.uuid4())
        hashes = list(batch.items.values_list("selected_input_hash", flat=True)); job_order = list(batch.jobs.values_list("pk", flat=True))
        RecurrenceSlot.objects.create(station=self.station, show=self.show, weekday=2, start_time=time(19), duration_seconds=1800, is_premiere=True)
        episodes = list(Episode.objects.filter(show=self.show).order_by("pk"))
        proposal = build_bulk_premiere_preview(show=self.show, episode_ids=[episodes[1].pk, episodes[0].pk], first_premiere_date=date(2026, 10, 7))
        confirm_bulk_premiere_plan(token=proposal["token"])
        self.assertEqual(list(batch.items.values_list("selected_input_hash", flat=True)), hashes)
        self.assertEqual(list(batch.jobs.values_list("pk", flat=True)), job_order)
