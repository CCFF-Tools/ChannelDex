"""Safe, local-only SQLite portability for the owner-facing Settings page.

Portable files contain the database only.  Secrets, media, and other files in
the application data directory are deliberately never included.
"""
from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path

PORTABLE_APP = "ChannelDex"
PORTABLE_FORMAT = 1
MAX_PORTABLE_BYTES = 512 * 1024 * 1024
METADATA_TABLE = "channeldex_portable_metadata"
ACTIVATION_MANIFEST = ".channeldex-activation.json"

def _fsync_data_dir(data_dir: Path) -> None:
    fd = os.open(str(data_dir), os.O_RDONLY)
    try: os.fsync(fd)
    finally: os.close(fd)


class PortableDatabaseError(ValueError):
    """An owner-facing validation or activation error."""


def _readonly_connection(path: os.PathLike[str] | str) -> sqlite3.Connection:
    value = str(path)
    if value.startswith("file:") and "?" in value:
        # Django's shared in-memory test databases already carry URI options.
        return sqlite3.connect(value, uri=True)
    return sqlite3.connect(f"file:{Path(value).resolve()}?mode=ro", uri=True)


def _is_sqlite_uri(value: os.PathLike[str] | str) -> bool:
    return str(value).startswith("file:") and "?" in str(value)


def _source_connection(path: os.PathLike[str] | str) -> sqlite3.Connection:
    return sqlite3.connect(str(path), uri=True) if _is_sqlite_uri(path) else sqlite3.connect(Path(path))


def _schema_fingerprint(connection: sqlite3.Connection) -> str:
    rows = connection.execute(
        "SELECT type, name, sql FROM sqlite_master WHERE sql IS NOT NULL "
        "AND name != ? ORDER BY type, name", (METADATA_TABLE,)
    ).fetchall()
    return hashlib.sha256("\n".join("|".join(str(x) for x in row) for row in rows).encode()).hexdigest()


def _metadata(connection: sqlite3.Connection) -> dict:
    try:
        row = connection.execute(
            f"SELECT payload FROM {METADATA_TABLE} WHERE id = 1"
        ).fetchone()
    except sqlite3.DatabaseError as exc:
        raise PortableDatabaseError("This file is not a ChannelDex portable database.") from exc
    if not row:
        raise PortableDatabaseError("The ChannelDex portability metadata is missing.")
    try:
        data = json.loads(row[0])
    except (TypeError, ValueError) as exc:
        raise PortableDatabaseError("The portability metadata is unreadable.") from exc
    if data.get("application") != PORTABLE_APP or data.get("format") != PORTABLE_FORMAT:
        raise PortableDatabaseError("This file was not exported by a compatible ChannelDex version.")
    if not isinstance(data.get("exported_at"), str) or not data.get("schema_fingerprint"):
        raise PortableDatabaseError("The portability metadata is incomplete.")
    migrations = data.get("migrations")
    if not isinstance(migrations, list):
        raise PortableDatabaseError("The portability migration metadata is invalid.")
    if any(not isinstance(item, list) or len(item) != 2 or any(not isinstance(value, str) or not value for value in item) for item in migrations):
        raise PortableDatabaseError("The portability migration metadata is invalid.")
    if len({tuple(item) for item in migrations}) != len(migrations):
        raise PortableDatabaseError("The portability migration metadata is invalid.")
    snapshot_id = data.get("snapshot_id")
    try:
        canonical_snapshot_id = str(uuid.UUID(snapshot_id)) if isinstance(snapshot_id, str) else ""
    except (ValueError, AttributeError):
        canonical_snapshot_id = ""
    if not canonical_snapshot_id or canonical_snapshot_id != snapshot_id:
        raise PortableDatabaseError("The portability snapshot identity is missing.")
    return data


def _migration_identities(connection: sqlite3.Connection) -> set[tuple[str, str]]:
    try:
        rows = connection.execute("SELECT app, name FROM django_migrations").fetchall()
    except sqlite3.DatabaseError:
        return set()
    return {(str(app), str(name)) for app, name in rows}


def validate_portable_database(path: os.PathLike[str] | str, *, max_bytes: int = MAX_PORTABLE_BYTES) -> dict:
    """Validate size, SQLite integrity, and ChannelDex schema metadata."""
    path = Path(path)
    if not path.is_file() or path.stat().st_size > max_bytes:
        raise PortableDatabaseError("Choose a local ChannelDex export smaller than 512 MB.")
    try:
        with _readonly_connection(path) as db:
            quick = db.execute("PRAGMA quick_check").fetchone()[0]
            if quick != "ok":
                raise PortableDatabaseError("The selected database failed SQLite's integrity check.")
            metadata = _metadata(db)
            actual_migrations = sorted([list(item) for item in _migration_identities(db)])
            if metadata.get("migrations") != actual_migrations:
                raise PortableDatabaseError("The portability migration metadata does not match the database.")
            expected = metadata.get("schema_fingerprint")
            if expected and expected != _schema_fingerprint(db):
                raise PortableDatabaseError("The selected database schema metadata does not match its contents.")
            return metadata
    except PortableDatabaseError:
        raise
    except (sqlite3.DatabaseError, OSError) as exc:
        raise PortableDatabaseError("The selected file is not a readable SQLite database.") from exc


