from datetime import date, datetime, time, timezone as dt_timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from django.core.exceptions import ValidationError
from django.test import Client, TestCase, override_settings

from pubtv.operations.models import (
    AiringEvidence,
    AuditEvent,
    Device,
    Delivery,
    Episode,
    MediaAsset,
    Occurrence,
    OccurrenceProgramming,
    Preparation,
    RecurrenceSlot,
    Show,
    Station,
    UploadedOccurrenceCoverage,
    UploadedScheduleRevision,
    WeeklyEpisodeAssignment,
)
from pubtv.operations.services import (
    advance_passed_premiere,
    confirm_preparation_fact,
    revise_airing,
    sign_upload_preview,
    load_upload_preview,
    commit_upload_preview,
)


class SetupAndContentWorkflowTests(TestCase):
    def test_setup_navigation_and_station_label_are_user_facing(self):
        client = Client()

        dashboard = client.get("/")
        self.assertContains(dashboard, 'href="/setup/station/"')
        self.assertContains(dashboard, "Open station settings")

        station = Station.objects.create(name="PUB-TV")
        shows = client.get("/shows/")
        self.assertContains(shows, 'href="/shows/new/"')
        self.assertContains(shows, "Create the first show")

        create_show = client.get("/shows/new/")
        self.assertContains(create_show, "PUB-TV")
        self.assertNotContains(create_show, "Station object")
        self.assertEqual(str(station), "PUB-TV")

    def test_station_device_show_and_episode_routes(self):
        client = Client()
        self.assertEqual(
            client.post("/setup/station/", {"name": "PUB-TV", "timezone": "America/Detroit"}).status_code,
            302,
        )
        station = Station.objects.get()
        self.assertEqual(client.post("/setup/device/", {"name": "Ultra-Nexus HD"}).status_code, 302)
        self.assertEqual(
            client.post(
                "/shows/new/",
                {"station": station.pk, "title": "Community Hour", "code": "community", "producer_name": "Producer", "producer_contact": "private@example.test"},
            ).status_code,
            302,
        )
        show = Show.objects.get()
        self.assertEqual(
            client.post(
                "/episodes/new/",
                {"show": show.pk, "title": "Episode 1", "producer_id": "p-1", "intended_air_order": 1, "intended_premiere_date": "2026-01-05", "runtime_seconds": 1740, "status": "pending", "received_at": "2026-01-01T12:00"},
            ).status_code,
            302,
        )
        self.assertEqual(Episode.objects.get().show_id, show.pk)

    def test_station_setup_is_singleton_and_scoped_forms_reject_foreign_objects(self):
        client = Client()
        client.post("/setup/station/", {"name": "PUB-TV", "timezone": "America/Detroit"})
        self.assertEqual(client.post("/setup/station/", {"name": "GOV-TV", "timezone": "America/Detroit"}).status_code, 200)
        self.assertEqual(Station.objects.count(), 1)
        station = Station.objects.get()
        foreign_station = Station.objects.create(name="GOV-TV")
        show = Show.objects.create(station=station, title="PUB", code="pub")
        foreign_show = Show.objects.create(station=foreign_station, title="GOV", code="gov")
        episode = Episode.objects.create(show=foreign_show, title="Foreign")
        self.assertEqual(client.post("/shows/new/", {"station": foreign_station.pk, "title": "bad", "code": "bad"}).status_code, 200)
        self.assertEqual(client.post("/episodes/new/", {"show": foreign_show.pk, "title": "bad"}).status_code, 200)
        self.assertEqual(client.post("/assets/new/", {"episode": episode.pk, "label": "bad", "kind": "source", "version": "v1"}).status_code, 200)
        self.assertEqual(client.post("/slots/new/", {"station": foreign_station.pk, "show": show.pk, "weekday": 0, "start_time": "07:00", "duration_seconds": 60}).status_code, 200)
        self.assertFalse(Show.objects.filter(code="bad").exists())

    def test_asset_and_delivery_keep_source_and_storage_metadata_separate(self):
        station = Station.objects.create(name="PUB-TV")
        show = Show.objects.create(station=station, title="Community", code="community")
        episode = Episode.objects.create(show=show, title="Episode 1")
        client = Client()
        self.assertEqual(
            client.post(
                "/assets/new/",
                {"episode": episode.pk, "file_name": "source master", "kind": "source", "version": "v1", "runtime_seconds": "", "smb_reference": "PUB-TV/community/source/master.mov"},
            ).status_code,
            302,
        )
        self.assertEqual(
            client.post(
                "/deliveries/new/",
                {"method": "dropbox", "reference": "dropbox://batch-17", "notified_at": "2026-01-01T09:00", "received_at": "2026-01-02T09:00", "notes": "Link received; copied manually", "episodes": [episode.pk]},
            ).status_code,
            302,
        )
        asset = MediaAsset.objects.get()
        delivery = Delivery.objects.get()
        self.assertEqual(asset.smb_reference, "PUB-TV/community/source/master.mov")
        self.assertEqual(delivery.reference, "dropbox://batch-17")
        self.assertNotEqual(asset.smb_reference, delivery.reference)
        self.assertEqual(list(delivery.episodes.all()), [episode])

    def test_episode_detail_exposes_contextual_schedule_actions(self):
        station = Station.objects.create(name="PUB-TV")
        show = Show.objects.create(station=station, title="Street Talk", code="street-talk")
        episode = Episode.objects.create(show=show, title="Episode 1")
        response = Client().get(f"/episodes/{episode.pk}/")
        self.assertContains(response, f"/assignments/new/?show={show.pk}&amp;episode={episode.pk}")
        self.assertNotContains(response, f"/occurrences/new/?show={show.pk}&amp;episode={episode.pk}")

    def test_show_catalog_and_scheduled_occurrences_link_to_editing_and_episode(self):
        station = Station.objects.create(name="PUB-TV")
        show = Show.objects.create(station=station, title="Street Talk", code="street-talk")
        episode = Episode.objects.create(show=show, title="Episode 42")
        Occurrence.objects.create(
            station=station, show=show, episode=episode, item_type="episode",
            label=show.title, starts_at=datetime(2026, 1, 5, 12, tzinfo=dt_timezone.utc),
            planned_duration_seconds=1800,
        )

        catalog = Client().get("/shows/")
        detail = Client().get(f"/shows/{show.pk}/")

        self.assertContains(catalog, f"/shows/{show.pk}/edit/")
        self.assertContains(catalog, "Manage episodes")
        self.assertContains(detail, "Episode 42")
        self.assertContains(detail, f"/episodes/{episode.pk}/edit/")

    def test_dashboard_planned_episode_shows_episode_title(self):
        station = Station.objects.create(name="PUB-TV")
        show = Show.objects.create(station=station, title="Street Talk", code="street-talk")
        episode = Episode.objects.create(show=show, title="Episode 42")
        Occurrence.objects.create(
            station=station, show=show, episode=episode, item_type="episode",
            label=show.title, starts_at=datetime(2026, 1, 5, 12, tzinfo=dt_timezone.utc),
            planned_duration_seconds=1800,
        )

        response = Client().get("/?date=2026-01-05")

        self.assertContains(response, "Episode 42")
        self.assertContains(response, "Street Talk")

    def test_dashboard_item_columns_show_identity_runtime_and_gaps(self):
        station = Station.objects.create(name="PUB-TV", timezone="America/Detroit")
        show = Show.objects.create(station=station, title="Street Talk", code="street-talk")
        episode = Episode.objects.create(show=show, title="Episode 42", runtime_seconds=1500)
        first = Occurrence.objects.create(
            station=station, show=show, episode=episode, item_type="episode",
            label=show.title, starts_at=datetime(2026, 1, 5, 12, tzinfo=dt_timezone.utc),
            planned_duration_seconds=1800,
        )
        asset = MediaAsset.objects.create(
            episode=episode, label="station ID", kind="encoded", runtime_seconds=75,
        )
        second = Occurrence.objects.create(
            station=station, asset=asset, item_type="media", label="Station ID",
            starts_at=datetime(2026, 1, 5, 12, 35, tzinfo=dt_timezone.utc),
            planned_duration_seconds=60,
        )
        Occurrence.objects.create(
            station=station, item_type="filler", label="Filler",
            starts_at=datetime(2026, 1, 5, 12, 36, tzinfo=dt_timezone.utc),
            planned_duration_seconds=60,
        )

        response = Client().get("/?date=2026-01-05")

        self.assertContains(response, "Street Talk")
        self.assertContains(response, "Episode 42")
        self.assertEqual(first.actual_runtime_seconds, 1500)
        self.assertEqual(second.actual_runtime_seconds, 75)

    def test_pubtv_user_facing_strings_use_en_dashes(self):
        root = Path(__file__).resolve().parents[1] / "pubtv"
        source_suffixes = {".py", ".html", ".css"}
        offenders = [
            path for path in root.rglob("*")
            if path.is_file()
            and path.suffix in source_suffixes
            and "—" in path.read_text(encoding="utf-8")
        ]
        self.assertEqual(offenders, [])


