from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from django.test import TestCase
from django.utils import timezone

from pubtv.operations.models import Episode, MediaAsset, Occurrence, Show, Station


class WorkflowHandoffTests(TestCase):
    def setUp(self):
        self.station = Station.objects.create(name="PUB-TV")
        self.show = Show.objects.create(station=self.station, title="Community", code="community")
        self.episode = Episode.objects.create(show=self.show, title="Gardens")
        self.asset = MediaAsset.objects.create(station=self.station, file_name="station-id.mp4", runtime_seconds=0)

    def test_reusable_plan_preserves_asset_and_selected_day(self):
        response = self.client.get(f"/occurrences/new/?asset={self.asset.pk}&date=2026-10-19")
        self.assertEqual(response.status_code, 200)
        initial = response.context["form"].initial
        self.assertEqual(initial["asset"], self.asset.pk)
        self.assertEqual(initial["item_type"], "media")
        self.assertEqual(initial["planned_duration_seconds"], 0)
        self.assertEqual(initial["starts_at"], datetime(2026, 10, 19, tzinfo=ZoneInfo(self.station.timezone)))

    def test_episode_context_and_malformed_ids_are_recoverable(self):
        response = self.client.get(f"/occurrences/new/?show={self.show.pk}&episode={self.episode.pk}")
        self.assertEqual(response.context["form"].initial["episode"], str(self.episode.pk))
        response = self.client.get("/occurrences/new/?show=bad&episode=bad&asset=bad")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.client.get("/occurrences/new/?date=bad").status_code, 400)

    def test_library_links_next_planned_use_and_schedule_defaults_to_week(self):
        item = Occurrence.objects.create(station=self.station, show=self.show, episode=self.episode,
            item_type="episode", starts_at=timezone.now() + timedelta(days=1), planned_duration_seconds=1800)
        response = self.client.get("/library/")
        self.assertEqual(response.context["rows"][0]["next_planned_use"]["url"], f"/occurrences/{item.pk}/")
        self.assertContains(response, "Next planned use")
        self.assertContains(self.client.get("/schedule/"), "Week plan")
        self.assertContains(self.client.get("/schedule/?view=capacity"), "Weekly capacity")

    def test_unprepared_reusable_media_can_be_saved_to_schedule(self):
        response = self.client.post(f"/occurrences/new/?asset={self.asset.pk}", {
            "asset": self.asset.pk, "item_type": "media", "label": "Station ID",
            "starts_at": "2026-10-16T09:00", "planned_duration_seconds_0": "0",
            "planned_duration_seconds_1": "1", "status": "planned",
        })
        self.assertEqual(response.status_code, 302)
        item = Occurrence.objects.get(asset=self.asset)
        self.assertEqual(item.station, self.station)
        self.assertEqual(item.planned_duration_seconds, 60)