def export_database(source: os.PathLike[str] | str, destination: os.PathLike[str] | str) -> Path:
    """Create a consistent self-contained snapshot using SQLite online backup."""
    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    with _source_connection(source) as src:
        src.execute("PRAGMA busy_timeout=5000")
        with sqlite3.connect(destination) as dst:
            src.backup(dst)
            dst.execute(f"CREATE TABLE IF NOT EXISTS {METADATA_TABLE} (id INTEGER PRIMARY KEY, payload TEXT NOT NULL)")
            payload = {
                "application": PORTABLE_APP,
                "format": PORTABLE_FORMAT,
                "exported_at": datetime.now(timezone.utc).isoformat(),
                "sqlite_user_version": src.execute("PRAGMA user_version").fetchone()[0],
                "schema_fingerprint": _schema_fingerprint(dst),
                "migrations": sorted([list(item) for item in _migration_identities(src)]),
                "snapshot_id": str(uuid.uuid4()),
            }
            dst.execute(f"INSERT OR REPLACE INTO {METADATA_TABLE} (id, payload) VALUES (1, ?)", (json.dumps(payload, sort_keys=True),))
            dst.commit()
    validate_portable_database(destination)
    return destination


def stage_import(upload, data_dir: os.PathLike[str] | str, *, active_database: os.PathLike[str] | str | None = None) -> tuple[Path, dict]:
    """Stream an uploaded file into the private data directory and validate it."""
    data_dir = Path(data_dir)
    staged = data_dir / ".staged-channeldex-import.sqlite3"
    temporary = data_dir / f".staged-channeldex-import.{uuid.uuid4().hex}.tmp"
    written = 0
    try:
        data_dir.mkdir(parents=True, exist_ok=True)
        with temporary.open("wb") as target:
            for chunk in upload.chunks():
                written += len(chunk)
                if written > MAX_PORTABLE_BYTES:
                    raise PortableDatabaseError("The selected export is larger than the 512 MB limit.")
                target.write(chunk)
        temporary.chmod(0o600)
        metadata = validate_portable_database(temporary)
        active_is_uri = bool(active_database and _is_sqlite_uri(active_database))
        if active_database and (active_is_uri or Path(active_database).exists()):
            with _readonly_connection(active_database) as active:
                active_migrations = _migration_identities(active)
            imported_migrations = {tuple(item) for item in metadata.get("migrations", []) if isinstance(item, list) and len(item) == 2}
            with _readonly_connection(active_database) as active:
                active_fingerprint = _schema_fingerprint(active)
            if imported_migrations != active_migrations or metadata.get("schema_fingerprint") != active_fingerprint:
                raise PortableDatabaseError("This export is for a different or incompatible ChannelDex version/schema.")
            try:
                from django.db.migrations.loader import MigrationLoader
                from django.db import connections
                known = set(MigrationLoader(connections["default"], ignore_no_migrations=True).disk_migrations)
                if imported_migrations - known:
                    raise PortableDatabaseError("This export contains unknown migration identities.")
            except PortableDatabaseError:
                raise
            except Exception as exc:
                raise PortableDatabaseError("The application's migration graph could not validate this export.") from exc
        os.replace(temporary, staged)
        return staged, metadata
    except PortableDatabaseError:
        temporary.unlink(missing_ok=True)
        raise
    except (OSError, sqlite3.DatabaseError) as exc:
        temporary.unlink(missing_ok=True)
        raise PortableDatabaseError("The selected export could not be copied or read locally.") from exc


