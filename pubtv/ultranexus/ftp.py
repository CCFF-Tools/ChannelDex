from dataclasses import dataclass
from ftplib import FTP
import hashlib, os, tempfile, uuid
from .exceptions import TransferError

@dataclass(frozen=True)
class TransferResult:
    remote_path: str
    sha256: str
    reused: bool = False

class FTPAdapter:
    def upload(self, local_path, remote_path, *, overwrite=False): raise NotImplementedError
    def download(self, remote_path, local_path): raise NotImplementedError

class StdlibFTPAdapter(FTPAdapter):
    def __init__(self, host, *, port=21, username=None, password=None, timeout=30, staging=True, ftp_factory=FTP):
        self.host, self.port, self.username, self.password, self.timeout = host, port, username, password, timeout
        self.staging = staging; self.ftp_factory = ftp_factory
    def _connect(self):
        ftp = self.ftp_factory(); ftp.connect(self.host, self.port, timeout=self.timeout); ftp.login(self.username or "anonymous", self.password or "anonymous@"); ftp.set_pasv(True); return ftp
    def upload(self, local_path, remote_path, *, overwrite=False, reuse_identical=True):
        digest_obj = hashlib.sha256(); size = 0
        with open(local_path, "rb") as source:
            while block := source.read(1024 * 1024): digest_obj.update(block); size += len(block)
        digest = digest_obj.hexdigest(); ftp = self._connect(); target = None
        try:
            try: existing_size = ftp.size(remote_path)
            except Exception: pass
            else:
                if not overwrite:
                    if reuse_identical and existing_size == size:
                        remote_hash = hashlib.sha256()
                        ftp.retrbinary("RETR " + remote_path, remote_hash.update, blocksize=1024 * 1024)
                        if remote_hash.hexdigest() == digest:
                            return TransferResult(remote_path, digest, reused=True)
                    raise TransferError("remote file exists and overwrite is disabled")
            target = remote_path + "." + uuid.uuid4().hex + ".part" if self.staging else remote_path
            with open(local_path, "rb") as source: ftp.storbinary("STOR " + target, source, blocksize=1024 * 1024)
            # Read back the exact remote bytes before exposing the final name.
            remote_hash = hashlib.sha256()
            ftp.retrbinary("RETR " + target, remote_hash.update, blocksize=1024 * 1024)
            if remote_hash.hexdigest() != digest:
                try: ftp.delete(target)
                except Exception: pass
                raise TransferError("remote upload hash verification failed")
            if self.staging: ftp.rename(target, remote_path)
            target = None
        except Exception:
            if target:
                try: ftp.delete(target)
                except Exception: pass
            raise
        finally: ftp.quit()
        return TransferResult(remote_path, digest)
    def download(self, remote_path, local_path):
        temp = local_path + "." + uuid.uuid4().hex + ".part"
        digest = hashlib.sha256()
        with open(temp, "wb") as out:
            def write(block): digest.update(block); out.write(block)
            ftp = self._connect()
            try: ftp.retrbinary("RETR " + remote_path, write, blocksize=1024 * 1024)
            except Exception:
                try: os.unlink(temp)
                except FileNotFoundError: pass
                raise
            finally: ftp.quit()
        os.replace(temp, local_path)
        return TransferResult(remote_path, digest.hexdigest())
