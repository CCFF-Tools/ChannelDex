from django.test import SimpleTestCase

from pubtv.operations.active_station import workspace_safe_url


class RedesignWorkspaceBoundaryTests(SimpleTestCase):
    def test_switching_channel_drops_private_intake_identity(self):
        self.assertEqual(
            workspace_safe_url('/intake/4e932481-65a9-44fb-a967-6909f1b76253/?episode=88'),
            '/work/',
        )

    def test_record_operations_return_to_collection_in_new_channel(self):
        for path in ('/episodes/88/archive/', '/shows/5/restore/', '/producers/9/edit/'):
            with self.subTest(path=path):
                self.assertEqual(workspace_safe_url(path), '/library/')

    def test_cycle_editor_drops_previous_channel_cycle(self):
        self.assertEqual(workspace_safe_url('/schedule/cycles/42/edit/?episode=88'), '/schedule/')

    def test_library_keeps_search_but_not_foreign_show(self):
        self.assertEqual(
            workspace_safe_url('/library/?q=gardens&view=episodes&show=88'),
            '/library/?q=gardens&view=episodes',
        )

    def test_publication_keeps_date_but_not_target_or_batch(self):
        self.assertEqual(
            workspace_safe_url('/schedule/publish/?date=2026-10-19&target=44&batch=99'),
            '/schedule/publish/?date=2026-10-19',
        )
