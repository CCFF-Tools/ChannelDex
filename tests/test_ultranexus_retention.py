import hashlib
import tempfile
from pathlib import Path
from unittest.mock import patch

from django.test import TestCase, override_settings
from pubtv.operations.artifact_retention import cleanup, plan_cleanup, set_pinned
from pubtv.operations.models import ArtifactRevision, AuditEvent, Device, SchedulePublicationBatch, UltraNexusTargetSettings


class UltraNexusRetentionTests(TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        override = override_settings(DATA_DIR=self.temp.name)
        override.enable()
        self.addCleanup(override.disable)
        self.root = Path(self.temp.name) / 'ultranexus' / 'schedules'
        self.root.mkdir(parents=True)
        self.device = Device.objects.create(name='retention')

    def artifact(self, number, device=None):
        batch = SchedulePublicationBatch.objects.create(target=device or self.device, status='verified')
        path = self.root / f'{batch.pk}-{number}.bin'
        path.write_bytes(str(number).encode())
        return ArtifactRevision.objects.create(publication_batch=batch, artifact_type='bin',
            file_reference=str(path), content_hash=hashlib.sha256(path.read_bytes()).hexdigest(), validation={'status':'passed'})

    def test_twenty_sets_per_target_and_pin_does_not_consume_window(self):
        first = [self.artifact(i) for i in range(22)]
        other = Device.objects.create(name='other')
        second = [self.artifact(i, other) for i in range(20)]
        set_pinned(first[0].publication_batch_id, pinned=True)
        report = cleanup()
        self.assertTrue(report['dry_run'])
        self.assertEqual([r['path'] for r in report['actions']], [first[1].file_reference])
        self.assertTrue(Path(first[1].file_reference).exists())
        result = cleanup(execute=True)
        self.assertEqual(len(result['completed']), 1)
        self.assertTrue(Path(first[0].file_reference).exists())
        self.assertTrue(all(Path(a.file_reference).exists() for a in second))
        self.assertEqual(ArtifactRevision.objects.count(), 42)
        self.assertEqual(AuditEvent.objects.filter(action='retention_delete').count(), 1)
        set_pinned(first[0].publication_batch_id, pinned=False)
        self.assertIn(first[0].file_reference, [r['path'] for r in plan_cleanup()['actions']])

    def test_all_historical_base_and_cross_batch_snapshot_references_survive(self):
        artifacts = [self.artifact(i) for i in range(22)]
        UltraNexusTargetSettings.objects.create(target=self.device, is_current=False, version=1,
                                               base_bin_path=artifacts[0].file_reference)
        another = SchedulePublicationBatch.objects.create(target=self.device,
            approval_2_snapshot={'artifacts':[{'content_hash': artifacts[1].content_hash}]})
        self.assertEqual(plan_cleanup()['actions'], [])
        self.assertIsNotNone(another.pk)

    def test_symlink_and_changed_bytes_are_never_removed(self):
        artifacts = [self.artifact(i) for i in range(22)]
        first = Path(artifacts[0].file_reference)
        external = Path(self.temp.name) / 'private.bin'
        external.write_bytes(b'0')
        first.unlink()
        first.symlink_to(external)
        Path(artifacts[1].file_reference).write_bytes(b'changed')
        self.assertFalse(cleanup(execute=True)['completed'])
        self.assertTrue(first.is_symlink())
        self.assertEqual(external.read_bytes(), b'0')

    def test_new_pin_between_preview_and_execution_is_rechecked(self):
        artifacts = [self.artifact(i) for i in range(21)]
        from pubtv.operations import artifact_retention as module
        original = module._audit
        def audit(action, *args, **kwargs):
            event = original(action, *args, **kwargs)
            if action == 'retention_delete_requested':
                set_pinned(artifacts[0].publication_batch_id, pinned=True)
            return event
        with patch.object(module, '_audit', side_effect=audit):
            result = cleanup(execute=True)
        self.assertFalse(result['completed'])
        self.assertEqual(len(result['failed']), 1)
        self.assertTrue(Path(artifacts[0].file_reference).exists())
        self.assertFalse(AuditEvent.objects.filter(action='retention_delete').exists())
