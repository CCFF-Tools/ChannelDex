from datetime import date, datetime, time, timezone as dt_timezone
from zoneinfo import ZoneInfo

from django.test import Client, TestCase

from pubtv.operations.models import (
    Device,
    Episode,
    MediaAsset,
    Occurrence,
    RecurrenceSlot,
    Show,
    Station,
    UploadedOccurrenceCoverage,
    UploadedScheduleRevision,
    WeeklyEpisodeAssignment,
)
from pubtv.operations.week_review import planned_columns


class WeekReviewTests(TestCase):
    def setUp(self):
        self.station = Station.objects.create(name="PUB-TV", timezone="America/Detroit")
        self.show = Show.objects.create(station=self.station, title="Week Show", code="week-show")
        self.client = Client()

    def occurrence(self, **kwargs):
        defaults = {
            "station": self.station,
            "item_type": "filler",
            "label": "Planned item",
            "starts_at": datetime(2026, 1, 5, 14, tzinfo=dt_timezone.utc),
            "planned_duration_seconds": 60,
        }
        defaults.update(kwargs)
        return Occurrence.objects.create(**defaults)

    def test_get_is_read_only_and_rejects_malformed_date_and_foreign_target(self):
        item = self.occurrence()
        before = (Station.objects.count(), Occurrence.objects.count(), item.revision)

        response = self.client.get("/schedule/review/?date=not-a-date")
        self.assertEqual(response.status_code, 400)
        response = self.client.get("/schedule/review/?date=2026-01-05&target=999999")
        self.assertEqual(response.status_code, 400)
        response = self.client.get("/schedule/review/?date=2026-01-05")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            (Station.objects.count(), Occurrence.objects.count(), Occurrence.objects.get(pk=item.pk).revision),
            before,
        )

    def test_week_review_keeps_all_planned_item_types_and_exact_upload_revision(self):
        live = self.occurrence(item_type="live", label="Live town hall")
        reusable_asset = MediaAsset.objects.create(
            station=self.station, file_name="reusable.mp4", kind="source", runtime_seconds=0,
        )
        reusable = self.occurrence(
            item_type="media", asset=reusable_asset, label="Reusable media",
            starts_at=datetime(2026, 1, 6, 14, tzinfo=dt_timezone.utc),
        )
        unassigned = self.occurrence(item_type="filler", label="Unassigned filler", starts_at=datetime(2026, 1, 7, 14, tzinfo=dt_timezone.utc))
        episode = Episode.objects.create(show=self.show, title="Episode", runtime_seconds=0)
        planned = self.occurrence(
            item_type="episode", show=self.show, episode=episode, label="Episode",
            starts_at=datetime(2026, 1, 8, 14, tzinfo=dt_timezone.utc),
        )
        Occurrence.objects.filter(pk=planned.pk).update(revision=2)
        planned.refresh_from_db()
        device = Device.objects.create(station=self.station, name="Playback")
        upload = UploadedScheduleRevision.objects.create(device=device)
        UploadedOccurrenceCoverage.objects.create(
            upload=upload, occurrence=planned, occurrence_revision=1,
        )

        response = self.client.get("/schedule/review/?date=2026-01-05&target=%s" % device.pk)

        self.assertEqual(response.status_code, 200)
        rows = response.context["rows"]
        self.assertEqual({row.pk for row in rows}, {live.pk, reusable.pk, unassigned.pk, planned.pk})
        self.assertFalse(next(row for row in rows if row.pk == planned.pk).upload_covered)
        self.assertFalse(next(row for row in rows if row.pk == planned.pk).runtime_unknown)
        self.assertEqual(next(row for row in rows if row.pk == planned.pk).review_preparation, "Waiting for media")

    def test_missing_runtime_is_distinct_from_recorded_zero(self):
        missing_episode = Episode.objects.create(show=self.show, title="Missing")
        missing = self.occurrence(
            item_type="episode", show=self.show, episode=missing_episode,
            label="Missing runtime",
        )
        zero_episode = Episode.objects.create(show=self.show, title="Zero", runtime_seconds=0)
        zero = self.occurrence(
            item_type="episode", show=self.show, episode=zero_episode, label="Zero runtime",
            starts_at=datetime(2026, 1, 6, 14, tzinfo=dt_timezone.utc),
        )

        response = self.client.get("/schedule/review/?date=2026-01-05")
        by_id = {row.pk: row for row in response.context["rows"]}
        self.assertTrue(by_id[missing.pk].runtime_unknown)
        self.assertFalse(by_id[zero.pk].runtime_unknown)

    def test_explicit_no_program_marks_overnight_capacity_on_following_day(self):
        slot = RecurrenceSlot.objects.create(
            station=self.station, show=self.show, weekday=6, start_time=time(23, 30),
            duration_seconds=2 * 60 * 60, is_premiere=True,
        )
        assignment = WeeklyEpisodeAssignment.objects.create(
            show=self.show, week_start=date(2026, 1, 5), premiere_date=date(2026, 1, 11),
            selection_type="none",
        )
        columns = planned_columns(
            self.station, date(2026, 1, 5), [],
            [{"date": date(2026, 1, 12), "intervals": [{
                "start": datetime(2026, 1, 12, tzinfo=ZoneInfo("America/Detroit")),
                "end": datetime(2026, 1, 12, 1, 30, tzinfo=ZoneInfo("America/Detroit")),
                "status": "reserved", "item": None,
            }]}],
        )
        item = columns[0]["items"][0]
        self.assertEqual(item["status"], "no-program")
        self.assertEqual(item["label"], "No program · explicit cycle choice")
        self.assertTrue(WeeklyEpisodeAssignment.objects.filter(pk=assignment.pk).exists())
