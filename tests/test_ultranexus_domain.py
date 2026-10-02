from datetime import datetime, timezone as dt_timezone

from django.core.exceptions import ValidationError
from django.test import TestCase

from pubtv.operations.models import (
    ArtifactRevision,
    ControllerSnapshot,
    Device,
    Episode,
    MediaAsset,
    MediaInspection,
    MediaBinding,
    Occurrence,
    PreparationBatch,
    PreparationJob,
    PreparationBatchItem,
    PublicationJob,
    SchedulePublicationBatch,
    Station,
    UltraNexusTargetSettings,
)
from pubtv.operations.automation import restricted_target_blockers


class UltraNexusDomainTests(TestCase):
    def setUp(self):
        self.station = Station.objects.create()
        self.device = Device.objects.create(name="UltraNEXUS")
        show = self.station.shows.create(title="News", code="news")
        episode = Episode.objects.create(show=show, title="One")
        self.asset = MediaAsset.objects.create(episode=episode, kind="encoded", file_name="one.mxf")
        self.occurrence = Occurrence.objects.create(
            station=self.station, show=show, episode=episode, item_type="episode", label="One",
            starts_at=datetime(2026, 1, 1, tzinfo=dt_timezone.utc), planned_duration_seconds=1800,
        )

    def test_defaults_keep_encoding_and_preserve_reconciliation(self):
        batch = PreparationBatch.objects.create(target=self.device)
        item = PreparationBatchItem.objects.create(batch=batch, asset=self.asset)
        publication = SchedulePublicationBatch.objects.create(target=self.device)
        self.assertTrue(item.encode_before_transfer)
        self.assertEqual(publication.reconciliation_mode, "preserve")
        self.assertEqual(publication.approval_2_status, "pending")
        self.assertEqual(publication.workflow_mode, "selected_changes")

    def test_full_week_requires_authoritative_reconciliation(self):
        publication = SchedulePublicationBatch(target=self.device, workflow_mode="full_week")
        with self.assertRaises(ValidationError):
            publication.full_clean()
        publication.reconciliation_mode = "authoritative"
        publication.full_clean()

    def test_target_scoped_versions_and_current_settings_are_unique(self):
        UltraNexusTargetSettings.objects.create(target=self.device, version=1)
        duplicate = UltraNexusTargetSettings(target=self.device, version=1)
        with self.assertRaises(ValidationError):
            duplicate.validate_constraints()
        UltraNexusTargetSettings.objects.create(target=self.device, version=2, is_current=True)
        another_current = UltraNexusTargetSettings(target=self.device, version=3, is_current=True)
        with self.assertRaises(ValidationError):
            another_current.validate_constraints()

    def test_approval_boundaries_are_separate_and_require_snapshots(self):
        batch = PreparationBatch.objects.create(target=self.device)
        item = PreparationBatchItem(batch=batch, asset=self.asset, approval_1_status="approved")
        with self.assertRaises(ValidationError):
            item.full_clean()
        item.approval_1_snapshot = {"asset": str(self.asset.asset_id)}
        item.approval_1_hash = "a" * 64
        item.approval_1_at = datetime(2026, 1, 1, tzinfo=dt_timezone.utc)
        item.full_clean()
        publication = SchedulePublicationBatch(target=self.device, approval_2_status="approved")
        with self.assertRaises(ValidationError):
            publication.full_clean()

    def test_stale_approval_is_explicit_and_snapshots_are_immutable(self):
        batch = PreparationBatch.objects.create(target=self.device)
        item = PreparationBatchItem.objects.create(batch=batch, asset=self.asset, approval_1_status="stale")
        self.assertTrue(item.approval_1_snapshot_stale)
        snapshot = ControllerSnapshot.objects.create(target=self.device, snapshot_hash="b" * 64, payload={"slot": 1})
        snapshot.payload = {"slot": 2}
        with self.assertRaises(ValidationError):
            snapshot.save()
        inspection = MediaInspection.objects.create(
            asset=self.asset, target=self.device, status="passed", local_path="/media/one.mxf", media_hash="c" * 64,
        )
        with self.assertRaises(ValidationError):
            inspection.delete()

    def test_passed_inspection_requires_exact_local_probe_identity(self):
        inspection = MediaInspection(asset=self.asset, target=self.device, status="passed", local_path="/media/one.mxf")
        with self.assertRaises(ValidationError):
            inspection.full_clean()
        inspection.media_hash = "c" * 64
        inspection.full_clean()

    def test_local_binding_requires_inspection_and_ids_are_target_scoped(self):
        binding = MediaBinding(
            asset=self.asset, target=self.device, verification_basis="locally_verified", media_id_uint16=7,
        )
        with self.assertRaises(ValidationError):
            binding.full_clean()
        inspection = MediaInspection.objects.create(
            asset=self.asset, target=self.device, status="passed", local_path="/media/one.mxf", media_hash="c" * 64,
        )
        binding.local_inspection = inspection
        binding.bare_filename = "Episode One.MXF"
        binding.full_clean()
        self.assertEqual(binding.casefold_key, "episode one.mxf")
        binding.save()
        duplicate = MediaBinding(asset=self.asset, target=self.device, media_id_uint16=7)
        with self.assertRaises(ValidationError):
            duplicate.validate_constraints()

    def test_target_settings_reject_invalid_port_and_non_opaque_secret_reference(self):
        settings = UltraNexusTargetSettings(target=self.device, port=70000)
        with self.assertRaises(ValidationError):
            settings.full_clean()
        settings.port = 2143
        settings.secret_reference = "raw secret"
        with self.assertRaises(ValidationError):
            settings.full_clean()

    def test_passed_target_qualification_requires_evidence_and_exact_contract(self):
        settings = UltraNexusTargetSettings(
            target=self.device, settings={"profile": "restricted"}, qualification_status="passed",
            controller_family="UltraNEXUS-HD", firmware_version="7.0.3.48",
            output_number=1, media_profile="Nexus Mono",
            media_directory="/Vol1/mpeg", schedule_path="/internal/schedule/schedule.bin",
            profile_identity_hash="1" * 64, nmg_template_sha256="2" * 64,
            bin_template_sha256="3" * 64, base_nmg_hash="4" * 64,
            base_bin_hash="5" * 64,
        )
        with self.assertRaises(ValidationError):
            settings.full_clean()
        settings.qualification_evidence_hash = "6" * 64
        settings.full_clean()
        self.assertEqual(restricted_target_blockers(settings), [])
        settings.output_number = 2
        self.assertTrue(any("output_number" in blocker for blocker in restricted_target_blockers(settings)))

    def test_schedule_artifacts_are_immutable_and_publication_jobs_idempotent(self):
        publication = SchedulePublicationBatch.objects.create(target=self.device)
        artifact = ArtifactRevision.objects.create(
            publication_batch=publication, artifact_type="nmg", file_reference="schedule.nmg", content_hash="d" * 64,
        )
        artifact.file_reference = "changed.nmg"
        with self.assertRaises(ValidationError):
            artifact.save()
        job = PublicationJob.objects.create(publication_batch=publication, kind="preview", idempotency_key="pub-1")
        duplicate = PublicationJob(publication_batch=publication, kind="preview", idempotency_key="pub-1")
        with self.assertRaises(ValidationError):
            duplicate.full_clean()

    def test_preparation_jobs_have_durable_idempotency_keys(self):
        batch = PreparationBatch.objects.create(target=self.device)
        PreparationJob.objects.create(batch=batch, idempotency_key="prepare-1")
        duplicate = PreparationJob(batch=batch, idempotency_key="prepare-1")
        with self.assertRaises(ValidationError):
            duplicate.full_clean()
