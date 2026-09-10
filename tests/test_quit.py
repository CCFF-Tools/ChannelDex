import os
import unittest
from unittest.mock import patch

from django.test import RequestFactory


class QuitViewTests(unittest.TestCase):
    def test_quit_requires_post_and_is_disabled_by_default(self):
        from pubtv.operations.views import quit_app
        factory = RequestFactory()
        with patch.dict(os.environ, {"PUBTV_ENABLE_QUIT": "0"}, clear=False), patch("os.kill") as kill:
            self.assertEqual(quit_app(factory.get("/quit/")).status_code, 405)
            self.assertEqual(quit_app(factory.post("/quit/")).status_code, 404)
            kill.assert_not_called()

    def test_enabled_quit_schedules_master_sigterm(self):
        from pubtv.operations.views import quit_app
        factory = RequestFactory()
        with patch.dict(os.environ, {"PUBTV_ENABLE_QUIT": "1", "PUBTV_MASTER_PID": "1234"}, clear=False), \
                patch("pubtv.operations.views.threading.Timer") as timer, \
                patch("pubtv.operations.views.os.kill") as kill:
            response = quit_app(factory.post("/quit/"))
        self.assertEqual(response.status_code, 200)
        timer.assert_called_once()
        timer.return_value.start.assert_called_once_with()
        kill.assert_not_called()

    def test_quit_without_explicit_master_pid_cannot_kill_parent(self):
        from pubtv.operations.views import quit_app
        factory = RequestFactory()
        with patch.dict(os.environ, {"PUBTV_ENABLE_QUIT": "1"}, clear=False), patch("os.kill") as kill:
            self.assertEqual(quit_app(factory.post("/quit/")).status_code, 404)
            kill.assert_not_called()
