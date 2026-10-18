from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from django.test import Client, TestCase
from django.utils import timezone

from pubtv.operations.models import (
    Device,
    Episode,
    MediaAsset,
    Occurrence,
    RecurrenceSlot,
    Show,
    Station,
    WeeklyEpisodeAssignment,
)
from pubtv.operations.schedule_changes import (
    commit_cycle_review,
    load_cycle_review,
    load_duration_review,
    sign_cycle_review,
    sign_duration_review,
)
from pubtv.operations.services import (
    create_upload_snapshot,
    ensure_carry_forward_week,
    materialize_assignment,
    suggested_pending_episode,
)


class ScheduleChangeReviewTests(TestCase):
    def setUp(self):
        self.station = Station.objects.create()
        self.show = Show.objects.create(station=self.station, title="Weekly", code="weekly")
        self.first = Episode.objects.create(show=self.show, title="First", intended_air_order=1)
        self.second = Episode.objects.create(show=self.show, title="Second", intended_air_order=2)
        self.slot = RecurrenceSlot.objects.create(station=self.station, show=self.show, weekday=0, start_time=time(7), duration_seconds=1800, is_premiere=True)
        self.assignment = WeeklyEpisodeAssignment.objects.create(show=self.show, week_start=date(2026, 1, 5), premiere_date=date(2026, 1, 5), episode=self.first, selection_type="premiere")
        materialize_assignment(self.assignment)

    def test_moved_exception_is_not_recreated_or_overwritten(self):
        occurrence = self.assignment.occurrences.get()
        occurrence.starts_at += timedelta(days=1)
        occurrence.is_exception = True
        occurrence.exception_reason = "operator edit"
        occurrence.save()
        self.assignment.episode = self.second
        self.assignment.save(update_fields=["episode"])
        materialize_assignment(self.assignment)
        occurrence.refresh_from_db()
        self.assertEqual(self.assignment.occurrences.count(), 1)
        self.assertEqual(occurrence.episode_id, self.first.pk)
        self.assertTrue(occurrence.is_exception)

    def test_cycle_review_rejects_stale_revision(self):
        occurrence = self.assignment.occurrences.get()
        token = sign_cycle_review(self.assignment, [occurrence])
        occurrence.revision += 1
        occurrence.save(update_fields=["revision"])
        with self.assertRaises(ValueError):
            commit_cycle_review(self.assignment, token_payload=load_cycle_review(token), episode=self.second)

    def test_duration_review_token_contains_affected_occurrence_revisions(self):
        occurrence = self.assignment.occurrences.get()
        token = sign_duration_review(self.show, [occurrence], 3600)
        payload = load_duration_review(token)
        self.assertEqual(payload["show"], self.show.pk)
        self.assertEqual(payload["occurrences"], [[occurrence.pk, occurrence.revision]])
        self.assertEqual(payload["duration_seconds"], 3600)


