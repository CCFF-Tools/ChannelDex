from datetime import timedelta

from django.test import TestCase
from django.utils import timezone

from pubtv.operations.audit_scope import station_id_for
from pubtv.operations.episode_schedule import project_episode_schedule
from pubtv.operations.models import (
    AiringEvidence,
    AuditEvent,
    Delivery,
    Device,
    Episode,
    MediaAsset,
    Occurrence,
    OccurrenceRevisionSelection,
    PreparationBatch,
    PreparationBatchItem,
    PreparationJob,
    PublicationJob,
    SchedulePublicationBatch,
    Show,
    Station,
)
from pubtv.operations.services import audit
from pubtv.operations.views import _archive_blockers


class RecordsRedesignTests(TestCase):
    def setUp(self):
        self.station = Station.objects.create()
        self.show = Show.objects.create(station=self.station, title="Public Affairs", code="pa")

    def test_archived_records_are_excluded_from_active_projection(self):
        active = Episode.objects.create(show=self.show, title="Active")
        archived = Episode.objects.create(show=self.show, title="Old", archived_at=timezone.now())
        self.assertIn(active, list(self.show.episodes.filter(archived_at__isnull=True)))
        self.assertNotIn(archived, list(self.show.episodes.filter(archived_at__isnull=True)))

    def test_episode_schedule_projection_keeps_future_play_order(self):
        episode = Episode.objects.create(show=self.show, title="Tonight")
        first = timezone.now() + timedelta(days=1)
        second = first + timedelta(days=7)
        Occurrence.objects.create(station=self.station, show=self.show, episode=episode, item_type="episode", label="Tonight", starts_at=first, planned_duration_seconds=1800)
        Occurrence.objects.create(station=self.station, show=self.show, episode=episode, item_type="episode", label="Tonight", starts_at=second, planned_duration_seconds=1800)
        projection = project_episode_schedule(self.show, timezone.localdate(first), timezone.localdate(second))
        self.assertEqual([item.starts_at for item in projection["plays"]], [first, second])

    def test_audit_scope_follows_evidence_and_direct_batch_events(self):
        episode = Episode.objects.create(show=self.show, title='Linked history')
        occurrence = Occurrence.objects.create(station=self.station, show=self.show, episode=episode,
            starts_at=timezone.now(), planned_duration_seconds=60)
        evidence = AiringEvidence.objects.create(occurrence=occurrence, aired_at=timezone.now(), status='reported', source='Synthetic observation')
        event = audit('create', 'AiringEvidence', evidence, 'Airing report')
        self.assertEqual(event.station_id, self.station.pk)
        response = self.client.get('/history/?tab=audit&q=Linked')
        self.assertContains(response, 'Airing report')
        self.assertContains(response, f'/episodes/{episode.pk}/')
        self.assertEqual(self.client.get('/history/?tab=uploads&q=Linked').status_code, 200)
        device = Device.objects.create(station=self.station, name='Test target')
        batch = PreparationBatch.objects.create(target=device, show=self.show)
        event = AuditEvent.objects.create(action='create', entity='PreparationBatch', entity_id=batch.pk, summary='Batch queued')
        self.assertEqual(event.station_id, self.station.pk)
        from importlib import import_module

        from django.apps import apps
        AuditEvent.objects.filter(pk=event.pk).update(station=None)
        import_module('pubtv.operations.migrations.0034_records_redesign').backfill_audit_stations(apps, None)
        event.refresh_from_db()
        self.assertEqual(event.station_id, self.station.pk)

    def test_delivery_station_scope_requires_one_distinct_episode_station(self):
        other = Station.objects.create(name="ALT-TV")
        local_episode = Episode.objects.create(show=self.show, title="Local")
        local_delivery = Delivery.objects.create(method="email", reference="local")
        local_delivery.episodes.add(local_episode)
        self.assertEqual(station_id_for(local_delivery), self.station.pk)
        foreign_show = Show.objects.create(station=other, title="Other", code="other")
        foreign_episode = Episode.objects.create(show=foreign_show, title="Foreign")
        mixed = Delivery.objects.create(method="email", reference="mixed")
        mixed.episodes.add(local_episode, foreign_episode)
        self.assertIsNone(station_id_for(mixed))
        empty = Delivery.objects.create(method="email", reference="empty")
        self.assertIsNone(station_id_for(empty))

    def test_delivery_audit_backfill_scopes_only_unambiguous_membership(self):
        other = Station.objects.create(name="ALT-TV")
        episode = Episode.objects.create(show=self.show, title="Delivered")
        delivery = Delivery.objects.create(method="email", reference="history")
        delivery.episodes.add(episode)
        event = AuditEvent.objects.create(action="create", entity="Delivery", entity_id=delivery.pk, summary="Received")
        from importlib import import_module

        from django.apps import apps
        import_module("pubtv.operations.migrations.0034_records_redesign").backfill_audit_stations(apps, None)
        event.refresh_from_db()
        self.assertEqual(event.station_id, self.station.pk)
        foreign_episode = Episode.objects.create(show=Show.objects.create(station=other, title="Other", code="other"), title="Foreign")
        delivery.episodes.add(foreign_episode)
        event.station = None
        event.save(update_fields=["station"])
        import_module("pubtv.operations.migrations.0034_records_redesign").backfill_audit_stations(apps, None)
        event.refresh_from_db()
        self.assertIsNone(event.station_id)
    def test_archive_checks_actual_publication_membership_and_not_sibling_jobs(self):
        episode = Episode.objects.create(show=self.show, title='Archive candidate')
        sibling = Episode.objects.create(show=self.show, title='Sibling')
        device = Device.objects.create(station=self.station, name='Test target')
        asset = MediaAsset.objects.create(station=self.station, episode=sibling, kind='source', file_name='sibling.mov')
        prep = PreparationBatch.objects.create(target=device, show=self.show)
        PreparationBatchItem.objects.create(batch=prep, asset=asset, position=1)
        PreparationJob.objects.create(batch=prep, idempotency_key='sibling-test')
        self.assertFalse(_archive_blockers('episode', episode, self.station)[1])
        self.assertTrue(_archive_blockers('episode', sibling, self.station)[1])
        past = Occurrence.objects.create(station=self.station, show=self.show, episode=episode,
            starts_at=timezone.now()-timedelta(days=1), planned_duration_seconds=60)
        publication = SchedulePublicationBatch.objects.create(target=device)
        OccurrenceRevisionSelection.objects.create(publication_batch=publication, occurrence=past, occurrence_revision=past.revision)
        PublicationJob.objects.create(publication_batch=publication, kind='generate_nmg', idempotency_key='publication-test')
        self.assertTrue(_archive_blockers('episode', episode, self.station)[1])
