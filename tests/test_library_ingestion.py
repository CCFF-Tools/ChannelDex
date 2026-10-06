import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import Mock, patch

from django.core.management import call_command
from django.test import Client, TestCase, override_settings

from pubtv.operations.automation import _establish_library_inputs, process_preparation_job
from pubtv.operations.media_identity import asset_claim
from pubtv.operations.media_queue import (
    IntakeValidationError, begin_relink_review, confirm_intake_review,
    confirm_relink_review, process_relink_review, relink_review_result,
    retry_media_queue_item, validate_rows,
)
from pubtv.operations.models import (
    AuditEvent, Device, Episode, MediaAsset, MediaIntakeReview, MediaRelinkReview,
    PreparationBatch, PreparationBatchItem, PreparationJob, ResearchGate,
    Show, Station, UltraNexusTargetSettings, WeeklyEpisodeAssignment,
)
from pubtv.ultranexus.ftp import TransferResult


class LibraryIngestionTests(TestCase):
    def setUp(self):
        self.directory = TemporaryDirectory(); self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name).resolve()
        override = override_settings(DATA_DIR=self.root); override.enable(); self.addCleanup(override.disable)
        self.station = Station.objects.create(name="PUB-TV")
        self.show = Show.objects.create(station=self.station, title="Library Show", code="library")
        self.episode = Episode.objects.create(show=self.show, title="Episode 1", episode_number=17)
        self.device = Device.objects.create(name="WinLGX")
        UltraNexusTargetSettings.objects.create(target=self.device, is_current=True, host="controller.test",
            media_directory="/Vol1/mpeg", secret_reference="test:owner", settings={"ftp_username": "owner"})
        self.source = self.root / 'Episode, "two"\n.part.mp4'; self.source.write_bytes(b"first video")
        self.client = Client()

    def row(self, **overrides):
        return {"source_path": str(self.source), "episode_id": "new", "new_title": "Editable title",
                "episode_number": "22", "runtime_seconds": "90", "encoding_mode": "needs_encoding", **overrides}

    def review(self, rows=None):
        response = self.client.post("/media/", {"action": "review_intake", "show": self.show.pk,
            "target": self.device.pk, "draft_rows": json.dumps(rows or [self.row()])})
        self.assertEqual(response.status_code, 200, response.content[:500])
        return MediaIntakeReview.objects.latest("pk")

    def accept(self, rows=None):
        review = self.review(rows)
        batch, created = confirm_intake_review(review)
        self.assertTrue(created)
        return batch, batch.items.select_related("asset__episode").get()

    def probe(self):
        return {"format": {"duration": "10.1"}, "streams": [
            {"codec_type": "video", "codec_name": "h264", "profile": "Main", "level": 41,
             "width": 1920, "height": 1080, "duration": "10.1", "r_frame_rate": "30000/1001",
             "avg_frame_rate": "30000/1001", "field_order": "progressive", "pix_fmt": "yuv420p",
             "sample_aspect_ratio": "1:1", "color_space": "bt709", "color_range": "tv"},
            {"codec_type": "audio", "codec_name": "aac", "sample_rate": "48000", "channels": 1}]}

    def test_review_and_confirmation_do_not_hash_copy_or_run_media_tools(self):
        with patch("pubtv.operations.media_queue._sha256_file", side_effect=AssertionError("HTTP hash")), \
             patch("pubtv.operations.automation.subprocess.run", side_effect=AssertionError("HTTP media tool")):
            review = self.review()
            batch, created = confirm_intake_review(review)
            again, repeated = confirm_intake_review(review)
        self.assertTrue(created); self.assertFalse(repeated); self.assertEqual(batch.pk, again.pk)
        self.assertEqual(PreparationJob.objects.count(), 1)
        self.assertEqual(MediaAsset.objects.count(), 1)
        item = batch.items.get(); asset = item.asset
        self.assertEqual(asset.local_path, str(self.source))
        self.assertEqual(asset.file_name, self.source.name)
        self.assertEqual(asset.content_identity, ""); self.assertEqual(item.selected_input_hash, "")
        self.assertEqual(item.reviewed_file_size, self.source.stat().st_size)
        self.assertEqual(item.reviewed_mtime_ns, self.source.stat().st_mtime_ns)
        self.assertEqual(asset.episode.episode_number, 22)
        self.assertEqual(asset.episode.runtime_provenance, "manual")
        self.assertFalse((self.root / "imports").exists()); self.assertFalse(WeeklyEpisodeAssignment.objects.exists())

    def test_review_request_token_is_idempotent_and_cannot_mutate_displayed_rows(self):
        import uuid
        data = {"action": "review_intake", "show": self.show.pk, "target": self.device.pk,
            "submission_token": str(uuid.uuid4()), "draft_rows": json.dumps([self.row()])}
        first = self.client.post("/media/", data); second = self.client.post("/media/", data)
        self.assertEqual(first.status_code, 200); self.assertEqual(second.status_code, 200)
        self.assertEqual(MediaIntakeReview.objects.count(), 1)
        review = MediaIntakeReview.objects.get()
        data["draft_rows"] = json.dumps([self.row(new_title="Altered unseen title")])
        conflict = self.client.post("/media/", data)
        self.assertEqual(conflict.status_code, 400)
        review.refresh_from_db(); self.assertEqual(review.payload["rows"][0]["new_title"], "Editable title")
        self.assertEqual(MediaIntakeReview.objects.count(), 1)
        batch, _ = confirm_intake_review(review); again, created = confirm_intake_review(review)
        self.assertEqual(batch.pk, again.pk); self.assertFalse(created)

    def test_browser_rejects_uploads_without_copying_or_hashing(self):
        from django.core.files.uploadedfile import SimpleUploadedFile
        with patch("pubtv.operations.media_queue._store_upload", side_effect=AssertionError("HTTP copy")), \
             patch("pubtv.operations.media_queue._sha256_file", side_effect=AssertionError("HTTP hash")):
            response = self.client.post("/media/", {"show": self.show.pk, "target": self.device.pk,
                "source_files": SimpleUploadedFile("source.mp4", b"video")})
        self.assertEqual(response.status_code, 400); self.assertFalse(MediaAsset.objects.exists())

    def test_already_encoded_input_has_its_own_encoded_kind(self):
        _batch, item = self.accept([self.row(encoding_mode="already_encoded")])
        self.assertEqual(item.asset.kind, "encoded"); self.assertFalse(item.encode_before_transfer)

    def test_explicit_encoding_mode_required_for_each_local_row(self):
        with self.assertRaisesMessage(IntakeValidationError, "Choose already encoded or needs encoding"):
            validate_rows([self.row(encoding_mode="")], show=self.show)
        self.assertFalse(PreparationBatch.objects.exists())

    def test_all_row_errors_and_drafts_are_preserved(self):
        rows = [self.row(source_path=str(self.root / "missing.mp4"), new_title="Keep this title", episode_number="7"),
                self.row(source_path=str(self.root / "missing-two.mp4"), new_title="Other", encoding_mode="")]
        response = self.client.post("/media/", {"action": "review_intake", "show": self.show.pk,
            "target": self.device.pk, "draft_rows": json.dumps(rows)})
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.context["connected"]["draft_rows"], rows)
        self.assertEqual(set(response.context["connected"]["row_errors"]), {0, 1})
        self.assertContains(response, 'queue-draft', status_code=400)
        self.assertFalse(MediaIntakeReview.objects.exists())

    def test_show_or_destination_error_preserves_draft(self):
        rows = [self.row()]
        response = self.client.post("/media/", {"action": "review_intake", "draft_rows": json.dumps(rows)})
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.context["connected"]["draft_rows"], rows)

    def test_duplicate_paths_and_incompatible_existing_episode_rejected(self):
        with self.assertRaisesMessage(IntakeValidationError, "only once"):
            validate_rows([self.row(), self.row(new_title="Second")], show=self.show)
        other_show = Show.objects.create(station=self.station, title="Other", code="other")
        other = Episode.objects.create(show=other_show, title="Other")
        with self.assertRaisesMessage(IntakeValidationError, "does not belong"):
            validate_rows([self.row(episode_id=other.pk)], show=self.show)

    def test_deleted_existing_episode_is_not_replaced_with_new_blank_episode(self):
        review = self.review([self.row(episode_id=self.episode.pk, new_title="")])
        self.episode.delete()
        with self.assertRaisesMessage(ValueError, "existing episode changed"):
            confirm_intake_review(review)
        self.assertFalse(PreparationBatch.objects.exists()); self.assertFalse(MediaAsset.objects.exists())
        review.refresh_from_db(); self.assertEqual(review.status, "pending")

    def test_confirmation_error_restores_review_rows_for_correction(self):
        review = self.review(); self.source.write_bytes(b"changed source")
        response = self.client.post("/media/", {"action": "confirm_intake", "review_token": review.token})
        self.assertEqual(response.status_code, 400)
        row = response.context["connected"]["draft_rows"][0]
        self.assertEqual(row["source_path"], str(self.source)); self.assertEqual(row["new_title"], "Editable title")
        self.assertEqual(row["encoding_mode"], "needs_encoding")
        self.assertEqual(row["episode_id"], "new")
        self.assertEqual(row["runtime_seconds"], "90"); self.assertEqual(row["episode_number"], "22")
        self.assertFalse(PreparationBatch.objects.exists())

    def test_accepted_intake_clears_browser_draft_only_after_success(self):
        review = self.review()
        response = self.client.post("/media/", {"action": "confirm_intake", "review_token": review.token})
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.cookies["media_intake_accepted"].value, "1")
        response = self.client.get("/media/")
        self.assertTrue(response.context["connected"]["accepted"])
        self.assertEqual(response.cookies["media_intake_accepted"]["max-age"], 0)

    def test_only_unambiguous_show_and_destination_defaults(self):
        response = self.client.get("/media/")
        self.assertEqual(response.context["connected"]["show"], self.show)
        self.assertEqual(response.context["connected"]["target"], self.device)
        Show.objects.create(station=self.station, title="Other", code="other")
        other = Device.objects.create(name="Other")
        UltraNexusTargetSettings.objects.create(target=other, is_current=True)
        response = self.client.get("/media/")
        self.assertIsNone(response.context["connected"]["show"])
        self.assertIsNone(response.context["connected"]["target"])

    def test_worker_establishes_stable_input_identity_and_measured_runtime(self):
        batch, item = self.accept([self.row(runtime_seconds="")])
        with asset_claim(item.asset_id):
            _establish_library_inputs(batch, probe_runner=lambda _path: self.probe())
        item.refresh_from_db(); item.asset.refresh_from_db(); item.asset.episode.refresh_from_db()
        expected = hashlib.sha256(self.source.read_bytes()).hexdigest()
        self.assertEqual(item.selected_input_hash, expected); self.assertEqual(item.asset.content_identity, expected)
        self.assertEqual(item.asset.runtime_seconds, 11); self.assertEqual(item.asset.runtime_provenance, "measured")
        self.assertEqual(item.asset.episode.runtime_seconds, 11)

    def test_measurement_does_not_overwrite_explicit_episode_runtime(self):
        batch, item = self.accept()
        _establish_library_inputs(batch, probe_runner=lambda _path: self.probe())
        item.asset.refresh_from_db(); item.asset.episode.refresh_from_db()
        self.assertEqual(item.asset.runtime_seconds, 11)
        self.assertEqual(item.asset.episode.runtime_seconds, 90)
        self.assertEqual(item.asset.episode.runtime_provenance, "manual")

    def test_worker_rejects_changed_reviewed_metadata_before_hash_or_encode(self):
        batch, item = self.accept(); self.source.write_bytes(b"changed")
        with patch("pubtv.operations.automation._sha256_file", side_effect=AssertionError("must reject before hash")):
            _establish_library_inputs(batch)
        item.refresh_from_db(); self.assertEqual(item.execution_status, "blocked")
        self.assertEqual(item.approval_1_status, "stale")
        self.assertEqual(item.asset.content_identity, "")
        with self.assertRaisesMessage(ValueError, "fresh intake review"):
            retry_media_queue_item(item.pk)

    def test_later_byte_changes_preserve_durable_original_identity(self):
        batch, item = self.accept(); _establish_library_inputs(batch, probe_runner=lambda _path: self.probe())
        item.refresh_from_db(); original = item.selected_input_hash; stat = self.source.stat()
        self.source.write_bytes(b"other video")  # Same size, forged mtime still must not inherit identity.
        os.utime(self.source, ns=(stat.st_atime_ns, stat.st_mtime_ns))
        _establish_library_inputs(batch, probe_runner=lambda _path: self.probe())
        item.refresh_from_db(); item.asset.refresh_from_db()
        self.assertEqual(item.approval_1_status, "stale"); self.assertEqual(item.asset.content_identity, original)

    def test_already_encoded_worker_uses_existing_verification_and_transfer_gates(self):
        ResearchGate.objects.create(target=self.device, key="nexus_mono_bypass", status="passed")
        batch, item = self.accept([self.row(encoding_mode="already_encoded")])
        class FTP:
            def __init__(self, *args, **kwargs): pass
            def upload(self, path, remote_path, overwrite=False):
                return TransferResult(remote_path, hashlib.sha256(Path(path).read_bytes()).hexdigest())
        process_preparation_job(batch.jobs.get(), probe_runner=lambda _path: self.probe(),
            credential_resolver=lambda _ref: "secret", ftp_factory=FTP)
        item.refresh_from_db(); item.asset.refresh_from_db()
        self.assertEqual(item.execution_status, "ready")
        self.assertEqual(item.resulting_inspection.local_path, str(self.source))
        self.assertEqual(item.asset.content_identity, hashlib.sha256(self.source.read_bytes()).hexdigest())
        self.assertEqual(item.resulting_binding.local_inspection_id, item.resulting_inspection_id)

    def test_library_search_number_and_readiness_are_independent_of_availability(self):
        asset = MediaAsset.objects.create(episode=self.episode, kind="source", file_name="missing.mp4", local_path="/missing/video.mp4")
        response = self.client.get("/media/library/?q=17&readiness=pending")
        self.assertEqual(len(response.context["rows"]), 1)
        row = response.context["rows"][0]; self.assertFalse(row["available"]); self.assertFalse(row["prepared"])
        self.assertEqual(row["assets"][0].pk, asset.pk)
        self.assertContains(response, 'data-plan-episode=')
        self.assertEqual(len(self.client.get("/media/library/?readiness=ready").context["rows"]), 0)

    def relink_asset(self, identity=None):
        return MediaAsset.objects.create(episode=self.episode, kind="source", file_name="original.mp4",
            local_path="/old/location.mp4", content_identity=identity or hashlib.sha256(self.source.read_bytes()).hexdigest(),
            runtime_seconds=120, runtime_provenance="manual")

    def test_relink_is_background_and_requires_explicit_matched_confirmation(self):
        asset = self.relink_asset()
        with patch("pubtv.operations.media_queue._sha256_file", side_effect=AssertionError("HTTP hash")):
            review = begin_relink_review(asset.pk, str(self.source))
            self.assertEqual(review.status, "queued")
            with self.assertRaisesMessage(ValueError, "Background identity review"):
                confirm_relink_review(review.token)
        process_relink_review(review); self.assertEqual(review.status, "matched")
        asset.refresh_from_db(); self.assertEqual(asset.local_path, "/old/location.mp4")
        with patch("pubtv.operations.media_queue._sha256_file", side_effect=AssertionError("HTTP hash")):
            matched, changed = confirm_relink_review(review.token)
            again, repeated = confirm_relink_review(review.token)
        self.assertTrue(changed); self.assertFalse(repeated); self.assertEqual(matched.pk, again.pk)
        review.refresh_from_db(); self.assertEqual(review.status, "confirm_queued")
        process_relink_review(review); matched.refresh_from_db()
        self.assertEqual(review.status, "confirmed")
        self.assertEqual(matched.local_path, str(self.source)); self.assertEqual(matched.runtime_seconds, 120)
        audit = AuditEvent.objects.get(action="relink"); self.assertIn("/old/location.mp4", audit.summary); self.assertIn(str(self.source), json.loads(audit.summary)["new_path"])

    def test_final_worker_rehash_rejects_same_size_and_restored_mtime_tampering(self):
        asset = self.relink_asset(); review = begin_relink_review(asset.pk, str(self.source)); process_relink_review(review)
        stat = self.source.stat(); self.source.write_bytes(b"other video")
        os.utime(self.source, ns=(stat.st_atime_ns, stat.st_mtime_ns))
        with patch("pubtv.operations.media_queue._sha256_file", side_effect=AssertionError("HTTP hash")):
            confirm_relink_review(review.token)
        review.refresh_from_db(); self.assertEqual(review.status, "confirm_queued")
        process_relink_review(review)
        self.assertEqual(review.status, "new_version")
        asset.refresh_from_db(); self.assertEqual(asset.local_path, "/old/location.mp4")
        self.assertEqual(asset.content_identity, hashlib.sha256(b"first video").hexdigest())

    def test_different_or_unknown_bytes_route_new_intake_without_inheritance(self):
        for identity in ("different", ""):
            asset = self.relink_asset(); asset.content_identity = identity; asset.save()
            review = begin_relink_review(asset.pk, str(self.source)); process_relink_review(review)
            self.assertEqual(review.status, "new_version")
            result = relink_review_result(review); self.assertIn("path=", result["new_intake_url"])
            with self.assertRaisesMessage(ValueError, "new-version intake"):
                confirm_relink_review(review.token)
            asset.refresh_from_db(); self.assertEqual(asset.local_path, "/old/location.mp4")
            self.assertEqual(asset.content_identity, identity); self.assertEqual(asset.runtime_seconds, 120)
        self.assertEqual(MediaAsset.objects.count(), 2)

    def test_relink_detects_candidate_and_asset_staleness(self):
        asset = self.relink_asset(); review = begin_relink_review(asset.pk, str(self.source))
        process_relink_review(review); self.source.write_bytes(b"stale candidate")
        with self.assertRaisesMessage(ValueError, "reviewed file changed"):
            confirm_relink_review(review.token)
        fresh = begin_relink_review(asset.pk, str(self.source)); process_relink_review(fresh)
        self.assertEqual(fresh.status, "new_version")
        asset.content_identity = hashlib.sha256(self.source.read_bytes()).hexdigest(); asset.save()
        stale = begin_relink_review(asset.pk, str(self.source)); process_relink_review(stale)
        asset.local_path = "/other/location"; asset.save()
        with self.assertRaisesMessage(ValueError, "asset changed"):
            confirm_relink_review(stale.token)

    def test_relink_stales_only_affected_nonready_inputs_preserves_sibling_jobs(self):
        batch, item = self.accept(); _establish_library_inputs(batch, probe_runner=lambda _path: self.probe())
        item.asset.refresh_from_db(); candidate = self.root / "moved.mp4"; candidate.write_bytes(self.source.read_bytes())
        sibling_asset = MediaAsset.objects.create(episode=self.episode, kind="source", file_name="sibling")
        sibling = PreparationBatchItem.objects.create(batch=batch, asset=sibling_asset)
        ready = PreparationBatchItem.objects.create(batch=batch, asset=MediaAsset.objects.create(episode=self.episode, kind="source"), execution_status="ready")
        sibling_job = PreparationJob.objects.create(batch=batch, idempotency_key="sibling-only", result={"item_ids": [sibling.pk]})
        original_job = batch.jobs.exclude(pk=sibling_job.pk).get()
        review = begin_relink_review(item.asset_id, str(candidate)); process_relink_review(review); confirm_relink_review(review.token)
        review.refresh_from_db(); process_relink_review(review)
        item.refresh_from_db(); sibling.refresh_from_db(); ready.refresh_from_db(); sibling_job.refresh_from_db(); original_job.refresh_from_db()
        self.assertEqual(item.approval_1_status, "stale"); self.assertEqual(sibling.execution_status, "pending")
        self.assertEqual(ready.execution_status, "ready"); self.assertEqual(sibling_job.status, "queued")
        self.assertEqual(original_job.status, "cancelled")

    def test_active_preparation_blocks_both_relink_review_and_confirmation(self):
        batch, item = self.accept(); _establish_library_inputs(batch, probe_runner=lambda _path: self.probe())
        review = begin_relink_review(item.asset_id, str(self.source)); process_relink_review(review)
        batch.jobs.update(status="running")
        with self.assertRaisesMessage(ValueError, "active preparation"):
            begin_relink_review(item.asset_id, str(self.source))
        with self.assertRaisesMessage(ValueError, "active preparation"):
            confirm_relink_review(review.token)

    def test_shared_asset_claim_blocks_preparation_relink_race(self):
        asset = self.relink_asset()
        with asset_claim(asset.pk):
            with self.assertRaisesMessage(ValueError, "active preparation or review"):
                begin_relink_review(asset.pk, str(self.source))
            with self.assertRaisesMessage(ValueError, "active preparation or review"):
                with asset_claim(asset.pk): pass
        # The lock is reusable after release.
        with asset_claim(asset.pk): pass

    def test_file_claim_is_effective_in_a_different_process(self):
        asset = self.relink_asset()
        lock_path = self.root / "ultranexus" / "asset-claims" / f"{asset.pk}.lock"
        script = "import fcntl,sys; h=open(sys.argv[1], 'a+'); fcntl.flock(h.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)"
        with asset_claim(asset.pk):
            result = subprocess.run([sys.executable, "-c", script, str(lock_path)], capture_output=True, timeout=5)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn(b"BlockingIOError", result.stderr)
        result = subprocess.run([sys.executable, "-c", script, str(lock_path)], capture_output=True, timeout=5)
        self.assertEqual(result.returncode, 0)

    def test_mismatch_intake_has_fresh_identity_and_runtime_not_old_evidence(self):
        asset = self.relink_asset("different")
        review = begin_relink_review(asset.pk, str(self.source)); process_relink_review(review)
        response = self.client.get(relink_review_result(review)["new_intake_url"])
        row = response.context["connected"]["draft_rows"][0]
        self.assertEqual(row["episode_id"], str(self.episode.pk))
        self.assertEqual(row["runtime_seconds"], ""); self.assertEqual(row["encoding_mode"], "")
        row["encoding_mode"] = "needs_encoding"
        new_review = self.review([row]); batch, _ = confirm_intake_review(new_review)
        replacement = batch.items.get().asset
        self.assertNotEqual(replacement.pk, asset.pk); self.assertEqual(replacement.content_identity, "")
        self.assertIsNone(replacement.runtime_seconds); self.assertFalse(replacement.ultranexus_bindings.exists())

    def test_ready_transfer_history_remains_ready_after_matching_source_relink(self):
        ResearchGate.objects.create(target=self.device, key="nexus_mono_bypass", status="passed")
        batch, item = self.accept([self.row(encoding_mode="already_encoded")])
        class FTP:
            def __init__(self, *args, **kwargs): pass
            def upload(self, path, remote_path, overwrite=False):
                return TransferResult(remote_path, hashlib.sha256(Path(path).read_bytes()).hexdigest())
        process_preparation_job(batch.jobs.get(), probe_runner=lambda _path: self.probe(),
            credential_resolver=lambda _ref: "secret", ftp_factory=FTP)
        item.refresh_from_db(); binding_id = item.resulting_binding_id; inspection_id = item.resulting_inspection_id
        candidate = self.root / "moved-encoded.mp4"; candidate.write_bytes(self.source.read_bytes())
        review = begin_relink_review(item.asset_id, str(candidate)); process_relink_review(review); confirm_relink_review(review.token)
        review.refresh_from_db(); process_relink_review(review)
        item.refresh_from_db()
        self.assertEqual(item.execution_status, "ready")
        self.assertEqual(item.resulting_binding_id, binding_id); self.assertEqual(item.resulting_inspection_id, inspection_id)
        self.assertEqual(item.selected_input_path, str(self.source))

    def test_worker_once_processes_durable_relink_review(self):
        asset = self.relink_asset(); review = begin_relink_review(asset.pk, str(self.source))
        call_command("run_ultranexus_worker", once=True)
        review.refresh_from_db(); self.assertEqual(review.status, "matched")

    def test_relink_token_cannot_be_confirmed_for_another_asset(self):
        asset = self.relink_asset(); review = begin_relink_review(asset.pk, str(self.source)); process_relink_review(review)
        other = self.relink_asset()
        response = self.client.post(f"/media/assets/{other.pk}/relink/", {"action": "confirm", "review_token": review.token})
        self.assertEqual(response.status_code, 400)
        asset.refresh_from_db(); self.assertEqual(asset.local_path, "/old/location.mp4")

    def test_picker_json_preserves_unusual_and_invalid_entries(self):
        missing = str(self.root / "missing,\nfile.mp4")
        with patch("pubtv.operations.views.subprocess.run", return_value=Mock(returncode=0,
            stdout=json.dumps([str(self.source), missing]), stderr="")) as run:
            response = self.client.post("/settings/browse/", {"kind": "files"})
        data = response.json(); self.assertEqual(data["paths"], [str(self.source), missing])
        self.assertEqual(data["entries"][0]["error"], ""); self.assertTrue(data["entries"][1]["error"])
        self.assertEqual(run.call_args.args[0][1:3], ("-l", "JavaScript"))

    def test_picker_cancel_busy_timeout_and_csrf_have_safe_responses(self):
        from pubtv.operations.views import _picker_claim
        with patch("pubtv.operations.views.subprocess.run", return_value=Mock(returncode=1, stdout="", stderr="(-128)")):
            self.assertTrue(self.client.post("/settings/browse/", {"kind": "files"}).json()["cancelled"])
        with _picker_claim:
            with patch("pubtv.operations.views.subprocess.run", side_effect=AssertionError("second picker")):
                self.assertEqual(self.client.post("/settings/browse/", {"kind": "files"}).status_code, 409)
        with patch("pubtv.operations.views.subprocess.run", side_effect=subprocess.TimeoutExpired("osascript", 120)):
            self.assertFalse(self.client.post("/settings/browse/", {"kind": "files"}).json()["available"])
        csrf = Client(enforce_csrf_checks=True)
        self.assertEqual(csrf.post("/settings/browse/", {"kind": "files"}).status_code, 403)
        self.assertEqual(csrf.post(f"/media/assets/{self.relink_asset().pk}/relink/", {"path": str(self.source)}).status_code, 403)


