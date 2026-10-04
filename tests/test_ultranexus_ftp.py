import tempfile
import unittest
from pathlib import Path

from pubtv.ultranexus.exceptions import TransferError
from pubtv.ultranexus.ftp import StdlibFTPAdapter


class FakeFTP:
    def __init__(self, *, existing=None, fail_store=False, corrupt_readback=False,
                 fail_rename=False, fail_retrieve=False, fail_store_times=0):
        self.files = dict(existing or {})
        self.fail_store = fail_store
        self.fail_store_times = fail_store_times
        self.corrupt_readback = corrupt_readback
        self.fail_rename = fail_rename
        self.fail_retrieve = fail_retrieve
        self.deleted = []
        self.stores = []

    def connect(self, host, port, timeout): self.connection = (host, port, timeout)
    def login(self, username, password): self.login_values = (username, password)
    def set_pasv(self, enabled): self.passive = enabled
    def size(self, path):
        if path not in self.files: raise FileNotFoundError(path)
        return len(self.files[path])
    def storbinary(self, command, source, blocksize):
        path = command.removeprefix("STOR ")
        self.stores.append(path)
        if self.fail_store or self.fail_store_times:
            self.fail_store_times = max(0, self.fail_store_times - 1)
            raise TimeoutError("transient store failure")
        self.files[path] = source.read()
    def retrbinary(self, command, callback, blocksize):
        if self.fail_retrieve: raise OSError("retrieve failed")
        path = command.removeprefix("RETR ")
        data = b"corrupt" if self.corrupt_readback else self.files[path]
        callback(data)
    def rename(self, old, new):
        if self.fail_rename: raise OSError("rename failed")
        self.files[new] = self.files.pop(old)
    def delete(self, path):
        self.deleted.append(path)
        self.files.pop(path, None)
    def quit(self): pass


