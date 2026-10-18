from datetime import UTC, datetime
from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import patch

from django.test import TestCase as DatabaseTestCase

from pubtv.operations.models import (
    ActivationEvidence,
    Device,
    Occurrence,
    OccurrenceRevisionSelection,
    SchedulePublicationBatch,
    Station,
)
from pubtv.operations.publication_workspace import (
    occurrence_blockers,
    occurrence_publication_state,
    publication_state,
    publication_view,
)


class PublicationWorkspaceContractTests(TestCase):
    def test_unsupported_live_and_missing_media_are_explicit(self):
        occurrence = SimpleNamespace(item_type="live", asset_id=None,
                                     starts_at=datetime(2026, 10, 17, 3, 30, tzinfo=UTC),
                                     ends_at=datetime(2026, 10, 17, 4, 30, tzinfo=UTC))
        blockers = occurrence_blockers(occurrence)
        self.assertTrue(any("Live" in item for item in blockers))
        self.assertTrue(any("midnight" in item for item in blockers))

    def test_state_distinguishes_activation_from_airing(self):
        batch = SimpleNamespace(status="verified", activated_at=datetime.now(UTC), activation_evidence=None)
        self.assertNotEqual(publication_state(batch), "independently verified")
        self.assertNotEqual(publication_state(batch), "aired")

    @patch("pubtv.operations.views.automation_dashboard")
    def test_publication_view_reuses_guarded_workflow(self, dashboard):
        request = SimpleNamespace(method="GET", GET={}, POST={})
        publication_view(request)
        dashboard.assert_called_once_with(request)


class PublicationFailureProjectionTests(DatabaseTestCase):
    def test_rollback_and_failed_latest_attempt_are_actionable(self):
        station = Station.objects.create(name='PUB-TV')
        device = Device.objects.create(station=station, name='Test controller')
        occurrence = Occurrence.objects.create(station=station, item_type='live', label='Test',
            starts_at=datetime(2030, 1, 2, 12, tzinfo=UTC), planned_duration_seconds=60)
        batch = SchedulePublicationBatch.objects.create(target=device, status='verified', activated_at=datetime.now(UTC))
        OccurrenceRevisionSelection.objects.create(publication_batch=batch, occurrence=occurrence, occurrence_revision=occurrence.revision)
        ActivationEvidence.objects.create(publication_batch=batch, target=device, status='activation_observed')
        self.assertEqual(occurrence_publication_state(occurrence), 'Verified on device')
        ActivationEvidence.objects.create(publication_batch=batch, target=device, status='rolled_back')
        self.assertIn('Rolled back', occurrence_publication_state(occurrence))
        newer = SchedulePublicationBatch.objects.create(target=device, status='failed')
        OccurrenceRevisionSelection.objects.create(publication_batch=newer, occurrence=occurrence, occurrence_revision=occurrence.revision)
        self.assertEqual(occurrence_publication_state(occurrence), 'Needs attention')
