from datetime import datetime, timezone
from types import SimpleNamespace
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import patch
import hashlib
import struct

from pubtv.operations.automation import canonical_hash, canonical_json, process_due_job_state


class UltraNexusWorkflowServiceTests(TestCase):
    def test_canonical_hash_is_order_independent(self):
        self.assertEqual(canonical_json({"b": 2, "a": 1}), '{"a":1,"b":2}')
        self.assertEqual(canonical_hash({"a": 1, "b": 2}), canonical_hash({"b": 2, "a": 1}))

    def test_due_job_requires_approval_and_is_idempotent(self):
        class Job:
            status = "queued"
            error = ""
            def save(self, **kwargs): self.saved = kwargs
        blocked = Job()
        blocked.publication_batch = SimpleNamespace(approval_2_status="pending", approval_2_hash="")
        process_due_job_state(blocked)
        self.assertEqual(blocked.status, "failed")
        done = Job()
        done.publication_batch = SimpleNamespace(approval_2_status="approved", approval_2_hash="a" * 64)
        process_due_job_state(done)
        self.assertEqual(done.status, "succeeded")
        process_due_job_state(done)
        self.assertEqual(done.status, "succeeded")


try:
    from django.test import Client, override_settings
    from pubtv.operations.models import ArtifactRevision, ControllerSnapshot, Device, Episode, MediaAsset, MediaBinding, MediaInspection, Occurrence, PreparationBatch, PreparationBatchItem, PreparationJob, ResearchGate, SchedulePublicationBatch, Show, Station, TransferAttempt, UltraNexusTargetSettings, UploadedOccurrenceCoverage
    from pubtv.operations.automation import approve_preparation_batch, generate_bin_artifact, generate_nmg_artifact, invalidate_snapshot, preview_schedule, process_preparation_batch, publication_artifact_blockers, publication_snapshot, schedule_ready_binding
    from pubtv.ultranexus.ftp import TransferResult
    from pubtv.ultranexus.nmg import MAGIC, RESOURCE_BASE, SCHEDULE_BASE, SCHEDULE_STRIDE, VERSION_OFFSET

    class UltraNexusWorkflowClientTests(__import__("django.test", fromlist=["TestCase"]).TestCase):
        def setUp(self):
            self.client = Client()
            self.station = Station.objects.create()
            self.device = Device.objects.create(name="UltraNEXUS")
            show = Show.objects.create(station=self.station, title="Test", code="test")
            episode = Episode.objects.create(show=show, title="Episode")
            asset = MediaAsset.objects.create(episode=episode, kind="encoded", file_name="episode.mxf")
            self.occurrence = Occurrence.objects.create(station=self.station, show=show, episode=episode, asset=asset, item_type="episode", label="Episode", starts_at=datetime(2026, 1, 1, 12, tzinfo=timezone.utc), planned_duration_seconds=1800)

        def test_publication_approval_stays_pending_without_preparation_and_research(self):
            batch = SchedulePublicationBatch.objects.create(target=self.device)
            batch.occurrence_selections.create(occurrence=self.occurrence, occurrence_revision=self.occurrence.revision)
            response = self.client.post("/automation/", {"action": "approve_publication", "batch": batch.pk})
            self.assertEqual(response.status_code, 302)
            batch.refresh_from_db()
            self.assertNotEqual(batch.approval_2_status, "approved")

        def test_stale_approval_is_explicit(self):
            batch = PreparationBatch.objects.create(target=self.device)
            item = PreparationBatchItem.objects.create(batch=batch, asset=self.occurrence.asset)
            batch.approval_1_snapshot = {"items": []}; batch.approval_1_hash = canonical_hash(batch.approval_1_snapshot); batch.approval_1_status = "approved"; batch.save()
            invalidate_snapshot(batch, approval=1, reason="source changed")
            self.assertEqual(batch.approval_1_status, "stale")

        def test_supported_ame_bridge_encodes_from_original_source_path(self):
            with TemporaryDirectory() as directory, override_settings(DATA_DIR=directory):
                source = Path(directory) / "source.mp4"
                source.write_bytes(b"approved source")
                preset = Path(directory) / "Nexus Mono.epr"
                preset.write_bytes(b"preset")
                asset = self.occurrence.asset
                asset.smb_reference = str(source)
                asset.save()
                UltraNexusTargetSettings.objects.create(
                    target=self.device, version=1, is_current=True, host="controller.test",
                    media_directory="/Vol1/mpeg", secret_reference="test:owner",
                    settings={"ame_preset": str(preset), "ftp_username": "owner"},
                )
                batch = PreparationBatch.objects.create(target=self.device)
                item = PreparationBatchItem.objects.create(batch=batch, asset=asset)
                approve_preparation_batch(batch)
                captured = {}

                class Bridge:
                    def available(self): return True
                    def status(self): return {"ready": True}
                    def render(self, input_path, preset_path, output_path, **kwargs):
                        captured["source"] = Path(input_path)
                        captured["preset"] = Path(preset_path)
                        Path(output_path).write_bytes(b"encoded")
                        return SimpleNamespace(returncode=0, stdout="", stderr="")

                class FTP:
                    def __init__(self, *args, **kwargs): pass
                    def upload(self, local_path, remote_path, overwrite=False):
                        digest = hashlib.sha256(Path(local_path).read_bytes()).hexdigest()
                        return TransferResult(remote_path, digest)

                probe = {"streams": [
                    {"codec_type": "video", "codec_name": "h264", "profile": "Main", "level": 41,
                     "width": 1920, "height": 1080, "duration": "10.0", "r_frame_rate": "30000/1001",
                     "avg_frame_rate": "30000/1001", "field_order": "progressive", "pix_fmt": "yuv420p",
                     "sample_aspect_ratio": "1:1", "color_space": "bt709", "color_range": "tv"},
                    {"codec_type": "audio", "codec_name": "aac", "sample_rate": "48000", "channels": 1},
                ]}
                result = process_preparation_batch(
                    batch, ame_bridge=Bridge(), credential_resolver=lambda ref: "password",
                    ftp_factory=FTP, probe_runner=lambda path: probe,
                )
                item.refresh_from_db()
                self.assertTrue(result["complete"], result)
                self.assertEqual(captured["source"], source.resolve())
                self.assertEqual(captured["preset"], preset)
                self.assertFalse(list((Path(directory) / "ultranexus" / "renditions").glob("approved-input-*")))
                self.assertEqual(item.resulting_inspection.details["encoder_backend"], "adobe_media_encoder_uxp")

        def test_ffmpeg_fallback_reads_original_source_without_research_gate(self):
            with TemporaryDirectory() as directory, override_settings(DATA_DIR=directory):
                source = Path(directory) / "source.mov"; source.write_bytes(b"source")
                preset = Path(directory) / "preset.epr"; preset.write_bytes(b"preset")
                self.occurrence.asset.smb_reference = str(source); self.occurrence.asset.save()
                UltraNexusTargetSettings.objects.create(
                    target=self.device, version=1, is_current=True, host="controller.test",
                    media_directory="/Vol1/mpeg", secret_reference="test:owner",
                    settings={"ame_preset": str(preset), "ffmpeg_executable": "/tools/ffmpeg",
                              "ftp_username": "owner"},
                )
                batch = PreparationBatch.objects.create(target=self.device)
                item = PreparationBatchItem.objects.create(batch=batch, asset=self.occurrence.asset)
                approve_preparation_batch(batch)
                commands = []

                class Bridge:
                    def available(self): return False
                    def status(self): return {"ready": False, "detail": "AME panel disconnected."}

                class FTP:
                    def __init__(self, *args, **kwargs): pass
                    def upload(self, local_path, remote_path, overwrite=False):
                        return TransferResult(remote_path, hashlib.sha256(Path(local_path).read_bytes()).hexdigest())

                def run(command, **kwargs):
                    commands.append(command)
                    if command[0] == "/tools/ffprobe":
                        return SimpleNamespace(returncode=0, stdout=__import__("json").dumps(probe), stderr="")
                    self.assertEqual(command[0], "/tools/ffmpeg")
                    self.assertEqual(Path(command[command.index("-i") + 1]), source.resolve())
                    Path(command[-1]).write_bytes(b"encoded")
                    return SimpleNamespace(returncode=0, stdout="", stderr="")

                probe = {"streams": [
                    {"codec_type": "video", "codec_name": "h264", "profile": "Main", "level": 41,
                     "width": 1920, "height": 1080, "duration": "10", "r_frame_rate": "30000/1001",
                     "avg_frame_rate": "30000/1001", "field_order": "progressive", "pix_fmt": "yuv420p",
                     "sample_aspect_ratio": "1:1", "color_space": "bt709", "color_range": "tv"},
                    {"codec_type": "audio", "codec_name": "aac", "sample_rate": "48000", "channels": 1},
                ]}
                result = process_preparation_batch(
                    batch, ame_bridge=Bridge(), ffmpeg_discoverer=lambda paths: "/tools/ffmpeg",
                    runner=run, credential_resolver=lambda ref: "password", ftp_factory=FTP,
                )
                item.refresh_from_db()
                self.assertTrue(result["complete"], result)
                self.assertEqual([command[0] for command in commands], ["/tools/ffmpeg", "/tools/ffprobe"])
                self.assertEqual(item.resulting_inspection.details["encoder_backend"], "ffmpeg")
                self.assertFalse(list((Path(directory) / "ultranexus" / "renditions").glob("approved-input-*")))

        def test_gap_is_visible_but_not_a_preview_blocker(self):
            later = Occurrence.objects.create(station=self.station, show=self.occurrence.show, item_type="filler", label="Filler", starts_at=datetime(2026, 1, 1, 13, tzinfo=timezone.utc), planned_duration_seconds=60)
            preview = preview_schedule([self.occurrence, later])
            self.assertTrue(any(row["is_gap"] for row in preview["rows"]))
            self.assertFalse(any("Switchback gap" in blocker for blocker in preview["blockers"]))

        def test_controller_bin_ids_are_reserved_for_new_media(self):
            from pubtv.operations.automation import _next_media_id, _resource_reference
            from pubtv.ultranexus.bin import CHECKSUM_OFFSET, RESOURCE_BASE as BIN_RESOURCE_BASE, stored_checksum
            from tests.test_ultranexus_bin import fixture
            with TemporaryDirectory() as directory:
                base = bytearray(fixture())
                struct.pack_into("<H", base, BIN_RESOURCE_BASE + 0x103, 1)
                struct.pack_into("<I", base, CHECKSUM_OFFSET, stored_checksum(base))
                path = Path(directory) / "base.bin"
                path.write_bytes(base)
                UltraNexusTargetSettings.objects.create(
                    target=self.device, version=1, is_current=True,
                    base_bin_path=str(path), base_bin_hash=hashlib.sha256(base).hexdigest(),
                )
                self.assertEqual(_next_media_id(self.device), 2)
                self.assertEqual(_resource_reference(self.device, "00000064" + "0" * 56), 101)

        def test_historical_allocation_is_not_recycled(self):
            from pubtv.operations.automation import _next_media_id, _resource_reference
            from pubtv.operations.models import MediaIdAllocation
            MediaIdAllocation.objects.create(
                target=self.device, media_id_uint16=1,
                resource_reference_uint32=100, casefold_key="old.mp4",
            )
            self.assertEqual(_next_media_id(self.device), 2)
            self.assertEqual(_resource_reference(self.device, "00000064" + "0" * 56), 101)

        def _qualified_bypass_batch(self, directory, *, compatible=True):
            source = Path(directory) / "selected source.mp4"
            source.write_bytes(b"exact selected bytes")
            self.occurrence.asset.smb_reference = str(source)
            self.occurrence.asset.file_name = "A very long non ascii épisode source filename.MP4"
            self.occurrence.asset.save()
            UltraNexusTargetSettings.objects.create(
                target=self.device, version=1, is_current=True, host="controller.test",
                media_directory="/Vol1/mpeg", secret_reference="channeldex:test",
                settings={"ftp_username": "owner"},
            )
            batch = PreparationBatch.objects.create(target=self.device)
            item = PreparationBatchItem.objects.create(
                batch=batch, asset=self.occurrence.asset, encode_before_transfer=False,
            )
            approve_preparation_batch(batch)
            batch.status = "approved"
            batch.save(update_fields=["status"])
            width = 1920 if compatible else 720
            probe = {
                "streams": [
                    {"codec_type": "video", "codec_name": "h264", "profile": "Main", "level": 41,
                     "width": width, "height": 1080, "duration": "10.0", "r_frame_rate": "30000/1001",
                     "avg_frame_rate": "30000/1001", "field_order": "progressive", "pix_fmt": "yuv420p",
                     "sample_aspect_ratio": "1:1", "color_space": "bt709", "color_range": "tv"},
                    {"codec_type": "audio", "codec_name": "aac", "sample_rate": "48000", "channels": 1},
                ]
            }
            return source, batch, item, probe

        def test_bypass_preserves_bytes_and_records_verified_transfer(self):
            with TemporaryDirectory() as directory, override_settings(DATA_DIR=directory):
                source, batch, item, probe = self._qualified_bypass_batch(directory)
                captured = {}

                class FTP:
                    def __init__(self, *args, **kwargs): pass
                    def upload(self, local_path, remote_path, overwrite=False):
                        captured["bytes"] = Path(local_path).read_bytes()
                        captured["remote"] = remote_path
                        return TransferResult(remote_path, __import__("hashlib").sha256(captured["bytes"]).hexdigest())

                result = process_preparation_batch(
                    batch, credential_resolver=lambda ref: "password", ftp_factory=FTP,
                    probe_runner=lambda path: probe,
                )
                item.refresh_from_db()
                self.assertTrue(result["complete"])
                self.assertEqual(captured["bytes"], source.read_bytes())
                self.assertLessEqual(len(Path(captured["remote"]).stem.encode("ascii")), 27)
                self.assertEqual(Path(captured["remote"]).suffix, ".MP4")
                self.assertEqual(item.resulting_inspection.media_hash, __import__("hashlib").sha256(source.read_bytes()).hexdigest())
                self.assertEqual(TransferAttempt.objects.get(item=item).status, "succeeded")
                self.assertEqual(UploadedOccurrenceCoverage.objects.count(), 0)

        def test_blank_approved_hash_is_captured_and_stale_retry_is_blocked(self):
            with TemporaryDirectory() as directory, override_settings(DATA_DIR=directory):
                source, batch, item, probe = self._qualified_bypass_batch(directory)
                item.selected_input_hash = ""
                item.save(update_fields=["selected_input_hash"])
                snapshot = batch.approval_1_snapshot
                snapshot["items"][0]["selected_input_hash"] = ""
                batch.approval_1_snapshot = snapshot
                batch.approval_1_hash = canonical_hash(snapshot)
                batch.save(update_fields=["approval_1_snapshot", "approval_1_hash"])
                result = process_preparation_batch(batch, credential_resolver=lambda ref: None,
                    ftp_factory=lambda *args, **kwargs: SimpleNamespace(upload=lambda *a, **k: TransferResult("remote", hashlib.sha256(source.read_bytes()).hexdigest())),
                    probe_runner=lambda path: probe)
                item.refresh_from_db()
                original_digest = hashlib.sha256(source.read_bytes()).hexdigest()
                self.assertFalse(result["complete"])
                self.assertEqual(item.selected_input_hash, original_digest)
                source.write_bytes(b"replacement bytes")
                retry = process_preparation_batch(batch, credential_resolver=lambda ref: "password",
                    probe_runner=lambda path: probe)
                self.assertFalse(retry["complete"])
                self.assertTrue(any("stale" in blocker.lower() for blocker in retry["blockers"]))
                item.refresh_from_db()
                self.assertEqual(item.selected_input_hash, original_digest)

        def test_bypass_reads_in_place_and_detects_source_change(self):
            with TemporaryDirectory() as directory, override_settings(DATA_DIR=directory):
                source, batch, item, probe = self._qualified_bypass_batch(directory)

                class FTP:
                    def __init__(self, *args, **kwargs): pass
                    def upload(self, local_path, remote_path, overwrite=False):
                        raise AssertionError("changed source must block before FTP")

                def mutate_during_probe(path):
                    self.assertEqual(Path(path), source.resolve())
                    source.write_bytes(b"changed after approval")
                    return probe

                result = process_preparation_batch(
                    batch, credential_resolver=lambda ref: "password", ftp_factory=FTP,
                    probe_runner=mutate_during_probe,
                )
                item.refresh_from_db()
                self.assertFalse(result["complete"])
                self.assertIn("changed during preparation", item.blocker)
                self.assertFalse(list((Path(directory) / "ultranexus" / "renditions").glob("approved-input-*")))

        def test_incompatible_bypass_blocks_without_silent_encoding_or_transfer(self):
            with TemporaryDirectory() as directory, override_settings(DATA_DIR=directory):
                _source, batch, item, probe = self._qualified_bypass_batch(directory, compatible=False)

                class FTP:
                    def __init__(self, *args, **kwargs):
                        raise AssertionError("FTP must not start for incompatible media")

                result = process_preparation_batch(
                    batch, credential_resolver=lambda ref: "password", ftp_factory=FTP,
                    probe_runner=lambda path: probe,
                )
                item.refresh_from_db()
                self.assertFalse(result["complete"])
                self.assertEqual(item.execution_status, "blocked")
                self.assertIn("dimensions", item.blocker)
                self.assertFalse(item.encode_before_transfer)

        def test_approval_one_queues_worker_and_cannot_publish(self):
            with TemporaryDirectory() as directory:
                source = Path(directory) / "episode.mp4"
                source.write_bytes(b"media")
                self.occurrence.asset.smb_reference = str(source)
                self.occurrence.asset.save()
                UltraNexusTargetSettings.objects.create(
                    target=self.device, version=1, is_current=True, host="controller.test",
                    media_directory="/Vol1/mpeg", secret_reference="channeldex:test",
                )
                batch = PreparationBatch.objects.create(target=self.device)
                PreparationBatchItem.objects.create(batch=batch, asset=self.occurrence.asset)
                response = self.client.post("/automation/", {"action": "approve_preparation", "batch": batch.pk})
                self.assertEqual(response.status_code, 302)
                batch.refresh_from_db()
                self.assertEqual(batch.approval_1_status, "approved")
                self.assertEqual(PreparationJob.objects.filter(batch=batch, status="queued").count(), 1)
                self.assertEqual(UploadedOccurrenceCoverage.objects.count(), 0)

        def test_approval_one_rejects_added_items_and_changed_target_settings(self):
            with TemporaryDirectory() as directory, override_settings(DATA_DIR=directory):
                _source, batch, _item, _probe = self._qualified_bypass_batch(directory)
                extra_path = Path(directory) / "extra.mp4"
                extra_path.write_bytes(b"extra")
                extra = MediaAsset.objects.create(kind="encoded", file_name="extra.mp4", smb_reference=str(extra_path))
                PreparationBatchItem.objects.create(batch=batch, asset=extra, encode_before_transfer=False)
                result = process_preparation_batch(batch)
                batch.refresh_from_db()
                self.assertFalse(result["complete"])
                self.assertEqual(batch.approval_1_status, "stale")

                batch.items.filter(asset=extra).delete()
                approve_preparation_batch(batch)
                settings = UltraNexusTargetSettings.objects.get(target=self.device, is_current=True)
                settings.host = "different-controller.test"
                settings.save(update_fields=["host"])
                result = process_preparation_batch(batch)
                batch.refresh_from_db()
                self.assertFalse(result["complete"])
                self.assertEqual(batch.approval_1_status, "stale")

        def test_legacy_binding_requires_explicit_attestation_for_schedule(self):
            binding = MediaBinding.objects.create(
                asset=self.occurrence.asset, target=self.device, binding_type="encoded",
                verification_basis="legacy", external_reference="/Vol1/mpeg/episode.mxf",
            )
            self.assertIsNone(schedule_ready_binding(self.occurrence.asset, self.device))
            binding.legacy_attestation_hash = "a" * 64
            binding.legacy_attested_at = datetime(2026, 1, 1, tzinfo=timezone.utc)
            binding.legacy_attested_by = "owner"
            binding.save()
            self.assertEqual(schedule_ready_binding(self.occurrence.asset, self.device), binding)

        def test_full_week_form_requires_authoritative_reconciliation(self):
            response = self.client.post("/automation/", {
                "action": "create_publication", "target": self.device.pk,
                "occurrences": [self.occurrence.pk], "workflow_mode": "full_week",
                "reconciliation_mode": "preserve",
            })
            self.assertEqual(response.status_code, 200)
            self.assertContains(response, "requires ChannelDex-owned authoritative reconciliation")
            self.assertEqual(SchedulePublicationBatch.objects.count(), 0)

        def test_selected_changes_generate_immutable_validated_nmg(self):
            with TemporaryDirectory() as directory, override_settings(DATA_DIR=directory):
                base = bytearray(34_923_624)
                base[:8] = MAGIC
                base[VERSION_OFFSET:VERSION_OFFSET + 8] = b"7.0.3.48"
                from tests.test_ultranexus_bin import fixture as bin_fixture
                from pubtv.ultranexus.bin import RESOURCE_BASE as BIN_RESOURCE_BASE, SCHEDULE_BASE as BIN_SCHEDULE_BASE
                bin_bytes = bin_fixture()
                base[RESOURCE_BASE:RESOURCE_BASE + 518] = bin_bytes[BIN_RESOURCE_BASE:BIN_RESOURCE_BASE + 518]
                gap, template = SCHEDULE_BASE, SCHEDULE_BASE + SCHEDULE_STRIDE
                struct.pack_into("<II", base, gap, 0, 0)
                base[gap + 0x1a] = 4
                struct.pack_into("<III", base, gap + 0x1b, 630000, 630000, 631800)
                struct.pack_into("<I", base, gap + 0x33, 1800)
                gap_title = b"Switchback 0:30:00\0"
                base[gap + 0x57:gap + 0x57 + len(gap_title)] = gap_title
                base[template:template + SCHEDULE_STRIDE] = bin_bytes[
                    BIN_SCHEDULE_BASE:BIN_SCHEDULE_BASE + SCHEDULE_STRIDE]
                base_path = Path(directory) / "base.nmg"
                base_path.write_bytes(base)
                bin_path = Path(directory) / "base.bin"
                bin_path.write_bytes(bin_bytes)
                nmg_template_hash = hashlib.sha256(
                    base[RESOURCE_BASE:RESOURCE_BASE + 518]
                    + base[template:template + SCHEDULE_STRIDE]
                ).hexdigest()
                bin_template_hash = hashlib.sha256(
                    bin_bytes[BIN_RESOURCE_BASE:BIN_RESOURCE_BASE + 518]
                    + bin_bytes[BIN_SCHEDULE_BASE:BIN_SCHEDULE_BASE + SCHEDULE_STRIDE]
                ).hexdigest()
                probe = {"streams": [
                    {"codec_type": "video", "codec_name": "h264", "width": 1920, "height": 1080,
                     "duration": "1800", "bit_rate": "6491701", "r_frame_rate": "30000/1001"},
                    {"codec_type": "audio", "codec_name": "aac", "sample_rate": "48000", "channels": 1},
                ]}
                inspection = MediaInspection.objects.create(
                    asset=self.occurrence.asset, target=self.device, local_path="/media/episode.mp4",
                    file_size=1, probe_json=probe, profile="Nexus Mono", status="passed", media_hash="b" * 64,
                )
                MediaBinding.objects.create(
                    asset=self.occurrence.asset, target=self.device, binding_type="encoded",
                    media_id_uint16=8, resource_reference_uint32=88, bare_filename="episode.mp4",
                    verification_basis="locally_verified", local_inspection=inspection,
                    external_reference="/Vol1/mpeg/episode.mp4",
                )
                UltraNexusTargetSettings.objects.create(
                    target=self.device, version=1, is_current=True, base_nmg_path=str(base_path),
                    base_nmg_hash=hashlib.sha256(base).hexdigest(),
                    base_bin_path=str(bin_path), base_bin_hash=hashlib.sha256(bin_bytes).hexdigest(),
                    media_directory="/Vol1/mpeg", schedule_path="/internal/schedule/schedule.bin",
                    controller_family="UltraNEXUS-HD", firmware_version="7.0.3.48",
                    output_number=1, media_profile="Nexus Mono", qualification_status="passed",
                    profile_identity_hash="1" * 64, nmg_template_sha256=nmg_template_hash,
                    bin_template_sha256=bin_template_hash, qualification_evidence_hash="3" * 64,
                    settings={"nmg_resource_template_reference": 100,
                              "nmg_schedule_template_base": template,
                              "bin_resource_template_reference": 100,
                              "bin_schedule_template_slot": 0},
                )
                batch = SchedulePublicationBatch.objects.create(target=self.device)
                batch.occurrence_selections.create(occurrence=self.occurrence, occurrence_revision=self.occurrence.revision)
                stale_path = Path(directory) / "stale.nmg"
                stale_path.write_bytes(b"stale")
                ArtifactRevision.objects.create(
                    publication_batch=batch, artifact_type="nmg", revision=1,
                    file_reference=str(stale_path), content_hash="0" * 64,
                    manifest={"items": []}, validation={"status": "passed"},
                )
                artifact = generate_nmg_artifact(batch)
                self.assertEqual(artifact.revision, 2)
                self.assertEqual(artifact.validation["status"], "passed")
                self.assertEqual(artifact.validation["audit"], artifact.manifest["audit"])
                self.assertTrue(artifact.manifest["audit"]["ranges"])
                self.assertEqual(hashlib.sha256(Path(artifact.file_reference).read_bytes()).hexdigest(), artifact.content_hash)
                generated = Path(artifact.file_reference).read_bytes()
                program = SCHEDULE_BASE
                if generated[program + 0x57:program + 0x61].startswith(b"Switchback"):
                    program += SCHEDULE_STRIDE
                self.assertEqual(struct.unpack_from("<I", generated, program + 0x1b)[0], 630000)
                captured_hash = hashlib.sha256(bin_bytes).hexdigest()
                snapshot = ControllerSnapshot.objects.create(
                    target=self.device, revision=1, snapshot_hash=captured_hash,
                    source_reference=str(bin_path),
                    payload={"captured_sha256": captured_hash, "captured_manifest": {}},
                )
                batch.controller_snapshot = snapshot
                batch.controller_snapshot_hash = snapshot.snapshot_hash
                batch.save(update_fields=["controller_snapshot", "controller_snapshot_hash"])
                bin_artifact = generate_bin_artifact(batch)
                self.assertEqual(bin_artifact.validation["parity"]["status"], "passed")
                self.assertEqual(bin_artifact.validation["parity"]["semantics"], "affected-only")
                batch.refresh_from_db()
                self.assertEqual(bin_artifact.manifest["mutation_plan_hash"], batch.mutation_plan_hash)
                self.assertEqual(batch.mutation_plan["controller_snapshot_hash"], snapshot.snapshot_hash)
                self.assertEqual(bin_artifact.manifest["introduced_resource_references"], [88])
                candidate = Path(bin_artifact.file_reference).read_bytes()
                self.assertEqual(
                    candidate[BIN_SCHEDULE_BASE:BIN_SCHEDULE_BASE + SCHEDULE_STRIDE],
                    bin_bytes[BIN_SCHEDULE_BASE:BIN_SCHEDULE_BASE + SCHEDULE_STRIDE],
                )
                blockers = publication_artifact_blockers(batch)
                self.assertNotIn("NMG occurrence revisions do not match the publication batch", blockers)
                self.assertEqual(publication_snapshot(batch)["artifacts"][0]["revision"], 2)
                before_files = set((Path(directory) / "ultranexus" / "schedules").iterdir())
                before_revisions = ArtifactRevision.objects.filter(
                    publication_batch=batch, artifact_type="bin").count()
                with patch("pubtv.operations.automation.os.replace", side_effect=OSError("disk failure")):
                    with self.assertRaisesRegex(OSError, "disk failure"):
                        generate_bin_artifact(batch)
                self.assertEqual(ArtifactRevision.objects.filter(
                    publication_batch=batch, artifact_type="bin").count(), before_revisions)
                self.assertEqual(set((Path(directory) / "ultranexus" / "schedules").iterdir()), before_files)
                self.assertEqual(hashlib.sha256(bin_path.read_bytes()).hexdigest(), hashlib.sha256(bin_bytes).hexdigest())
                with self.assertRaises(Exception):
                    artifact.save()
except ImportError:
    pass
