from datetime import date, time
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from django.test import TestCase

from pubtv.operations.automation import process_due_job_state
from pubtv.operations.guided_delivery import (
    approve_and_queue_guided_delivery, confirm_guided_intake,
    guided_delivery_versions, process_guided_delivery_job,
    queue_guided_publication_jobs, record_guided_rollback,
    start_guided_encoded_delivery,
)
from pubtv.operations.models import (
    Device, Episode, GuidedEpisodeDelivery, MediaAsset, PublicationJob,
    RecurrenceSlot, ScheduleDeliveryOperation, Show, Station, UltraNexusTargetSettings,
)


class GuidedEncodedDeliveryTests(TestCase):
    def setUp(self):
        self.station = Station.objects.create()
        self.show = Show.objects.create(station=self.station, title="Civic", code="civic")
        RecurrenceSlot.objects.create(station=self.station, show=self.show, weekday=2,
            start_time=time(19, 0), duration_seconds=1800, is_premiere=True)
        self.target = Device.objects.create(name="UltraNEXUS guided")
        UltraNexusTargetSettings.objects.create(
            target=self.target, is_current=True, host="controller.test",
            media_directory="/Vol1/mpeg", secret_reference="test:owner",
        )

    def test_intake_is_hash_bound_and_bypasses_encoding(self):
        with TemporaryDirectory() as directory:
            path = Path(directory) / "episode.mxf"
            path.write_bytes(b"encoded bytes")
            delivery = start_guided_encoded_delivery(target=self.target, show=self.show, file_path=str(path), episode_title="One")
            self.assertEqual(delivery.state, "intake_review")
            self.assertEqual(delivery.intake_snapshot["encode_before_transfer"], False)
            self.assertEqual(delivery.asset.kind, "encoded")
            self.assertEqual(len(delivery.intake_snapshot["file"]["sha256"]), 64)

    def test_changed_selected_bytes_block_confirmation(self):
        with TemporaryDirectory() as directory:
            path = Path(directory) / "episode.mxf"
            path.write_bytes(b"encoded bytes")
            delivery = start_guided_encoded_delivery(target=self.target, show=self.show, file_path=str(path), episode_title="One")
            path.write_bytes(b"changed bytes")
            with self.assertRaisesMessage(ValueError, "Selected encoded bytes changed"):
                confirm_guided_intake(delivery, premiere_date=date(2026, 10, 7))
            delivery.refresh_from_db()
            self.assertEqual(delivery.state, "blocked")

    def test_versions_are_visible_before_remote_work(self):
        with TemporaryDirectory() as directory:
            path = Path(directory) / "episode.mxf"
            path.write_bytes(b"encoded bytes")
            delivery = start_guided_encoded_delivery(target=self.target, show=self.show, file_path=str(path), episode_title="One")
            self.assertEqual(guided_delivery_versions(delivery), {
                "candidate": "", "current": "", "rollback": "",
                "candidate_path": "", "rollback_path": "", "remote_backup_path": "",
            })

    def test_routed_entry_is_discoverable_and_confirmation_is_idempotent(self):
        self.assertContains(self.client.get("/schedule/delivery/"), "Guided already-encoded delivery")
        with TemporaryDirectory() as directory:
            path = Path(directory) / "episode.mxf"
            path.write_bytes(b"encoded bytes")
            response = self.client.post("/schedule/guided-delivery/", {
                "action": "start", "target": self.target.pk, "show": self.show.pk,
                "episode_title": "One", "file_path": str(path),
            })
            self.assertRedirects(response, "/schedule/guided-delivery/")
            delivery = GuidedEpisodeDelivery.objects.get()
            first = confirm_guided_intake(delivery, premiere_date=date(2026, 10, 7))
            second = confirm_guided_intake(delivery, premiere_date=date(2026, 10, 7))
            self.assertEqual(first.preparation_batch_id, second.preparation_batch_id)
            self.assertEqual(GuidedEpisodeDelivery.objects.count(), 1)


