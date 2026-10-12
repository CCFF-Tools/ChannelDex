from datetime import date, datetime, time, timezone as dt_timezone
from zoneinfo import ZoneInfo

from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.test import TestCase
from django.urls import reverse

from pubtv.operations.models import (
    AiringEvidence, Device, Episode, Occurrence, Preparation, RecurrenceSlot,
    Show, Station, UploadedOccurrenceCoverage, UploadedScheduleRevision,
    WeeklyEpisodeAssignment,
)
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

    def test_existing_explicit_episode_cycle_blocks_preview(self):
        WeeklyEpisodeAssignment.objects.create(
            show=self.show, week_start=date(2026, 9, 14), premiere_date=date(2026, 9, 16),
            episode=self.two, selection_type="rerun",
        )
        with self.assertRaises(ValidationError):
            build_bulk_premiere_preview(
                show=self.show, episode_ids=[self.one.pk], first_premiere_date=date(2026, 9, 16),
            )

    def test_database_allows_one_active_assignment_while_retaining_superseded_history(self):
        old = WeeklyEpisodeAssignment.objects.create(
            show=self.show, week_start=date(2026, 9, 14), premiere_date=date(2026, 9, 16),
            episode=self.two, selection_type="premiere", is_automatic_carry_forward=True,
        )
        with self.assertRaises(IntegrityError), transaction.atomic():
            WeeklyEpisodeAssignment.objects.create(
                show=self.show, week_start=old.week_start, premiere_date=old.premiere_date,
                episode=self.one, selection_type="premiere",
            )
        old.state = "superseded"
        old.save(update_fields=["state"])
        replacement = WeeklyEpisodeAssignment.objects.create(
            show=self.show, week_start=old.week_start, premiere_date=old.premiere_date,
            episode=self.one, selection_type="premiere",
        )
        old.superseded_by = replacement
        old.save(update_fields=["superseded_by"])
        self.assertEqual(WeeklyEpisodeAssignment.objects.get(show=self.show).pk, replacement.pk)
        self.assertEqual(WeeklyEpisodeAssignment.all_objects.filter(show=self.show).count(), 2)

    def test_reviewed_plan_supersedes_only_automatic_carry_forward_and_retains_evidence(self):
        carried = WeeklyEpisodeAssignment.objects.create(
            show=self.show, week_start=date(2026, 9, 14), premiere_date=date(2026, 9, 16),
            episode=self.two, selection_type="premiere", is_automatic_carry_forward=True,
        )
        from pubtv.operations.services import materialize_assignment
        materialize_assignment(carried)
        old_occurrence = carried.occurrences.order_by("starts_at").first()
        device = Device.objects.create(station=self.station, name="Recorded destination")
        upload = UploadedScheduleRevision.objects.create(device=device)
        coverage = UploadedOccurrenceCoverage.objects.create(
            upload=upload, occurrence=old_occurrence, occurrence_revision=old_occurrence.revision,
        )
        evidence = AiringEvidence.objects.create(
            occurrence=old_occurrence, status="reported", source="owner note",
            aired_at=datetime(2026, 9, 16, 23, tzinfo=dt_timezone.utc),
        )

        preview = build_bulk_premiere_preview(
            show=self.show, episode_ids=[self.one.pk], first_premiere_date=date(2026, 9, 16),
        )
        self.assertEqual(preview["cycles"][0]["replaces_assignment_id"], carried.pk)
        created = confirm_bulk_premiere_plan(token=preview["token"])

        carried = WeeklyEpisodeAssignment.all_objects.get(pk=carried.pk)
        self.assertEqual(carried.state, "superseded")
        self.assertEqual(carried.superseded_by_id, created[0].pk)
        old_occurrence.refresh_from_db()
        self.assertEqual((old_occurrence.status, old_occurrence.revision), ("rescheduled", 2))
        self.assertEqual(UploadedOccurrenceCoverage.objects.get(pk=coverage.pk).occurrence_id, old_occurrence.pk)
        self.assertEqual(AiringEvidence.objects.get(pk=evidence.pk).occurrence_id, old_occurrence.pk)
        fresh = list(created[0].occurrences.order_by("starts_at"))
        self.assertTrue(fresh)
        self.assertTrue(all(item.status == "planned" and item.revision == 1 for item in fresh))
        self.assertFalse(UploadedOccurrenceCoverage.objects.filter(occurrence__in=fresh).exists())

    def test_preview_becomes_stale_if_automatic_assignment_is_made_explicit(self):
        carried = WeeklyEpisodeAssignment.objects.create(
            show=self.show, week_start=date(2026, 9, 14), premiere_date=date(2026, 9, 16),
            episode=self.two, selection_type="premiere", is_automatic_carry_forward=True,
        )
        preview = build_bulk_premiere_preview(
            show=self.show, episode_ids=[self.one.pk], first_premiere_date=date(2026, 9, 16),
        )
        carried.is_automatic_carry_forward = False
        carried.save(update_fields=["is_automatic_carry_forward"])
        with self.assertRaises(ValidationError):
            confirm_bulk_premiere_plan(token=preview["token"])
        self.assertEqual(WeeklyEpisodeAssignment.objects.get(pk=carried.pk).episode_id, self.two.pk)

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

    def test_route_confirms_into_first_premiere_scheduled_week_with_message(self):
        preview = build_bulk_premiere_preview(
            show=self.show, episode_ids=[self.one.pk], first_premiere_date=date(2026, 9, 16),
        )
        response = self.client.post(
            reverse("bulk-premiere-planner", args=[self.show.pk]),
            {"action": "confirm", "preview_token": preview["token"]},
            follow=True,
        )
        self.assertEqual(response.redirect_chain[0][0], "/schedule/?mode=week&date=2026-09-16&view=schedule")
        self.assertContains(response, "Planned 1 premiere cycle")
        self.assertEqual(response.context["view_mode"], "schedule")