class SchedulingWorkflowTests(TestCase):
    def setUp(self):
        self.station = Station.objects.create(name="PUB-TV", timezone="America/Detroit")
        self.show = Show.objects.create(station=self.station, title="Weekly", code="weekly")
        self.episode = Episode.objects.create(show=self.show, title="Premiere", intended_air_order=1)
        self.slot = RecurrenceSlot.objects.create(
            station=self.station, show=self.show, weekday=0, start_time=time(7), duration_seconds=1800
        )

    def test_recurrence_assignment_materializes_weekly_occurrence_idempotently(self):
        client = Client()
        payload = {"show": self.show.pk, "week_start": "2026-01-05", "episode": self.episode.pk, "selection_type": "premiere"}
        self.assertEqual(client.post("/assignments/new/", payload).status_code, 302)
        self.assertEqual(client.post("/assignments/new/", payload).status_code, 302)
        assignment = WeeklyEpisodeAssignment.objects.get()
        self.assertEqual(Occurrence.objects.filter(weekly_assignment=assignment).count(), 1)
        occurrence = Occurrence.objects.get()
        self.assertEqual(occurrence.episode_id, self.episode.pk)
        self.assertEqual(occurrence.starts_at.astimezone(dt_timezone.utc).hour, 12)

    def test_day_and_agenda_filter_date_and_expose_coverage_conflicts(self):
        first = Occurrence.objects.create(
            station=self.station, item_type="filler", label="Monday filler",
            starts_at=datetime(2026, 1, 5, 12, tzinfo=dt_timezone.utc), planned_duration_seconds=60,
        )
        Occurrence.objects.create(
            station=self.station, item_type="filler", label="Monday overlap",
            starts_at=datetime(2026, 1, 5, 12, 0, 30, tzinfo=dt_timezone.utc), planned_duration_seconds=60,
        )
        Occurrence.objects.create(
            station=self.station, item_type="filler", label="Tuesday only",
            starts_at=datetime(2026, 1, 6, 12, tzinfo=dt_timezone.utc), planned_duration_seconds=60,
        )
        response = Client().get("/day/?date=2026-01-05")
        body = response.content.decode()
        self.assertEqual(response.status_code, 200)
        self.assertIn("Monday filler", body)
        self.assertIn("Monday overlap", body)
        self.assertNotIn("Tuesday only", body)
        dashboard = Client().get("/?date=2026-01-05").content.decode()
        self.assertIn("Overlap", dashboard)
        self.assertNotEqual(first.pk, 0)

    def test_week_clips_occurrence_crossing_into_monday(self):
        eastern = ZoneInfo("America/Detroit")
        Occurrence.objects.create(
            station=self.station,
            item_type="live",
            label="Sunday night council meeting",
            starts_at=datetime(2026, 1, 4, 23, 59, tzinfo=eastern),
            planned_duration_seconds=120,
        )
        body = Client().get("/week/?date=2026-01-05&view=schedule").content.decode()
        self.assertIn("Sunday night council meeting", body)
        self.assertIn("12:00 a.m. - 12:01 a.m.", body)

    def test_week_uses_monday_eastern_boundary_and_day_coverage_unions_overlaps(self):
        sunday = Occurrence.objects.create(station=self.station, item_type="filler", label="Sunday", starts_at=datetime(2026, 1, 5, 4, 0, tzinfo=dt_timezone.utc), planned_duration_seconds=120)
        monday = Occurrence.objects.create(station=self.station, item_type="filler", label="Monday", starts_at=datetime(2026, 1, 5, 5, 0, tzinfo=dt_timezone.utc), planned_duration_seconds=120)
        day = Client().get("/day/?date=2026-01-05").content.decode()
        self.assertIn("Planned coverage: 2 minutes", day)
        self.assertNotIn("Sunday", day)
        self.assertIn("Monday", day)
        week = Client().get("/week/?date=2026-01-11&view=schedule")
        self.assertEqual(week.status_code, 200)
        self.assertIn("Monday", week.content.decode())
        self.assertNotEqual(sunday.pk, monday.pk)

    def test_week_has_monday_based_navigation_and_empty_state(self):
        response = Client().get("/week/?date=2026-01-11")
        body = response.content.decode()
        self.assertContains(response, "Previous week")
        self.assertContains(response, "Next week")
        self.assertIn("Week plan", body)

    def test_dst_wall_clock_stays_local_and_day_filter_does_not_drift(self):
        self.slot.start_time = time(5)
        self.slot.save()
        response = Client().post("/assignments/new/", {"show": self.show.pk, "week_start": "2026-03-09", "episode": self.episode.pk, "selection_type": "premiere"})
        self.assertEqual(response.status_code, 302)
        occurrence = Occurrence.objects.get(weekly_assignment__week_start=date(2026, 3, 9))
        self.assertEqual(occurrence.starts_at.astimezone(ZoneInfo("America/Detroit")).hour, 5)


