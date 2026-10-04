import os
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch


class LauncherTests(unittest.TestCase):
    def test_default_data_dir(self):
        from pubtv.config import launcher
        support = Path.home() / "Library" / "Application Support"
        expected = support / "PUB-TV" if (support / "ChannelDex").exists() is False and (support / "PUB-TV" / "pubtv.sqlite3").exists() else support / "ChannelDex"
        self.assertEqual(launcher.default_data_dir(), expected)

    def test_existing_legacy_data_dir_is_preserved(self):
        from pubtv.config import launcher
        with tempfile.TemporaryDirectory() as temp_dir:
            support = Path(temp_dir) / "Library" / "Application Support"
            support.mkdir(parents=True)
            legacy = support / "PUB-TV"
            legacy.mkdir()
            (legacy / "pubtv.sqlite3").touch()
            with patch("pathlib.Path.home", return_value=Path(temp_dir)):
                self.assertEqual(launcher.default_data_dir(), legacy)

    def test_data_dir_override_creates_protected_secret(self):
        from pubtv.config import launcher

        with tempfile.TemporaryDirectory() as temp_dir:
            with patch.dict(os.environ, {"PUBTV_DATA_DIR": temp_dir}, clear=False):
                path = launcher.prepare_data_dir()
            self.assertEqual(path, Path(temp_dir))
            self.assertEqual(path.stat().st_mode & 0o777, 0o700)
            self.assertEqual((path / "secret.key").stat().st_mode & 0o777, 0o600)

    def test_wait_until_ready_accepts_test_port(self):
        from pubtv.config import launcher
        with patch("socket.create_connection") as connect:
            self.assertTrue(launcher.wait_until_ready("127.0.0.1", 43123, timeout=0.2))
            connect.assert_called_once_with(("127.0.0.1", 43123), timeout=0.25)

    def test_main_uses_loopback_port_and_browser_helper(self):
        from pubtv.config import launcher
        app = object()
        with tempfile.TemporaryDirectory() as temp_dir, \
                patch.dict(os.environ, {"PUBTV_DATA_DIR": temp_dir, "PUBTV_PORT": "43210"}, clear=False), \
                patch.object(launcher, "_load_application", return_value=app), \
                patch.object(launcher, "_open_browser_when_ready") as browser, \
                patch.object(launcher, "supervise_worker") as supervisor:
            launcher.main()
        browser.assert_called_once_with("127.0.0.1", 43210)
        supervisor.assert_called_once_with(app, "127.0.0.1", 43210)

    def test_main_sets_private_umask(self):
        from pubtv.config import launcher
        with tempfile.TemporaryDirectory() as temp_dir, \
                patch.dict(os.environ, {"PUBTV_DATA_DIR": temp_dir}, clear=False), \
                patch.object(launcher.os, "umask") as umask, \
                patch.object(launcher, "_load_application", return_value=object()), \
                patch.object(launcher, "_open_browser_when_ready"), \
                patch.object(launcher, "supervise_worker"):
            launcher.main()
        umask.assert_called_once_with(0o077)

    def test_main_continues_when_staged_import_is_recoverably_invalid(self):
        from pubtv.config import launcher
        with tempfile.TemporaryDirectory() as temp_dir, \
                patch.dict(os.environ, {"PUBTV_DATA_DIR": temp_dir}, clear=False), \
                patch.object(launcher, "apply_staged_import", side_effect=launcher.PortableDatabaseError("bad staged file")), \
                patch.object(launcher, "_load_application", return_value=object()), \
                patch.object(launcher, "_open_browser_when_ready"), \
                patch.object(launcher, "supervise_worker"):
            launcher.main()

    def test_main_rolls_back_failed_activated_database_before_retry(self):
        from pubtv.config import launcher
        events = []
        with tempfile.TemporaryDirectory() as temp_dir, patch.dict(os.environ, {"PUBTV_DATA_DIR": temp_dir}, clear=False), \
                patch.object(launcher, "apply_staged_import", return_value=True), patch.object(launcher, "rollback_activation", side_effect=lambda path: events.append("rollback") or True) as rollback, \
                patch.object(launcher, "finalize_activation") as finalize, patch.object(launcher, "_load_application", side_effect=[RuntimeError("migration"), object()]) as load, \
                patch.object(launcher, "_open_browser_when_ready"), patch.object(launcher, "supervise_worker"), patch("django.db.connections.close_all", side_effect=lambda: events.append("close")) as close:
            launcher.main()
        self.assertEqual(load.call_count, 2); close.assert_called_once_with(); rollback.assert_called_once_with(Path(temp_dir)); finalize.assert_called_once_with(Path(temp_dir))
        self.assertEqual(events, ["close", "rollback"])

    def test_main_propagates_load_failure_without_activation(self):
        from pubtv.config import launcher
        with tempfile.TemporaryDirectory() as temp_dir, patch.dict(os.environ, {"PUBTV_DATA_DIR": temp_dir}, clear=False), patch.object(launcher, "apply_staged_import", return_value=False), patch.object(launcher, "_load_application", side_effect=RuntimeError("migration")):
            with self.assertRaises(RuntimeError): launcher.main()

    def test_supervisor_runs_gunicorn_on_calling_thread_and_cleans_up_worker(self):
        from pubtv.config import launcher

        class Worker:
            def __init__(self):
                self.terminated = False
            def poll(self):
                return 0 if self.terminated else None
            def terminate(self):
                self.terminated = True
            def wait(self, timeout):
                return 0

        worker = Worker()
        calling_thread = threading.current_thread()
        with patch.object(launcher, "start_worker", return_value=worker), \
                patch.object(launcher, "run_gunicorn") as gunicorn:
            gunicorn.side_effect = lambda *_: self.assertIs(threading.current_thread(), calling_thread)
            launcher.supervise_worker(object(), "127.0.0.1", 43211)
        self.assertTrue(worker.terminated)

    def test_supervisor_restarts_exited_worker_and_terminates_replacement(self):
        from pubtv.config import launcher

        class Worker:
            def __init__(self, exited=False):
                self.exited = exited
                self.terminated = False
            def poll(self):
                return 1 if self.exited else (0 if self.terminated else None)
            def terminate(self):
                self.terminated = True
            def wait(self, timeout):
                return 0

        exited, replacement = Worker(exited=True), Worker()
        with patch.object(launcher, "start_worker", side_effect=[exited, replacement]) as start, \
                patch.object(launcher, "run_gunicorn", side_effect=lambda *_: time.sleep(2.2)):
            launcher.supervise_worker(object(), "127.0.0.1", 43212)
        self.assertEqual(start.call_count, 2)
        self.assertTrue(replacement.terminated)

    def test_frozen_worker_mode_runs_only_worker_command(self):
        from pubtv.config import launcher

        with patch.object(launcher.sys, "argv", ["ChannelDex", "--worker"]), \
                patch.object(launcher.sys, "frozen", True, create=True), \
                patch("django.setup") as setup, \
                patch("django.core.management.call_command") as command, \
                patch.object(launcher, "prepare_data_dir") as prepare, \
                patch.object(launcher, "_load_application") as load, \
                patch.object(launcher, "supervise_worker") as supervise:
            launcher.main()
        setup.assert_called_once_with()
        command.assert_called_once_with("run_ultranexus_worker")
        prepare.assert_not_called()
        load.assert_not_called()
        supervise.assert_not_called()
