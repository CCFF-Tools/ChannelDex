from datetime import date, time

from django.test import TestCase
from django.urls import reverse

from pubtv.operations.models import Episode, RecurrenceSlot, Show, Station


class ReservedScheduleViewTests(TestCase):
    def setUp(self):
        self.station = Station.objects.create(name="Test station", timezone="America/Detroit")
        self.show = Show.objects.create(station=self.station, title="Neighborhood Report", code="neighborhood-report", slot_duration_seconds=1800)

    def test_lists_active_recurring_show_slots_without_episode_details(self):
        selected = date(2026, 9, 14)  # Monday
        RecurrenceSlot.objects.create(
            station=self.station, show=self.show, weekday=0, start_time=time(7),
            duration_seconds=900, active_from=selected,
        )
        Episode.objects.create(show=self.show, title="Episode 1")

        response = self.client.get(reverse("reserved-schedule"), {"date": selected.isoformat()})

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Neighborhood Report")
        self.assertContains(response, "7:00 AM")
        self.assertContains(response, "7:30 AM")
        self.assertNotContains(response, "Available")
        self.assertNotContains(response, "Virtual-channel filler")
        self.assertNotContains(response, "Episode 1")

    def test_days_parameter_is_bounded(self):
        response = self.client.get(reverse("reserved-schedule"), {"days": "32"})
        self.assertEqual(response.status_code, 400)

    def test_show_catalog_renders_annotated_episode_count(self):
        Episode.objects.create(show=self.show, title="Episode 1")
        Episode.objects.create(show=self.show, title="Episode 2")

        response = self.client.get(reverse("show-list"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "2 episodes")


class NavigationTests(TestCase):
    def test_schedule_navigation_has_upcoming_shows_and_no_upload_action(self):
        response = self.client.get(reverse("dashboard"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Upcoming shows")
        self.assertNotContains(response, "Record schedule upload")
        self.assertNotContains(response, reverse("scheduling-today"))
        self.assertNotContains(response, reverse("upload-create"))

    def test_daily_scheduling_does_not_prompt_for_upload(self):
        response = self.client.get(reverse("scheduling-today"))
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, "Record schedule upload")
