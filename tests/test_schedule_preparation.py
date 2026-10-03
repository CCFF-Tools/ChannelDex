import json
import uuid
from datetime import date, datetime, timedelta, timezone as dt_timezone

from django.core import signing
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from pubtv.operations.models import (ArtifactRevision, Device, Episode, MediaAsset,
    MediaBinding, MediaInspection, Occurrence, PublicationJob, SchedulePublicationBatch,
    Show, Station, UltraNexusTargetSettings, UploadedOccurrenceCoverage,
    UploadedScheduleRevision, WeeklyEpisodeAssignment)
from pubtv.operations.schedule_preparation import (SALT, SchedulePreparationError,
    SchedulePreparationStale, confirm_schedule_preparation, preview_schedule_preparation,
    publication_readiness)


class SchedulePreparationAcceptanceTests(TestCase):
    def setUp(self):
        self.now = datetime(2026, 10, 2, 12, tzinfo=dt_timezone.utc)
        self.station = Station.objects.create(name="PUB-TV", timezone="America/Detroit")
        self.show = Show.objects.create(station=self.station, title="Civic Hour", code="civic")
        self.target = Device.objects.create(name="UltraNEXUS A")
        self.other_target = Device.objects.create(name="UltraNEXUS B")
        self.settings = UltraNexusTargetSettings.objects.create(target=self.target, version=1, is_current=True,
            host="controller.test", media_directory="/Vol1/mpeg", secret_reference="channeldex:test")
        self.episode = Episode.objects.create(show=self.show, title="Episode One")
        self.asset = MediaAsset.objects.create(episode=self.episode, kind="encoded", file_name="one.mxf",
                                               version="v1", smb_reference="/private/source/one.mov")
        self.assignment = WeeklyEpisodeAssignment.objects.create(show=self.show, week_start=date(2026, 10, 5),
            premiere_date=date(2026, 10, 7), episode=self.episode, selection_type="premiere")
        self.occurrence = self.make_occurrence(self.assignment, self.now + timedelta(days=5))

    def make_occurrence(self, assignment, starts_at, *, asset=None, status="planned", label="Episode"):
        return Occurrence.objects.create(station=self.station, show=self.show, episode=assignment.episode,
            asset=asset, weekly_assignment=assignment, item_type="episode", label=label,
            starts_at=starts_at, planned_duration_seconds=1800, status=status)

    def choice(self, assignment=None, asset=None):
        return {"assignment": assignment or self.assignment, "asset": self.asset if asset is None else asset}

    def preview(self, choices=None):
        return preview_schedule_preparation(self.target, choices or [self.choice()], now=self.now)

    def make_ready(self, asset=None, target=None):
        asset, target = asset or self.asset, target or self.target
        inspection = MediaInspection.objects.create(asset=asset, target=target, local_path="/private/rendition.mxf",
            file_size=10, probe_json={}, status="passed", media_hash=(str(asset.asset_id).replace("-", "") * 2)[:64])
        return MediaBinding.objects.create(asset=asset, target=target, binding_type="encoded",
            verification_basis="locally_verified", local_inspection=inspection,
            external_reference=f"/Vol1/mpeg/{asset.file_name}")

    def test_waiting_media_can_be_confirmed_without_activation_or_generation(self):
        review = self.preview()
        self.assertFalse(review["ready"])
        self.assertEqual(review["rows"][0].readiness, "waiting")
        batch = confirm_schedule_preparation(self.target, [self.choice()], review["snapshot"], review_token=uuid.uuid4())
        self.assertEqual(publication_readiness(batch)["label"], "Waiting for media")
        self.make_ready()
        self.assertTrue(publication_readiness(batch)["ready"])
        batch.refresh_from_db()
        self.assertEqual(batch.status, "previewed")
        self.assertFalse(PublicationJob.objects.filter(publication_batch=batch).exists())
        self.assertFalse(ArtifactRevision.objects.filter(publication_batch=batch).exists())
        self.assertIsNone(batch.requested_activation_at)

    def test_occurrence_change_after_review_is_stale(self):
        review = self.preview()
        self.occurrence.label = "Changed after review"; self.occurrence.save()
        with self.assertRaises(SchedulePreparationStale):
            confirm_schedule_preparation(self.target, [self.choice()], review["snapshot"])

    def test_cycle_or_media_change_after_review_is_stale(self):
        review = self.preview()
        self.assignment.selection_type = "rerun"; self.assignment.save(update_fields=["selection_type"])
        with self.assertRaises(SchedulePreparationStale):
            confirm_schedule_preparation(self.target, [self.choice()], review["snapshot"])
        self.assignment.selection_type = "premiere"; self.assignment.save(update_fields=["selection_type"])
        review = self.preview()
        self.asset.version = "v2"; self.asset.save(update_fields=["version"])
        with self.assertRaises(SchedulePreparationStale):
            confirm_schedule_preparation(self.target, [self.choice()], review["snapshot"])

    def test_target_settings_change_after_review_is_stale(self):
        review = self.preview()
        self.settings.host = "replacement-controller.test"; self.settings.save(update_fields=["host"])
        with self.assertRaises(SchedulePreparationStale):
            confirm_schedule_preparation(self.target, [self.choice()], review["snapshot"])

    def test_replacing_existing_occurrence_media_requires_explicit_confirmation(self):
        old = MediaAsset.objects.create(episode=self.episode, kind="encoded", file_name="old.mxf", version="old")
        self.occurrence.asset = old; self.occurrence.save(update_fields=["asset"]); self.occurrence.refresh_from_db()
        review = self.preview()
        with self.assertRaisesMessage(SchedulePreparationError, "explicit confirmation"):
            confirm_schedule_preparation(self.target, [self.choice()], review["snapshot"])
        batch = confirm_schedule_preparation(self.target, [self.choice()], review["snapshot"], confirm_replacements=True)
        self.occurrence.refresh_from_db()
        self.assertEqual(self.occurrence.asset_id, self.asset.pk)
        self.assertEqual(batch.occurrence_selections.count(), 1)

    def test_coverage_excludes_only_active_matching_target_and_exact_revision(self):
        matching = self.occurrence
        wrong_target = self.make_occurrence(self.assignment, self.now + timedelta(days=6), label="Wrong target")
        stale_revision = self.make_occurrence(self.assignment, self.now + timedelta(days=7), label="Stale coverage")
        uncovered = self.make_occurrence(self.assignment, self.now + timedelta(days=8), label="Uncovered")
        active = UploadedScheduleRevision.objects.create(device=self.target, state="active")
        other = UploadedScheduleRevision.objects.create(device=self.other_target, state="active")
        UploadedOccurrenceCoverage.objects.create(upload=active, occurrence=matching, occurrence_revision=matching.revision)
        UploadedOccurrenceCoverage.objects.create(upload=other, occurrence=wrong_target, occurrence_revision=wrong_target.revision)
        UploadedOccurrenceCoverage.objects.create(upload=active, occurrence=stale_revision, occurrence_revision=stale_revision.revision - 1)
        review = self.preview()
        self.assertEqual([o.pk for o in review["included"]], [wrong_target.pk, stale_revision.pk, uncovered.pk])
        self.assertEqual([o.pk for o in review["excluded"]], [matching.pk])

    def test_past_cancelled_and_matching_covered_are_excluded(self):
        self.occurrence.starts_at = self.now - timedelta(minutes=1); self.occurrence.save(update_fields=["starts_at"])
        cancelled = self.make_occurrence(self.assignment, self.now + timedelta(days=6), status="cancelled")
        future = self.make_occurrence(self.assignment, self.now + timedelta(days=7))
        review = self.preview()
        self.assertEqual([o.pk for o in review["included"]], [future.pk])
        reasons = {entry["occurrence"].pk: entry["reason"] for entry in review["rows"][0].entries}
        self.assertEqual(reasons[self.occurrence.pk], "Past")
        self.assertEqual(reasons[cancelled.pk], "Cancelled")

    def test_multiple_cycles_and_assets_form_one_publication_and_media_can_be_reused_later(self):
        episode2 = Episode.objects.create(show=self.show, title="Episode Two")
        asset2 = MediaAsset.objects.create(episode=episode2, kind="encoded", file_name="two.mxf", version="v1")
        assignment2 = WeeklyEpisodeAssignment.objects.create(show=self.show, week_start=date(2026, 10, 12),
            premiere_date=date(2026, 10, 14), episode=episode2, selection_type="premiere")
        self.make_occurrence(assignment2, self.now + timedelta(days=12))
        choices = [self.choice(), {"assignment": assignment2, "asset": asset2}]
        review = self.preview(choices)
        batch = confirm_schedule_preparation(self.target, choices, review["snapshot"], review_token=uuid.uuid4())
        self.assertEqual(batch.cycle_selections.count(), 2)
        self.assertEqual(batch.occurrence_selections.count(), 2)

        later = WeeklyEpisodeAssignment.objects.create(show=self.show, week_start=date(2026, 10, 19),
            premiere_date=date(2026, 10, 21), episode=self.episode, selection_type="rerun")
        self.make_occurrence(later, self.now + timedelta(days=19))
        later_choice = {"assignment": later, "asset": self.asset}
        later_review = self.preview([later_choice])
        later_batch = confirm_schedule_preparation(self.target, [later_choice], later_review["snapshot"], review_token=uuid.uuid4())
        self.assertEqual(later_batch.cycle_selections.get().asset_id, self.asset.pk)

    def test_review_token_is_idempotent_and_cannot_authorize_different_proposal(self):
        token = uuid.uuid4(); review = self.preview()
        first = confirm_schedule_preparation(self.target, [self.choice()], review["snapshot"], review_token=token)
        again = confirm_schedule_preparation(self.target, [self.choice()], review["snapshot"], review_token=token)
        self.assertEqual(again.pk, first.pk)
        changed = dict(review["snapshot"]); changed["hash"] = "0" * 64
        with self.assertRaises(SchedulePreparationStale):
            confirm_schedule_preparation(self.target, [self.choice()], changed, review_token=token)

    def test_review_snapshot_and_signed_route_do_not_disclose_private_source_path(self):
        review = self.preview()
        self.assertNotIn(self.asset.smb_reference, json.dumps(review["snapshot"]))
        response = self.client.post(reverse("schedule-prepare"), {
            "target": self.target.pk, "assignment": [str(self.assignment.pk)],
            f"asset_{self.assignment.pk}": str(self.asset.pk), "action": "preview"})
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, self.asset.smb_reference)

    def test_signed_confirm_with_partial_asset_map_cannot_drop_a_row(self):
        episode2 = Episode.objects.create(show=self.show, title="Episode Two")
        asset2 = MediaAsset.objects.create(episode=episode2, kind="encoded", file_name="two.mxf")
        assignment2 = WeeklyEpisodeAssignment.objects.create(show=self.show, week_start=date(2026, 10, 12),
            premiere_date=date(2026, 10, 14), episode=episode2, selection_type="premiere")
        self.make_occurrence(assignment2, self.now + timedelta(days=12))
        choices = [self.choice(), {"assignment": assignment2, "asset": asset2}]
        snapshot = self.preview(choices)["snapshot"]
        malformed = signing.dumps({"target_id": self.target.pk,
            "ids": [str(self.assignment.pk), str(assignment2.pk)],
            "assets": {str(self.assignment.pk): str(self.asset.pk)},
            "snapshot": snapshot, "uuid": str(uuid.uuid4())}, salt=SALT)
        response = self.client.post(reverse("schedule-prepare"), {"action": "confirm", "review_token": malformed})
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "invalid or expired")
        self.assertFalse(SchedulePublicationBatch.objects.exists())