class GuidedDeliveryIntegrationTests(TestCase):
    def setUp(self):
        from tests.test_ultranexus_publication_delivery import AttendedDeliveryTests
        AttendedDeliveryTests.setUp(self)
        self.show = Show.objects.create(station=self.station, title="Guided", code="guided")
        self.episode = Episode.objects.create(show=self.show, title="Episode")
        self.asset = MediaAsset.objects.create(
            episode=self.episode, kind="encoded", file_name="episode.mp4", version="v1")
        self.guided = GuidedEpisodeDelivery.objects.create(
            target=self.target, show=self.show, episode=self.episode, asset=self.asset,
            publication_batch=self.batch, state="change_review", intake_hash="5" * 64,
        )

    def _pending_review(self):
        self.batch.approval_2_status = "pending"
        self.batch.approval_2_hash = ""
        self.batch.approval_2_snapshot = {}
        self.batch.save(update_fields=["approval_2_status", "approval_2_hash", "approval_2_snapshot"])
        from pubtv.operations.publication_review import publication_review
        return publication_review(self.batch)

    def test_ordered_prepare_job_pulls_once_and_exposes_exact_review(self):
        self.guided.state = "cycle_planned"
        self.guided.save(update_fields=["state"])
        job = PublicationJob.objects.create(
            publication_batch=self.batch, kind="prepare_publication",
            idempotency_key="guided-prepare-integration")
        nmg = self.batch.artifact_revisions.get(artifact_type="nmg")
        binary = self.batch.artifact_revisions.get(artifact_type="bin")
        with patch("pubtv.operations.publication_review.pull_current_controller_schedule",
                   return_value=self.batch.controller_snapshot) as pull, \
             patch("pubtv.operations.automation.generate_nmg_artifact", return_value=nmg), \
             patch("pubtv.operations.automation.generate_bin_artifact", return_value=binary):
            process_due_job_state(job)
        job.refresh_from_db(); self.guided.refresh_from_db()
        self.assertEqual(job.status, "succeeded")
        self.assertEqual(self.guided.state, "change_review")
        self.assertEqual(self.guided.candidate_hash, self.candidate_hash)
        self.assertEqual(self.guided.current_hash, self.base_hash)
        pull.assert_called_once()
        response = self.client.get("/schedule/guided-delivery/")
        self.assertContains(response, "Exact controller change review")
        self.assertContains(response, "Approve changes and deliver now")

    def test_manual_mode_requires_explicit_fresh_pull_authorization(self):
        self.station.auto_pull_controller_schedule = False
        self.station.save(update_fields=["auto_pull_controller_schedule"])
        self.guided.state = "cycle_planned"
        self.guided.save(update_fields=["state"])
        job = PublicationJob.objects.create(
            publication_batch=self.batch, kind="prepare_publication",
            idempotency_key=f"guided:prepare:{self.batch.pk}:{self.guided.intake_hash}")
        with patch("pubtv.operations.publication_review.pull_current_controller_schedule") as pull:
            process_due_job_state(job)
        job.refresh_from_db()
        self.assertEqual(job.status, "failed")
        self.assertIn("explicitly authorized", job.error)
        pull.assert_not_called()

        queue_guided_publication_jobs(self.guided, manual_pull_requested=True)
        job.refresh_from_db()
        nmg = self.batch.artifact_revisions.get(artifact_type="nmg")
        binary = self.batch.artifact_revisions.get(artifact_type="bin")
        with patch("pubtv.operations.publication_review.pull_current_controller_schedule",
                   return_value=self.batch.controller_snapshot) as pull, \
             patch("pubtv.operations.automation.generate_nmg_artifact", return_value=nmg), \
             patch("pubtv.operations.automation.generate_bin_artifact", return_value=binary):
            process_due_job_state(job)
        job.refresh_from_db()
        self.assertEqual(job.status, "succeeded")
        pull.assert_called_once_with(self.batch, automatic=False)

    def test_approval_queues_one_delivery_and_activation_runs_once(self):
        review = self._pending_review()
        job = approve_and_queue_guided_delivery(self.guided, review["review_token"])
        artifact = self.batch.artifact_revisions.get(artifact_type="bin")
        calls = {"stage": 0, "activate": 0}

        def stage(batch_id, *, expected_hash):
            calls["stage"] += 1
            return ScheduleDeliveryOperation.objects.create(
                publication_batch=self.batch, target=self.target, artifact=artifact,
                approval_hash=self.batch.approval_2_hash, base_hash=self.base_hash,
                rollback_hash=self.base_hash, rollback_path=str(self.base_path),
                staging_path="/internal/schedule/candidate.part", state="staged")

        def activate(operation_id, *, expected_hash):
            calls["activate"] += 1
            operation = ScheduleDeliveryOperation.objects.get(pk=operation_id)
            operation.state = "activation_acknowledged"
            operation.save(update_fields=["state"])
            return operation

        process_guided_delivery_job(job, stage_fn=stage, activate_fn=activate)
        process_guided_delivery_job(job, stage_fn=stage, activate_fn=activate)
        self.guided.refresh_from_db(); job.refresh_from_db()
        self.assertEqual(calls, {"stage": 1, "activate": 1})
        self.assertEqual(job.status, "succeeded")
        self.assertEqual(self.guided.state, "verification_pending")
        self.assertEqual(self.guided.activation_count, 1)
        response = self.client.get("/schedule/guided-delivery/")
        self.assertContains(response, "Independent verification required")
        self.assertContains(response, self.base_hash)

    def test_stale_review_rejected_and_rollback_updates_visible_version(self):
        self._pending_review()
        with self.assertRaisesMessage(ValueError, "Review the current controller diff"):
            approve_and_queue_guided_delivery(self.guided, "tampered")
        self.assertFalse(PublicationJob.objects.filter(kind="guided_delivery").exists())
        artifact = self.batch.artifact_revisions.get(artifact_type="bin")
        operation = ScheduleDeliveryOperation.objects.create(
            publication_batch=self.batch, target=self.target, artifact=artifact,
            approval_hash="a" * 64, base_hash=self.base_hash,
            rollback_hash=self.base_hash, state="activation_acknowledged")
        self.guided.current_hash = self.candidate_hash
        self.guided.rollback_hash = self.base_hash
        self.guided.state = "verification_pending"
        self.guided.save(update_fields=["current_hash", "rollback_hash", "state"])
        record_guided_rollback(self.guided, operation)
        self.guided.refresh_from_db()
        self.assertEqual(self.guided.state, "rolled_back")
        self.assertEqual(self.guided.current_hash, self.base_hash)

    def test_guided_verification_prompt_calls_existing_evidence_boundary(self):
        artifact = self.batch.artifact_revisions.get(artifact_type="bin")
        operation = ScheduleDeliveryOperation.objects.create(
            publication_batch=self.batch, target=self.target, artifact=artifact,
            approval_hash=self.batch.approval_2_hash, base_hash=self.base_hash,
            rollback_hash=self.base_hash, state="activation_acknowledged")
        self.guided.state = "verification_pending"
        self.guided.candidate_hash = self.candidate_hash
        self.guided.activation_count = 1
        self.guided.save(update_fields=["state", "candidate_hash", "activation_count"])

        def observed(operation_id, *, evidence_file):
            operation.state = "activation_observed"
            operation.save(update_fields=["state"])
            return operation

        with patch("pubtv.operations.publication_delivery.observe_activation",
                   side_effect=observed) as observe:
            response = self.client.post("/schedule/guided-delivery/", {
                "action": "verify_activation", "delivery": self.guided.pk,
                "evidence_file": "/private/evidence.json", "confirm_observation": "on",
            })
        self.assertRedirects(response, "/schedule/guided-delivery/")
        observe.assert_called_once_with(operation.pk, evidence_file="/private/evidence.json")
        self.guided.refresh_from_db()
        self.assertEqual(self.guided.state, "verified")
