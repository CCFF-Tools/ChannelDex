from django.test import Client, TestCase
from django.urls import reverse

from pubtv.operations.forms import AutomationPreparationForm, AutomationPublicationForm, DeviceForm, StationForm
from pubtv.operations.models import Device, Show, Station


class ChannelWorkspaceTests(TestCase):
    def setUp(self):
        self.pub = Station.objects.create(name="PUB-TV")
        self.other = Station.objects.create(name="ALT-TV")

    def test_selector_defaults_to_pub_tv_and_switches_session(self):
        client = Client()
        response = client.get(reverse("dashboard"))
        self.assertEqual(response.context["active_station"], self.pub)
        response = client.post(reverse("station-switch"), {"station": self.other.pk}, HTTP_REFERER="/shows/")
        self.assertRedirects(response, "/shows/")
        self.assertEqual(client.get(reverse("dashboard")).context["active_station"], self.other)

    def test_selector_rejects_unknown_station(self):
        response = Client().post(reverse("station-switch"), {"station": 99999})
        self.assertEqual(response.status_code, 400)

    def test_selector_get_never_mutates(self):
        client = Client()
        self.assertEqual(client.get(reverse("station-switch")).status_code, 405)
        self.assertEqual(client.get(reverse("dashboard")).context["active_station"], self.pub)

    def test_selector_uses_posted_current_path_without_referrer(self):
        response = Client().post(reverse("station-switch"), {"station": self.other.pk, "next": "/media/"})
        self.assertRedirects(response, "/media/")

    def test_selector_rejects_external_posted_next(self):
        response = Client().post(reverse("station-switch"), {"station": self.other.pk, "next": "https://evil.example/"})
        self.assertRedirects(response, "/")

    def test_station_form_allows_owner_named_channels(self):
        form = StationForm({"name": "Community-TV", "timezone": "America/Detroit"})
        self.assertTrue(form.is_valid())

    def test_device_names_are_unique_per_station(self):
        Device.objects.create(station=self.pub, name="WinLGX")
        form = DeviceForm({"name": "WinLGX"}, station=self.pub)
        self.assertFalse(form.is_valid())
        self.assertTrue(DeviceForm({"name": "WinLGX"}, station=self.other).is_valid())

    def test_show_url_does_not_cross_station_boundary(self):
        show = Show.objects.create(station=self.other, title="Other", code="other")
        client = Client()
        self.assertEqual(client.get(reverse("show-detail", args=[show.pk])).status_code, 404)

    def test_automation_forms_reject_other_station_targets_and_occurrences(self):
        foreign_device = Device.objects.create(station=self.other, name="Foreign")
        prep = AutomationPreparationForm({"target": foreign_device.pk}, station=self.pub)
        self.assertFalse(prep.is_valid())
        self.assertNotIn(foreign_device, prep.fields["target"].queryset)
        publication = AutomationPublicationForm({"target": foreign_device.pk}, station=self.pub)
        self.assertFalse(publication.is_valid())
