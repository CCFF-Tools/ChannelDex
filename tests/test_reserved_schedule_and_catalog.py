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
        self.assertContains(response, "7:00 a.m.")
        self.assertContains(response, "7:30 a.m.")
        self.assertNotContains(response, "Available")
        self.assertNotContains(response, "Virtual-channel filler")
        self.assertNotContains(response, "Episode 1")

    def test_days_parameter_is_bounded(self):
        response = self.client.get(reverse("reserved-schedule"), {"days": "32"})
        self.assertEqual(response.status_code, 400)

    def test_show_catalog_renders_annotated_episode_count(self):
        Episode.objects.create(show=self.show, title="Episode 1")
        Episode.objects.create(show=self.show, title="Episode 2")
        RecurrenceSlot.objects.create(
            station=self.station, show=self.show, weekday=2, start_time=time(19, 30),
            duration_seconds=1800,
        )

        response = self.client.get(reverse("show-list"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "2 episodes")
        self.assertContains(response, "Weekly reserved times")
        self.assertContains(response, "Wednesday")
        self.assertContains(response, "7:30 p.m.")

    def test_show_catalog_can_sort_by_episode_count(self):
        other = Show.objects.create(station=self.station, title="A Smaller Show", code="smaller")
        Episode.objects.create(show=self.show, title="Episode 1")
        Episode.objects.create(show=self.show, title="Episode 2")
        response = self.client.get(reverse("show-list"), {"sort": "episodes"})
        body = response.content.decode()
        self.assertLess(body.index("A Smaller Show"), body.index("Neighborhood Report"))


class NavigationTests(TestCase):
    def test_today_navigation_has_recurring_times_and_no_upload_action(self):
        response = self.client.get(reverse("dashboard"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Schedule")
        self.assertContains(self.client.get(reverse("reserved-schedule")), "Recurring times")
        self.assertNotContains(response, "Record schedule upload")
        self.assertNotContains(response, reverse("upload-create"))

    def test_daily_scheduling_does_not_prompt_for_upload(self):
        response = self.client.get(reverse("scheduling-today"))
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, "Record schedule upload")
