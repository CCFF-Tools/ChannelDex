from datetime import date, datetime, time, timedelta, timezone as dt_timezone

from django.test import Client, TestCase
from django.utils import timezone

from pubtv.operations.forms import (
    AssetForm, AssetPreparationForm, AssetTargetTransferForm, EpisodeForm,
    ProgrammingForm, ShowForm, SlotForm,
)
from pubtv.operations.models import (
    AuditEvent,
    AiringEvidence,
    AssetPreparation,
    AssetTargetTransfer,
    Delivery,
    Device,
    Episode,
    MediaAsset,
    Occurrence,
    OccurrenceProgramming,
    Producer,
    RecurrenceSlot,
    Show,
    Station,
    WeeklyEpisodeAssignment,
    UploadedOccurrenceCoverage,
    UploadedScheduleRevision,
)
from pubtv.operations.services import materialize_assignment


class RequestedUsabilityTests(TestCase):
    def setUp(self):
        self.station = Station.objects.create(name="PUB-TV")
        self.show = Show.objects.create(station=self.station, title="Community Hour", code="community-hour")
        self.episode = Episode.objects.create(show=self.show, title="Episode 1")
        self.client = Client()

    def test_create_show_can_add_a_producer_in_a_dialog(self):
        response = self.client.get("/shows/new/")

        self.assertContains(response, "data-producer-dialog-open")
        self.assertContains(response, "data-producer-dialog")
        self.assertContains(response, "/producers/new/?popup=1")

    def test_producer_popup_allows_only_same_origin_embedding(self):
        response = self.client.get("/producers/new/?popup=1")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers["X-Frame-Options"], "SAMEORIGIN")
        self.assertContains(response, "Add and select producer")

    def test_popup_producer_creation_returns_selectable_parent_message(self):
        response = self.client.post(
            "/producers/new/?popup=1",
            {"first_name": "Jamie", "last_name": "Rivera", "phone": "", "email": "", "external_membership_number": "", "notes": ""},
        )

        producer = Producer.objects.get(first_name="Jamie", last_name="Rivera")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "channeldex:producer-created")
        self.assertContains(response, f"id:'{producer.pk}'")

    def test_show_producer_choices_are_restricted_to_the_station(self):
        local = Producer.objects.create(station=self.station, first_name="Local", last_name="Producer")
        other_station = Station.objects.create(name="GOV-TV")
        Producer.objects.create(station=other_station, first_name="Other", last_name="Producer")

        form = ShowForm(station=self.station)

        self.assertEqual(list(form.fields["primary_producer"].queryset), [local])

    def test_duration_units_stay_visible_after_values_are_entered(self):
        form = SlotForm(
            data={
                "station": self.station.pk,
                "show": self.show.pk,
                "weekday": 0,
                "start_time": "07:00",
                "duration_seconds_0": "1",
                "duration_seconds_1": "30",
                "duration_seconds_2": "0",
                "active_from": "",
                "active_until": "",
            },
            station=self.station,
        )

        rendered = str(form["duration_seconds"])
        self.assertIn(">Hours</span>", rendered)
        self.assertIn(">Minutes</span>", rendered)
        self.assertIn('value="1"', rendered)
        self.assertIn('value="30"', rendered)
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.cleaned_data["duration_seconds"], 5400)

    def test_episode_runtime_exposes_editable_seconds(self):
        form = EpisodeForm(
            data={
                "show": self.show.pk,
                "title": "Episode with exact runtime",
                "producer": "",
                "intended_air_order": "",
                "intended_premiere_date": "",
                "runtime_seconds_0": "1",
                "runtime_seconds_1": "2",
                "runtime_seconds_2": "3",
            },
            station=self.station,
        )

        rendered = str(form["runtime_seconds"])
        self.assertIn(">Seconds</span>", rendered)
        self.assertIn('value="3"', rendered)
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.cleaned_data["runtime_seconds"], 3723)

    def test_each_asset_has_an_optional_editable_duration(self):
        create = self.client.post(
            f"/assets/new/?episode={self.episode.pk}",
            {
                "episode": self.episode.pk,
                "file_name": "episode-1-encoded.mxf",
                "kind": "encoded",
                "version": "v1",
                "runtime_seconds_0": "0",
                "runtime_seconds_1": "28",
                "runtime_seconds_2": "0",
                "smb_reference": "",
            },
        )
        asset = MediaAsset.objects.get()

        self.assertEqual(create.status_code, 302)
        self.assertEqual(asset.runtime_seconds, 1680)
        self.assertContains(self.client.get(f"/assets/{asset.pk}/edit/"), ">Seconds</span>", html=False)
        detail = self.client.get(f"/episodes/{self.episode.pk}/")
        self.assertContains(detail, "Duration: 28 min")
        self.assertContains(detail, f"/assets/{asset.pk}/edit/")

        update = self.client.post(
            f"/assets/{asset.pk}/edit/",
            {
                "episode": self.episode.pk,
                "file_name": asset.file_name,
                "kind": asset.kind,
                "version": asset.version,
                "runtime_seconds_0": "0",
                "runtime_seconds_1": "29",
                "runtime_seconds_2": "0",
                "smb_reference": "",
            },
        )
        asset.refresh_from_db()

        self.assertEqual(update.status_code, 302)
        self.assertEqual(asset.runtime_seconds, 1740)

    def test_asset_duration_remains_optional(self):
        form = AssetForm(
            data={
                "episode": self.episode.pk,
                "file_name": "source.mov",
                "kind": "source",
                "version": "v1",
                "runtime_seconds_0": "",
                "runtime_seconds_1": "",
                "runtime_seconds_2": "",
                "smb_reference": "",
            },
            station=self.station,
        )

        self.assertTrue(form.is_valid(), form.errors)
        self.assertIsNone(form.cleaned_data["runtime_seconds"])

    def _create_weekly_cycle(self):
        RecurrenceSlot.objects.create(
            station=self.station,
            show=self.show,
            weekday=0,
            start_time=time(19, 0),
            duration_seconds=3600,
            is_premiere=True,
        )
        assignment = WeeklyEpisodeAssignment.objects.create(
            show=self.show,
            week_start=date(2099, 1, 5),
            premiere_date=date(2099, 1, 5),
            episode=self.episode,
            selection_type="premiere",
        )
        materialize_assignment(assignment)
        return assignment

    def test_carry_forward_setting_is_global_opt_in_and_audited(self):
        response = self.client.get("/settings/")

        self.assertContains(response, "Schedule defaults")
        self.assertContains(response, "Keep the most recent episode")
        self.assertFalse(self.station.carry_forward_unassigned_episodes)

        response = self.client.post(
            "/settings/",
            {"carry_forward_unassigned_episodes": "on"},
        )
        self.station.refresh_from_db()

        self.assertRedirects(response, "/settings/")
        self.assertTrue(self.station.carry_forward_unassigned_episodes)
        self.assertTrue(
            AuditEvent.objects.filter(
                entity="Station",
                entity_id=self.station.pk,
                summary="Automatic episode carry-forward enabled",
            ).exists()
        )

        response = self.client.post("/settings/", {})
        self.station.refresh_from_db()

        self.assertRedirects(response, "/settings/")
        self.assertFalse(self.station.carry_forward_unassigned_episodes)

    def test_enabled_setting_carries_forward_and_labels_the_replay_once(self):
        self._create_weekly_cycle()

        self.client.get("/week/?date=2099-01-12")
        self.assertFalse(
            WeeklyEpisodeAssignment.objects.filter(
                show=self.show,
                week_start=date(2099, 1, 12),
            ).exists()
        )

        self.station.carry_forward_unassigned_episodes = True
        self.station.save(update_fields=["carry_forward_unassigned_episodes"])

        response = self.client.get("/week/?date=2099-01-12&view=schedule")
        carried = WeeklyEpisodeAssignment.objects.get(
            show=self.show,
            week_start=date(2099, 1, 12),
        )
        occurrence = Occurrence.objects.get(weekly_assignment=carried)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(carried.episode, self.episode)
        self.assertEqual(carried.selection_type, "rerun")
        self.assertTrue(carried.is_automatic_carry_forward)
        self.assertEqual(occurrence.schedule_context, "Automatic carry-forward replay")
        self.assertContains(response, "Automatic carry-forward replay")
        self.assertContains(response, "can be replaced by an explicit plan")

        self.client.get("/week/?date=2099-01-12")
        self.assertEqual(
            WeeklyEpisodeAssignment.objects.filter(
                show=self.show,
                week_start=date(2099, 1, 12),
            ).count(),
            1,
        )
        self.assertEqual(Occurrence.objects.filter(weekly_assignment=carried).count(), 1)

        self.client.post("/settings/", {})
        self.assertTrue(WeeklyEpisodeAssignment.objects.filter(pk=carried.pk).exists())
        self.client.get("/week/?date=2099-01-19")
        self.assertFalse(
            WeeklyEpisodeAssignment.objects.filter(
                show=self.show,
                week_start=date(2099, 1, 19),
            ).exists()
        )

    def test_explicit_episode_replaces_carry_forward_and_no_program_stops_it(self):
        self._create_weekly_cycle()
        self.station.carry_forward_unassigned_episodes = True
        self.station.save(update_fields=["carry_forward_unassigned_episodes"])
        self.client.get("/week/?date=2099-01-12")
        replacement = Episode.objects.create(show=self.show, title="Episode 2")

        response = self.client.post(
            "/assignments/new/",
            {
                "show": self.show.pk,
                "premiere_date": "2099-01-12",
                "episode": replacement.pk,
            },
        )
        assignment = WeeklyEpisodeAssignment.objects.get(
            show=self.show,
            week_start=date(2099, 1, 12),
        )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(assignment.episode, replacement)
        self.assertEqual(assignment.selection_type, "premiere")
        self.assertFalse(assignment.is_automatic_carry_forward)
        self.assertEqual(
            Occurrence.objects.get(weekly_assignment=assignment).episode,
            replacement,
        )

        self.client.get("/week/?date=2099-01-19")
        response = self.client.post(
            "/assignments/new/",
            {"show": self.show.pk, "premiere_date": "2099-01-19", "episode": ""},
        )
        no_program = WeeklyEpisodeAssignment.objects.get(
            show=self.show,
            week_start=date(2099, 1, 19),
        )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(no_program.selection_type, "none")
        self.assertFalse(no_program.is_automatic_carry_forward)

        self.client.get("/week/?date=2099-01-26")
        self.assertFalse(
            WeeklyEpisodeAssignment.objects.filter(
                show=self.show,
                week_start=date(2099, 1, 26),
            ).exists()
        )

    def test_duplicate_episode_prefills_editable_identity_without_copying_workflow(self):
        producer = Producer.objects.create(
            station=self.station, first_name="Alex", last_name="Morgan"
        )
        self.episode.producer = producer
        self.episode.intended_air_order = 4
        self.episode.intended_premiere_date = date(2099, 2, 2)
        self.episode.runtime_seconds = 3723
        self.episode.save()
        Delivery.objects.create(method="email_link", reference="private-link").episodes.add(self.episode)
        MediaAsset.objects.create(
            episode=self.episode, file_name="original.mov", kind="source", version="v1"
        )

        page = self.client.get(f"/episodes/{self.episode.pk}/duplicate/")

        self.assertEqual(page.status_code, 200)
        self.assertEqual(page.context["form"].initial["show"], self.show.pk)
        self.assertEqual(page.context["form"].initial["title"], self.episode.title)
        self.assertEqual(page.context["form"].initial["producer"], producer.pk)
        self.assertEqual(page.context["form"].initial["runtime_seconds"], 3723)

        response = self.client.post(
            f"/episodes/{self.episode.pk}/duplicate/",
            {
                "show": self.show.pk,
                "title": "Episode 1 duplicate",
                "producer": producer.pk,
                "intended_air_order": 5,
                "intended_premiere_date": "2099-02-09",
                "runtime_seconds_0": 1,
                "runtime_seconds_1": 2,
                "runtime_seconds_2": 3,
            },
        )

        duplicate = Episode.objects.exclude(pk=self.episode.pk).get()
        self.assertRedirects(response, f"/episodes/{duplicate.pk}/")
        self.assertEqual(duplicate.runtime_seconds, 3723)
        self.assertFalse(duplicate.deliveries.exists())
        self.assertFalse(duplicate.assets.exists())
        self.assertTrue(
            AuditEvent.objects.filter(
                entity="Episode", entity_id=duplicate.pk,
                summary=f"Episode duplicated from {self.episode.pk}",
            ).exists()
        )

    def test_premiere_planner_handles_missing_date_and_updates_legacy_week(self):
        missing = self.client.post(
            "/assignments/new/", {"show": self.show.pk, "premiere_date": "", "episode": self.episode.pk}
        )
        self.assertEqual(missing.status_code, 200)
        self.assertContains(missing, "Choose the premiere date")

        RecurrenceSlot.objects.create(
            station=self.station, show=self.show, weekday=0,
            start_time=time(19), duration_seconds=1800, is_premiere=True,
        )
        legacy = WeeklyEpisodeAssignment.objects.create(
            show=self.show, week_start=date(2099, 1, 5), premiere_date=None,
            episode=self.episode, selection_type="rerun",
        )
        response = self.client.post(
            "/assignments/new/",
            {"show": self.show.pk, "premiere_date": "2099-01-05", "episode": self.episode.pk},
        )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(WeeklyEpisodeAssignment.objects.filter(show=self.show).count(), 1)
        legacy.refresh_from_db()
        self.assertEqual(legacy.premiere_date, date(2099, 1, 5))

    def test_episode_workflow_summary_uses_canonical_facts_and_exact_upload(self):
        received_at = timezone.now()
        delivery = Delivery.objects.create(
            method="email_link", reference="delivery", received_at=received_at
        )
        delivery.episodes.add(self.episode)
        asset = MediaAsset.objects.create(
            episode=self.episode, file_name="encoded.mxf", kind="encoded", version="v1"
        )
        AssetPreparation.objects.create(
            asset=asset, source_available="yes", source_available_at=received_at,
            ame_preset="PUB-TV", encoded_at=received_at,
        )
        self.episode.refresh_from_db()
        self.assertEqual(self.episode.workflow_summary["label"], "Encoded")

        device = Device.objects.create(name="WinLGX")
        AssetTargetTransfer.objects.create(
            asset=asset, device=device, ftp_details="FTP complete",
            library_registration="Library 12", transferred_at=received_at,
        )
        occurrence = Occurrence.objects.create(
            station=self.station, show=self.show, episode=self.episode, asset=asset,
            item_type="episode", label=self.show.title,
            starts_at=datetime(2099, 1, 5, 19, tzinfo=dt_timezone.utc),
            planned_duration_seconds=1800,
        )
        OccurrenceProgramming.objects.create(
            occurrence=occurrence, device=device, slot_assignment="Slot 12"
        )
        upload = UploadedScheduleRevision.objects.create(device=device)
        UploadedOccurrenceCoverage.objects.create(
            upload=upload, occurrence=occurrence, occurrence_revision=occurrence.revision
        )
        episode = Episode.objects.get(pk=self.episode.pk)
        self.assertEqual(episode.workflow_summary["label"], "Schedule uploaded")

        AiringEvidence.objects.create(
            occurrence=occurrence, status="reported", source="operator note",
            aired_at=occurrence.starts_at,
        )
        episode = Episode.objects.get(pk=self.episode.pk)
        self.assertEqual(episode.workflow_summary["label"], "Airing evidence recorded")

    def test_workbench_is_explicit_wherever_an_occurrence_is_listed(self):
        starts_at = timezone.now() + timedelta(hours=1)
        occurrence = Occurrence.objects.create(
            station=self.station, show=self.show, episode=self.episode,
            item_type="episode", label=self.show.title, starts_at=starts_at,
            planned_duration_seconds=1800,
        )
        link = f'/occurrences/{occurrence.pk}/'

        self.assertContains(self.client.get("/agenda/?days=2"), link)
        self.assertContains(self.client.get(f"/shows/{self.show.pk}/"), "Open workbench")
        self.assertContains(self.client.get(f"/episodes/{self.episode.pk}/"), "Open workbench")
        selected = timezone.localdate(starts_at)
        self.assertContains(self.client.get(f"/schedule-today/?date={selected.isoformat()}"), link)

    def test_schedule_toolbar_and_weekly_times_keep_actions_in_fixed_columns(self):
        RecurrenceSlot.objects.create(
            station=self.station, show=self.show, weekday=1,
            start_time=time(7), duration_seconds=1800, is_premiere=True,
        )
        day = self.client.get("/schedule/?mode=day&date=2099-01-05")
        shows = self.client.get("/shows/")
        detail = self.client.get(f"/shows/{self.show.pk}/")

        self.assertContains(day, 'class="schedule-toolbar"')
        self.assertContains(day, "Add to schedule")
        self.assertContains(shows, 'class="weekly-slot-role"')
        self.assertContains(shows, "<time>7:00 a.m.</time>", html=True)
        self.assertNotContains(detail, "This works without JavaScript")

    def test_preparation_forms_use_plain_operational_language_without_provenance_entry(self):
        preparation = AssetPreparationForm()
        transfer = AssetTargetTransferForm(station=self.station)
        programming = ProgrammingForm()

        self.assertIn("preset name and version", preparation.fields["ame_preset"].help_text)
        self.assertEqual(transfer.fields["library_registration"].label, "WinLGX library registration")
        self.assertNotIn("transfer_provenance", transfer.fields)
        self.assertEqual(programming.fields["slot_assignment"].label, "WinLGX playback slot")