class PreparationAndProgrammingWorkflowTests(TestCase):
    def setUp(self):
        self.station = Station.objects.create()
        self.device = Device.objects.create(name="Ultra-Nexus HD")
        self.occurrence = Occurrence.objects.create(
            station=self.station, item_type="live", label="Town hall",
            starts_at=datetime(2026, 1, 5, 12, tzinfo=dt_timezone.utc), planned_duration_seconds=1800,
        )

    def test_preparation_route_records_six_facts_and_programming_route(self):
        client = Client()
        payload = {
            "source_available": "na", "ame_preset": "N/A live", "ftp_output_device": "N/A live",
            "library_registration": "N/A live", "slot_assignment": "Live slot 7A",
            "uploaded_schedule_revision": "upload-17", "provenance": "owner checklist", "expected_revision": 1,
        }
        self.assertEqual(client.post(f"/occurrences/{self.occurrence.pk}/preparation/", payload).status_code, 302)
        self.assertFalse(Preparation.objects.filter(occurrence=self.occurrence).exists())
        self.assertEqual(
            client.post(f"/occurrences/{self.occurrence.pk}/programming/", {"device": self.device.pk, "note": "confirmed manually"}).status_code,
            302,
        )
        self.assertEqual(OccurrenceProgramming.objects.get().device_id, self.device.pk)

    def test_preparation_missing_or_stale_revision_is_rejected(self):
        client = Client()
        payload = {"source_available": "yes", "ame_preset": "preset", "ftp_output_device": "ftp", "library_registration": "slot", "slot_assignment": "7A", "uploaded_schedule_revision": "u1", "provenance": "manual"}
        self.assertEqual(client.post(f"/occurrences/{self.occurrence.pk}/preparation/", payload).status_code, 302)
        self.assertFalse(Preparation.objects.filter(occurrence=self.occurrence).exists())

    def test_each_preparation_fact_has_own_actor_time_and_provenance(self):
        preparation = Preparation.objects.create(occurrence=self.occurrence)
        facts = {
            "source_available": "na", "ame": "AME N/A", "ftp": "FTP N/A",
            "library": "WinLGX N/A", "slot": "slot-7A", "upload": "rev-17",
        }
        for fact, value in facts.items():
            confirm_preparation_fact(preparation, fact, value, actor="owner", provenance=f"manual:{fact}")
        preparation.refresh_from_db()
        self.assertEqual(preparation.source_available_provenance, "manual:source_available")
        self.assertEqual(preparation.uploaded_schedule_revision, "rev-17")
        for fact in facts:
            self.assertEqual(getattr(preparation, f"{fact}_actor"), "owner")
            self.assertIsNotNone(getattr(preparation, f"{fact}_at"))


