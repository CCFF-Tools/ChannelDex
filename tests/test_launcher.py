import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


class LauncherTests(unittest.TestCase):
    def test_uses_gunicorn_next_to_current_interpreter(self):
        from pubtv.config import launcher

        with tempfile.TemporaryDirectory() as temp_dir:
            executable = Path(temp_dir) / "python"
            gunicorn = executable.with_name("gunicorn")
            executable.touch()
            gunicorn.touch()
            with patch.dict(os.environ, {"PUBTV_DATA_DIR": temp_dir}, clear=False), \
                    patch.object(launcher.sys, "executable", str(executable)), \
                    patch.object(launcher.os, "execv") as execv, \
                    patch("django.core.management.call_command") as call_command, \
                    patch("django.setup"):
                launcher.main()
            self.assertEqual(execv.call_args.args[0], str(gunicorn))
            self.assertEqual(execv.call_args.args[1][1:3], ["--bind", "127.0.0.1:8000"])
            self.assertEqual([call.args[0] for call in call_command.call_args_list], ["migrate", "collectstatic"])
