from datetime import date, datetime, timezone

from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import SimpleTestCase, TestCase, TransactionTestCase

from pubtv.operations.forms import PreparationForm, ProducerForm, ShowForm
from pubtv.operations.models import (
    AiringEvidence,
    Device,
    Episode,
    MediaAsset,
    Occurrence,
    OccurrenceProgramming,
    Producer,
    RecurrenceSlot,
    Show,
    Station,
    UploadedScheduleRevision,
    WeeklyEpisodeAssignment,
)


class MetadataFormsTests(SimpleTestCase):
    def test_show_and_producer_forms_expose_new_contact_fields(self):
        self.assertIn("description", ShowForm().fields)
        self.assertIn("primary_producer", ShowForm().fields)
        self.assertNotIn("producer_phone", ShowForm().fields)
        self.assertNotIn("producer_email", ShowForm().fields)
        self.assertNotIn("producer_contact", ShowForm().fields)
        self.assertEqual(set(ProducerForm().fields) & {"phone", "email"}, {"phone", "email"})
        self.assertNotIn("contact", ProducerForm().fields)

    def test_preparation_form_uses_clear_labels_without_aggregate_provenance(self):
        form = PreparationForm()
        self.assertNotIn("provenance", form.fields)
        self.assertNotIn("library_registration", form.fields)
        self.assertNotIn("slot_assignment", form.fields)
        self.assertIn("source_available_at", form.fields)
        self.assertEqual(form.fields["source_available_at"].label, "Source available at")
        self.assertEqual(form.fields["ame_preset"].label, "Adobe Media Encoder preset")


class HumanReadableLabelsTests(TestCase):
    def setUp(self):
        self.station = Station.objects.create(name="PUB-TV")
        self.show = Show.objects.create(station=self.station, title="Community Hour", code="community")
        self.episode = Episode.objects.create(show=self.show, title="Episode 4")
        self.occurrence = Occurrence.objects.create(
            station=self.station,
            show=self.show,
            episode=self.episode,
            item_type="episode",
            label="Episode 4",
            starts_at=datetime(2026, 1, 5, 7, tzinfo=timezone.utc),
            planned_duration_seconds=1800,
        )

    def test_human_readable_model_labels(self):
        device = Device.objects.create(name="UltraNexus")
        asset = MediaAsset.objects.create(episode=self.episode, label="Source", file_name="episode4.mov", kind="source")
        upload = UploadedScheduleRevision.objects.create(device=device, external_reference="January schedule")
        slot = RecurrenceSlot.objects.create(station=self.station, show=self.show, weekday=0, start_time="07:00", duration_seconds=1800)
        assignment = WeeklyEpisodeAssignment.objects.create(show=self.show, week_start=date(2026, 1, 5), episode=self.episode, selection_type="premiere")
        programming = OccurrenceProgramming.objects.create(occurrence=self.occurrence, device=device)
        evidence = AiringEvidence.objects.create(occurrence=self.occurrence, status="reported", source="operator log", aired_at=datetime(2026, 1, 5, 7, tzinfo=timezone.utc))

        for value in (device, asset, upload, slot, assignment, programming, evidence, self.occurrence):
            self.assertNotIn(" object (", str(value))
        self.assertIn("UltraNexus", str(device))
        self.assertIn("episode4.mov", str(asset))
        self.assertIn("January schedule", str(upload))
        self.assertIn("Community Hour", str(slot))
        self.assertIn("Community Hour · Episode 4 · 2026-01-05 2:00 a.m.", str(self.occurrence))

    def test_recurrence_slot_label_accepts_unsaved_string_time(self):
        slot = RecurrenceSlot(
            station=self.station,
            show=self.show,
            weekday=0,
            start_time="07:00",
            duration_seconds=1800,
        )
        self.assertIn("Community Hour · Monday 07:00", str(slot))


class ContactMigrationPreservationTests(TransactionTestCase):
    """The contact split must preserve values from the pre-0009 schema."""

    migrate_from = ("operations", "0008_media_asset_identity")
    migrate_to = ("operations", "0009_show_producer_metadata_and_labels")

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        executor = MigrationExecutor(connection)
        executor.migrate([cls.migrate_from])
        cls.old_apps = executor.loader.project_state([cls.migrate_from]).apps

    @classmethod
    def tearDownClass(cls):
        executor = MigrationExecutor(connection)
        executor.migrate(executor.loader.graph.leaf_nodes())
        super().tearDownClass()

    def test_contacts_are_renamed_without_losing_values(self):
        Station = self.old_apps.get_model("operations", "Station")
        Show = self.old_apps.get_model("operations", "Show")
        Producer = self.old_apps.get_model("operations", "Producer")
        station = Station.objects.create(name="PUB-TV")
        show = Show.objects.create(
            station=station,
            title="Legacy Show",
            code="legacy-show",
            producer_contact="legacy-show-contact",
        )
        producer = Producer.objects.create(
            station=station,
            first_name="Legacy",
            last_name="Producer",
            contact="legacy-producer-contact",
        )

        executor = MigrationExecutor(connection)
        executor.migrate([self.migrate_to])
        new_apps = executor.loader.project_state([self.migrate_to]).apps
        NewShow = new_apps.get_model("operations", "Show")
        NewProducer = new_apps.get_model("operations", "Producer")
        migrated_show = NewShow.objects.get(pk=show.pk)
        migrated_producer = NewProducer.objects.get(pk=producer.pk)

        self.assertEqual(migrated_show.legacy_producer_contact, "legacy-show-contact")
        self.assertEqual(migrated_producer.legacy_contact, "legacy-producer-contact")
        self.assertEqual(migrated_show.description, "")
        self.assertEqual(migrated_show.producer_phone, "")
        self.assertEqual(migrated_show.producer_email, "")
        self.assertEqual(migrated_producer.phone, "")
        self.assertEqual(migrated_producer.email, "")
