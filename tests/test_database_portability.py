import io
import json
import sqlite3
import tempfile
import uuid
import unittest
from unittest.mock import patch
from pathlib import Path

from pubtv.operations.database_portability import (
    PortableDatabaseError,
    apply_staged_import,
    export_database,
    rollback_activation,
    stage_import,
    validate_portable_database,
)


class Upload:
    def __init__(self, data):
        self.data = data

    def chunks(self):
        yield self.data[:7]
        yield self.data[7:]


class DatabasePortabilityTests(unittest.TestCase):
    def test_export_contains_metadata_and_is_valid(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            source, target = root / "source.sqlite3", root / "export.sqlite3"
            with sqlite3.connect(source) as db:
                db.execute("CREATE TABLE example (id INTEGER PRIMARY KEY, value TEXT)")
                db.execute("INSERT INTO example(value) VALUES ('stable')")
            export_database(source, target)
            metadata = validate_portable_database(target)
            self.assertEqual(metadata["application"], "ChannelDex")
            self.assertEqual(sqlite3.connect(target).execute("SELECT value FROM example").fetchone()[0], "stable")

    def test_corrupt_and_incompatible_files_are_rejected(self):
        with tempfile.TemporaryDirectory() as folder:
            bad = Path(folder) / "bad.sqlite3"
            bad.write_bytes(b"not sqlite")
            with self.assertRaises(PortableDatabaseError):
                validate_portable_database(bad)

    def test_required_metadata_and_migration_table_match(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder); source, export = root / "source.sqlite3", root / "export.sqlite3"
            with sqlite3.connect(source) as db:
                db.execute("CREATE TABLE django_migrations (id INTEGER PRIMARY KEY, app TEXT, name TEXT, applied TEXT)")
                db.execute("CREATE TABLE operations_auditevent (id INTEGER PRIMARY KEY, actor TEXT, occurred_at TEXT, action TEXT, entity TEXT, summary TEXT)")
            export_database(source, export)
            with sqlite3.connect(export) as db:
                db.execute("UPDATE channeldex_portable_metadata SET payload = '{}' WHERE id = 1")
            with self.assertRaises(PortableDatabaseError):
                validate_portable_database(export)

    def test_metadata_migration_list_must_match_database_rows(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder); source, export = root / "source.sqlite3", root / "export.sqlite3"
            with sqlite3.connect(source) as db:
                db.execute("CREATE TABLE django_migrations (id INTEGER PRIMARY KEY, app TEXT, name TEXT, applied TEXT)")
                db.execute("INSERT INTO django_migrations(app, name, applied) VALUES ('operations', '0001_initial', 'now')")
            export_database(source, export)
            with sqlite3.connect(export) as db:
                payload = json.loads(db.execute("SELECT payload FROM channeldex_portable_metadata WHERE id = 1").fetchone()[0])
                payload["migrations"] = []
                db.execute("UPDATE channeldex_portable_metadata SET payload = ? WHERE id = 1", (json.dumps(payload),))
            with self.assertRaisesRegex(PortableDatabaseError, "migration metadata does not match"):
                validate_portable_database(export)

    def test_snapshot_identity_must_be_a_canonical_uuid(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder); source, export = root / "source.sqlite3", root / "export.sqlite3"
            with sqlite3.connect(source) as db:
                db.execute("CREATE TABLE example (id INTEGER)")
            export_database(source, export)
            with sqlite3.connect(export) as db:
                payload = json.loads(db.execute("SELECT payload FROM channeldex_portable_metadata WHERE id = 1").fetchone()[0])
                payload["snapshot_id"] = "../outside"
                db.execute("UPDATE channeldex_portable_metadata SET payload = ? WHERE id = 1", (json.dumps(payload),))
            with self.assertRaisesRegex(PortableDatabaseError, "snapshot identity"):
                validate_portable_database(export)

    def test_unknown_migration_is_rejected_against_django_graph(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder); source, export, active = root / "source.sqlite3", root / "export.sqlite3", root / "active.sqlite3"
            for path in (source, active):
                with sqlite3.connect(path) as db:
                    db.execute("CREATE TABLE django_migrations (id INTEGER PRIMARY KEY, app TEXT, name TEXT, applied TEXT)")
                    db.execute("INSERT INTO django_migrations(app, name, applied) VALUES ('operations', '0099_unknown', 'now')")
            export_database(source, export)
            with self.assertRaisesRegex(PortableDatabaseError, "unknown migration"):
                stage_import(Upload(export.read_bytes()), root, active_database=active)

    def test_interrupted_prepared_activation_restores_old_database(self):
        with tempfile.TemporaryDirectory() as folder:
            snapshot_id = "4c168ad3-4791-4e5f-b078-ff835c57881f"
            root = Path(folder); current = root / "pubtv.sqlite3"; recovery = root / "pubtv.sqlite3.recovery-x"; failed = root / f".failed-import-{snapshot_id}.sqlite3"
            current.write_bytes(b"imported"); recovery.write_bytes(b"old"); (root / "pubtv.sqlite3.recovery-x-wal").write_bytes(b"wal")
            (root / "pubtv.sqlite3.recovery-x-shm").write_bytes(b"shm")
            (root / ".channeldex-activation.json").write_text(json.dumps({"state":"prepared", "current":str(current), "recovery":str(recovery), "staged":str(root/".staged-channeldex-import.sqlite3"), "snapshot_id":snapshot_id}))
            self.assertFalse(apply_staged_import(root))
            self.assertEqual(current.read_bytes(), b"old")
            self.assertEqual((root / "pubtv.sqlite3-wal").read_bytes(), b"wal")
            self.assertEqual((root / "pubtv.sqlite3-shm").read_bytes(), b"shm")
            self.assertTrue(failed.exists())
            self.assertFalse((root/".channeldex-activation.json").exists())

    def test_rollback_quarantines_imported_sidecars_when_recovery_has_none(self):
        with tempfile.TemporaryDirectory() as folder:
            snapshot_id = "4c168ad3-4791-4e5f-b078-ff835c57881f"
            root = Path(folder); current = root / "pubtv.sqlite3"; recovery = root / "pubtv.sqlite3.recovery-x"
            current.write_bytes(b"imported"); recovery.write_bytes(b"old")
            (root / "pubtv.sqlite3-wal").write_bytes(b"imported-wal")
            (root / "pubtv.sqlite3-shm").write_bytes(b"imported-shm")
            (root / ".channeldex-activation.json").write_text(json.dumps({"state":"activated", "current":str(current), "recovery":str(recovery), "staged":str(root/".staged-channeldex-import.sqlite3"), "snapshot_id":snapshot_id}))
            self.assertTrue(rollback_activation(root))
            self.assertEqual(current.read_bytes(), b"old")
            self.assertFalse((root / "pubtv.sqlite3-wal").exists())
            self.assertFalse((root / "pubtv.sqlite3-shm").exists())
            self.assertEqual((root / f".failed-import-{snapshot_id}.sqlite3-wal").read_bytes(), b"imported-wal")
            self.assertEqual((root / f".failed-import-{snapshot_id}.sqlite3-shm").read_bytes(), b"imported-shm")

    def test_same_migrations_with_forged_schema_is_rejected(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder); source, export, active = root / "source.sqlite3", root / "export.sqlite3", root / "active.sqlite3"
            with sqlite3.connect(source) as db:
                db.execute("CREATE TABLE django_migrations (id INTEGER PRIMARY KEY, app TEXT, name TEXT, applied TEXT)")
                db.execute("CREATE TABLE operations_auditevent (id INTEGER PRIMARY KEY, actor TEXT, occurred_at TEXT, action TEXT, entity TEXT, summary TEXT)")
            with sqlite3.connect(active) as db:
                db.execute("CREATE TABLE django_migrations (id INTEGER PRIMARY KEY, app TEXT, name TEXT, applied TEXT)")
                db.execute("CREATE TABLE operations_auditevent (id INTEGER PRIMARY KEY, actor TEXT, occurred_at TEXT, action TEXT, entity TEXT, summary TEXT)")
                db.execute("CREATE TABLE different (id INTEGER)")
            export_database(source, export)
            with self.assertRaises(PortableDatabaseError):
                stage_import(Upload(export.read_bytes()), root, active_database=active)

    def test_stage_and_activate_keep_recovery_and_remove_sidecars(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            source, export = root / "source.sqlite3", root / "export.sqlite3"
            with sqlite3.connect(source) as db:
                db.execute("CREATE TABLE example (id INTEGER)")
                db.execute("CREATE TABLE operations_auditevent (id INTEGER PRIMARY KEY, actor TEXT, occurred_at TEXT, action TEXT, entity TEXT, summary TEXT)")
            export_database(source, export)
            current = root / "pubtv.sqlite3"
            current.write_bytes(b"old")
            (root / "pubtv.sqlite3-wal").write_bytes(b"wal")
            (root / "pubtv.sqlite3-shm").write_bytes(b"shm")
            staged, _ = stage_import(Upload(export.read_bytes()), root)
            self.assertTrue(staged.exists())
            self.assertTrue(apply_staged_import(root))
            self.assertTrue(list(root.glob("pubtv.sqlite3.recovery-*")))
            self.assertFalse((root / ".staged-channeldex-import.sqlite3").exists())

    def test_activation_audit_contains_snapshot_and_recovery_once(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder); source, export = root / "source.sqlite3", root / "export.sqlite3"
            with sqlite3.connect(source) as db:
                db.execute("CREATE TABLE operations_auditevent (id INTEGER PRIMARY KEY, actor TEXT, occurred_at TEXT, action TEXT, entity TEXT, summary TEXT)")
            export_database(source, export)
            metadata = validate_portable_database(export)
            (root / "pubtv.sqlite3").write_bytes(b"old")
            stage_import(Upload(export.read_bytes()), root)
            self.assertTrue(apply_staged_import(root))
            with sqlite3.connect(root / "pubtv.sqlite3") as db:
                rows = db.execute("SELECT summary FROM operations_auditevent WHERE action='activate' AND entity='DatabasePortability'").fetchall()
            self.assertEqual(len(rows), 1)
            self.assertIn(metadata["snapshot_id"], rows[0][0])
            self.assertIn("pubtv.sqlite3.recovery-", rows[0][0])

    def test_activation_audit_failure_restores_old_database_and_staged_import(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder); source, export = root / "source.sqlite3", root / "export.sqlite3"
            with sqlite3.connect(source) as db:
                db.execute("CREATE TABLE example (id INTEGER)")
            export_database(source, export)
            current = root / "pubtv.sqlite3"; current.write_bytes(b"old")
            stage_import(Upload(export.read_bytes()), root)
            with self.assertRaises(PortableDatabaseError):
                apply_staged_import(root)
            self.assertEqual(current.read_bytes(), b"old")
            self.assertTrue((root / ".staged-channeldex-import.sqlite3").exists())

    def test_newer_migration_identity_is_rejected(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            source, export, active = root / "source.sqlite3", root / "export.sqlite3", root / "pubtv.sqlite3"
            with sqlite3.connect(source) as db:
                db.execute("CREATE TABLE django_migrations (id INTEGER PRIMARY KEY, app TEXT, name TEXT, applied TEXT)")
                db.execute("INSERT INTO django_migrations(app, name, applied) VALUES ('operations', '0099_future', 'now')")
            with sqlite3.connect(active) as db:
                db.execute("CREATE TABLE django_migrations (id INTEGER PRIMARY KEY, app TEXT, name TEXT, applied TEXT)")
            export_database(source, export)
            with self.assertRaises(PortableDatabaseError):
                stage_import(Upload(export.read_bytes()), root, active_database=active)
            self.assertFalse((root / ".staged-channeldex-import.sqlite3").exists())

    def test_invalid_replacement_keeps_previous_pending_import(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            source, export = root / "source.sqlite3", root / "export.sqlite3"
            with sqlite3.connect(source) as db:
                db.execute("CREATE TABLE example (id INTEGER)")
            export_database(source, export)
            stage_import(Upload(export.read_bytes()), root)
            pending = (root / ".staged-channeldex-import.sqlite3").read_bytes()
            with self.assertRaises(PortableDatabaseError):
                stage_import(Upload(b"invalid replacement"), root)
            self.assertEqual((root / ".staged-channeldex-import.sqlite3").read_bytes(), pending)

    def test_uri_active_database_is_checked_for_newer_migration(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            source, export = root / "source.sqlite3", root / "export.sqlite3"
            with sqlite3.connect(source) as db:
                db.execute("CREATE TABLE django_migrations (id INTEGER PRIMARY KEY, app TEXT, name TEXT, applied TEXT)")
                db.execute("INSERT INTO django_migrations(app, name, applied) VALUES ('operations', '0099_future', 'now')")
            export_database(source, export)
            uri = f"file:portability-active-{uuid.uuid4().hex}?mode=memory&cache=shared"
            with sqlite3.connect(uri, uri=True) as active:
                active.execute("CREATE TABLE django_migrations (id INTEGER PRIMARY KEY, app TEXT, name TEXT, applied TEXT)")
                with self.assertRaises(PortableDatabaseError):
                    stage_import(Upload(export.read_bytes()), root, active_database=uri)

    def test_activation_failure_restores_database_sidecars_and_staged_file(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            source, export = root / "source.sqlite3", root / "export.sqlite3"
            with sqlite3.connect(source) as db:
                db.execute("CREATE TABLE example (id INTEGER)")
            export_database(source, export)
            current = root / "pubtv.sqlite3"
            current.write_bytes(b"old"); (root / "pubtv.sqlite3-wal").write_bytes(b"wal"); (root / "pubtv.sqlite3-shm").write_bytes(b"shm")
            stage_import(Upload(export.read_bytes()), root)
            original_replace = __import__("os").replace
            calls = {"count": 0}
            def fail_third(source_path, destination_path):
                calls["count"] += 1
                if calls["count"] == 3:
                    raise OSError("injected failure")
                return original_replace(source_path, destination_path)
            with patch("pubtv.operations.database_portability.os.replace", side_effect=fail_third), self.assertRaises(PortableDatabaseError):
                apply_staged_import(root)
            self.assertEqual(current.read_bytes(), b"old")
            self.assertEqual((root / "pubtv.sqlite3-wal").read_bytes(), b"wal")
            self.assertEqual((root / "pubtv.sqlite3-shm").read_bytes(), b"shm")
            self.assertTrue((root / ".staged-channeldex-import.sqlite3").exists())
