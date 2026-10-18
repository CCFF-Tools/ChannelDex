from datetime import UTC, date, datetime
from unittest.mock import patch

from django.test import RequestFactory, TestCase

from pubtv.operations.models import Episode, Occurrence, Show, Station
from pubtv.operations.publication_workspace import work_view
from pubtv.operations.work_workspace import build_work_workspace


class WorkWorkspaceContractTests(TestCase):
    def test_projection_is_derived_and_has_separate_waiting_queue(self):
        self.assertEqual(build_work_workspace(None)["action_items"], [])
        result = build_work_workspace(None)
        self.assertIn("waiting_items", result)
        self.assertTrue(all(item.get("derived") for item in result["waiting_items"]))

    @patch("django.shortcuts.render")
    @patch("pubtv.operations.active_station.resolve_active_station", return_value=None)
    def test_work_view_handles_no_station(self, _station, render):
        request = RequestFactory().get("/work/")
        work_view(request)
        self.assertEqual(render.call_args.args[1], "work.html")

    def test_unscheduled_episode_media_action_keeps_suggested_airtime(self):
        station = Station.objects.create(name="PUB-TV", timezone="America/Detroit")
        show = Show.objects.create(station=station, title="Autumn Harvest", code="harvest")
        episode = Episode.objects.create(show=show, title="Episode 1", status="pending")
        # An occurrence with no asset produces the actionable media card; the
        # episode queue must not add a second date-review card.
        when = datetime(2026, 10, 19, 19, tzinfo=UTC)
        Occurrence.objects.create(station=station, show=show, episode=episode,
                                  item_type="episode", label="Episode 1", starts_at=when,
                                  planned_duration_seconds=1800)
        result = build_work_workspace(station, date(2026, 10, 14))
        titles = [item["title"] for item in result["groups"][0]["items"]]
        self.assertEqual(sum("Episode 1" in title for title in titles), 1)