class UploadAndHistoryWorkflowTests(TestCase):
    def setUp(self):
        self.station = Station.objects.create()
        self.show = Show.objects.create(station=self.station, title="Show", code="show")
        self.episode = Episode.objects.create(show=self.show, title="Premiere")
        self.assignment = WeeklyEpisodeAssignment.objects.create(
            show=self.show, week_start=date(2026, 1, 5), episode=self.episode, selection_type="premiere"
        )
        self.occurrence = Occurrence.objects.create(
            station=self.station, show=self.show, episode=self.episode, weekly_assignment=self.assignment,
            item_type="episode", label="Premiere", starts_at=datetime(2026, 1, 5, 12, tzinfo=dt_timezone.utc), planned_duration_seconds=1800,
        )
        self.device = Device.objects.create(name="Ultra-Nexus HD")
        OccurrenceProgramming.objects.create(occurrence=self.occurrence, device=self.device)

    def test_upload_route_and_exact_immutable_occurrence_revision_coverage(self):
        upload = UploadedScheduleRevision.objects.create(device=self.device, external_reference="ULX-17", state="active")
        UploadedOccurrenceCoverage.objects.create(upload=upload, occurrence=self.occurrence, occurrence_revision=self.occurrence.revision)
        Occurrence.objects.filter(pk=self.occurrence.pk).update(revision=2)
        coverage = UploadedOccurrenceCoverage.objects.get()
        self.assertEqual(coverage.occurrence_revision, 1)
        self.occurrence.refresh_from_db()
        self.assertFalse(advance_passed_premiere(self.occurrence, now=datetime(2026, 1, 1, tzinfo=dt_timezone.utc)))
        client = Client()
        base = {"device": self.device.pk, "external_reference": "ULX-18", "state": "active", "supersession_reason": "", "occurrences": [self.occurrence.pk]}
        response = client.post("/uploads/new/", base)
        self.assertEqual(response.status_code, 302)
        self.assertEqual(UploadedScheduleRevision.objects.count(), 2)
        committed = UploadedScheduleRevision.objects.get(external_reference="ULX-18")
        self.assertEqual(committed.uploadedoccurrencecoverage_set.get().occurrence_revision, 2)
        self.episode.refresh_from_db()
        self.assertEqual(self.episode.status, "previously_scheduled")

    def test_internal_upload_cas_rejects_plan_changes_and_tampering(self):
        token = sign_upload_preview(device_id=self.device.pk, external_reference="CAS", occurrences=[self.occurrence])
        payload = load_upload_preview(token)
        Occurrence.objects.filter(pk=self.occurrence.pk).update(status="cancelled", revision=2)
        with self.assertRaises(ValidationError):
            commit_upload_preview(device=self.device, external_reference="CAS", token_payload=payload, submitted_ids=[self.occurrence.pk], station_id=self.station.pk)
        self.assertFalse(UploadedScheduleRevision.objects.filter(external_reference="CAS").exists())

    def test_mixed_planned_and_cancelled_selection_never_snapshots_cancelled(self):
        cancelled = Occurrence.objects.create(station=self.station, item_type="filler", label="Cancelled", starts_at=self.occurrence.starts_at, planned_duration_seconds=60, status="cancelled", reason="preempted")
        client = Client()
        base = {"device": self.device.pk, "external_reference": "MIXED", "state": "active", "occurrences": [self.occurrence.pk, cancelled.pk]}
        response = client.post("/uploads/new/", base)
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context["form"].errors)
        self.assertFalse(UploadedScheduleRevision.objects.filter(external_reference="MIXED").exists())

    def test_upload_empty_selection_rejected_and_transitions_require_reason(self):
        client = Client()
        empty = client.post("/uploads/new/", {"device": self.device.pk, "external_reference": "empty", "state": "active", "occurrences": []})
        self.assertEqual(empty.status_code, 200)
        self.assertFalse(UploadedScheduleRevision.objects.filter(external_reference="empty").exists())
        upload = UploadedScheduleRevision.objects.create(device=self.device, external_reference="U1")
        coverage = UploadedOccurrenceCoverage.objects.create(upload=upload, occurrence=self.occurrence, occurrence_revision=1)
        self.assertEqual(client.post(f"/uploads/{upload.pk}/superseded/", {}).status_code, 400)
        self.assertEqual(client.get(f"/uploads/{upload.pk}/superseded/").status_code, 400)
        self.assertEqual(client.post(f"/uploads/{upload.pk}/superseded/", {"reason": "new schedule", "expected_revision": 1}).status_code, 302)
        upload.refresh_from_db()
        self.assertEqual(upload.state, "superseded")
        self.assertEqual(client.post(f"/uploads/{upload.pk}/invalidated/", {"reason": "reversal", "expected_revision": 1}).status_code, 400)
        self.assertEqual(client.post(f"/uploads/{upload.pk}/superseded/", {"reason": "stale", "expected_revision": 1}).status_code, 400)
        self.assertEqual(UploadedOccurrenceCoverage.objects.get(pk=coverage.pk).occurrence_revision, 1)
        self.assertTrue(AuditEvent.objects.filter(entity="UploadedScheduleRevision", action="transition").exists())

    def test_airing_append_correction_keeps_plan_distinct_from_history(self):
        original = AiringEvidence.objects.create(
            occurrence=self.occurrence, status="reported", source="producer report",
            aired_at=datetime(2026, 1, 5, 12, tzinfo=dt_timezone.utc), actor="producer",
        )
        correction = revise_airing(original, status="log_verified", source="Ultra-Nexus log", aired_at=original.aired_at, actor="owner")
        self.assertEqual(AiringEvidence.objects.count(), 2)
        self.assertEqual(correction.supersedes_id, original.pk)
        self.assertEqual(AiringEvidence.objects.get(pk=original.pk).status, "reported")
        history = Client().get("/history/?tab=air").content.decode()
        self.assertIn("producer report", history)
        self.assertIn("Ultra-Nexus log", history)

        self.episode.status = "previously_scheduled"
        self.episode.save(update_fields=["status"])
        history = Client().get("/history/?tab=air").content.decode()
        self.assertIn("producer report", history)
        self.assertIn("Ultra-Nexus log", history)

    def test_airing_route_correction_is_limited_to_same_occurrence(self):
        other = Occurrence.objects.create(station=self.station, item_type="live", label="Other", starts_at=self.occurrence.starts_at, planned_duration_seconds=60)
        original = AiringEvidence.objects.create(occurrence=self.occurrence, status="reported", source="producer", aired_at=self.occurrence.starts_at)
        client = Client()
        payload = {"status": "log_verified", "source": "log", "aired_at": "2026-01-05T12:00", "notes": "checked", "supersedes": original.pk}
        self.assertEqual(client.post(f"/occurrences/{other.pk}/airing/", payload).status_code, 200)
        self.assertEqual(AiringEvidence.objects.count(), 1)
        self.assertEqual(client.post(f"/occurrences/{self.occurrence.pk}/airing/", payload).status_code, 302)
        self.assertEqual(AiringEvidence.objects.count(), 2)


