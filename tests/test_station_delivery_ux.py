from pathlib import Path
import tempfile
import stat
import plistlib
from datetime import datetime
from unittest.mock import Mock, patch

from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import IntegrityError
from django.test import Client, TestCase, TransactionTestCase, override_settings
from django.utils import timezone

from pubtv.operations.models import (AuditEvent, Device, Episode, MediaAsset, Occurrence,
                                     PreparationBatch, PreparationBatchItem, PreparationJob,
                                     SchedulePublicationBatch, Show, Station, UltraNexusTargetSettings)
from pubtv.ultranexus.encoding import discover_ame, discover_ffmpeg
from pubtv.ultranexus.secrets import KeychainSecretStore, device_secret_reference


class StationDeliveryUXTests(TestCase):
    def test_keychain_write_keeps_secret_out_of_argv(self):
        runner = Mock(return_value=Mock(stdout=""))
        KeychainSecretStore(runner).set(device_secret_reference(7), "private-password")
        argv = runner.call_args.args[0]
        assert "private-password" not in argv
        assert runner.call_args.kwargs["input"] == "private-password\n"

    def test_discovery_accepts_existing_candidates(self):
        with tempfile.TemporaryDirectory() as directory:
            ame = Path(directory) / "AME"; ame.touch()
            ffmpeg = Path(directory) / "ffmpeg"; ffmpeg.touch()
            assert discover_ame((str(ame),)) == str(ame)
            assert discover_ffmpeg((str(ffmpeg),)) == str(ffmpeg)

    def test_browse_rejects_unknown_kind(self):
        response = Client().post("/settings/browse/", {"kind": "directory"})
        assert response.status_code == 400

    def test_workspaces_have_separate_media_and_schedule_actions(self):
        response = Client().get("/schedule/delivery/")
        self.assertContains(response, "Review schedule")
        self.assertContains(response, "Confirm activation")
        self.assertNotContains(response, "Schedule insertion")
        media = Client().get("/media/")
        self.assertContains(media, "Queue preparation &amp; transfer")
        self.assertNotContains(media, "Planned schedule occurrence")

    def test_device_setup_requires_password(self):
        device = Device.objects.create(name="WinLGX")
        response = Client().post(f"/settings/devices/{device.pk}/", {"settings_version": 0, "host": "controller", "schedule_path": "/internal/schedule/schedule.bin", "username": "owner"})
        assert response.status_code == 200
        assert "first device setup" in response.content.decode()

    def test_existing_device_page_renders_current_settings_version(self):
        device = Device.objects.create(name="WinLGX")
        UltraNexusTargetSettings.objects.create(
            target=device, version=7, is_current=True, command_port=23,
            schedule_path="/internal/schedule/schedule.bin",
        )
        response = Client().get(f"/settings/devices/{device.pk}/")
        assert response.status_code == 200
        assert 'name="settings_version" value="7"' in response.content.decode()
        assert "secret_reference" not in response.content.decode()
        assert "command_secret_reference" not in response.content.decode()
        save_response = Client().post(f"/settings/devices/{device.pk}/", {
            "settings_version": 7,
            "schedule_path": "/internal/schedule/schedule.bin",
            "command_port": 23,
            "reconciliation_mode": "preserve",
            "qualification_status": "open",
        })
        assert save_response.status_code == 302
        assert UltraNexusTargetSettings.objects.get(target=device, is_current=True).version == 8

    @patch("pubtv.operations.views.KeychainSecretStore.set")
    def test_device_save_shares_reference_and_blank_save_preserves_settings(self, save):
        device = Device.objects.create(name="WinLGX")
        data = {"settings_version": 0, "host": "controller", "port": 21, "username": "owner", "password": "secret", "schedule_path": "/internal/schedule/schedule.bin", "media_directory": "/Vol1/mpeg", "ame_executable": "/Applications/Adobe Media Encoder.app/Contents/MacOS/Adobe Media Encoder", "base_bin_hash": "a" * 64, "base_bin_path": "/tmp/base.bin", "qualification_status": "open", "capability_flags": "media_transfer,schedule_bin_delivery"}
        assert Client().post(f"/settings/devices/{device.pk}/", data).status_code == 302
        first = UltraNexusTargetSettings.objects.get(target=device, is_current=True)
        assert first.secret_reference == first.command_secret_reference
        assert first.command_username == first.settings["ftp_username"] == "owner"
        second_data = dict(data); second_data["settings_version"] = 1; second_data["password"] = ""; second_data.pop("capability_flags")
        assert Client().post(f"/settings/devices/{device.pk}/", second_data).status_code == 302
        second = UltraNexusTargetSettings.objects.get(target=device, is_current=True)
        assert second.version == 2 and second.secret_reference == first.secret_reference
        assert second.base_bin_hash == first.base_bin_hash and second.settings["ame_executable"] == first.settings["ame_executable"] == data["ame_executable"]
        assert second.capability_flags == []
        assert second.settings_hash

    def test_keychain_change_uses_isolated_reference_and_cleans_it_on_commit_failure(self):
        device = Device.objects.create(name="WinLGX")
        reference = device_secret_reference(device.pk)
        UltraNexusTargetSettings.objects.create(
            target=device, version=1, is_current=True, host="controller", port=21,
            command_port=23, schedule_path="/internal/schedule/schedule.bin",
            secret_reference=f"{reference.service}:{reference.account}",
            command_secret_reference=f"{reference.service}:{reference.account}",
            command_username="owner", settings={"ftp_username": "owner"},
        )
        data = {"settings_version": 1, "host": "controller", "port": 21, "command_port": 23, "username": "owner", "password": "new-secret", "schedule_path": "/internal/schedule/schedule.bin", "reconciliation_mode": "preserve", "qualification_status": "open"}
        with patch.object(KeychainSecretStore, "get") as get_secret, patch.object(KeychainSecretStore, "set") as save_secret, patch.object(KeychainSecretStore, "delete") as delete_secret, patch.object(UltraNexusTargetSettings, "save", side_effect=IntegrityError):
            with self.assertRaises(IntegrityError):
                Client().post(f"/settings/devices/{device.pk}/", data)
        get_secret.assert_not_called()
        new_reference = save_secret.call_args.args[0]
        assert new_reference != reference
        assert save_secret.call_args.args[1] == "new-secret"
        delete_secret.assert_called_once_with(new_reference)

    @patch("pubtv.operations.views.subprocess.run")
    def test_browse_application_normalizes_and_cancel_is_safe(self, run):
        app = Path("/Applications/Adobe Media Encoder.app")
        run.return_value = Mock(returncode=0, stdout=str(app) + "\n")
        with patch("pubtv.operations.views.Path.exists", return_value=True):
            response = Client().post("/settings/browse/", {"kind": "application"})
        assert "choose file with prompt \"Choose an application\" of type {\"com.apple.application-bundle\"}" in run.call_args.args[0][2]
        assert response.json()["path"].endswith("Contents/MacOS/Adobe Media Encoder")
        run.return_value = Mock(returncode=1, stdout="", stderr="execution error: User canceled. (-128)")
        assert Client().post("/settings/browse/", {"kind": "file"}).json()["cancelled"] is True

        run.return_value = Mock(returncode=1, stdout="", stderr="execution error: picker failed (7)")
        failed = Client().post("/settings/browse/", {"kind": "file"}).json()
        assert failed["cancelled"] is False
        assert failed["error"] == "The local picker failed."
        assert "execution error" not in failed["error"]
        assert "(7)" not in failed["error"]

    @patch("pubtv.operations.views.subprocess.run")
    def test_browse_application_uses_plist_executable_and_trailing_slash(self, run):
        with tempfile.TemporaryDirectory() as directory:
            app = Path(directory) / "Adobe Media Encoder 2026" / "Adobe Media Encoder 2026.app"
            contents = app / "Contents"
            macos = contents / "MacOS"
            macos.mkdir(parents=True)
            executable = macos / "Adobe Media Encoder 2025"
            executable.touch()
            with (contents / "Info.plist").open("wb") as stream:
                plistlib.dump({"CFBundleExecutable": executable.name}, stream)
            run.return_value = Mock(returncode=0, stdout=str(app) + "/\n")
            response = Client().post("/settings/browse/", {"kind": "application"})
        assert response.json()["path"] == str(executable)
        assert response.json()["resolved"] is True

    def test_valid_upload_creates_private_episode_media_without_changing_occurrence(self):
        import uuid
        station = Station.objects.create(name="PUB-TV")
        device = Device.objects.create(name="WinLGX")
        UltraNexusTargetSettings.objects.create(target=device, version=1, is_current=True,
            schedule_path="/internal/schedule/schedule.bin", command_port=23)
        show = Show.objects.create(station=station, title="News", code="news")
        episode = Episode.objects.create(show=show, title="Episode 1")
        occurrence = Occurrence.objects.create(station=station, show=show, episode=episode,
            item_type="episode", label="Episode 1", starts_at=timezone.now(), planned_duration_seconds=1800)
        with tempfile.TemporaryDirectory() as directory, self.settings(DATA_DIR=Path(directory)):
            response = Client().post("/prepare/", {"source_files": [SimpleUploadedFile("episode.mp4", b"video")],
                "target": device.pk, "show": show.pk, "episode_id_0": episode.pk,
                "submission_token": str(uuid.uuid4()), "encode_before_transfer": "on"})
            self.assertEqual(response.status_code, 302)
            item = PreparationBatchItem.objects.get(asset__episode=episode)
            source_path = Path(item.selected_input_path)
            self.assertTrue(source_path.exists())
            self.assertTrue(PreparationJob.objects.filter(batch=item.batch).exists())
            self.assertEqual(stat.S_IMODE(source_path.stat().st_mode), 0o600)
            self.assertEqual(stat.S_IMODE(source_path.parent.stat().st_mode), 0o700)
            self.assertIsNone(item.occurrence_id)
            occurrence.refresh_from_db()
            self.assertIsNone(occurrence.asset_id)
            self.assertEqual(occurrence.revision, 1)

    def test_ready_publication_creation_is_idempotent(self):
        station = Station.objects.create(name="PUB-TV"); device = Device.objects.create(name="WinLGX")
        show = Show.objects.create(station=station, title="News", code="news"); episode = Episode.objects.create(show=show, title="Episode 1")
        occurrence = Occurrence.objects.create(station=station, show=show, episode=episode, item_type="episode", label="Episode 1", starts_at=timezone.now(), planned_duration_seconds=1800)
        asset = MediaAsset.objects.create(episode=episode, file_name="episode.mp4", kind="source")
        batch = PreparationBatch.objects.create(target=device, label="ready"); item = PreparationBatchItem.objects.create(batch=batch, asset=asset, occurrence=occurrence, execution_status="ready")
        client = Client(); assert client.post("/automation/", {"action": "create_delivery_publication", "item": item.pk}).status_code == 302; client.post("/automation/", {"action": "create_delivery_publication", "item": item.pk})
        assert SchedulePublicationBatch.objects.filter(target=device).count() == 1
        assert SchedulePublicationBatch.objects.get(target=device).occurrence_selections.count() == 1

    def test_legacy_automation_url_renders_schedule_delivery(self):
        response = Client().get("/automation/")
        self.assertContains(response, "Schedule delivery")
        for label in ("Review changes", "Generate and validate", "Stage upload", "Confirm activation", "Record independent confirmation"):
            self.assertContains(response, label)

    def test_durable_publication_relation_exists(self):
        assert "preparation_item" in Path(__file__).resolve().parents[1].joinpath("pubtv/operations/models.py").read_text()


