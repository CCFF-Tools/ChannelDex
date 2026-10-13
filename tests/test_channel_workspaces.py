from unittest.mock import patch

from django.test import Client, TestCase, override_settings, RequestFactory
from django.urls import reverse

from pubtv.operations.forms import AssetForm, AutomationPreparationForm, AutomationPublicationForm, DeviceForm, StationForm
from pubtv.operations.models import Device, MediaAsset, Show, Station


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

    def test_workspace_switch_collapses_every_owned_object_route(self):
        cases = {
            "/shows/7/edit/": "/shows/", "/shows/7/premieres/": "/shows/",
            "/shows/7/slots/": "/shows/", "/shows/7/slots/length/": "/shows/",
            "/episodes/8/duplicate/": "/episodes/", "/episodes/8/workflow/new/": "/episodes/",
            "/assets/9/edit/": "/media/library/", "/assets/9/transfers/new/": "/media/library/",
            "/assets/9/transfers/3/edit/": "/media/library/", "/slots/4/edit/": "/schedule/recurring/",
            "/settings/devices/9/": "/settings/devices/", "/settings/devices/9/edit/": "/settings/devices/",
            "/occurrences/6/preparation/": "/schedule/", "/occurrences/6/programming/": "/schedule/",
            "/occurrences/6/airing/?date=2026-10-12&unsafe=x": "/schedule/?date=2026-10-12",
            "/media/preparation/?show=1&episode=2&asset=3&scope=attention": "/media/preparation/?scope=attention",
            "/schedule/guided-delivery/?asset=3": "/schedule/guided-delivery/",
            "/assets/new/?episode=2": "/assets/new/", "/episodes/new/?show=1": "/episodes/new/",
            "/occurrences/new/?asset=3": "/occurrences/new/",
            "/shows/?q=civic&sort=title&show=9": "/shows/?q=civic&sort=title",
            "/episodes/?show=9": "/episodes/",
            "/old-or-unknown/object/": "/",
        }
        for source, expected in cases.items():
            response = Client().post("/stations/switch/", {"station": self.other.pk, "next": source})
            self.assertEqual(response["Location"], expected, source)

    @override_settings(DEBUG=False)
    def test_error_pages_keep_shared_recovery_and_workspace_selector(self):
        response = Client().get("/definitely-missing/")
        self.assertEqual(response.status_code, 404)
        self.assertContains(response, "Working channel", status_code=404)
        self.assertContains(response, "Open Schedule", status_code=404)
        response = Client().post("/shows/new/", {}, HTTP_SEC_FETCH_SITE="same-origin")
        self.assertContains(response, "Nothing was changed", status_code=400)

    @override_settings(DEBUG=False)
    def test_server_error_template_has_database_independent_recovery(self):
        from pubtv.operations.views import server_error
        response = server_error(RequestFactory().get("/broken/"))
        self.assertEqual(response.status_code, 500)
        self.assertIn(b"Open ChannelDex", response.content)

    def test_station_form_allows_owner_named_channels(self):
        form = StationForm({"name": "Community-TV", "timezone": "America/Detroit"})
        self.assertTrue(form.is_valid())

    def test_device_names_are_unique_per_station(self):
        Device.objects.create(station=self.pub, name="WinLGX")
        form = DeviceForm({"name": "WinLGX"}, station=self.pub)
        self.assertFalse(form.is_valid())
        self.assertTrue(DeviceForm({"name": "WinLGX"}, station=self.other).is_valid())

    def test_destination_choices_identify_device_channel_and_adapter(self):
        device = Device.objects.create(station=self.pub, name="Playback A")
        form = AutomationPreparationForm(station=self.pub)
        self.assertIn("Playback A · PUB-TV · adapter not configured", str(form["target"]))
        self.assertEqual(form.initial["target"], device)
        Device.objects.create(station=self.pub, name="Playback B")
        form = AutomationPreparationForm(station=self.pub)
        self.assertNotIn("target", form.initial)
        self.assertIn("Choose a destination", str(form["target"]))

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

    def test_stale_form_from_another_workspace_is_rejected_without_writing(self):
        client = Client()
        session = client.session
        session["active_station_id"] = self.other.pk
        session.save()
        response = client.post(
            "/shows/new/",
            {"originating_station": str(self.pub.pk), "station": self.other.pk,
             "title": "Wrong workspace", "code": "wrong-workspace"},
        )
        self.assertEqual(response.status_code, 303)
        self.assertEqual(response["Location"], "/shows/new/")
        self.assertFalse(Show.objects.filter(title="Wrong workspace").exists())

    def test_server_renders_origin_and_browser_post_without_it_is_rejected(self):
        response = Client().get("/shows/new/")
        self.assertContains(response, f'name="originating_station" value="{self.pub.pk}"', count=2)
        response = Client().post(
            "/shows/new/", {"title": "Missing origin", "code": "missing-origin"},
            HTTP_SEC_FETCH_SITE="same-origin", HTTP_SEC_FETCH_MODE="navigate",
        )
        self.assertEqual(response.status_code, 400)
        self.assertFalse(Show.objects.filter(code="missing-origin").exists())

    def test_browser_picker_accepts_matching_origin_and_rejects_stale_origin(self):
        headers = {"HTTP_SEC_FETCH_SITE": "same-origin", "HTTP_SEC_FETCH_MODE": "cors"}
        with patch("pubtv.operations.views.subprocess.run") as run:
            run.return_value.returncode = 0
            run.return_value.stdout = "[]"
            run.return_value.stderr = ""
            matching = Client().post(
                "/settings/browse/", {"kind": "files", "originating_station": str(self.pub.pk)}, **headers,
            )
            self.assertEqual(matching.status_code, 200)
            stale = Client().post(
                "/settings/browse/", {"kind": "files", "originating_station": str(self.other.pk)}, **headers,
            )
            self.assertEqual(stale.status_code, 303)
            self.assertEqual(stale["Location"], "/settings/browse/")
            self.assertEqual(run.call_count, 1)

    def test_deleted_session_workspace_falls_back_with_announcement(self):
        client = Client()
        session = client.session
        session["active_station_id"] = self.other.pk
        session.save()
        self.other.delete()
        response = client.get("/")
        self.assertEqual(response.context["active_station"], self.pub)
        self.assertContains(response, "previous working channel is unavailable")

    def test_reusable_media_is_owned_and_browsed_only_in_its_workspace(self):
        client = Client()
        response = client.post("/assets/new/?next=/media/library/", {
            "episode": "", "file_name": "station-id.mxf", "kind": "encoded", "version": "v1",
            "runtime_seconds_0": "0", "runtime_seconds_1": "0", "runtime_seconds_2": "0",
            "local_path": "/private/media/station-id.mxf", "smb_reference": "",
        })
        self.assertRedirects(response, "/media/library/")
        asset = MediaAsset.objects.get(file_name="station-id.mxf")
        self.assertEqual(asset.station_id, self.pub.pk)
        self.assertIsNone(asset.episode_id)
        self.assertContains(client.get("/media/library/"), "station-id.mxf")
        session = client.session
        session["active_station_id"] = self.other.pk
        session.save()
        self.assertNotContains(client.get("/media/library/"), "station-id.mxf")

    def test_optional_asset_duration_preserves_unknown_and_explicit_zero(self):
        base = {"episode": "", "file_name": "asset.mxf", "kind": "encoded", "version": "v1",
                "local_path": "", "smb_reference": ""}
        unknown = AssetForm({**base, "runtime_seconds_0": "", "runtime_seconds_1": "", "runtime_seconds_2": ""}, station=self.pub)
        self.assertTrue(unknown.is_valid(), unknown.errors)
        self.assertIsNone(unknown.cleaned_data["runtime_seconds"])
        zero = AssetForm({**base, "runtime_seconds_0": "0", "runtime_seconds_1": "0", "runtime_seconds_2": "0"}, station=self.pub)
        self.assertTrue(zero.is_valid(), zero.errors)
        self.assertEqual(zero.cleaned_data["runtime_seconds"], 0)


class BootstrapWorkspaceTests(TestCase):
    def test_no_workspace_origin_allows_first_station_setup_and_disabled_quit(self):
        client = Client()
        setup = client.post(
            "/setup/station/", {"originating_station": "none", "name": "PUB-TV", "timezone": "America/Detroit"},
            HTTP_SEC_FETCH_SITE="same-origin", HTTP_SEC_FETCH_MODE="navigate",
        )
        self.assertEqual(setup.status_code, 302)
        self.assertTrue(Station.objects.filter(name="PUB-TV").exists())
        Station.objects.all().delete()
        with patch.dict("os.environ", {"PUBTV_ENABLE_QUIT": "0"}, clear=False):
            quit_response = client.post(
                "/quit/", {"originating_station": "none"},
                HTTP_SEC_FETCH_SITE="same-origin", HTTP_SEC_FETCH_MODE="navigate",
            )
        self.assertEqual(quit_response.status_code, 404)
