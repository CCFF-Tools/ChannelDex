from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import patch

from django.test import Client, TestCase
from pubtv.operations.models import Device, UltraNexusTargetSettings
from pubtv.operations.forms import UltraNexusSettingsForm
from pubtv.operations.views import _file_evidence, _run_device_diagnostics


class DeviceSettingsContractTests(TestCase):
    def test_ordinary_form_excludes_qualification_and_capability_controls(self):
        names = set(UltraNexusSettingsForm().fields)
        self.assertNotIn("qualification_status", names)
        self.assertNotIn("capability_flags", names)
        self.assertNotIn("bin_resource_template_reference", names)
        self.assertNotIn("base_bin_hash", names)

    def test_file_evidence_reports_stale_observed_fingerprint(self):
        with TemporaryDirectory() as directory:
            path = Path(directory) / "evidence.bin"
            path.write_bytes(b"observed")
            result = _file_evidence(path, "0" * 64)
        self.assertTrue(result["readable"])
        self.assertTrue(result["stale"])
        self.assertNotEqual(result["observed_hash"], result["approved_hash"])

    def test_readable_evidence_without_approved_hash_is_blocked(self):
        with TemporaryDirectory() as directory:
            path = Path(directory) / "evidence.bin"
            path.write_bytes(b"observed")
            current = UltraNexusTargetSettings(
                target=Device(name="WinLGX"), base_nmg_path=str(path), base_bin_path=str(path),
                base_nmg_hash="", base_bin_hash="", settings={"ffmpeg_qualification_manifest": str(path)},
            )
            result = _run_device_diagnostics(current.target, {}, current)

        integrity = next(item for item in result if item["name"] == "Evidence-file integrity")
        self.assertEqual(integrity["status"], "blocked")
        self.assertTrue(all("approved fingerprint missing" in detail for detail in integrity["details"]))

    @patch("pubtv.operations.views.subprocess.run")
    def test_missing_file_is_reported_without_exception(self, run):
        result = _file_evidence("/path/that/does/not/exist", "")
        self.assertFalse(result["readable"])
        run.assert_not_called()

    @patch("pubtv.operations.views.KeychainSecretStore.set")
    def test_save_preserves_trusted_values_when_post_forges_protected_fields(self, save):
        device = Device.objects.create(name="WinLGX")
        current = UltraNexusTargetSettings.objects.create(
            target=device, version=4, is_current=True, host="old-host", port=21,
            schedule_path="/internal/schedule/schedule.bin", command_port=23,
            qualification_status="passed", qualification_evidence_hash="a" * 64,
            profile_identity_hash="b" * 64, ame_preset_sha256="c" * 64,
            ffmpeg_profile_sha256="d" * 64, nmg_template_sha256="e" * 64,
            bin_template_sha256="f" * 64, base_nmg_hash="1" * 64,
            base_bin_hash="2" * 64, capability_flags=["media_transfer"],
            settings={"ftp_username": "owner", "bin_resource_template_reference": 8,
                      "bin_schedule_template_slot": 9, "ffmpeg_build_sha256": "3" * 64},
        )
        response = Client().post(f"/settings/devices/{device.pk}/", {
            "settings_version": current.version, "host": "new-host", "port": 21,
            "username": "owner", "schedule_path": "/internal/schedule/schedule.bin",
            "command_port": 23, "password": "", "qualification_status": "open",
            "reconciliation_mode": "authoritative",
            "capability_flags": "forged", "base_bin_hash": "0" * 64,
            "bin_resource_template_reference": 999,
        })
        self.assertEqual(response.status_code, 302)
        saved = UltraNexusTargetSettings.objects.get(target=device, is_current=True)
        self.assertEqual(saved.qualification_status, "passed")
        self.assertEqual(saved.qualification_evidence_hash, "a" * 64)
        self.assertEqual(saved.capability_flags, ["media_transfer"])
        self.assertEqual(saved.base_bin_hash, "2" * 64)
        self.assertEqual(saved.settings["bin_resource_template_reference"], 8)
        self.assertEqual(saved.reconciliation_mode, "preserve")

    @patch("pubtv.operations.views.FTP")
    def test_diagnostics_uses_unsaved_connection_and_never_writes(self, ftp_cls):
        ftp = ftp_cls.return_value
        device = Device.objects.create(name="WinLGX")
        result = _run_device_diagnostics(device, {
            "host": "unsaved-host", "port": "2121", "username": "owner",
            "password": "posted-secret", "media_directory": "/incoming",
        }, None)
        ftp.connect.assert_called_once_with("unsaved-host", 2121, timeout=3)
        ftp.login.assert_called_once_with("owner", "posted-secret")
        ftp.nlst.assert_called_once_with("/incoming")
        ftp.quit.assert_called_once_with()
        self.assertFalse(any(name.startswith(("stor", "write")) for name in (call.args[0].lower() for call in ftp.method_calls if call.args)))
        self.assertEqual(result[0]["status"], "pass")

    @patch("pubtv.operations.views.subprocess.run")
    @patch("pubtv.operations.views.FTP")
    def test_focused_ffmpeg_test_does_not_probe_ftp(self, ftp_cls, run):
        run.return_value.returncode = 0
        run.return_value.stdout = "ffmpeg version test\n"
        run.return_value.stderr = ""
        device = Device.objects.create(name="WinLGX")

        result = _run_device_diagnostics(
            device, {"ffmpeg_executable": "/tools/ffmpeg"}, None, only="ffmpeg",
        )

        self.assertEqual([item["name"] for item in result], ["FFmpeg and ffprobe"])
        ftp_cls.assert_not_called()
        self.assertEqual(run.call_count, 2)

    def test_device_page_shows_fixed_constraints_and_focused_tests(self):
        device = Device.objects.create(name="WinLGX")
        UltraNexusTargetSettings.objects.create(
            target=device, is_current=True, host="controller",
            schedule_path="/internal/schedule/schedule.bin", reconciliation_mode="preserve",
        )

        response = Client().get(f"/settings/devices/{device.pk}/")

        self.assertContains(response, "Fixed schedule path")
        self.assertContains(response, "Test FTP login and directory")
        self.assertContains(response, "Test AME application and preset")
        self.assertContains(response, "Test FFmpeg and ffprobe")
        self.assertContains(response, "Test evidence-file integrity")
        self.assertNotContains(response, 'name="schedule_path"')
        self.assertNotContains(response, 'name="reconciliation_mode"')

    @patch("pubtv.operations.views.FTP", side_effect=TimeoutError("secret host details"))
    def test_diagnostics_redacts_ftp_failure(self, ftp_cls):
        device = Device.objects.create(name="WinLGX")
        result = _run_device_diagnostics(device, {"host": "host", "username": "owner", "password": "secret"}, None)
        self.assertEqual(result[0]["status"], "fail")
        self.assertNotIn("secret", str(result[0]))
        self.assertNotIn("host details", str(result[0]))