class StationDeviceConcurrencyTests(TransactionTestCase):
    @patch("pubtv.operations.views.KeychainSecretStore.delete")
    @patch("pubtv.operations.views.KeychainSecretStore.set")
    def test_stale_blank_password_save_cannot_restore_superseded_reference(self, save_secret, delete_secret):
        device = Device.objects.create(name="WinLGX")
        original = device_secret_reference(device.pk)
        first = UltraNexusTargetSettings.objects.create(
            target=device, version=1, is_current=True, host="old-controller", port=21,
            command_port=23, schedule_path="/internal/schedule/schedule.bin",
            secret_reference=f"{original.service}:{original.account}",
            command_secret_reference=f"{original.service}:{original.account}",
            command_username="owner", settings={"ftp_username": "owner"},
        )
        stale_form = {"settings_version": first.version, "host": "stale-controller", "port": 21, "command_port": 23, "username": "owner", "password": "", "schedule_path": "/internal/schedule/schedule.bin", "reconciliation_mode": "preserve", "qualification_status": "open"}
        password_form = dict(stale_form, password="new-secret", host="new-controller")
        assert Client().post(f"/settings/devices/{device.pk}/", password_form).status_code == 302
        current = UltraNexusTargetSettings.objects.get(target=device, is_current=True)
        assert current.version == 2 and current.secret_reference != first.secret_reference
        response = Client().post(f"/settings/devices/{device.pk}/", stale_form)
        assert response.status_code == 200
        assert "changed in another window" in response.content.decode()
        unchanged = UltraNexusTargetSettings.objects.get(target=device, is_current=True)
        assert unchanged.pk == current.pk and unchanged.secret_reference == current.secret_reference