def apply_staged_import(data_dir: os.PathLike[str] | str, *, database_name: str = "pubtv.sqlite3") -> bool:
    """Activate a validated staged import before Django setup; never rebind live DB."""
    data_dir = Path(data_dir)
    manifest = data_dir / ACTIVATION_MANIFEST
    if manifest.exists():
        try:
            previous = json.loads(manifest.read_text(encoding="utf-8"))
            if previous.get("state") == "activated":
                return True
            if previous.get("state") == "prepared":
                rollback_activation(data_dir)
        except (OSError, ValueError, KeyError) as exc:
            raise PortableDatabaseError("The previous database activation needs recovery before launch.") from exc
    staged = data_dir / ".staged-channeldex-import.sqlite3"
    if not staged.exists():
        return False
    metadata = validate_portable_database(staged)
    current = data_dir / database_name
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    recovery = data_dir / f"{database_name}.recovery-{stamp}-{uuid.uuid4().hex[:8]}"
    moved: list[tuple[Path, Path]] = []
    def persist(state):
        payload = {"state": state, "staged": str(staged), "current": str(current), "recovery": str(recovery), "snapshot_id": metadata["snapshot_id"]}
        temporary_manifest = manifest.with_suffix(".tmp")
        temporary_manifest.write_text(json.dumps(payload, sort_keys=True), encoding="utf-8")
        with temporary_manifest.open("rb") as handle:
            os.fsync(handle.fileno())
        os.replace(temporary_manifest, manifest)
        _fsync_data_dir(data_dir)
    try:
        persist("prepared")
        with staged.open("rb") as handle:
            os.fsync(handle.fileno())
        if current.exists():
            os.replace(current, recovery); moved.append((current, recovery))
        for suffix in ("-wal", "-shm"):
            sidecar = data_dir / f"{database_name}{suffix}"
            if sidecar.exists():
                sidecar_recovery = data_dir / f"{recovery.name}{suffix}"
                os.replace(sidecar, sidecar_recovery); moved.append((sidecar, sidecar_recovery))
        os.replace(staged, current); moved.append((staged, current))
        # Keep an activation provenance record without requiring Django to be
        # initialized (this function intentionally runs before django.setup).
        summary = f"Snapshot {metadata['snapshot_id']} activated; recovery copy {recovery}"
        with sqlite3.connect(current) as db:
            if not db.execute("SELECT 1 FROM operations_auditevent WHERE action = 'activate' AND entity = 'DatabasePortability' AND summary = ? LIMIT 1", (summary,)).fetchone():
                db.execute("INSERT INTO operations_auditevent (actor, occurred_at, action, entity, summary) VALUES (?, ?, ?, ?, ?)", ("owner", datetime.now(timezone.utc).isoformat(), "activate", "DatabasePortability", summary))
            db.commit()
        persist("activated")
        return True
    except Exception:
        for source, destination in reversed(moved):
            if destination.exists():
                os.replace(destination, source)
        manifest.unlink(missing_ok=True)
        _fsync_data_dir(data_dir)
        raise PortableDatabaseError("Import could not be activated; the staged import was retained for recovery.")


def rollback_activation(data_dir: os.PathLike[str] | str) -> bool:
    """Restore the recovery database recorded by the last activation."""
    data_dir = Path(data_dir)
    manifest = data_dir / ACTIVATION_MANIFEST
    if not manifest.exists():
        return False
    payload = json.loads(manifest.read_text(encoding="utf-8"))
    current, recovery = Path(payload["current"]), Path(payload["recovery"])
    try:
        snapshot_id = str(uuid.UUID(payload["snapshot_id"]))
    except (ValueError, TypeError, KeyError, AttributeError) as exc:
        raise PortableDatabaseError("The activation recovery manifest has an invalid snapshot identity.") from exc
    root = data_dir.resolve()
    staged = Path(payload.get("staged", ""))
    expected_current = data_dir / "pubtv.sqlite3"
    if (
        current.parent.resolve() != root
        or recovery.parent.resolve() != root
        or staged.parent.resolve() != root
        or current != expected_current
        or not recovery.name.startswith(f"{expected_current.name}.recovery-")
        or staged.name != ".staged-channeldex-import.sqlite3"
    ):
        raise PortableDatabaseError("The activation recovery manifest points outside the application data directory.")
    if recovery.exists():
        failed = data_dir / f".failed-import-{snapshot_id}.sqlite3"
        if current.exists():
            os.replace(current, failed)
        os.replace(recovery, current)
        for suffix in ("-wal", "-shm"):
            recovery_sidecar = Path(str(recovery) + suffix)
            current_sidecar = Path(str(current) + suffix)
            # Quarantine every sidecar belonging to the failed imported DB,
            # even when the restored DB did not have a matching sidecar.
            if current_sidecar.exists():
                os.replace(current_sidecar, Path(str(failed) + suffix))
            if recovery_sidecar.exists():
                os.replace(recovery_sidecar, current_sidecar)
        manifest.unlink(missing_ok=True)
        _fsync_data_dir(data_dir)
        return True
    return False


def finalize_activation(data_dir: os.PathLike[str] | str) -> None:
    root = Path(data_dir)
    (root / ACTIVATION_MANIFEST).unlink(missing_ok=True)
    _fsync_data_dir(root)
