from datetime import date, datetime, time, timedelta, timezone as dt_timezone
from zoneinfo import ZoneInfo

from django.core.exceptions import ValidationError
from django.test import Client, TestCase, TransactionTestCase
from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.utils import timezone

from pubtv.operations.forms import AssignmentForm, DeliveryForm, DurationWidget, EpisodeForm, ShowForm, SlotForm
from pubtv.operations.models import (
    AiringEvidence, Device, Delivery, Episode, EpisodeWorkflowMilestone,
    Occurrence, Preparation, Producer, RecurrenceSlot, Show, Station, UploadedOccurrenceCoverage,
    UploadedScheduleRevision, WeeklyEpisodeAssignment, MediaAsset, AuditEvent,
)
from pubtv.operations.services import preparation_readiness, calendar_capacity


class TodoWorkflowTests(TestCase):
    def setUp(self):
        self.station = Station.objects.create(name="PUB-TV", timezone="America/Detroit")
        self.show = Show.objects.create(station=self.station, title="Street Talk", code="street-talk")
        self.episode = Episode.objects.create(show=self.show, title="Episode 1")
        self.slot = RecurrenceSlot.objects.create(station=self.station, show=self.show, weekday=0, start_time=time(7), duration_seconds=1800)

    def test_media_assets_have_distinct_stable_ids_and_filenames(self):
        asset = MediaAsset.objects.create(episode=self.episode, label="master.mov", kind="source")
        first_id = asset.asset_id
        self.assertEqual(asset.file_name, "master.mov")
        asset.refresh_from_db()
        self.assertEqual(asset.asset_id, first_id)
        encoded = MediaAsset.objects.create(episode=self.episode, file_name="master.mp4", kind="encoded")
        self.assertNotEqual(asset.asset_id, encoded.asset_id)

    def test_pending_episode_queue_reorder_requires_complete_same_show_payload_and_audits(self):
        second = Episode.objects.create(show=self.show, title="Episode 2", intended_air_order=2)
        self.episode.intended_air_order = 1
        self.episode.save(update_fields=["intended_air_order"])
        client = Client()
        response = client.post(f"/shows/{self.show.pk}/episodes/queue/", {"episode_ids": f"{second.pk},{self.episode.pk}"})
        self.assertEqual(response.status_code, 302)
        self.episode.refresh_from_db(); second.refresh_from_db()
        self.assertEqual((self.episode.intended_air_order, second.intended_air_order), (2, 1))
        self.assertTrue(AuditEvent.objects.filter(entity="EpisodeQueue", action="reorder").exists())
        self.assertEqual(client.post(f"/shows/{self.show.pk}/episodes/queue/", {"episode_ids": str(self.episode.pk)}).status_code, 400)


    def test_show_links_delivery_choices_and_other_validation(self):
        show = Show.objects.get()
        response = Client().post(f"/shows/{show.pk}/edit/", {
            "station": self.station.pk,
            "title": show.title,
            "code": show.code,
            "website": "https://example.test",
            "youtube": "https://youtube.test/x",
            "facebook": "https://facebook.test/x",
            "instagram": "https://instagram.test/x",
            "primary_delivery_method": "google_drive",
            "primary_delivery_other": "",
        })
        self.assertEqual(response.status_code, 302)
        show.refresh_from_db()
        self.assertEqual((show.website, show.youtube, show.facebook, show.instagram), ("https://example.test", "https://youtube.test/x", "https://facebook.test/x", "https://instagram.test/x"))
        self.assertEqual(dict(Show.DELIVERY_METHODS), {"google_drive":"Google Drive", "dropbox":"Dropbox", "email_link":"Email link", "smb_transfer":"SMB Transfer", "other":"Other"})
        self.assertFalse(ShowForm(data={"station": self.station.pk, "title":"X", "code":"x", "primary_delivery_method":"other"}).is_valid())
        self.assertFalse(DeliveryForm(data={"method":"other", "reference":"ref"}).is_valid())

    def test_delivery_links_multiple_episodes_and_contextual_episode_preselection(self):
        second = Episode.objects.create(show=self.show, title="Episode 2")
        delivery = Delivery.objects.create(method="email_link", reference="https://example.test/file")
        delivery.episodes.set([self.episode, second])
        self.assertEqual(set(delivery.episodes.all()), {self.episode, second})
        response = Client().get(f"/deliveries/new/?episode={self.episode.pk}")
        self.assertEqual(response.context["form"].initial["episode"], self.episode.pk)
        foreign = Station.objects.create(name="GOV-TV")
        foreign_episode = Episode.objects.create(show=Show.objects.create(station=foreign, title="G", code="g"), title="G1")
        self.assertEqual(Client().get(f"/episodes/new/?show={foreign_episode.show_id}").status_code, 404)
        self.assertContains(Client().get(f"/shows/{self.show.pk}/"), "Edit show")
        self.assertContains(Client().get(f"/episodes/{self.episode.pk}/"), "Edit episode")

    def test_repeated_valid_station_setup_updates_singleton(self):
        client = Client()
        self.assertEqual(client.post("/setup/station/", {"name":"PUB-TV", "timezone":"America/Detroit"}).status_code, 302)
        self.assertEqual(Station.objects.count(), 1)

    def test_assignment_correction_updates_planned_recurrences_and_is_locked_after_evidence(self):
        other = Episode.objects.create(show=self.show, title="Episode 2")
        assignment = WeeklyEpisodeAssignment.objects.create(show=self.show, week_start=date(2026, 1, 5), episode=self.episode, selection_type="premiere")
        from pubtv.operations.services import materialize_assignment
        materialize_assignment(assignment)
        client = Client()
        payload = {"show": self.show.pk, "week_start":"2026-01-05", "episode":other.pk, "selection_type":"rerun"}
        self.assertEqual(client.post("/assignments/new/", payload).status_code, 302)
        self.assertEqual(Occurrence.objects.filter(weekly_assignment=assignment, episode=other).count(), 1)
        occurrence = Occurrence.objects.get(weekly_assignment=assignment)
        upload = UploadedScheduleRevision.objects.create(device=Device.objects.create(name="D"))
        UploadedOccurrenceCoverage.objects.create(upload=upload, occurrence=occurrence, occurrence_revision=occurrence.revision)
        self.assertEqual(client.post("/assignments/new/", {**payload, "episode":self.episode.pk}).status_code, 400)
        AiringEvidence.objects.create(occurrence=occurrence, status="reported", source="log", aired_at=occurrence.starts_at)
        self.assertEqual(client.post("/assignments/new/", {**payload, "episode":self.episode.pk}).status_code, 400)

    def test_assignment_correction_preserves_cancelled_history_and_workflow_facts(self):
        other = Episode.objects.create(show=self.show, title="Episode 2")
        assignment = WeeklyEpisodeAssignment.objects.create(
            show=self.show,
            week_start=date(2026, 1, 5),
            episode=self.episode,
            selection_type="premiere",
        )
        from pubtv.operations.services import materialize_assignment
        materialize_assignment(assignment)
        original = Occurrence.objects.get(weekly_assignment=assignment)
        client = Client()
        none_payload = {
            "show": self.show.pk,
            "week_start": "2026-01-05",
            "episode": "",
            "selection_type": "none",
        }
        self.assertEqual(client.post("/assignments/new/", none_payload).status_code, 302)
        original.refresh_from_db()
        self.assertEqual(original.status, "cancelled")

        rerun_payload = {**none_payload, "episode": other.pk, "selection_type": "rerun"}
        self.assertEqual(client.post("/assignments/new/", rerun_payload).status_code, 302)
        replacement = Occurrence.objects.get(weekly_assignment=assignment, status="planned")
        self.assertNotEqual(replacement.pk, original.pk)
        self.assertEqual(replacement.episode_id, other.pk)

        self.assertEqual(client.get(f"/occurrences/{replacement.pk}/preparation/").status_code, 200)
        view_only_update = {**rerun_payload, "episode": self.episode.pk}
        self.assertEqual(client.post("/assignments/new/", view_only_update).status_code, 302)
        replacement.refresh_from_db()
        self.assertEqual(replacement.episode_id, self.episode.pk)

        other = Episode.objects.create(show=self.show, title="Episode 3")
        preparation = Preparation.objects.get(occurrence=replacement)
        preparation.source_available = "yes"
        preparation.save(update_fields=["source_available"])
        blocked = {**rerun_payload, "episode": other.pk}
        self.assertEqual(client.post("/assignments/new/", blocked).status_code, 400)
        replacement.refresh_from_db()
        self.assertEqual(replacement.episode_id, self.episode.pk)

    def test_preparation_readiness_normal_and_live(self):
        normal = Occurrence.objects.create(station=self.station, show=self.show, episode=self.episode, item_type="episode", label="E", starts_at=datetime(2026,1,5,12,tzinfo=dt_timezone.utc), planned_duration_seconds=1800)
        self.assertEqual(preparation_readiness(normal)["label"], "0/6 preparation facts")
        Preparation.objects.create(occurrence=normal, source_available="yes", ame_preset="a", ftp_output_device="f", library_registration="l", slot_assignment="s", uploaded_schedule_revision="u")
        self.assertTrue(preparation_readiness(normal)["ready"])
        live = Occurrence.objects.create(station=self.station, item_type="live", label="Live", starts_at=normal.starts_at, planned_duration_seconds=1800)
        self.assertEqual(preparation_readiness(live)["label"], "4/6 preparation facts")
        Preparation.objects.create(occurrence=live, slot_assignment="slot", uploaded_schedule_revision="upload")
        self.assertEqual(preparation_readiness(live)["label"], "6/6 preparation facts")

    def test_day_cross_midnight_and_dst_capacity_totals(self):
        Occurrence.objects.create(station=self.station, item_type="filler", label="Overnight", starts_at=datetime(2026,1,5,4,59,tzinfo=dt_timezone.utc), planned_duration_seconds=120)
        body = Client().get("/day/?date=2026-01-04").content.decode()
        self.assertIn("Overnight", body)
        body = Client().get("/day/?date=2026-03-08").content.decode()
        self.assertNotIn("unknown coverage", body.lower())
        self.assertIn("Available", body)
        body = Client().get("/day/?date=2026-11-01").content.decode()
        self.assertNotIn("unknown coverage", body.lower())
        self.assertIn("Available", body)

    def test_capacity_union_available_filler_missing_runtime_and_cross_midnight(self):
        day = date(2026, 1, 5)
        Occurrence.objects.create(station=self.station, show=self.show, episode=self.episode,
            recurrence_slot=self.slot, item_type="episode", label="Short", starts_at=datetime(2026, 1, 5, 7, tzinfo=ZoneInfo("America/Detroit")), planned_duration_seconds=1800)
        self.episode.runtime_seconds = 1200
        self.episode.save(update_fields=["runtime_seconds"])
        RecurrenceSlot.objects.create(station=self.station, weekday=0, start_time=time(23), duration_seconds=7200)
        intervals = calendar_capacity(self.station, day)[0]["intervals"]
        labels = [item["label"] for item in intervals]
        self.assertIn("Virtual-channel filler (reserved)", labels)
        filler = next(item for item in intervals if item["status"] == "virtual-channel-filler")
        self.assertLessEqual(filler["end"], datetime(2026, 1, 5, 7, 30, tzinfo=ZoneInfo("America/Detroit")))
        self.assertTrue(any(item["status"] == "available" for item in intervals))
        overnight = calendar_capacity(self.station, date(2026, 1, 6))[0]["intervals"]
        self.assertTrue(any(item["status"] == "reserved" for item in overnight))
        self.episode.runtime_seconds = None
        self.episode.save(update_fields=["runtime_seconds"])
        missing = calendar_capacity(self.station, day)[0]["intervals"]
        matched_slot = next(
            item for item in missing
            if item["status"] == "reserved" and item["start"].hour == 7
        )
        self.assertEqual(matched_slot["label"], "Reserved (runtime not determined)")

    def test_capacity_uses_elapsed_time_across_dst_and_plain_labels_for_timed_items(self):
        eastern = ZoneInfo("America/Detroit")
        utc = ZoneInfo("UTC")
        RecurrenceSlot.objects.create(
            station=self.station, show=self.show, weekday=6,
            start_time=time(1, 30), duration_seconds=3600,
        )
        for transition_day in (date(2026, 3, 8), date(2026, 11, 1)):
            intervals = calendar_capacity(self.station, transition_day)[0]["intervals"]
            reserved = next(
                item for item in intervals
                if item["status"] == "reserved" and item["start"].hour == 1
            )
            self.assertEqual(
                reserved["end"].astimezone(utc) - reserved["start"].astimezone(utc),
                timedelta(seconds=3600),
            )

        timed_day = date(2026, 1, 7)
        for index, item_type in enumerate(("filler", "live")):
            Occurrence.objects.create(
                station=self.station, item_type=item_type, label=item_type.title(),
                starts_at=datetime(2026, 1, 7, 12 + index, tzinfo=eastern),
                planned_duration_seconds=1800,
            )
        timed = calendar_capacity(self.station, timed_day)[0]["intervals"]
        timed_labels = [item["label"] for item in timed if item["status"] == "reserved"]
        self.assertEqual(timed_labels, ["Reserved", "Reserved"])

    def test_show_type_choices_other_clearing_and_calendar_filters(self):
        self.show.show_type = "arts_and_culture"
        self.show.show_type_other = "stale"
        self.show.save(update_fields=["show_type", "show_type_other"])
        form = ShowForm(data={"station": self.station.pk, "title": "X", "code": "x", "show_type": "arts_and_culture", "show_type_other": "stale"})
        self.assertTrue(form.is_valid())
        self.assertEqual(form.cleaned_data["show_type_other"], "")
        invalid = ShowForm(data={"station": self.station.pk, "title": "X", "code": "x", "show_type": "other"})
        self.assertFalse(invalid.is_valid())
        starts = datetime(2026, 1, 5, 7, tzinfo=ZoneInfo("America/Detroit"))
        Occurrence.objects.create(station=self.station, show=self.show, episode=self.episode, item_type="episode", label="E", starts_at=starts, planned_duration_seconds=60)
        self.assertContains(Client().get("/day/?date=2026-01-05&capacity=reserved"), "Reserved")
        self.assertContains(Client().get("/week/?date=2026-01-05&capacity=available"), "Available")
        self.show.show_type = "arts_and_culture"
        self.show.save(update_fields=["show_type"])
        self.assertContains(Client().get("/shows/?show_type=arts_and_culture"), "Street Talk")
        self.assertContains(Client().get("/day/?date=2026-01-05&show_type=arts_and_culture"), "Reserved")
        self.assertContains(Client().get("/agenda/?days=7"), "E")

    def test_calendar_warnings_context_and_history_readiness(self):
        today = timezone.localdate()
        week_start = today - timedelta(days=today.weekday())
        starts_at = datetime.combine(today, time(7), ZoneInfo("America/Detroit"))
        occurrence = Occurrence.objects.create(station=self.station, show=self.show, episode=self.episode, item_type="episode", label="Premiere", starts_at=starts_at, planned_duration_seconds=60)
        assignment = WeeklyEpisodeAssignment.objects.create(show=self.show, week_start=week_start, episode=self.episode, selection_type="premiere")
        occurrence.weekly_assignment = assignment; occurrence.save(skip_revision=True)
        Occurrence.objects.create(
            station=self.station,
            item_type="filler",
            label="Overlap",
            starts_at=starts_at + timedelta(seconds=30),
            planned_duration_seconds=60,
        )
        self.assertContains(Client().get(f"/week/?date={today.isoformat()}"), "Warnings")
        self.assertContains(Client().get("/agenda/?days=31"), "New premiere")
        self.assertContains(Client().get("/history/"), "0/6 preparation facts")

    def test_named_producer_flow_and_private_optional_membership(self):
        client = Client()
        response = client.post(
            f"/producers/new/?show={self.show.pk}",
            {
                "first_name": "Jamie",
                "last_name": "Rivera",
                "contact": "private@example.test",
                "external_membership_number": "MT-42",
                "notes": "Owner supplied",
            },
        )
        producer = Producer.objects.get()
        self.assertRedirects(
            response,
            f"/episodes/new/?producer={producer.pk}&show={self.show.pk}",
            fetch_redirect_response=False,
        )
        create = client.get(response.url)
        self.assertEqual(create.context["form"].initial["producer"], producer.pk)
        self.assertNotIn("producer_id", EpisodeForm().fields)
        self.assertNotIn("status", EpisodeForm().fields)
        self.assertContains(client.get(f"/producers/new/?show={self.show.pk}"), "External membership number (optional)")

    def test_episode_workflow_milestones_are_progressive_and_do_not_change_queue_status(self):
        client = Client()
        received = {
            "stage": "received", "completed_at": "2026-01-01T09:00",
            "actor": "owner", "provenance": "manual handoff", "notes": "notice received",
        }
        self.assertEqual(client.post(f"/episodes/{self.episode.pk}/workflow/new/", received).status_code, 302)
        self.assertEqual(client.post(f"/episodes/{self.episode.pk}/workflow/new/", {**received, "stage": "encoded"}).status_code, 302)
        blocked = client.post(f"/episodes/{self.episode.pk}/workflow/new/", {**received, "stage": "downloaded"})
        self.assertEqual(blocked.status_code, 200)
        self.assertEqual(EpisodeWorkflowMilestone.objects.count(), 2)
        self.episode.refresh_from_db()
        self.assertEqual(self.episode.status, "pending")
        self.assertEqual(self.episode.latest_workflow_stage, "Encoded")

    def test_weekly_time_manager_uses_names_local_controls_and_role_snapshots(self):
        self.slot.delete()
        client = Client()
        premiere = {
            "station": self.station.pk, "show": self.show.pk, "weekday": 2,
            "start_time": "19:00", "duration_seconds_0": 0,
            "duration_seconds_1": 30, "duration_seconds_2": 0, "is_premiere": "on",
            "active_from": "", "active_until": "",
        }
        self.assertEqual(client.post(f"/slots/new/?show={self.show.pk}", premiere).status_code, 302)
        replay = {**premiere, "weekday": 4, "start_time": "20:00"}
        replay.pop("is_premiere")
        self.assertEqual(client.post(f"/slots/new/?show={self.show.pk}", replay).status_code, 302)
        manager = client.get(f"/shows/{self.show.pk}/slots/")
        self.assertContains(manager, "Wednesday")
        self.assertContains(manager, "Friday")
        self.assertContains(manager, "7:00 PM")
        self.assertContains(manager, "30 min")
        assignment = WeeklyEpisodeAssignment.objects.create(
            show=self.show, week_start=date(2026, 1, 5), episode=self.episode,
            selection_type="premiere",
        )
        from pubtv.operations.services import materialize_assignment
        materialize_assignment(assignment)
        self.assertEqual(
            set(Occurrence.objects.filter(weekly_assignment=assignment).values_list("schedule_role", flat=True)),
            {"premiere", "replay"},
        )

    def test_editing_used_slot_creates_effective_version_and_preserves_history(self):
        assignment = WeeklyEpisodeAssignment.objects.create(
            show=self.show, week_start=date(2026, 1, 5), episode=self.episode,
            selection_type="premiere",
        )
        from pubtv.operations.services import materialize_assignment
        materialize_assignment(assignment)
        original_pk = self.slot.pk
        response = Client().post(
            f"/slots/{original_pk}/edit/",
            {
                "weekday": 1, "start_time": "08:00",
                "duration_seconds_0": 1, "duration_seconds_1": 0,
                "duration_seconds_2": 0, "is_premiere": "on",
                "active_from": "", "active_until": "",
            },
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(RecurrenceSlot.objects.filter(show=self.show).count(), 2)
        original = RecurrenceSlot.objects.get(pk=original_pk)
        replacement = RecurrenceSlot.objects.exclude(pk=original_pk).get()
        self.assertEqual((original.weekday, original.start_time, original.duration_seconds), (0, time(7), 1800))
        self.assertIsNotNone(original.active_until)
        self.assertEqual((replacement.weekday, replacement.start_time, replacement.duration_seconds), (1, time(8), 1800))
        occurrence = Occurrence.objects.get(weekly_assignment=assignment)
        self.assertEqual(occurrence.schedule_role, "premiere")

    def test_date_time_and_duration_fields_hide_storage_formats(self):
        slot = SlotForm(instance=self.slot, station=self.station)
        self.assertEqual(slot.fields["start_time"].widget.input_type, "time")
        self.assertEqual(slot.fields["active_from"].widget.input_type, "date")
        assignment = AssignmentForm(station=self.station)
        self.assertEqual(assignment.fields["premiere_date"].label, "Premiere date")
        self.assertEqual(assignment.fields["premiere_date"].widget.input_type, "date")
        episode = EpisodeForm(station=self.station)
        self.assertEqual(episode.fields["intended_premiere_date"].widget.input_type, "date")
        self.assertEqual(episode.fields["received_at"].widget.input_type, "datetime-local")
        self.assertIsInstance(episode.fields["runtime_seconds"].widget, DurationWidget)

    def test_navigation_groups_workflows_and_quit_is_separate(self):
        response = Client().get("/")
        self.assertContains(response, "Dashboard")
        self.assertContains(response, "Schedule")
        self.assertContains(response, "Catalog")
        self.assertContains(response, "Operations")
        self.assertContains(response, "Settings")
        self.assertNotContains(response, "Quit ChannelDex")

    def test_premiere_cycle_handles_monday_replay_before_wednesday_premiere(self):
        self.slot.delete()
        self.show.slot_duration_seconds = 1800
        self.show.save(update_fields=["slot_duration_seconds"])
        premiere = RecurrenceSlot.objects.create(
            station=self.station, show=self.show, weekday=2, start_time=time(19),
            duration_seconds=1800, is_premiere=True,
        )
        RecurrenceSlot.objects.create(
            station=self.station, show=self.show, weekday=0, start_time=time(9),
            duration_seconds=1800, is_premiere=False,
        )
        previous_episode = Episode.objects.create(
            show=self.show, title="Previous episode", status="previously_scheduled"
        )
        previous = WeeklyEpisodeAssignment.objects.create(
            show=self.show, week_start=date(2025, 12, 29), premiere_date=date(2025, 12, 31),
            episode=previous_episode, selection_type="rerun",
        )
        current = WeeklyEpisodeAssignment.objects.create(
            show=self.show, week_start=date(2026, 1, 5), premiere_date=date(2026, 1, 7),
            episode=self.episode, selection_type="premiere",
        )
        from pubtv.operations.services import materialize_assignment
        materialize_assignment(previous)
        materialize_assignment(current)
        monday_before = Occurrence.objects.get(starts_at__date=date(2026, 1, 5))
        wednesday = Occurrence.objects.get(starts_at__date=date(2026, 1, 7))
        monday_after = Occurrence.objects.get(starts_at__date=date(2026, 1, 12))
        self.assertEqual(monday_before.episode, previous_episode)
        self.assertEqual(wednesday.episode, self.episode)
        self.assertEqual(monday_after.episode, self.episode)
        self.assertEqual(wednesday.recurrence_slot, premiere)

    def test_owner_facing_cycle_form_derives_internal_assignment_type(self):
        self.slot.weekday = 2
        self.slot.is_premiere = True
        self.slot.save()
        response = Client().post(
            "/assignments/new/",
            {
                "show": self.show.pk,
                "premiere_date": "2026-01-07",
                "episode": self.episode.pk,
            },
        )
        self.assertEqual(response.status_code, 302)
        assignment = WeeklyEpisodeAssignment.objects.get()
        self.assertEqual(assignment.week_start, date(2026, 1, 5))
        self.assertEqual(assignment.premiere_date, date(2026, 1, 7))
        self.assertEqual(assignment.selection_type, "premiere")
        self.assertEqual(Occurrence.objects.get().schedule_role, "premiere")

    def test_planning_suggests_queue_and_daily_list_removes_exact_uploads(self):
        self.slot.is_premiere = True
        self.slot.save()
        response = Client().get(f"/assignments/new/?show={self.show.pk}")
        self.assertEqual(response.context["form"].initial["episode"], self.episode.pk)
        self.assertContains(response, "Suggested next")
        today = timezone.localdate()
        starts_at = datetime.combine(today, time(10), ZoneInfo("America/Detroit"))
        occurrence = Occurrence.objects.create(
            station=self.station, item_type="filler", label="Station ID",
            starts_at=starts_at, planned_duration_seconds=60,
        )
        daily = Client().get("/schedule-today/")
        self.assertContains(daily, "Station ID")
        upload = UploadedScheduleRevision.objects.create(device=Device.objects.create(name="Scheduler"))
        UploadedOccurrenceCoverage.objects.create(
            upload=upload, occurrence=occurrence, occurrence_revision=occurrence.revision
        )
        daily = Client().get("/schedule-today/")
        self.assertNotContains(daily, "Station ID")
        self.assertContains(daily, "Nothing left to schedule")

    def test_now_buttons_and_conditional_other_delivery_field_are_rendered(self):
        episode_form = Client().get(f"/episodes/new/?show={self.show.pk}")
        self.assertContains(episode_form, 'data-now-for="id_received_at"')
        delivery_form = Client().get(f"/deliveries/new/?episode={self.episode.pk}")
        self.assertContains(delivery_form, 'data-now-for="id_notified_at"')
        self.assertContains(delivery_form, 'data-now-for="id_received_at"')
        self.assertContains(delivery_form, "data-other-method")


class MediaAssetIdentityMigrationTests(TransactionTestCase):
    """Verify 0008 can upgrade populated databases without UUID collisions/nulls."""

    reset_sequences = True

    def setUp(self):
        self.migrate_from = [("operations", "0007_show_type")]
        self.migrate_to = [("operations", "0008_media_asset_identity")]
        executor = MigrationExecutor(connection)
        executor.migrate(self.migrate_from)
        apps = executor.loader.project_state(self.migrate_from).apps
        station = apps.get_model("operations", "Station").objects.create(name="PUB-TV")
        show = apps.get_model("operations", "Show").objects.create(station=station, title="Show", code="show")
        episode = apps.get_model("operations", "Episode").objects.create(show=show, title="Episode")
        assets = apps.get_model("operations", "MediaAsset").objects
        assets.create(episode=episode, label="source.mov", kind="source")
        assets.create(episode=episode, label="encoded.mp4", kind="encoded")
        # Use a fresh executor so its migration recorder does not retain the prior state.
        self.executor = MigrationExecutor(connection)
        self.executor.migrate(self.migrate_to)

    def tearDown(self):
        MigrationExecutor(connection).migrate(self.migrate_to)
        super().tearDown()

    def test_existing_assets_receive_distinct_ids_and_filename_backfill(self):
        MediaAsset = self.executor.loader.project_state(self.migrate_to).apps.get_model("operations", "MediaAsset")
        assets = list(MediaAsset.objects.order_by("pk"))
        self.assertEqual([asset.file_name for asset in assets], ["source.mov", "encoded.mp4"])
        self.assertEqual(len({asset.asset_id for asset in assets}), 2)
        self.assertTrue(all(asset.asset_id is not None for asset in assets))
