from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import Mock, patch
from datetime import timedelta

from django.test import Client, TestCase, override_settings
from django.utils import timezone

from pubtv.operations.automation import generate_bin_artifact, process_due_job_state
from pubtv.operations.forms import CarryForwardSettingsForm
from pubtv.operations.models import (
    AuditEvent, ControllerSnapshot, Device, PublicationJob,
    SchedulePublicationBatch, Station, UltraNexusTargetSettings,
)
from pubtv.operations.publication_review import capture_controller_snapshot


class FreshControllerBinContractTests(TestCase):
    def setUp(self):
        self.station = Station.objects.create()
        self.device = Device.objects.create(name="WinLGX")

    def test_station_default_form_and_audit_setting(self):
        self.assertTrue(self.station.auto_pull_controller_schedule)
        form = CarryForwardSettingsForm({"auto_pull_controller_schedule": ""}, instance=self.station)
        self.assertTrue(form.is_valid())
        response = Client().post("/settings/", {"carry_forward_unassigned_episodes": "", "auto_pull_controller_schedule": ""})
        self.assertEqual(response.status_code, 302)
        self.assertFalse(Station.objects.get(pk=self.station.pk).auto_pull_controller_schedule)
        self.assertTrue(AuditEvent.objects.filter(entity="Station", summary__icontains="manual controller pull").exists())

    def test_manual_capture_records_metadata_and_identical_pulls_are_audited(self):
        settings = UltraNexusTargetSettings.objects.create(
            target=self.device, version=1, is_current=True, host="controller", port=21,
            schedule_path="/internal/schedule/schedule.bin", media_directory="/Vol1/mpeg",
            controller_family="UltraNEXUS-HD", firmware_version="7.0.3.48", output_number=1,
            media_profile="Nexus Mono", qualification_status="passed",
            profile_identity_hash="a" * 64, nmg_template_sha256="b" * 64,
            bin_template_sha256="c" * 64, qualification_evidence_hash="d" * 64,
            settings_hash="e" * 64,
        )
        batch = SchedulePublicationBatch.objects.create(target=self.device)
        fake_image = Mock()
        adapter = Mock()
        with TemporaryDirectory() as directory, override_settings(DATA_DIR=directory), \
                patch("pubtv.operations.publication_review.BinImage.from_file", return_value=fake_image), \
                patch("pubtv.operations.publication_review._manifest", return_value={"sha256": "f" * 64}), \
                patch("pubtv.operations.publication_review.sha256_file", return_value="f" * 64):
            adapter.download.side_effect = lambda _src, dst: Path(dst).write_bytes(b"same")
            first = capture_controller_snapshot(batch, adapter=adapter, trigger="manual", actor="owner")
            batch.refresh_from_db()
            second = capture_controller_snapshot(batch, adapter=adapter, trigger="manual", actor="owner")
        self.assertEqual(first.snapshot_hash, second.snapshot_hash)
        self.assertEqual(ControllerSnapshot.objects.filter(target=self.device).count(), 2)
        self.assertEqual(second.trigger, "manual")
        self.assertEqual(second.settings_revision, settings.version)
        self.assertEqual(batch.controller_snapshot_hash, second.snapshot_hash)

    def test_capture_reloads_batch_before_invalidating_concurrent_approval(self):
        UltraNexusTargetSettings.objects.create(
            target=self.device, version=1, is_current=True, host="controller", port=21,
            schedule_path="/internal/schedule/schedule.bin", media_directory="/Vol1/mpeg",
            controller_family="UltraNEXUS-HD", firmware_version="7.0.3.48", output_number=1,
            media_profile="Nexus Mono", qualification_status="passed",
            profile_identity_hash="a" * 64, nmg_template_sha256="b" * 64,
            bin_template_sha256="c" * 64, qualification_evidence_hash="d" * 64,
            settings_hash="e" * 64,
        )
        old = ControllerSnapshot.objects.create(
            target=self.device, revision=1, snapshot_hash="a" * 64,
            payload={"captured_sha256": "a" * 64}, source_reference="/tmp/old.bin")
        batch = SchedulePublicationBatch.objects.create(
            target=self.device, controller_snapshot=old, controller_snapshot_hash=old.snapshot_hash)
        SchedulePublicationBatch.objects.filter(pk=batch.pk).update(
            approval_2_status="approved", approval_2_hash="approved")
        adapter = Mock()
        with TemporaryDirectory() as directory, override_settings(DATA_DIR=directory), \
                patch("pubtv.operations.publication_review.BinImage.from_file", return_value=Mock()), \
                patch("pubtv.operations.publication_review._manifest", return_value={"sha256": "b" * 64}), \
                patch("pubtv.operations.publication_review.sha256_file", return_value="b" * 64):
            adapter.download.side_effect = lambda _src, dst: Path(dst).write_bytes(b"changed")
            capture_controller_snapshot(batch, adapter=adapter)
        batch.refresh_from_db()
        self.assertEqual(batch.approval_2_status, "stale")

    def test_generation_rejects_publication_without_batch_bound_snapshot(self):
        batch = SchedulePublicationBatch.objects.create(target=self.device)
        with patch("pubtv.operations.automation._target_config", return_value=(Mock(), {"schedule_path": "/internal/schedule/schedule.bin"})), \
                patch("pubtv.operations.automation.restricted_target_blockers", return_value=[]):
            with self.assertRaisesRegex(ValueError, "fresh batch-bound"):
                generate_bin_artifact(batch)

    def test_worker_requires_manual_pull_when_automatic_mode_is_disabled(self):
        self.station.auto_pull_controller_schedule = False
        self.station.save(update_fields=["auto_pull_controller_schedule"])
        batch = SchedulePublicationBatch.objects.create(target=self.device)
        job = PublicationJob.objects.create(publication_batch=batch, kind="prepare_publication", idempotency_key="fresh-manual")
        process_due_job_state(job)
        job.refresh_from_db()
        self.assertEqual(job.status, "failed")
        self.assertIn("manual controller pull", job.error)

    def test_future_queued_job_is_not_claimed(self):
        batch = SchedulePublicationBatch.objects.create(
            target=self.device, requested_activation_at=timezone.now() + timedelta(hours=1))
        job = PublicationJob.objects.create(publication_batch=batch, kind="prepare_publication",
                                            idempotency_key="future-queued")
        result = process_due_job_state(job)
        result.refresh_from_db()
        self.assertEqual(result.status, "queued")

    def test_direct_running_job_is_worker_owned_and_not_reprocessed(self):
        batch = SchedulePublicationBatch.objects.create(target=self.device)
        job = PublicationJob.objects.create(publication_batch=batch, kind="generate_bin",
                                            idempotency_key="running-owned", status="running")
        with patch("pubtv.operations.automation.generate_bin_artifact") as generate:
            result = process_due_job_state(job)
        self.assertEqual(result.status, "running")
        generate.assert_not_called()

    def test_automatic_job_refreshes_an_existing_bound_snapshot(self):
        old = ControllerSnapshot.objects.create(
            target=self.device, revision=1, snapshot_hash="a" * 64,
            payload={"captured_sha256": "a" * 64}, source_reference="/tmp/old.bin")
        batch = SchedulePublicationBatch.objects.create(
            target=self.device, controller_snapshot=old, controller_snapshot_hash=old.snapshot_hash)
        job = PublicationJob.objects.create(
            publication_batch=batch, kind="prepare_publication", idempotency_key="refresh-existing")

        def refresh(publication, automatic=False):
            self.assertTrue(automatic)
            snapshot = ControllerSnapshot.objects.create(
                target=self.device, revision=2, snapshot_hash="b" * 64,
                payload={"captured_sha256": "b" * 64}, source_reference="/tmp/new.bin",
                trigger="automatic")
            publication.controller_snapshot = snapshot
            publication.controller_snapshot_hash = snapshot.snapshot_hash
            publication.save(update_fields=["controller_snapshot", "controller_snapshot_hash"])
            return snapshot

        with patch("pubtv.operations.publication_review.pull_current_controller_schedule",
                   side_effect=refresh) as pull:
            result = process_due_job_state(job)
        result.refresh_from_db()
        batch.refresh_from_db()
        self.assertEqual(result.status, "succeeded")
        self.assertEqual(batch.controller_snapshot_hash, "b" * 64)
        pull.assert_called_once()
