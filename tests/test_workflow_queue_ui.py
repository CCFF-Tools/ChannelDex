from datetime import date, datetime, time, timezone as dt_timezone
from unittest.mock import patch

from django.core.management import call_command
from django.test import TestCase

from pubtv.operations.models import (
    AuditEvent, Device, Episode, Occurrence, OccurrenceProgramming,
    RecurrenceSlot, Show, Station, UploadedOccurrenceCoverage,
    UploadedScheduleRevision, WeeklyEpisodeAssignment,
)
from pubtv.operations.services import advance_passed_premiere, reconcile_passed_premieres


class QueueAdvancementTests(TestCase):
    def setUp(self):
        self.station = Station.objects.create()
        self.show = Show.objects.create(station=self.station, title="Queue show", code="queue-show")
        self.episode = Episode.objects.create(show=self.show, title="Episode 1")
        self.slot = RecurrenceSlot.objects.create(
            station=self.station, show=self.show, weekday=0, start_time=time(7),
            duration_seconds=1800, is_premiere=True,
        )
        self.assignment = WeeklyEpisodeAssignment.objects.create(
            show=self.show, week_start=date(2026, 1, 5), premiere_date=date(2026, 1, 5),
            episode=self.episode, selection_type="premiere",
        )
        self.occurrence = Occurrence.objects.create(
            station=self.station, show=self.show, episode=self.episode,
            weekly_assignment=self.assignment, recurrence_slot=self.slot,
            schedule_role="premiere", item_type="episode", label="Episode 1",
            starts_at=datetime(2026, 1, 5, 7, tzinfo=dt_timezone.utc),
            planned_duration_seconds=1800,
        )
        self.device = Device.objects.create(name="Ultra-Nexus")

    def cover(self, revision=None, device=None, state="active"):
        device = device or self.device
        upload = UploadedScheduleRevision.objects.create(device=device, state=state)
        UploadedOccurrenceCoverage.objects.create(
            upload=upload, occurrence=self.occurrence,
            occurrence_revision=revision or self.occurrence.revision,
        )
        OccurrenceProgramming.objects.create(
            occurrence=self.occurrence, device=device, slot_assignment="A1",
        )

    def test_future_upload_reconciles_only_after_start(self):
        self.cover()
        before = datetime(2026, 1, 5, 6, 59, tzinfo=dt_timezone.utc)
        after = datetime(2026, 1, 5, 7, 1, tzinfo=dt_timezone.utc)
        self.assertEqual(reconcile_passed_premieres(before), 0)
        self.episode.refresh_from_db()
        self.assertEqual(self.episode.status, "pending")
        self.assertEqual(reconcile_passed_premieres(after), 1)
        self.episode.refresh_from_db()
        self.assertEqual(self.episode.status, "previously_scheduled")

    def test_replay_cancelled_preempted_wrong_device_and_stale_coverage_fail(self):
        self.cover()
        self.occurrence.schedule_role = "replay"
        self.occurrence.save(update_fields=["schedule_role"])
        self.assertFalse(advance_passed_premiere(self.occurrence, now=datetime(2026, 1, 6, tzinfo=dt_timezone.utc)))
        self.occurrence.schedule_role = "premiere"
        self.occurrence.status = "cancelled"
        self.occurrence.save(update_fields=["schedule_role", "status"])
        self.assertFalse(advance_passed_premiere(self.occurrence, now=datetime(2026, 1, 6, tzinfo=dt_timezone.utc)))
        self.occurrence.status = "preempted"
        self.occurrence.save(update_fields=["status"])
        self.assertFalse(advance_passed_premiere(self.occurrence, now=datetime(2026, 1, 6, tzinfo=dt_timezone.utc)))
        self.occurrence.status = "planned"
        self.occurrence.save(update_fields=["status"])
        other = Device.objects.create(name="Other")
        OccurrenceProgramming.objects.all().delete()
        OccurrenceProgramming.objects.create(occurrence=self.occurrence, device=other, slot_assignment="B1")
        self.assertFalse(advance_passed_premiere(self.occurrence, now=datetime(2026, 1, 6, tzinfo=dt_timezone.utc)))
        UploadedScheduleRevision.objects.filter(device=self.device).update(state="superseded")
        self.assertFalse(advance_passed_premiere(self.occurrence, now=datetime(2026, 1, 6, tzinfo=dt_timezone.utc)))
        self.episode.refresh_from_db()
        self.assertEqual(self.episode.status, "pending")

    def test_reconcile_is_exactly_once_and_audited_once(self):
        self.cover()
        now = datetime(2026, 1, 6, tzinfo=dt_timezone.utc)
        self.assertEqual(reconcile_passed_premieres(now), 1)
        self.assertEqual(reconcile_passed_premieres(now), 0)
        self.assertEqual(AuditEvent.objects.filter(action="advance", entity="Episode", entity_id=self.episode.pk).count(), 1)

    def test_worker_once_runs_reconciliation(self):
        with patch("pubtv.operations.management.commands.run_ultranexus_worker.reconcile_passed_premieres", return_value=0) as reconcile:
            call_command("run_ultranexus_worker", once=True, poll_seconds=0.25)
        self.assertGreaterEqual(reconcile.call_count, 2)