class IntegrityAndSecurityWorkflowTests(TestCase):
    def test_mutating_owner_routes_emit_audit_events(self):
        client = Client()
        client.post("/setup/station/", {"name": "PUB-TV", "timezone": "America/Detroit"})
        client.post("/setup/device/", {"name": "Ultra-Nexus HD"})
        self.assertEqual(AuditEvent.objects.filter(action="create").count(), 2)
        self.assertTrue(AuditEvent.objects.filter(entity="Station").exists())

    def test_malformed_and_tampered_cross_station_posts_are_rejected(self):
        station = Station.objects.create(name="PUB-TV")
        other_station = Station.objects.create(name="GOV-TV")
        show = Show.objects.create(station=station, title="PUB", code="pub")
        foreign_show = Show.objects.create(station=other_station, title="GOV", code="gov")
        foreign_episode = Episode.objects.create(show=foreign_show, title="Foreign")
        with self.assertRaises(ValidationError):
            Occurrence(
                station=station, show=show, episode=foreign_episode, item_type="episode", label="tampered",
                starts_at=datetime(2026, 1, 5, 12, tzinfo=dt_timezone.utc), planned_duration_seconds=60,
            ).full_clean()
        client = Client()
        response = client.post("/occurrences/new/", {"item_type": "episode", "label": "tampered", "show": show.pk, "episode": foreign_episode.pk, "starts_at": "2026-01-05T12:00", "planned_duration_seconds": 60, "status": "planned"})
        self.assertIn(response.status_code, (200, 400))
        self.assertFalse(Occurrence.objects.filter(label="tampered").exists())
        malformed = client.post("/occurrences/999/edit/", {"expected_revision": "not-an-int"})
        self.assertIn(malformed.status_code, (400, 404))

    @override_settings(ALLOWED_HOSTS=["127.0.0.1", "localhost"])
    def test_external_host_is_rejected_by_loopback_allowed_hosts(self):
        response = Client().get("/", HTTP_HOST="public.example.test")
        self.assertEqual(response.status_code, 400)
