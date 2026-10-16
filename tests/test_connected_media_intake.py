import re
import tempfile
from pathlib import Path
from unittest.mock import patch

from django.db import OperationalError
from django.test import RequestFactory, TestCase
from django.contrib.messages.middleware import MessageMiddleware
from django.contrib.sessions.middleware import SessionMiddleware
from pubtv.operations.active_station import SESSION_KEY
from pubtv.operations.media_intake import (
    _catalog_payload, catalog_intake, preparation_monitor, safe_return_url,
    save_catalog_intake,
)
from pubtv.operations.models import (
    AuditEvent, CatalogIntakeSubmission, Device, Episode, MediaAsset, PreparationBatch, PreparationJob,
    Show, Station, UltraNexusTargetSettings,
)


class ConnectedMediaIntakeTests(TestCase):
    def setUp(self):
        self.station = Station.objects.create(name="PUB-TV")
        self.other = Station.objects.create(name="GOV-TV")
        self.show = Show.objects.create(station=self.station, title="Civic", code="civic")
        self.episode = Episode.objects.create(show=self.show, title="Existing")
        self.factory = RequestFactory()

    def request(self, method="get", path="/assets/new/", data=None):
        request = getattr(self.factory, method)(path, data=data or {})
        SessionMiddleware(lambda _request: None).process_request(request)
        request.session[SESSION_KEY] = self.station.pk
        MessageMiddleware(lambda _request: None).process_request(request)
        return request

    def test_new_episode_catalog_save_does_not_require_device(self):
        payload = {
            "purpose": "new_episode", "show": self.show, "episode": None,
            "title": "Fresh", "file_name": "fresh.mp4", "episode_number": 4,
            "runtime_seconds": None, "source_reference": "email:submission",
            "storage_reference": "/Volumes/Media",
        }
        asset, created = save_catalog_intake(station=self.station, payload=payload)
        self.assertTrue(created)
        self.assertEqual(asset.episode.title, "Fresh")
        self.assertFalse(Device.objects.exists())
        self.assertIn("submission source: email:submission", AuditEvent.objects.get(entity="MediaAsset").summary)

    def test_reusable_and_existing_episode_are_supported(self):
        reusable = {"purpose": "reusable", "show": None, "episode": None, "title": "",
                    "file_name": "station-id.mov", "episode_number": None, "runtime_seconds": None,
                    "source_reference": "producer", "storage_reference": "local disk"}
        asset, _ = save_catalog_intake(station=self.station, payload=reusable)
        self.assertIsNone(asset.episode)
        existing = dict(reusable, purpose="existing_episode", show=self.show, episode=self.episode,
                        file_name="existing.mp4", runtime_seconds=120)
        asset, _ = save_catalog_intake(station=self.station, payload=existing)
        self.assertEqual(asset.episode, self.episode)

    def test_foreign_show_is_rejected_before_write(self):
        foreign = Show.objects.create(station=self.other, title="Other", code="other")
        data = {"purpose": "new_episode", "show": str(foreign.pk), "title": "Bad", "file_name": "bad.mp4"}
        request = self.request("post", data=data)
        response = catalog_intake(request)
        self.assertEqual(response.status_code, 400)
        self.assertEqual(Episode.objects.filter(title="Bad").count(), 0)
        self.assertEqual(MediaAsset.objects.filter(file_name="bad.mp4").count(), 0)

    def test_invalid_metadata_has_no_partial_episode_or_asset(self):
        data = {"purpose": "new_episode", "show": str(self.show.pk), "title": "Bad",
                "file_name": "bad.mp4", "runtime_seconds": "zero"}
        request = self.request("post", data=data)
        response = catalog_intake(request)
        self.assertEqual(response.status_code, 400)
        self.assertFalse(Episode.objects.filter(title="Bad").exists())
        self.assertFalse(MediaAsset.objects.filter(file_name="bad.mp4").exists())

    def test_safe_return_preserves_filters_and_rejects_external(self):
        self.assertEqual(safe_return_url("/media/library/?q=abc&show=2"),
                         "/media/library/?q=abc&show=2")
        self.assertEqual(safe_return_url("https://evil.example/"), "/media/library/")

    def test_preparation_monitor_has_recovery_without_device(self):
        request = self.request("get", "/media/preparation/?asset=999")
        response = preparation_monitor(request)
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"No destination configured", response.content)

    def test_get_malformed_ids_renders_form(self):
        response = catalog_intake(self.request("get", "/assets/new/?show=abc&episode=nope"))
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Add media", response.content)

    def test_payload_preserves_zero_kind_version_and_path(self):
        request = self.request("post", data={
            "purpose": "existing_episode", "show": str(self.show.pk),
            "episode": str(self.episode.pk), "file_name": "master.mxf",
            "kind": "encoded", "version": "v4", "local_path": "/media/master.mxf",
            "runtime_seconds_0": "0", "runtime_seconds_1": "0", "runtime_seconds_2": "0",
            "source_reference": "Email ref", "source_method": "email_link",
        })
        payload = _catalog_payload(request, self.station)
        self.assertEqual(payload["runtime_seconds"], 0)
        self.assertEqual(payload["kind"], "encoded")
        self.assertEqual(payload["version"], "v4")
        self.assertEqual(payload["local_path"], "/media/master.mxf")
        asset, _ = save_catalog_intake(station=self.station, payload=payload)
        self.assertEqual(asset.runtime_seconds, 0)
        self.assertEqual(asset.kind, "encoded")
        self.assertEqual(asset.version, "v4")
        self.assertEqual(asset.local_path, "/media/master.mxf")

    def test_submission_delivery_is_durable_and_source_is_separate(self):
        request = self.request("post", data={
            "purpose": "existing_episode", "show": str(self.show.pk),
            "episode": str(self.episode.pk), "file_name": "episode.mp4",
            "kind": "source", "version": "v1", "source_reference": "Dropbox ref",
            "source_method": "dropbox", "storage_reference": "/Volumes/Library",
        })
        payload = _catalog_payload(request, self.station)
        asset, _ = save_catalog_intake(station=self.station, payload=payload)
        self.assertEqual(asset.smb_reference, "/Volumes/Library")
        delivery = self.episode.deliveries.get()
        self.assertEqual(delivery.reference, "Dropbox ref")
        self.assertEqual(delivery.method, "dropbox")

    def test_duplicate_submission_token_replays_saved_asset(self):
        data = {
            "purpose": "reusable", "file_name": "station-id.mp4",
            "kind": "source", "version": "v1", "submission_token": "token-1",
        }
        session = self.client.session
        session[SESSION_KEY] = self.station.pk
        session.save()
        response = self.client.post("/assets/new/", data)
        self.assertEqual(response.status_code, 302)
        self.assertRegex(response["Location"], r"/media/intake/saved/\d+/")
        self.assertEqual(MediaAsset.objects.count(), 1)
        submission = CatalogIntakeSubmission.objects.get(station=self.station, token="token-1")
        self.assertEqual(submission.asset, MediaAsset.objects.get())
        # Idempotency is durable across browser sessions, not session-local.
        self.client.cookies.clear()
        session = self.client.session
        session[SESSION_KEY] = self.station.pk
        session.save()
        response = self.client.post("/assets/new/", data)
        self.assertEqual(response.status_code, 302)
        self.assertEqual(MediaAsset.objects.count(), 1)

    def test_duplicate_submission_token_rejects_different_payload(self):
        session = self.client.session
        session[SESSION_KEY] = self.station.pk
        session.save()
        base = {
            "purpose": "reusable", "file_name": "station-id.mp4",
            "kind": "source", "version": "v1", "submission_token": "shared-token",
        }
        self.assertEqual(self.client.post("/assets/new/", base).status_code, 302)
        response = self.client.post("/assets/new/", {**base, "file_name": "different.mp4"})
        self.assertEqual(response.status_code, 400)
        self.assertContains(response, "already used for different media", status_code=400)
        self.assertEqual(MediaAsset.objects.count(), 1)
        self.assertEqual(CatalogIntakeSubmission.objects.count(), 1)

    def test_sqlite_writer_lock_returns_retryable_error_without_catalog_write(self):
        session = self.client.session
        session[SESSION_KEY] = self.station.pk
        session.save()
        data = {
            "purpose": "reusable", "file_name": "station-id.mp4",
            "kind": "source", "version": "v1", "submission_token": "lock-token",
        }
        with patch.object(CatalogIntakeSubmission.objects, "create", side_effect=OperationalError("database is locked")):
            response = self.client.post("/assets/new/", data)
        self.assertEqual(response.status_code, 400)
        self.assertContains(response, "being saved", status_code=400)
        self.assertFalse(MediaAsset.objects.exists())
        self.assertFalse(CatalogIntakeSubmission.objects.exists())

    def test_get_renders_real_submission_token_and_derives_existing_purpose(self):
        session = self.client.session
        session[SESSION_KEY] = self.station.pk
        session.save()
        response = self.client.get(f"/assets/new/?episode={self.episode.pk}")
        self.assertEqual(response.status_code, 200)
        content = response.content.decode()
        self.assertRegex(content, r'name="submission_token" value="[0-9a-f-]{36}"')
        self.assertIn('value="existing_episode" checked', content)

    def test_existing_episode_asset_post_review_and_confirm_reuses_asset(self):
        self._assert_existing_asset_preparation(reusable=False)

    def test_reusable_asset_post_review_and_confirm_reuses_asset(self):
        self._assert_existing_asset_preparation(reusable=True)

    def _assert_existing_asset_preparation(self, *, reusable):
        target = Device.objects.create(station=self.station, name=f"Target-{reusable}")
        UltraNexusTargetSettings.objects.create(target=target, is_current=True, host="controller")
        handle = tempfile.NamedTemporaryFile(suffix=".mp4", delete=False)
        self.addCleanup(lambda: Path(handle.name).unlink(missing_ok=True))
        handle.write(b"reviewed source")
        handle.close()
        asset = MediaAsset.objects.create(
            station=self.station, episode=None if reusable else self.episode,
            file_name=Path(handle.name).name, kind="source", version="v1", local_path=handle.name,
        )
        session = self.client.session
        session[SESSION_KEY] = self.station.pk
        session.save()
        setup = self.client.get(f"/media/preparation/?asset={asset.pk}&target={target.pk}")
        token_match = re.search(r'name="submission_token" value="([0-9a-f-]{36})"', setup.content.decode())
        self.assertIsNotNone(token_match)
        review = self.client.post("/media/preparation/", {
            "action": "review_intake", "target": target.pk, "asset": asset.pk,
            "show": "" if reusable else self.show.pk, "episode": "" if reusable else self.episode.pk,
            "submission_token": token_match.group(1), "originating_station": self.station.pk,
        })
        self.assertEqual(review.status_code, 200)
        self.assertContains(review, "Review preparation")
        batch = PreparationBatch.objects.get(submission_token=token_match.group(1))
        self.assertEqual(batch.items.get().asset, asset)
        self.assertEqual(MediaAsset.objects.count(), 1)
        confirm = self.client.post("/media/preparation/", {
            "action": "confirm_existing_asset", "batch_id": batch.pk,
            "submission_token": token_match.group(1), "originating_station": self.station.pk,
        })
        self.assertEqual(confirm.status_code, 302)
        batch.refresh_from_db()
        self.assertEqual(batch.status, "approved")
        self.assertEqual(MediaAsset.objects.count(), 1)
        self.assertTrue(PreparationJob.objects.filter(batch=batch).exists())

    def test_prepare_context_keeps_existing_asset_and_reusable_asset(self):
        episode_asset = MediaAsset.objects.create(station=self.station, episode=self.episode,
                                                   file_name="existing.mp4", kind="source", version="v1")
        response = preparation_monitor(self.request("get", f"/media/preparation/?asset={episode_asset.pk}"))
        self.assertEqual(response.status_code, 200)
        reusable = MediaAsset.objects.create(station=self.station, file_name="id.mp4",
                                              kind="source", version="v1")
        response = preparation_monitor(self.request("get", f"/media/preparation/?asset={reusable.pk}"))
        self.assertEqual(response.status_code, 200)