class ScheduleChangeClientTests(TestCase):
    def setUp(self):
        self.station = Station.objects.create()
        self.show = Show.objects.create(station=self.station, title="Future Weekly", code="future")
        self.first = Episode.objects.create(show=self.show, title="First", intended_air_order=1)
        self.second = Episode.objects.create(show=self.show, title="Second", intended_air_order=2)
        today = timezone.localdate()
        self.monday = today + timedelta(days=(7 - today.weekday()) % 7 or 7)
        self.slot = RecurrenceSlot.objects.create(
            station=self.station, show=self.show, weekday=0, start_time=time(7),
            duration_seconds=1800, is_premiere=True,
        )
        self.assignment = WeeklyEpisodeAssignment.objects.create(
            show=self.show, week_start=self.monday, premiere_date=self.monday,
            episode=self.first, selection_type="premiere",
        )
        materialize_assignment(self.assignment)
        self.client = Client()

    def _cycle_post(self, *, premiere_date=None, episode=None, token=None):
        data = {
            "show": self.show.pk,
            "premiere_date": (premiere_date or self.monday).isoformat(),
            "episode": "" if episode is None else episode.pk,
        }
        if token:
            data.update({"review_token": token, "confirm_review": "1"})
        return self.client.post(f"/schedule/cycles/{self.assignment.pk}/edit/", data)

    def test_real_occurrence_editor_creates_exception_preserved_by_cycle_save(self):
        occurrence = self.assignment.occurrences.get()
        moved = timezone.localtime(occurrence.starts_at) + timedelta(hours=1)
        response = self.client.post(f'/occurrences/{occurrence.pk}/edit/', {
            'expected_revision': occurrence.revision, 'item_type': 'episode',
            'episode': self.first.pk, 'show': self.show.pk, 'asset': '',
            'starts_at': moved.strftime('%Y-%m-%dT%H:%M'),
            'planned_duration_seconds_0': '0', 'planned_duration_seconds_1': '30',
            'planned_duration_seconds_2': '0', 'status': 'planned', 'reason': 'One-off move',
        })
        self.assertEqual(response.status_code, 302)
        occurrence.refresh_from_db()
        self.assertTrue(occurrence.is_exception)
        preview = self._cycle_post(episode=self.second)
        self.assertContains(preview, 'Preserve exception')
        confirmed = self._cycle_post(episode=self.second, token=preview.context['cycle_review_token'])
        self.assertEqual(confirmed.status_code, 302)
        occurrence.refresh_from_db()
        self.assertEqual(occurrence.starts_at, moved)
        self.assertEqual(occurrence.episode_id, self.first.pk)
        self.assertEqual(self.assignment.occurrences.count(), 1)

    def test_confirm_rejects_episode_tampered_after_preview(self):
        preview = self._cycle_post(episode=self.second)
        self.assertEqual(preview.status_code, 200)
        response = self._cycle_post(episode=self.first, token=preview.context["cycle_review_token"])
        self.assertEqual(response.status_code, 200)
        self.assignment.refresh_from_db()
        self.assertEqual(self.assignment.episode_id, self.first.pk)
        self.assertIn("do not match the reviewed change", str(response.context["form"].errors))

    def test_no_program_saves_empty_assignment_and_cancels_canonical_play(self):
        preview = self._cycle_post(episode=None)
        self.assertContains(preview, "Cancel play")
        self.assertContains(preview, "Before:")
        self.assertContains(preview, "After:")
        self.assertContains(preview, "Removed from the planned schedule")
        response = self._cycle_post(episode=None, token=preview.context["cycle_review_token"])
        self.assertEqual(response.status_code, 302, response.context["form"].errors if response.context else None)
        self.assignment.refresh_from_db()
        occurrence = self.assignment.occurrences.get()
        self.assertIsNone(self.assignment.episode_id)
        self.assertEqual(self.assignment.selection_type, "none")
        self.assertEqual(occurrence.status, "cancelled")
        self.assertEqual(occurrence.episode_id, self.first.pk)

    def test_changed_premiere_date_moves_canonical_row_without_recreation(self):
        occurrence = self.assignment.occurrences.get()
        asset = MediaAsset.objects.create(
            episode=self.first, file_name="first.mov", kind="source", version="original",
        )
        occurrence.asset = asset
        occurrence.save()
        occurrence.refresh_from_db()
        original_pk = occurrence.pk
        changed_date = self.monday + timedelta(days=7)
        preview = self._cycle_post(premiere_date=changed_date, episode=self.first)
        response = self._cycle_post(
            premiere_date=changed_date, episode=self.first,
            token=preview.context["cycle_review_token"],
        )
        self.assertEqual(response.status_code, 302, response.context["form"].errors if response.context else None)
        occurrence.refresh_from_db()
        self.assertEqual(occurrence.pk, original_pk)
        self.assertEqual(timezone.localtime(occurrence.starts_at).date(), changed_date)
        self.assertEqual(occurrence.asset_id, asset.pk)
        self.assertEqual(self.assignment.occurrences.count(), 1)

    def test_added_future_row_after_preview_invalidates_exact_set(self):
        preview = self._cycle_post(episode=self.second)
        Occurrence.objects.create(
            station=self.station, show=self.show, episode=self.first,
            weekly_assignment=self.assignment, item_type="episode", label="Added later",
            starts_at=timezone.now() + timedelta(days=30), planned_duration_seconds=60,
        )
        response = self._cycle_post(episode=self.second, token=preview.context["cycle_review_token"])
        self.assertEqual(response.status_code, 200)
        self.assignment.refresh_from_db()
        self.assertEqual(self.assignment.episode_id, self.first.pk)
        self.assertContains(response, "changed after preview")

    def test_cycle_change_retains_uploaded_snapshot_and_advances_occurrence_revision(self):
        occurrence = self.assignment.occurrences.get()
        device = Device.objects.create(station=self.station, name="Playout")
        upload = create_upload_snapshot(device=device, occurrences=[occurrence])
        coverage = upload.uploadedoccurrencecoverage_set.get()
        preview = self._cycle_post(episode=self.second)
        self.assertContains(preview, "Change episode")
        self.assertContains(preview, "First")
        self.assertContains(preview, "Second")
        self.assertContains(preview, "A revised schedule publication will be required")
        self.assertContains(preview, "Republication required")
        response = self._cycle_post(episode=self.second, token=preview.context["cycle_review_token"])
        self.assertEqual(response.status_code, 302)
        occurrence.refresh_from_db()
        coverage.refresh_from_db()
        self.assertEqual(coverage.occurrence_revision, 1)
        self.assertEqual(occurrence.revision, 2)
        self.assertEqual(occurrence.episode_id, self.second.pk)

    def test_cancelled_moved_exception_is_preserved_during_episode_change(self):
        occurrence = self.assignment.occurrences.get()
        occurrence.starts_at += timedelta(days=1)
        occurrence.status = "cancelled"
        occurrence.is_exception = True
        occurrence.exception_reason = "operator cancellation"
        occurrence.save()
        occurrence.refresh_from_db()
        preview = self._cycle_post(episode=self.second)
        self.assertContains(preview, "Preserve exception")
        self.assertContains(preview, "keeps its existing date, episode, status, and evidence")
        response = self._cycle_post(episode=self.second, token=preview.context["cycle_review_token"])
        self.assertEqual(response.status_code, 302, response.context["form"].errors if response.context else None)
        occurrence.refresh_from_db()
        self.assertEqual(occurrence.episode_id, self.first.pk)
        self.assertEqual(occurrence.status, "cancelled")
        self.assertTrue(occurrence.is_exception)
        self.assertEqual(self.assignment.occurrences.count(), 1)

    def test_archived_episode_is_rejected_before_preview(self):
        self.second.archived_at = timezone.now()
        self.second.save(update_fields=["archived_at"])
        response = self._cycle_post(episode=self.second)
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context['form'].errors.get('episode'))
        self.assertFalse(response.context.get('cycle_review_token'))
        self.assertNotIn("cycle_review_token", response.context)

    def test_archive_show_blocks_active_recurrence_with_actionable_link(self):
        self.assignment.occurrences.all().delete()
        self.assignment.delete()
        response = self.client.post(f"/shows/{self.show.pk}/archive/")
        self.assertEqual(response.status_code, 400)
        self.assertContains(response, "Manage weekly times", status_code=400)
        self.show.refresh_from_db()
        self.assertIsNone(self.show.archived_at)

    def test_carry_forward_skips_archived_show(self):
        self.station.carry_forward_unassigned_episodes = True
        self.station.save(update_fields=["carry_forward_unassigned_episodes"])
        self.show.archived_at = timezone.now()
        self.show.save(update_fields=["archived_at"])
        next_week = self.monday + timedelta(days=7)
        self.assertEqual(ensure_carry_forward_week(self.station, next_week), [])
        self.assertFalse(self.show.weekly_assignments.filter(week_start=next_week).exists())

    def test_pending_suggestion_skips_archived_episode_and_show(self):
        self.second.archived_at = timezone.now()
        self.second.save(update_fields=["archived_at"])
        self.assertIsNone(suggested_pending_episode(self.show))
        self.show.archived_at = timezone.now()
        self.show.save(update_fields=["archived_at"])
        self.assertIsNone(suggested_pending_episode(self.show))

    def test_weekday_change_moves_future_play_and_preserves_slot_version(self):
        data = {
            "show": self.show.pk, "weekday": 1, "start_time": "08:00",
            "duration_seconds_0": 0, "duration_seconds_1": 30,
            "duration_seconds_2": 0, "is_premiere": "on",
            "active_from": self.monday.isoformat(), "active_until": "",
        }
        preview = self.client.post(f"/slots/{self.slot.pk}/edit/", data)
        self.assertEqual(preview.status_code, 200)
        self.assertIn("recurring_review_token", preview.context, preview.context["form"].errors)
        data.update({"review_token": preview.context["recurring_review_token"], "confirm_review": "1"})
        response = self.client.post(f"/slots/{self.slot.pk}/edit/", data)
        self.assertEqual(response.status_code, 302, response.context["form"].errors if response.context else None)
        self.slot.refresh_from_db()
        occurrence = self.assignment.occurrences.get()
        self.assignment.refresh_from_db()
        local_start = timezone.localtime(occurrence.starts_at)
        self.assertEqual(self.slot.active_until, self.monday - timedelta(days=1))
        self.assertEqual(local_start.weekday(), 1)
        self.assertEqual(local_start.time().replace(tzinfo=None), time(8))
        self.assertEqual(self.assignment.premiere_date, local_start.date())

    def test_weekday_change_rejects_overlap_with_other_planned_item(self):
        conflict_start = datetime.combine(self.monday + timedelta(days=1), time(8), tzinfo=ZoneInfo(self.station.timezone))
        Occurrence.objects.create(
            station=self.station, item_type="live", label="Council meeting",
            starts_at=conflict_start, planned_duration_seconds=3600,
        )
        data = {
            "show": self.show.pk, "weekday": 1, "start_time": "08:00",
            "duration_seconds_0": 0, "duration_seconds_1": 30,
            "duration_seconds_2": 0, "is_premiere": "on",
            "active_from": self.monday.isoformat(), "active_until": "",
        }
        preview = self.client.post(f"/slots/{self.slot.pk}/edit/", data)
        self.assertContains(preview, "This change cannot be committed yet")
        self.assertContains(preview, "Council meeting")
        self.assertNotContains(preview, 'name="confirm_review"')
        self.assertContains(preview, "Preview adjusted change")
        data.update({"review_token": preview.context["recurring_review_token"], "confirm_review": "1"})
        response = self.client.post(f"/slots/{self.slot.pk}/edit/", data)
        self.assertEqual(response.status_code, 200)
        self.assertIn("overlaps", str(response.context["form"].errors))
        self.slot.refresh_from_db()
        self.assertIsNone(self.slot.active_until)
        self.assertEqual(self.show.slots.count(), 1)

    def test_reviewed_end_date_cancels_canonical_future_and_ends_slot(self):
        end_date = timezone.localdate() - timedelta(days=1)
        data = {
            "show": self.show.pk, "weekday": 0, "start_time": "07:00",
            "duration_seconds_0": 0, "duration_seconds_1": 30,
            "duration_seconds_2": 0, "is_premiere": "on",
            "active_from": "", "active_until": end_date.isoformat(),
        }
        preview = self.client.post(f"/slots/{self.slot.pk}/edit/", data)
        self.assertIn("recurring_review_token", preview.context)
        data.update({"review_token": preview.context["recurring_review_token"], "confirm_review": "1"})
        response = self.client.post(f"/slots/{self.slot.pk}/edit/", data)
        self.assertEqual(response.status_code, 302)
        self.slot.refresh_from_db()
        occurrence = self.assignment.occurrences.get()
        self.assertEqual(self.slot.active_until, end_date)
        self.assertEqual(occurrence.status, "cancelled")

    def test_duration_review_honors_effective_date_and_preserves_past(self):
        past = Occurrence.objects.create(
            station=self.station, show=self.show, episode=self.first,
            item_type="episode", label=self.show.title,
            starts_at=timezone.now() - timedelta(days=1), planned_duration_seconds=1800,
        )
        future = self.assignment.occurrences.get()
        data = {
            "slot_duration_seconds_0": 1, "slot_duration_seconds_1": 0,
            "slot_duration_seconds_2": 0, "effective_date": self.monday.isoformat(),
        }
        preview = self.client.post(f"/shows/{self.show.pk}/slots/length/", data)
        self.assertContains(preview, "Change duration")
        self.assertContains(preview, "1800 seconds")
        self.assertContains(preview, "3600 seconds")
        data.update({"review_token": preview.context["recurring_review_token"], "confirm_review": "1"})
        response = self.client.post(f"/shows/{self.show.pk}/slots/length/", data)
        self.assertEqual(response.status_code, 302)
        past.refresh_from_db(); future.refresh_from_db(); self.show.refresh_from_db()
        self.assertEqual(past.planned_duration_seconds, 1800)
        self.assertEqual(future.planned_duration_seconds, 3600)
        self.assertEqual(self.show.slot_duration_seconds, 3600)
