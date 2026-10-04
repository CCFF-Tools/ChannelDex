from dataclasses import dataclass
from ftplib import FTP, error_perm, error_temp
import hashlib
import os
import re
import time
import uuid

from .exceptions import TransferError


@dataclass(frozen=True)
class TransferResult:
    remote_path: str
    sha256: str
    reused: bool = False


class FTPAdapter:
    def upload(self, local_path, remote_path, *, overwrite=False):
        raise NotImplementedError

    def download(self, remote_path, local_path):
        raise NotImplementedError


class StdlibFTPAdapter(FTPAdapter):
    """Conservative FTP transfer with bounded transport retries."""

    def __init__(self, host, *, port=21, username=None, password=None, timeout=30,
                 staging=True, ftp_factory=FTP, retry_attempts=2,
                 retry_backoff=0.25, sleep=time.sleep):
        self.host, self.port, self.username, self.password, self.timeout = host, port, username, password, timeout
        self.staging, self.ftp_factory = staging, ftp_factory
        # Keep operator/configuration mistakes from turning a media job into
        # an effectively unbounded unattended retry loop.
        self.retry_attempts = min(5, max(0, int(retry_attempts)))
        self.retry_backoff = min(60.0, max(0, float(retry_backoff)))
        self._sleep = sleep
        self.progress_callback = None

    def _progress(self, stage):
        if self.progress_callback:
            self.progress_callback(stage)

    def _connect_once(self):
        ftp = None
        try:
            ftp = self.ftp_factory()
            ftp.connect(self.host, self.port, timeout=self.timeout)
            ftp.login(self.username or "anonymous", self.password or "anonymous@")
            ftp.set_pasv(True)
            return ftp
        except Exception:
            for method in ("close", "quit"):
                try:
                    getattr(ftp, method)()
                    break
                except Exception:
                    pass
            raise

    def _connect(self):
        return self._retry(self._connect_once)

    def _retry(self, operation, reconnect=None):
        for attempt in range(self.retry_attempts + 1):
            try:
                return operation()
            except (TimeoutError, ConnectionError, OSError, EOFError, error_temp) as exc:
                if isinstance(exc, (FileNotFoundError, PermissionError)) or attempt >= self.retry_attempts:
                    raise
                if self.retry_backoff:
                    self._sleep(self.retry_backoff * (2 ** attempt))
                if reconnect:
                    reconnect()

    @staticmethod
    def _missing(exc):
        if isinstance(exc, FileNotFoundError):
            return True
        if isinstance(exc, error_perm):
            text = str(exc).lower()
            return text.startswith("550") and any(word in text for word in ("not found", "no such", "missing"))
        return False

    def _size(self, ftp, path, reconnect=None):
        current = ftp
        def read_size():
            return current.size(path)
        def refresh():
            nonlocal current
            if reconnect:
                current = reconnect()
        try:
            return self._retry(read_size, refresh if reconnect else None)
        except Exception as exc:
            if self._missing(exc):
                return None
            raise TransferError(f"unable to inspect remote file {path}: {exc}") from exc

    def _hash(self, ftp, path, reconnect=None):
        current = ftp
        def read_once():
            digest = hashlib.sha256()
            current.retrbinary("RETR " + path, digest.update, blocksize=1024 * 1024)
            return digest.hexdigest()
        def refresh():
            nonlocal current
            if reconnect:
                current = reconnect()
        return self._retry(read_once, refresh if reconnect else None)

    @staticmethod
    def _staging_path(remote_path, owner_token):
        if owner_token is None:
            suffix = uuid.uuid4().hex
        else:
            token = str(owner_token)
            if not token or not re.fullmatch(r"[A-Za-z0-9._-]+", token):
                raise ValueError("owner_token must be a non-empty path-safe token")
            suffix = token
        return remote_path + "." + suffix + ".part"

    def upload(self, local_path, remote_path, *, overwrite=False, reuse_identical=True,
               owner_token=None):
        digest_obj = hashlib.sha256()
        size = 0
        with open(local_path, "rb") as source:
            while block := source.read(1024 * 1024):
                digest_obj.update(block)
                size += len(block)
        digest = digest_obj.hexdigest()
        ftp = self._connect()
        def reconnect():
            nonlocal ftp
            old = ftp
            ftp = self._connect()
            try:
                old.quit()
            except Exception:
                pass
            return ftp
        target = None
        try:
            existing_size = self._size(ftp, remote_path, reconnect)
            if existing_size is not None:
                if not overwrite:
                    if reuse_identical and existing_size == size:
                        self._progress("verifying")
                        if self._hash(ftp, remote_path, reconnect) == digest:
                            return TransferResult(remote_path, digest, reused=True)
                    raise TransferError("remote file exists and overwrite is disabled")

            target = self._staging_path(remote_path, owner_token) if self.staging else remote_path
            if self.staging and owner_token is not None:
                partial_size = self._size(ftp, target, reconnect)
                if partial_size is not None:
                    self._progress("verifying")
                    if partial_size == size and self._hash(ftp, target, reconnect) == digest:
                        try:
                            destination_size = self._size(ftp, remote_path, reconnect)
                            if destination_size is not None:
                                if destination_size == size and self._hash(ftp, remote_path, reconnect) == digest:
                                    self._retry(lambda: ftp.delete(target))
                                    target = None
                                    return TransferResult(remote_path, digest, reused=True)
                                raise TransferError("remote file appeared during promotion")
                            ftp.rename(target, remote_path)
                        except Exception as exc:
                            if self._size(ftp, remote_path, reconnect) == size and self._hash(ftp, remote_path, reconnect) == digest:
                                return TransferResult(remote_path, digest, reused=True)
                            raise TransferError(f"remote promotion failed: {exc}") from exc
                        target = None
                        return TransferResult(remote_path, digest)
                    self._retry(lambda: ftp.delete(target), reconnect)

            self._progress("transferring")
            def store_once():
                with open(local_path, "rb") as source:
                    return ftp.storbinary("STOR " + target, source, blocksize=1024 * 1024)
            self._retry(store_once, reconnect)
            self._progress("verifying")
            if self._hash(ftp, target, reconnect) != digest:
                raise TransferError("remote upload hash verification failed")
            if self.staging:
                try:
                    destination_size = self._size(ftp, remote_path, reconnect)
                    if destination_size is not None:
                        if destination_size == size and self._hash(ftp, remote_path, reconnect) == digest:
                            self._retry(lambda: ftp.delete(target), reconnect)
                            target = None
                            return TransferResult(remote_path, digest, reused=True)
                        raise TransferError("remote file appeared during promotion")
                    ftp.rename(target, remote_path)
                except Exception as exc:
                    try:
                        if self._size(ftp, remote_path, reconnect) == size and self._hash(ftp, remote_path, reconnect) == digest:
                            target = None
                            return TransferResult(remote_path, digest, reused=True)
                    except Exception:
                        pass
                    raise TransferError(f"remote promotion failed: {exc}") from exc
                target = None
        except Exception:
            if target and self.staging and owner_token is None:
                try:
                    ftp.delete(target)
                except Exception:
                    pass
            raise
        finally:
            try:
                ftp.quit()
            except Exception:
                pass
        return TransferResult(remote_path, digest)

    def download(self, remote_path, local_path):
        temp = local_path + "." + uuid.uuid4().hex + ".part"
        digest = hashlib.sha256()
        with open(temp, "wb") as out:
            ftp = self._connect()
            current = ftp
            def reconnect():
                nonlocal ftp, current
                old = current
                current = self._connect()
                ftp = current
                try:
                    old.quit()
                except Exception:
                    pass
                return ftp
            try:
                def retrieve_once():
                    nonlocal digest
                    digest = hashlib.sha256()
                    out.seek(0)
                    out.truncate()
                    def write(block):
                        digest.update(block)
                        out.write(block)
                    return current.retrbinary("RETR " + remote_path, write, blocksize=1024 * 1024)
                self._retry(retrieve_once, reconnect)
            except Exception:
                try:
                    os.unlink(temp)
                except FileNotFoundError:
                    pass
                raise
            finally:
                try:
                    ftp.quit()
                except Exception:
                    pass
        os.replace(temp, local_path)
        return TransferResult(remote_path, digest.hexdigest())
