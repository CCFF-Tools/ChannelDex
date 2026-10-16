from datetime import date, datetime, timezone as dt_timezone

from django.test import TestCase
from django.urls import reverse

from pubtv.operations.active_station import workspace_safe_url
from pubtv.operations.models import Device, Episode, MediaAsset, Occurrence, Show, Station, WeeklyEpisodeAssignment


class CycleReviewContextTests(TestCase):
    def setUp(self):
        self.station = Station.objects.create(name="PUB-TV", timezone="America/Detroit")
        self.other_station = Station.objects.create(name="GOV-TV", timezone="America/Detroit")
        self.show = Show.objects.create(station=self.station, title="Civic Hour", code="civic")
        self.other_show = Show.objects.create(station=self.other_station, title="Other Hour", code="other")
        self.device = Device.objects.create(station=self.station, name="WinLGX A")
        self.other_device = Device.objects.create(station=self.other_station, name="WinLGX B")
        self.episode = Episode.objects.create(show=self.show, title="Episode One")
        self.other_episode = Episode.objects.create(show=self.other_show, title="Foreign Episode")
        self.asset = MediaAsset.objects.create(station=self.station, episode=self.episode, kind="encoded", file_name="one.mxf")
        self.other_asset = MediaAsset.objects.create(station=self.other_station, episode=self.other_episode, kind="encoded", file_name="foreign.mxf")

        # This replay starts on Monday but belongs to the prior producer cycle.
        self.prior = WeeklyEpisodeAssignment.objects.create(
            show=self.show, week_start=date(2026, 10, 5), premiere_date=date(2026, 10, 7),
            episode=self.episode, selection_type="rerun",
        )
        Occurrence.objects.create(
            station=self.station, show=self.show, episode=self.episode,
            weekly_assignment=self.prior, item_type="episode", label="Monday replay",
            starts_at=datetime(2026, 10, 12, 5, tzinfo=dt_timezone.utc), planned_duration_seconds=1800,
        )
        self.outside = WeeklyEpisodeAssignment.objects.create(
            show=self.show, week_start=date(2026, 10, 19), premiere_date=date(2026, 10, 21),
            episode=self.episode, selection_type="premiere",
        )
        Occurrence.objects.create(
            station=self.station, show=self.show, episode=self.episode,
            weekly_assignment=self.outside, item_type="episode", label="Later",
            starts_at=datetime(2026, 10, 20, 1, tzinfo=dt_timezone.utc), planned_duration_seconds=1800,
        )
        self.foreign = WeeklyEpisodeAssignment.objects.create(
            show=self.other_show, week_start=date(2026, 10, 12), premiere_date=date(2026, 10, 14),
            episode=self.other_episode, selection_type="premiere",
        )
        Occurrence.objects.create(
            station=self.other_station, show=self.other_show, episode=self.other_episode,
            weekly_assignment=self.foreign, item_type="episode", label="Foreign",
            starts_at=datetime(2026, 10, 14, 1, tzinfo=dt_timezone.utc), planned_duration_seconds=1800,
        )

    def select_station(self, station):
        session = self.client.session
        session["active_station_id"] = station.pk
        session.save()

    def test_cycle_options_use_actual_occurrence_overlap_and_keep_monday_replay(self):
        self.select_station(self.station)
        response = self.client.get(reverse("schedule-prepare"), {"date": "2026-10-12", "target": self.device.pk})
        self.assertEqual(response.status_code, 200)
        assignments = [assignment.pk for assignment, _assets in response.context["cycle_options"]]
        self.assertEqual(assignments, [self.prior.pk])
        self.assertContains(response, "Review cycle delivery")
        self.assertNotContains(response, "Foreign Episode")

    def test_foreign_target_and_assignment_are_rejected_without_leaking_context(self):
        self.select_station(self.station)
        response = self.client.post(reverse("schedule-prepare"), {
            "action": "preview", "date": "2026-10-12", "target": self.other_device.pk,
            "assignment": [str(self.foreign.pk)], f"asset_{self.foreign.pk}": str(self.other_asset.pk),
        })
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "current channel")
        self.assertNotContains(response, "Foreign Episode")

    def test_malformed_target_and_date_context_render_recovery_instead_of_500(self):
        self.select_station(self.station)
        target_response = self.client.get(reverse("schedule-prepare"), {"date": "2026-10-12", "target": "abc"})
        self.assertEqual(target_response.status_code, 200)
        self.assertContains(target_response, "current channel")
        date_response = self.client.get(reverse("schedule-prepare"), {"date": "not-a-date"})
        self.assertEqual(date_response.status_code, 200)
        self.assertContains(date_response, "valid calendar date")

    def test_foreign_assignment_context_is_explained_without_rendering_foreign_cycle(self):
        self.select_station(self.station)
        response = self.client.get(reverse("schedule-prepare"), {
            "date": "2026-10-12", "assignment": str(self.foreign.pk),
        })
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "unavailable for this channel")
        self.assertNotContains(response, "Foreign Episode")

    def test_workspace_switch_preserves_date_but_clears_cycle_object_context(self):
        self.assertEqual(workspace_safe_url("/schedule/prepare/?date=2026-10-12&target=4&assignment=9"), "/schedule/prepare/?date=2026-10-12")
        self.assertEqual(workspace_safe_url("/schedule/review/?date=2026-10-12&target=4&assignment=9"), "/schedule/review/?date=2026-10-12")
        self.assertEqual(workspace_safe_url("/media/intake/saved/42/?next=/media/"), "/media/library/")
