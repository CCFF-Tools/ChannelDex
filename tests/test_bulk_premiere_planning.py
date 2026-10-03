from datetime import date, time
from zoneinfo import ZoneInfo

from django.core.exceptions import ValidationError
from django.test import TestCase
from django.urls import reverse

from pubtv.operations.models import Episode, Occurrence, Preparation, RecurrenceSlot, Show, Station, WeeklyEpisodeAssignment
from pubtv.operations.premiere_planning import (
    build_bulk_premiere_preview,
    confirm_bulk_premiere_plan,
)


class BulkPremierePlanningTests(TestCase):
    def setUp(self):
        self.station = Station.objects.create(name="PUB-TV", timezone="America/Detroit")
        self.show = Show.objects.create(station=self.station, title="Civic Hour", code="civic-hour", slot_duration_seconds=1800)
        RecurrenceSlot.objects.create(station=self.station, show=self.show, weekday=2, start_time=time(19), duration_seconds=1800, is_premiere=True)
        RecurrenceSlot.objects.create(station=self.station, show=self.show, weekday=0, start_time=time(19), duration_seconds=1800)
        self.one = Episode.objects.create(show=self.show, title="One", intended_air_order=1)
        self.two = Episode.objects.create(show=self.show, title="Two", intended_air_order=2)

    def test_preview_preserves_order_and_includes_replay_after_week_wrap(self):
        preview = build_bulk_premiere_preview(show=self.show, episode_ids=[self.two.pk, self.one.pk], first_premiere_date=date(2026, 9, 16))
        self.assertEqual([cycle["episode_title"] for cycle in preview["cycles"]], ["Two", "One"])
        self.assertEqual(preview["cycles"][0]["occurrences"][-1]["date"], "2026-09-21")

    def test_existing_no_program_cycle_blocks_preview(self):
        WeeklyEpisodeAssignment.objects.create(show=self.show, week_start=date(2026, 9, 14), premiere_date=date(2026, 9, 16), selection_type="none")
        with self.assertRaises(ValidationError):
            build_bulk_premiere_preview(show=self.show, episode_ids=[self.one.pk], first_premiere_date=date(2026, 9, 16))

    def test_stale_preview_cannot_confirm(self):
        preview = build_bulk_premiere_preview(show=self.show, episode_ids=[self.one.pk], first_premiere_date=date(2026, 9, 16))
        self.one.title = "Changed"
        self.one.save(update_fields=["title"])
        with self.assertRaises(ValidationError):
            confirm_bulk_premiere_plan(token=preview["token"])
        self.assertFalse(WeeklyEpisodeAssignment.objects.exists())

    def test_confirmation_matches_preview_and_keeps_queue_and_media_facts(self):
        preview = build_bulk_premiere_preview(show=self.show, episode_ids=[self.one.pk, self.two.pk], first_premiere_date=date(2026, 9, 16))
        confirm_bulk_premiere_plan(token=preview["token"])
        rows = list(WeeklyEpisodeAssignment.objects.order_by("premiere_date"))
        self.assertEqual([row.episode_id for row in rows], [self.one.pk, self.two.pk])
        self.one.refresh_from_db(); self.two.refresh_from_db()
        self.assertEqual([self.one.status, self.two.status], ["pending", "pending"])
        actual = list(Occurrence.objects.order_by("starts_at").values_list("starts_at", "schedule_role"))
        expected = [(item["date"], item["time"], item["premiere"]) for cycle in preview["cycles"] for item in cycle["occurrences"]]
        self.assertEqual(len(actual), len(expected))
        self.assertEqual([(value.astimezone(ZoneInfo(self.station.timezone)).date().isoformat(), value.astimezone(ZoneInfo(self.station.timezone)).time().isoformat(timespec="minutes"), role == "premiere") for value, role in actual], expected)
        self.assertEqual(Preparation.objects.count(), 0)
        with self.assertRaises(ValidationError):
            confirm_bulk_premiere_plan(token=preview["token"])
        self.assertEqual(WeeklyEpisodeAssignment.objects.count(), 2)

    def test_invalid_first_date_and_ambiguous_or_inactive_recurrence_block(self):
        with self.assertRaises(ValidationError):
            build_bulk_premiere_preview(show=self.show, episode_ids=[self.one.pk], first_premiere_date=date(2026, 9, 15))
        RecurrenceSlot.objects.create(station=self.station, show=self.show, weekday=3, start_time=time(19), duration_seconds=1800, is_premiere=True)
        with self.assertRaises(ValidationError):
            build_bulk_premiere_preview(show=self.show, episode_ids=[self.one.pk], first_premiere_date=date(2026, 9, 16))
        RecurrenceSlot.objects.filter(weekday=3, is_premiere=True).delete()
        RecurrenceSlot.objects.filter(weekday=2, is_premiere=True).update(active_from=date(2026, 10, 1))
        with self.assertRaises(ValidationError):
            build_bulk_premiere_preview(show=self.show, episode_ids=[self.one.pk], first_premiere_date=date(2026, 9, 16))

    def test_malformed_ids_and_changed_recurrence_or_timezone_are_rejected(self):
        with self.assertRaises(ValidationError):
            build_bulk_premiere_preview(show=self.show, episode_ids=["oops"], first_premiere_date=date(2026, 9, 16))
        preview = build_bulk_premiere_preview(show=self.show, episode_ids=[self.one.pk], first_premiere_date=date(2026, 9, 16))
        self.show.station.timezone = "UTC"
        self.show.station.save(update_fields=["timezone"])
        with self.assertRaises(ValidationError):
            confirm_bulk_premiere_plan(token=preview["token"])

    def test_route_preserves_posted_order(self):
        response = self.client.post(reverse("bulk-premiere-planner", args=[self.show.pk]), {"action": "preview", "first_premiere_date": "2026-09-16", "episode_ids": [str(self.two.pk), str(self.one.pk)]})
        self.assertEqual(response.status_code, 200)
        body = response.content.decode()
        self.assertLess(body.index("Two · premiere"), body.index("One · premiere"))
