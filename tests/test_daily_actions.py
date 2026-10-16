from datetime import date, datetime, time
import tempfile
from zoneinfo import ZoneInfo

from django.test import TestCase
from unittest.mock import patch

from pubtv.operations.action_items import build_daily_work
from pubtv.operations.models import (
    Device,
    Episode,
    MediaAsset,
    Occurrence,
    OccurrenceProgramming,
    PreparationBatch,
    PreparationBatchItem,
    RecurrenceSlot,
    Show,
    Station,
    WeeklyEpisodeAssignment,
)


class DailyActionsTests(TestCase):
    def setUp(self):
        self.station = Station.objects.create(name="PUB-TV", timezone="America/Detroit")
        self.other = Station.objects.create(name="ALT-TV", timezone="America/Detroit")
        self.show = Show.objects.create(station=self.station, title="Civic Hour", code="civic")
        self.episode = Episode.objects.create(show=self.show, title="Episode 1")
        self.asset = MediaAsset.objects.create(station=self.station, episode=self.episode, file_name="episode.mp4", kind="source")
        self.when = datetime(2026, 10, 14, 19, 0, tzinfo=ZoneInfo("America/Detroit"))

    def test_no_station_is_empty_and_has_no_hardware_assumption(self):
        self.assertEqual(build_daily_work(None, date(2026, 10, 14)), {"action_groups": [], "waiting_items": [], "action_count": 0})

    def test_missing_source_is_deduplicated_for_repeated_uses(self):
        for offset in (0, 7):
            Occurrence.objects.create(
                station=self.station, show=self.show, episode=self.episode, asset=self.asset,
                item_type="episode", label="Episode 1", starts_at=self.when.replace(day=14 + offset),
                planned_duration_seconds=1800,
            )
        work = build_daily_work(self.station, date(2026, 10, 14))
        source_items = [item for group in work["action_groups"] for item in group["items"] if item["key"] == f"source:{self.asset.pk}"]
        self.assertEqual(len(source_items), 1)
        self.assertIn("Affected uses", source_items[0]["reason"])

    def test_failed_and_waiting_preparation_are_separate(self):
        device = Device.objects.create(station=self.station, name="Playback")
        failed = PreparationBatch.objects.create(target=device, status="failed")
        PreparationBatchItem.objects.create(batch=failed, asset=self.asset, execution_status="failed", blocker="Source changed")
        waiting = PreparationBatch.objects.create(target=device, status="in_progress")
        PreparationBatchItem.objects.create(batch=waiting, asset=self.asset, execution_status="pending")
        work = build_daily_work(self.station, date(2026, 10, 14))
        self.assertTrue(any(item["key"].startswith("preparation:") for group in work["action_groups"] for item in group["items"]))
        self.assertTrue(any(item["key"].startswith("preparation:") for item in work["waiting_items"]))

    def test_foreign_station_records_do_not_appear(self):
        foreign_show = Show.objects.create(station=self.other, title="Other", code="other")
        foreign_episode = Episode.objects.create(show=foreign_show, title="Foreign")
        Occurrence.objects.create(
            station=self.other, show=foreign_show, episode=foreign_episode, item_type="episode", label="Foreign",
            starts_at=self.when, planned_duration_seconds=1800,
        )
        work = build_daily_work(self.station, date(2026, 10, 14))
        self.assertFalse(any("Foreign" in item["title"] for group in work["action_groups"] for item in group["items"]))

    def test_unassigned_premiere_is_action_but_no_program_stops_it(self):
        RecurrenceSlot.objects.create(station=self.station, show=self.show, weekday=2, start_time=time(19), duration_seconds=1800, is_premiere=True)
        work = build_daily_work(self.station, date(2026, 10, 14))
        self.assertTrue(any(item["key"].startswith("assignment:") for group in work["action_groups"] for item in group["items"]))
        WeeklyEpisodeAssignment.objects.create(show=self.show, week_start=date(2026, 10, 12), selection_type="none")
        WeeklyEpisodeAssignment.objects.create(show=self.show, week_start=date(2026, 10, 19), selection_type="none")
        work = build_daily_work(self.station, date(2026, 10, 14))
        self.assertFalse(any(item["key"].startswith("assignment:") for group in work["action_groups"] for item in group["items"]))

    def test_cancelled_batch_is_not_waiting_or_actionable(self):
        device = Device.objects.create(station=self.station, name="Playback")
        batch = PreparationBatch.objects.create(target=device, status="cancelled")
        PreparationBatchItem.objects.create(batch=batch, asset=self.asset, execution_status="failed", blocker="old failure")
        work = build_daily_work(self.station, date(2026, 10, 14))
        self.assertFalse(any("old failure" in item["reason"] for group in work["action_groups"] for item in group["items"]))
        self.assertFalse(any("old failure" in item["reason"] for item in work["waiting_items"]))

    @patch("pubtv.operations.action_items.schedule_ready_binding", return_value=object())
    def test_prepared_binding_suppresses_missing_source(self, _ready):
        device = Device.objects.create(station=self.station, name="Playback")
        batch = PreparationBatch.objects.create(target=device, status="complete")
        PreparationBatchItem.objects.create(batch=batch, asset=self.asset, execution_status="ready")
        Occurrence.objects.create(
            station=self.station, show=self.show, episode=self.episode, asset=self.asset,
            item_type="episode", label="Episode 1", starts_at=self.when, planned_duration_seconds=1800,
        )
        work = build_daily_work(self.station, date(2026, 10, 14))
        self.assertFalse(any(item["key"] == f"source:{self.asset.pk}" for group in work["action_groups"] for item in group["items"]))

    def test_runtime_order_uses_actual_time_and_zero_is_known(self):
        early = Episode.objects.create(show=self.show, title="Morning", runtime_seconds=None)
        late = Episode.objects.create(show=self.show, title="Afternoon", runtime_seconds=0)
        early_asset = MediaAsset.objects.create(station=self.station, episode=early, file_name="morning.mp4", kind="source")
        late_asset = MediaAsset.objects.create(station=self.station, episode=late, file_name="afternoon.mp4", kind="source")
        Occurrence.objects.create(station=self.station, show=self.show, episode=late, asset=late_asset, item_type="episode", label="Afternoon", starts_at=self.when.replace(hour=13), planned_duration_seconds=1800)
        Occurrence.objects.create(station=self.station, show=self.show, episode=early, asset=early_asset, item_type="episode", label="Morning", starts_at=self.when.replace(hour=9), planned_duration_seconds=1800)
        work = build_daily_work(self.station, date(2026, 10, 14))
        runtime = [item for group in work["action_groups"] for item in group["items"] if item["key"].startswith("runtime:")]
        self.assertEqual([item["title"] for item in runtime], ["Record runtime for Civic Hour · Morning"])

    @patch("pubtv.operations.action_items.schedule_ready_binding")
    def test_ready_target_does_not_hide_unready_destination(self, ready):
        first = Device.objects.create(station=self.station, name="A")
        second = Device.objects.create(station=self.station, name="B")
        batch_a = PreparationBatch.objects.create(target=first, status="complete")
        batch_b = PreparationBatch.objects.create(target=second, status="complete")
        PreparationBatchItem.objects.create(batch=batch_a, asset=self.asset, execution_status="ready")
        PreparationBatchItem.objects.create(batch=batch_b, asset=self.asset, execution_status="ready")
        ready.side_effect = lambda asset, target: object() if target.pk == second.pk else None
        Occurrence.objects.create(station=self.station, show=self.show, episode=self.episode, asset=self.asset, item_type="episode", label="Episode 1", starts_at=self.when, planned_duration_seconds=1800)
        with patch("pubtv.operations.action_items.effective_preparation_status", return_value=("ready", "")):
            work = build_daily_work(self.station, date(2026, 10, 14))
        delivery = [item for group in work["action_groups"] for item in group["items"] if item["key"].startswith("coverage:")]
        self.assertTrue(any(item.get("destination", "").startswith("A ·") for item in delivery))
        self.assertTrue(any(item.get("destination", "").startswith("B ·") for item in delivery))
        actions = [item for group in work["action_groups"] for item in group["items"]]
        self.assertTrue(any(item["key"] == f"source:{self.asset.pk}" for item in actions))
        self.assertFalse(any(item["key"] == f"preparation:{self.asset.pk}:{second.pk}" for item in actions))

    def test_known_source_without_preparation_gets_prepare_action(self):
        with tempfile.NamedTemporaryFile(suffix=".mp4") as source:
            self.asset.local_path = source.name
            self.asset.save(update_fields=["local_path"])
            device = Device.objects.create(station=self.station, name="Playback")
            occurrence = Occurrence.objects.create(station=self.station, show=self.show, episode=self.episode, asset=self.asset, item_type="episode", label="Episode 1", starts_at=self.when, planned_duration_seconds=1800)
            OccurrenceProgramming.objects.create(occurrence=occurrence, device=device)
            work = build_daily_work(self.station, date(2026, 10, 14))
        self.assertTrue(any(item["title"].startswith("Prepare media") for group in work["action_groups"] for item in group["items"]))

    def test_future_overlap_link_uses_first_occurrence_local_date(self):
        first = Occurrence.objects.create(station=self.station, show=self.show, episode=self.episode, asset=self.asset, item_type="episode", label="First", starts_at=self.when.replace(day=21, hour=9), planned_duration_seconds=3600)
        second = Occurrence.objects.create(station=self.station, show=self.show, episode=self.episode, asset=self.asset, item_type="episode", label="Second", starts_at=self.when.replace(day=21, hour=9, minute=30), planned_duration_seconds=1800)
        work = build_daily_work(self.station, date(2026, 10, 14))
        conflicts = [item for group in work["action_groups"] for item in group["items"] if item["key"] == f"conflict:{first.pk}:{second.pk}"]
        self.assertEqual(len(conflicts), 1)
        self.assertIn("date=2026-10-21", conflicts[0]["url"])

    def test_date_only_work_sorts_with_timed_work(self):
        Occurrence.objects.create(station=self.station, show=self.show, episode=self.episode,
            asset=self.asset, item_type="episode", starts_at=self.when.replace(day=16), planned_duration_seconds=1800)
        work = build_daily_work(self.station, date(2026, 10, 14))
        upcoming = next(group["items"] for group in work["action_groups"] if group["title"] == "Upcoming work")
        dates = [item["needed_at"][:10] for item in upcoming if item.get("needed_at")]
        self.assertEqual(dates, sorted(dates))
