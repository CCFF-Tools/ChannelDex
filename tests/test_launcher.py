import os
import tempfile
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
                patch.object(launcher, "run_gunicorn") as gunicorn:
            launcher.main()
        browser.assert_called_once_with("127.0.0.1", 43210)
        gunicorn.assert_called_once_with(app, "127.0.0.1", 43210)

    def test_main_sets_private_umask(self):
        from pubtv.config import launcher
        with tempfile.TemporaryDirectory() as temp_dir, \
                patch.dict(os.environ, {"PUBTV_DATA_DIR": temp_dir}, clear=False), \
                patch.object(launcher.os, "umask") as umask, \
                patch.object(launcher, "_load_application", return_value=object()), \
                patch.object(launcher, "_open_browser_when_ready"), \
                patch.object(launcher, "run_gunicorn"):
            launcher.main()
        umask.assert_called_once_with(0o077)