class FTPBoundaryTests(unittest.TestCase):
    def adapter(self, fake):
        return StdlibFTPAdapter("controller", username="owner", password="reference-secret",
                                ftp_factory=lambda: fake)

    def test_collision_cancels_before_upload(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "clip.mp4"; source.write_bytes(b"new bytes")
            fake = FakeFTP(existing={"/Vol1/mpeg/clip.mp4": b"old bytes"})
            with self.assertRaisesRegex(TransferError, "overwrite is disabled"):
                self.adapter(fake).upload(source, "/Vol1/mpeg/clip.mp4")
            self.assertEqual(fake.stores, [])

    def test_identical_collision_is_reused_after_readback(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "clip.mp4"; source.write_bytes(b"same bytes")
            fake = FakeFTP(existing={"/Vol1/mpeg/clip.mp4": b"same bytes"})
            result = self.adapter(fake).upload(source, "/Vol1/mpeg/clip.mp4")
            self.assertTrue(result.reused)
            self.assertEqual(fake.stores, [])

    def test_store_readback_and_rename_failures_remove_staging_name(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "clip.mp4"; source.write_bytes(b"candidate")
            for fake in (FakeFTP(fail_store=True), FakeFTP(corrupt_readback=True),
                         FakeFTP(fail_rename=True)):
                with self.subTest(fake=type(fake).__name__, flags=fake.__dict__):
                    with self.assertRaises(Exception):
                        self.adapter(fake).upload(source, "/Vol1/mpeg/clip.mp4")
                    self.assertEqual(len(fake.deleted), 1)
                    self.assertTrue(fake.deleted[0].endswith(".part"))
                    self.assertNotIn("/Vol1/mpeg/clip.mp4", fake.files)

    def test_failed_download_removes_partial_local_file(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "schedule.bin"
            fake = FakeFTP(fail_retrieve=True)
            with self.assertRaises(OSError):
                self.adapter(fake).download("/internal/schedule/schedule.bin", str(target))
            self.assertFalse(target.exists())
            self.assertEqual(list(Path(directory).iterdir()), [])

    def test_transient_store_failures_are_bounded_and_retried(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "clip.mp4"; source.write_bytes(b"candidate")
            fake = FakeFTP(fail_store_times=2)
            adapter = StdlibFTPAdapter("controller", ftp_factory=lambda: fake,
                                       retry_backoff=0, retry_attempts=2)
            result = adapter.upload(source, "/Vol1/mpeg/clip.mp4")
            self.assertEqual(result.sha256, __import__("hashlib").sha256(b"candidate").hexdigest())
            self.assertEqual(len(fake.stores), 3)

    def test_owned_partial_is_verified_and_promoted_without_reupload(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "clip.mp4"; source.write_bytes(b"candidate")
            partial = "/Vol1/mpeg/clip.mp4.job-7.part"
            fake = FakeFTP(existing={partial: b"candidate"})
            result = self.adapter(fake).upload(source, "/Vol1/mpeg/clip.mp4", owner_token="job-7")
            self.assertEqual(result.remote_path, "/Vol1/mpeg/clip.mp4")
            self.assertEqual(fake.stores, [])
            self.assertNotIn(partial, fake.files)

    def test_mismatched_owned_partial_is_replaced(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "clip.mp4"; source.write_bytes(b"candidate")
            partial = "/Vol1/mpeg/clip.mp4.job-8.part"
            fake = FakeFTP(existing={partial: b"stale partial"})
            self.adapter(fake).upload(source, "/Vol1/mpeg/clip.mp4", owner_token="job-8")
            self.assertEqual(fake.files["/Vol1/mpeg/clip.mp4"], b"candidate")
            self.assertIn(partial, fake.deleted)

    def test_owned_partial_survives_interrupted_store(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "clip.mp4"; source.write_bytes(b"candidate")
            fake = FakeFTP(fail_store=True)
            with self.assertRaises(TimeoutError):
                self.adapter(fake).upload(source, "/Vol1/mpeg/clip.mp4", owner_token="job-9")
            self.assertEqual(fake.deleted, [])

    def test_transient_store_reconnects_before_retry(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "clip.mp4"; source.write_bytes(b"candidate")
            fake = FakeFTP(fail_store_times=1)
            calls = []
            def factory():
                calls.append(True)
                return fake
            adapter = StdlibFTPAdapter("controller", ftp_factory=factory,
                                       retry_backoff=0, retry_attempts=1)
            adapter.upload(source, "/Vol1/mpeg/clip.mp4")
            self.assertGreaterEqual(len(calls), 2)

    def test_readback_retry_reconnects_to_fresh_session(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "clip.mp4"; source.write_bytes(b"same bytes")
            first = FakeFTP(existing={"/Vol1/mpeg/clip.mp4": b"same bytes"})
            second = FakeFTP(existing={"/Vol1/mpeg/clip.mp4": b"same bytes"})
            first.retrbinary = lambda *args, **kwargs: (_ for _ in ()).throw(TimeoutError("dead session"))
            sessions = iter((first, second))
            adapter = StdlibFTPAdapter("controller", ftp_factory=lambda: next(sessions),
                                       retry_backoff=0, retry_attempts=1)
            result = adapter.upload(source, "/Vol1/mpeg/clip.mp4")
            self.assertTrue(result.reused)

    def test_download_retry_reconnects_to_fresh_session(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "schedule.bin"
            first = FakeFTP(existing={"/internal/schedule/schedule.bin": b"payload"})
            second = FakeFTP(existing={"/internal/schedule/schedule.bin": b"payload"})
            first.retrbinary = lambda *args, **kwargs: (_ for _ in ()).throw(TimeoutError("dead session"))
            sessions = iter((first, second))
            adapter = StdlibFTPAdapter("controller", ftp_factory=lambda: next(sessions),
                                       retry_backoff=0, retry_attempts=1)
            adapter.download("/internal/schedule/schedule.bin", str(target))
            self.assertEqual(target.read_bytes(), b"payload")


if __name__ == "__main__": unittest.main()
