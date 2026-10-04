import tempfile
from pathlib import Path

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client, TransactionTestCase, override_settings

from pubtv.operations.database_portability import export_database
from pubtv.operations.models import AuditEvent


class DatabasePortabilityViewsTests(TransactionTestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.client = Client()

    def test_settings_exposes_portability_forms(self):
        response = self.client.get("/settings/")
        self.assertContains(response, "Export database")
        self.assertContains(response, "Validate and stage import")
        self.assertContains(response, "Do not open one database concurrently")

    @override_settings()
    def test_export_download_has_safe_filename_and_audit(self):
        data_dir = Path(self.temp_dir.name)
        with override_settings(DATA_DIR=data_dir):
            response = self.client.post("/settings/database/export/")
        self.assertEqual(response.status_code, 200)
        b"".join(response.streaming_content)
        response.close()
        self.assertIn("channeldex-export.sqlite3", response["Content-Disposition"])
        self.assertTrue((data_dir / ".exports/channeldex-export.sqlite3").exists())
        self.assertTrue(AuditEvent.objects.filter(entity="DatabasePortability", action="export").exists())

    def test_invalid_import_returns_owner_error(self):
        with override_settings(DATA_DIR=Path(self.temp_dir.name)):
            response = self.client.post("/settings/database/import/", {"database": SimpleUploadedFile("bad.sqlite3", b"bad")}, follow=True)
        self.assertContains(response, "not a readable SQLite database")

    def test_valid_import_stages_and_audits(self):
        export = Path(self.temp_dir.name) / "export.sqlite3"
        export_database(self._db_path(), export)
        with override_settings(DATA_DIR=Path(self.temp_dir.name)):
            response = self.client.post("/settings/database/import/", {"database": SimpleUploadedFile("export.sqlite3", export.read_bytes())}, follow=True)
        self.assertEqual(response.status_code, 200)
        self.assertTrue((Path(self.temp_dir.name) / ".staged-channeldex-import.sqlite3").exists(), response.content[:1000])
        self.assertTrue(AuditEvent.objects.filter(entity="DatabasePortability", action="stage").exists())

    def _db_path(self):
        from django.db import connection
        return connection.settings_dict["NAME"]
