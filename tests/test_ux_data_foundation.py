from datetime import datetime, timezone as dt_timezone

from django.core.exceptions import ValidationError
from django.test import TestCase

from pubtv.operations.models import (
    AssetPreparation,
    AssetTargetTransfer,
    Delivery,
    Device,
    Episode,
    MediaAsset,
    Occurrence,
    OccurrenceProgramming,
    Preparation,
    Producer,
    RecurrenceSlot,
    Show,
    Station,
    UploadedOccurrenceCoverage,
    UploadedScheduleRevision,
)
from pubtv.operations.services import preparation_readiness, suggested_pending_episode


class CanonicalDataFoundationTests(TestCase):
    def setUp(self):
        self.station = Station.objects.create()
        self.show = Show.objects.create(station=self.station, title="News", code="news", slot_duration_seconds=1800)
        self.episode = Episode.objects.create(show=self.show, title="One")
        self.occurrence = Occurrence.objects.create(
            station=self.station, show=self.show, episode=self.episode,
            item_type="episode", label="legacy", starts_at=datetime(2026, 1, 1, tzinfo=dt_timezone.utc),
            planned_duration_seconds=1800,
        )
        self.device = Device.objects.create(name="WinLGX")

    def test_episode_received_at_prefers_earliest_delivery_and_falls_back(self):
        legacy = datetime(2026, 1, 1, tzinfo=dt_timezone.utc)
        self.episode.legacy_received_at = legacy
        self.episode.save(update_fields=["legacy_received_at"])
        self.assertEqual(self.episode.received_at, legacy)
        delivery_time = datetime(2025, 12, 31, tzinfo=dt_timezone.utc)
        delivery = Delivery.objects.create(method="dropbox", reference="ref", received_at=delivery_time)
        delivery.episodes.add(self.episode)
        self.assertEqual(self.episode.received_at, delivery_time)

    def test_media_asset_does_not_mirror_legacy_label(self):
        asset = MediaAsset.objects.create(episode=self.episode, file_name="canonical.mov", legacy_label="old")
        asset.file_name = "new.mov"
        asset.save(update_fields=["file_name"])
        asset.refresh_from_db()
        self.assertEqual(asset.file_name, "new.mov")
        self.assertEqual(asset.legacy_label, "old")

    def test_legacy_aliases_preserve_old_python_callers_without_mirroring(self):
        asset = MediaAsset(episode=self.episode, kind="source", label="old.mov")
        asset.save()
        self.assertEqual(asset.label, "old.mov")
        asset.label = "canonical.mov"
        asset.save()
        asset.refresh_from_db()
        self.assertEqual(asset.label, "canonical.mov")
        self.assertEqual(asset.legacy_label, "old.mov")

    def test_asset_transfer_is_unique_per_device(self):
        asset = MediaAsset.objects.create(episode=self.episode, file_name="episode.mov", kind="encoded")
        AssetTargetTransfer.objects.create(asset=asset, device=self.device, ftp_details="ftp://box")
        duplicate = AssetTargetTransfer(asset=asset, device=self.device, ftp_details="ftp://box")
        with self.assertRaises(ValidationError):
            duplicate.validate_constraints()

    def test_programming_is_unique_per_occurrence_and_device(self):
        OccurrenceProgramming.objects.create(occurrence=self.occurrence, device=self.device, slot_assignment="A1")
        duplicate = OccurrenceProgramming(occurrence=self.occurrence, device=self.device)
        with self.assertRaises(ValidationError):
            duplicate.validate_constraints()

    def test_recurrence_slot_follows_show_duration_without_mutating_show(self):
        slot = RecurrenceSlot.objects.create(
            station=self.station, show=self.show, weekday=0, start_time="07:00", duration_seconds=900,
        )
        self.assertEqual(slot.duration_seconds, 1800)
        self.assertEqual(Show.objects.get(pk=self.show.pk).slot_duration_seconds, 1800)

    def test_occurrence_display_label_derives_episode_identity(self):
        self.assertEqual(self.occurrence.display_label, "News · One")

    def test_asset_preparation_requires_timestamp_for_available_source(self):
        asset = MediaAsset.objects.create(episode=self.episode, file_name="source.mov", kind="source")
        record = AssetPreparation(asset=asset, source_available="yes")
        with self.assertRaises(ValidationError):
            record.full_clean()

    def test_legacy_preparation_aliases_write_only_legacy_columns(self):
        prep = Preparation(occurrence=self.occurrence, ftp_output_device="WinLGX", uploaded_schedule_revision="rev-1")
        prep.save()
        self.assertEqual(prep.ftp_output_device, "WinLGX")
        self.assertEqual(prep.uploaded_schedule_revision, "rev-1")
        self.assertEqual(prep.legacy_ftp_output_device, "WinLGX")
        self.assertEqual(prep.legacy_uploaded_schedule_revision, "rev-1")

    def test_pending_episode_order_uses_preserved_receipt_column(self):
        earlier = Episode.objects.create(
            show=self.show, title="Earlier", legacy_received_at=datetime(2025, 12, 1, tzinfo=dt_timezone.utc),
            intended_air_order=None,
        )
        self.assertEqual(suggested_pending_episode(self.show).pk, earlier.pk)

    def test_readiness_requires_one_device_chain(self):
        other_device = Device.objects.create(name="Other")
        asset = MediaAsset.objects.create(episode=self.episode, file_name="episode.mov", kind="encoded")
        self.occurrence.asset = asset
        self.occurrence.save(skip_revision=True, update_fields=["asset"])
        AssetPreparation.objects.create(asset=asset, source_available="yes", ame_preset="preset")
        AssetTargetTransfer.objects.create(asset=asset, device=self.device, ftp_details="ftp", library_registration="L1")
        OccurrenceProgramming.objects.create(occurrence=self.occurrence, device=other_device, slot_assignment="B2")
        upload = UploadedScheduleRevision.objects.create(device=self.device, external_reference="upload")
        UploadedOccurrenceCoverage.objects.create(upload=upload, occurrence=self.occurrence, occurrence_revision=self.occurrence.revision)
        readiness = preparation_readiness(self.occurrence)
        self.assertFalse(readiness["ready"])
        self.assertEqual(readiness["complete"], 2)

        programming = self.occurrence.programming.first()
        programming.device = self.device
        programming.save(update_fields=["device"])
        self.assertTrue(preparation_readiness(self.occurrence)["ready"])

    def test_readiness_does_not_substitute_episode_asset_for_occurrence_asset(self):
        episode_asset = MediaAsset.objects.create(episode=self.episode, file_name="prepared.mov", kind="encoded")
        AssetPreparation.objects.create(asset=episode_asset, source_available="yes", ame_preset="preset")
        readiness = preparation_readiness(self.occurrence)
        self.assertFalse(readiness["ready"])
        self.assertEqual(readiness["complete"], 0)
