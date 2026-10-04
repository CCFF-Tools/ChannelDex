from types import SimpleNamespace
from pathlib import Path
from unittest.mock import patch
from django.test import TestCase
from pubtv.operations.publication_review import (_diff, capture_controller_snapshot,
    publication_review, validate_review_token, cancel_publication)
from pubtv.operations.models import AuditEvent, ControllerSnapshot
from tests.test_ultranexus_publication_delivery import AttendedDeliveryTests, FakeFTPAdapter, FakeFTPConnection
from pubtv.operations.publication_delivery import stage_publication, activate_publication


class PublicationReviewTests(TestCase):
    def setUp(self):
        AttendedDeliveryTests.setUp(self)
        self.selection = self.batch.occurrence_selections.get()
        self.batch.approval_2_snapshot = {}
        self.batch.approval_2_hash = ''
        self.batch.approval_2_status = 'pending'
        self.batch.save()

    def test_first_approval_uses_displayed_token_and_rejects_changed_revision(self):
        review = publication_review(self.batch)
        self.assertFalse(review['blockers'])
        response = self.client.get('/schedule/delivery/')
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'review_token')
        with patch('pubtv.operations.views.preview_schedule', return_value={'blockers': [], 'rows': []}):
            response = self.client.post('/automation/', {'action':'approve_publication', 'batch':self.batch.pk,
                                                       'review_token':review['review_token']})
        self.batch.refresh_from_db()
        self.assertEqual(self.batch.approval_2_status, 'approved')
        self.occurrence.revision += 1
        self.occurrence.save(update_fields=['revision'])
        with self.assertRaisesRegex(ValueError, 'changed'):
            validate_review_token(self.batch, review['review_token'])
        with self.assertRaises(ValueError):
            validate_review_token(self.batch, review['review_token'] + 'tampered')

    def test_diff_preserves_repeated_resource_events_and_real_moves(self):
        def row(occ, start, digest, slot):
            return {'occurrence':occ, 'reference':1, 'slot':slot, 'sha256':digest,
                    'day':1, 'start':start, 'end':start+60, 'title':'Clip', 'filename':'clip.mp4'}
        old = {'resources':[], 'schedules':[row(1,100,'a',0), row(2,300,'b',1)]}
        new = {'resources':[], 'schedules':[row(2,300,'b',0), row(1,600,'c',1), row(3,800,'d',2)]}
        diff = _diff(old,new)
        self.assertEqual(len(diff['unchanged']),1)
        self.assertEqual(len(diff['moves']),1)
        self.assertEqual(len(diff['adds']),1)
        self.assertFalse(diff['deletes'])

    def test_changed_legacy_controller_base_is_preserved_without_blocking_fresh_review(self):
        from pubtv.ultranexus.delivery import RecoverableFTPAdapter
        FakeFTPConnection.files['/internal/schedule/schedule.bin'] = self.candidate_path.read_bytes()
        adapter = RecoverableFTPAdapter(FakeFTPAdapter('test', port=21, username='test', password='test'))
        snapshot = capture_controller_snapshot(self.batch, adapter=adapter)
        self.assertTrue(Path(snapshot.source_reference).is_file())
        self.assertFalse(any('differs' in x for x in publication_review(self.batch)['blockers']))

    def test_legacy_and_missing_snapshots_fail_closed_on_page(self):
        self.batch.controller_snapshot = None
        self.batch.controller_snapshot_hash = ""
        self.batch.save(update_fields=["controller_snapshot", "controller_snapshot_hash"])
        ControllerSnapshot.objects.create(target=self.target, revision=2, snapshot_hash='a'*64, payload={'manual': True})
        self.assertIn('predates', publication_review(self.batch)['blockers'][0])
        self.assertEqual(self.client.get('/schedule/delivery/').status_code, 200)

    def test_staged_cancel_releases_target_without_remote_mutation_and_blocks_reapproval(self):
        from pubtv.operations.automation import approve_snapshot, publication_snapshot
        review = publication_review(self.batch)
        approve_snapshot(self.batch, {**publication_snapshot(self.batch), 'review_hash':review['review_hash']}, approval=2)
        operation = stage_publication(self.batch.pk, expected_hash=self.candidate_hash,
                                     ftp_factory=FakeFTPAdapter, secret_resolver=lambda _: 'test')
        before = dict(FakeFTPConnection.files)
        cancel_publication(self.batch.pk)
        operation.refresh_from_db()
        self.assertEqual(operation.state,'cancelled')
        self.assertEqual(FakeFTPConnection.files, before)
        self.client.post('/automation/', {'action':'approve_publication', 'batch':self.batch.pk,
                                         'review_token':review['review_token']})
        self.batch.refresh_from_db()
        self.assertEqual(self.batch.status,'cancelled')
        self.assertNotEqual(self.batch.approval_2_status,'approved')
        with self.assertRaises(ValueError):
            activate_publication(operation.pk, expected_hash=self.candidate_hash)
        self.assertTrue(AuditEvent.objects.filter(action='cancel').exists())

    def test_cancel_rejects_prepared_and_promoting_operations(self):
        from pubtv.operations.models import ScheduleDeliveryOperation
        artifact = self.batch.artifact_revisions.filter(artifact_type='bin').first()
        op = ScheduleDeliveryOperation.objects.create(publication_batch=self.batch,target=self.target,
                artifact=artifact,approval_hash='a'*64,base_hash=self.base_hash,state='prepared')
        for state in ('prepared','promotion_started','ambiguous','activation_observed'):
            op.state=state
            op.save()
            with self.assertRaises(ValueError):
                cancel_publication(self.batch.pk)
        self.batch.refresh_from_db()
        self.assertNotEqual(self.batch.status,'cancelled')

    def test_prepared_or_cancelled_publication_rejects_selection_edit(self):
        from pubtv.operations.models import ScheduleDeliveryOperation
        artifact = self.batch.artifact_revisions.filter(artifact_type='bin').first()
        op = ScheduleDeliveryOperation.objects.create(publication_batch=self.batch, target=self.target,
                artifact=artifact, approval_hash='a'*64, base_hash=self.base_hash, state='prepared')
        response = self.client.post('/automation/', {'action': 'set_publication_change',
            'selection': self.selection.pk, 'operation': 'delete', 'source_bin_slot': '1',
            'source_bin_record_hash': 'a' * 64})
        self.assertEqual(response.status_code, 302)
        self.selection.refresh_from_db()
        self.assertEqual(self.selection.operation, 'add')

        op.delete()
        self.batch.status = 'cancelled'
        self.batch.save(update_fields=['status'])
        response = self.client.post('/automation/', {'action': 'set_publication_change',
            'selection': self.selection.pk, 'operation': 'delete', 'source_bin_slot': '1',
            'source_bin_record_hash': 'a' * 64})
        self.assertEqual(response.status_code, 302)
        self.selection.refresh_from_db()
        self.assertEqual(self.selection.operation, 'add')

    def test_operation_edit_invalidates_approval_and_displayed_token(self):
        from pubtv.operations.automation import approve_snapshot, publication_snapshot
        review = publication_review(self.batch)
        approve_snapshot(self.batch, {**publication_snapshot(self.batch),
                         'review_hash': review['review_hash']}, approval=2)
        self.client.post('/automation/', {'action': 'set_publication_change',
            'selection': self.selection.pk, 'operation': 'delete', 'source_bin_slot': '1',
            'source_bin_record_hash': 'a' * 64})
        self.selection.refresh_from_db()
        self.batch.refresh_from_db()
        self.assertEqual(self.selection.operation, 'delete')
        self.assertEqual(self.batch.approval_2_status, 'stale')
        with self.assertRaises(ValueError):
            validate_review_token(self.batch, review['review_token'])

    def test_late_review_change_blocks_approval_without_server_error(self):
        review = publication_review(self.batch)
        with patch('pubtv.operations.views.preview_schedule', return_value={'blockers': [], 'rows': []}), \
             patch('pubtv.operations.views.validate_review_token',
                   side_effect=[review, ValueError('Displayed review changed')]):
            response = self.client.post('/automation/', {'action': 'approve_publication',
                'batch': self.batch.pk, 'review_token': review['review_token']})
        self.assertEqual(response.status_code, 302)
        self.batch.refresh_from_db()
        self.assertEqual(self.batch.approval_2_status, 'stale')
        self.assertFalse(self.batch.approval_2_hash)