from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import TransactionTestCase


class LibraryIdentityBackfillTests(TransactionTestCase):
    def test_only_unambiguous_input_evidence_backfills_no_episode_number_guess(self):
        prior = [("operations", "0029_library_identity_metadata")]
        latest = [("operations", "0031_device_station")]
        executor = MigrationExecutor(connection); executor.migrate(prior)
        try:
            apps = executor.loader.project_state(prior).apps
            station = apps.get_model("operations", "Station").objects.create(name="PUB-TV")
            show = apps.get_model("operations", "Show").objects.create(station=station, title="Source", code="source")
            episode = apps.get_model("operations", "Episode").objects.create(show=show, title="Unknown episode number", intended_air_order=8)
            target = apps.get_model("operations", "Device").objects.create(name="Target")
            Asset = apps.get_model("operations", "MediaAsset")
            Batch = apps.get_model("operations", "PreparationBatch")
            Item = apps.get_model("operations", "PreparationBatchItem")
            good = Asset.objects.create(episode=episode, kind="source", file_name="good.mp4")
            ambiguous = Asset.objects.create(episode=episode, kind="source", file_name="ambiguous.mp4")
            output_only = Asset.objects.create(episode=episode, kind="source", file_name="source.mp4")
            batch = Batch.objects.create(target=target)
            Item.objects.create(batch=batch, asset=good, selected_input_path="/source/good.mp4", selected_input_hash="a" * 64)
            Item.objects.create(batch=batch, asset=ambiguous, selected_input_path="/source/one.mp4", selected_input_hash="b" * 64)
            second = Batch.objects.create(target=target)
            Item.objects.create(batch=second, asset=ambiguous, selected_input_path="/source/two.mp4", selected_input_hash="c" * 64)
            apps.get_model("operations", "MediaInspection").objects.create(asset=output_only, target=target, status="passed",
                local_path="/prepared/output.mp4", media_hash="d" * 64)
            executor = MigrationExecutor(connection); executor.migrate(latest)
            current = executor.loader.project_state(latest).apps
            assets = current.get_model("operations", "MediaAsset").objects
            self.assertEqual(assets.get(pk=good.pk).local_path, "/source/good.mp4")
            self.assertEqual(assets.get(pk=good.pk).content_identity, "a" * 64)
            self.assertEqual(assets.get(pk=ambiguous.pk).local_path, "")
            self.assertEqual(assets.get(pk=output_only.pk).content_identity, "")
            self.assertIsNone(current.get_model("operations", "Episode").objects.get(pk=episode.pk).episode_number)
        finally:
            MigrationExecutor(connection).migrate(latest)
