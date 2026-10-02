"""Fail-closed schedule.bin delivery primitives.

The adapter is injected so these functions are safe to exercise without an
FTP server.  ``FTPAdapter.upload`` already performs unique staging, readback,
and an atomic remote rename; this module adds the publication record and
explicit recovery hooks for adapters that expose them.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import hashlib
import posixpath
import uuid

from .exceptions import TransferError, ValidationError
from .utils import sha256_file


@dataclass(frozen=True)
class DeliveryResult:
    remote_path: str
    sha256: str
    staging_path: str | None = None
    backup_path: str | None = None
    promoted: bool = False
    recovered: bool = False


def _safe_remote_path(path: str) -> str:
    if not isinstance(path, str) or not path.startswith("/") or ".." in path.split("/"):
        raise ValidationError("remote schedule path must be an absolute safe path")
    if not path.endswith(".bin"):
        raise ValidationError("schedule delivery requires a .bin destination")
    return path


def stage_schedule(adapter, local_path, remote_path: str, *, token: str | None = None):
    """Stage a schedule through an adapter exposing ``stage``.

    The returned opaque staging name is never treated as live schedule data.
    """
    remote_path = _safe_remote_path(remote_path)
    source = Path(local_path)
    if not source.is_file():
        raise ValidationError("schedule source does not exist")
    name = token or uuid.uuid4().hex
    staging = f"{remote_path}.{name}.part"
    if hasattr(adapter, "stage"):
        adapter.stage(str(source), staging)
    else:
        RecoverableFTPAdapter(adapter).stage(str(source), staging)
    return staging


class RecoverableFTPAdapter:
    """Add explicit stage, backup, promote, and rollback to ``FTPAdapter``.

    It uses the existing adapter's private connection factory but does not call
    its ``upload`` method, whose overwrite path cannot retain a rollback copy.
    """

    def __init__(self, adapter):
        if not hasattr(adapter, "_connect"):
            raise TransferError("FTP adapter lacks a recoverable connection factory")
        self.adapter = adapter

    def _connect(self):
        return self.adapter._connect()

    def stage(self, local_path, staging_path):
        digest = sha256_file(local_path)
        ftp = self._connect()
        try:
            with open(local_path, "rb") as source:
                ftp.storbinary("STOR " + staging_path, source, blocksize=1024 * 1024)
            remote_hash = hashlib.sha256()
            ftp.retrbinary("RETR " + staging_path, remote_hash.update, blocksize=1024 * 1024)
            if remote_hash.hexdigest() != digest:
                try: ftp.delete(staging_path)
                except Exception: pass
                raise TransferError("staged schedule hash verification failed")
        finally:
            ftp.quit()
        return digest

    def read_hash(self, remote_path):
        ftp = self._connect()
        try:
            digest = hashlib.sha256()
            ftp.retrbinary("RETR " + remote_path, digest.update, blocksize=1024 * 1024)
            return digest.hexdigest()
        finally:
            ftp.quit()

    def is_missing(self, remote_path):
        """Confirm absence from a readable parent listing; errors stay uncertain."""
        ftp = self._connect()
        try:
            parent = posixpath.dirname(remote_path)
            names = ftp.nlst(parent)
            if not isinstance(names, (list, tuple)):
                raise TransferError("remote directory listing is unavailable")
            visible = {
                posixpath.basename(name.rstrip("/")) for name in names
                if posixpath.dirname(name) in ("", parent)
            }
            return posixpath.basename(remote_path) not in visible
        finally:
            ftp.quit()

    def backup(self, remote_path, backup_path):
        ftp = self._connect()
        try:
            # A failed SIZE is ambiguous; the caller must have captured the
            # current remote schedule before this destructive rename.
            ftp.size(remote_path)
            ftp.rename(remote_path, backup_path)
            return True
        finally:
            ftp.quit()

    def promote(self, staging_path, remote_path):
        ftp = self._connect()
        try:
            ftp.rename(staging_path, remote_path)
        finally:
            ftp.quit()

    def rollback(self, staging_path, remote_path, backup_path):
        ftp = self._connect()
        try:
            try: ftp.delete(staging_path)
            except Exception: pass
            try: ftp.delete(remote_path)
            except Exception: pass
            ftp.rename(backup_path, remote_path)
        finally:
            ftp.quit()


def deliver_schedule(adapter, local_path, remote_path: str, *, allow_overwrite: bool = False) -> DeliveryResult:
    """Stage, read back, backup the live schedule, then promote atomically.

    ``allow_overwrite`` is retained for call compatibility but cannot bypass
    backup creation.  A failed promotion leaves recoverable remote artifacts.
    """
    remote_path = _safe_remote_path(remote_path)
    source = Path(local_path)
    if not source.is_file():
        raise ValidationError("schedule source does not exist")
    digest = sha256_file(source)
    worker = adapter if all(hasattr(adapter, x) for x in ("stage", "backup", "promote")) else RecoverableFTPAdapter(adapter)
    token = uuid.uuid4().hex
    staging = f"{remote_path}.{token}.part"
    backup = f"{remote_path}.{token}.bak"
    worker.stage(str(source), staging)
    try:
        had_backup = worker.backup(remote_path, backup)
        if not had_backup:
            raise TransferError("a known-good remote schedule is required")
        worker.promote(staging, remote_path)
    except Exception as exc:
        raise TransferError("schedule promotion failed; rollback artifact retained") from exc
    return DeliveryResult(remote_path, digest, staging, backup if had_backup else None, promoted=True)


def promote_staged_schedule(adapter, staging_path: str, remote_path: str, *, expected_hash: str,
                            backup_path: str | None = None) -> DeliveryResult:
    """Promote a previously reviewed candidate only after checking its bytes."""
    remote_path = _safe_remote_path(remote_path)
    if not staging_path.startswith(remote_path + ".") or not staging_path.endswith(".part"):
        raise ValidationError("staging path is not bound to the qualified schedule")
    worker = adapter if all(hasattr(adapter, x) for x in ("read_hash", "backup", "promote")) else RecoverableFTPAdapter(adapter)
    if worker.read_hash(staging_path) != expected_hash:
        raise TransferError("staged schedule hash changed")
    backup = backup_path or f"{remote_path}.{uuid.uuid4().hex}.bak"
    if not backup.startswith(remote_path + ".") or not backup.endswith(".bak"):
        raise ValidationError("backup path is not bound to the qualified schedule")
    if worker.read_hash(remote_path) == expected_hash:
        raise TransferError("candidate is already the live schedule; reconcile before activation")
    try:
        if not worker.backup(remote_path, backup):
            raise TransferError("known-good remote schedule is missing")
        worker.promote(staging_path, remote_path)
        if worker.read_hash(remote_path) != expected_hash:
            raise TransferError("promoted schedule hash mismatch")
    except Exception as exc:
        raise TransferError("schedule promotion is uncertain; inspect remote backup and live paths") from exc
    return DeliveryResult(remote_path, expected_hash, staging_path, backup, promoted=True)


def recover_schedule(adapter, staging_path: str, remote_path: str, *, rollback: bool = False, backup_path: str | None = None) -> bool:
    """Promote or delete a known staging artifact after operator review."""
    remote_path = _safe_remote_path(remote_path)
    if not staging_path or "/" not in staging_path or not staging_path.endswith(".part"):
        raise ValidationError("invalid staging path")
    worker = adapter if all(hasattr(adapter, x) for x in ("rollback", "promote")) else RecoverableFTPAdapter(adapter)
    if rollback and not backup_path:
        raise ValidationError("backup path is required for rollback")
    action = "rollback" if rollback else "promote"
    method = getattr(worker, action, None)
    if method is None:
        raise TransferError("adapter does not support schedule recovery")
    try:
        if rollback:
            method(staging_path, remote_path, backup_path)
        else:
            method(staging_path, remote_path)
    except Exception as exc:
        raise TransferError(f"schedule {action} failed") from exc
    return True


ScheduleDelivery = deliver_schedule
