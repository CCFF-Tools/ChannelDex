from datetime import datetime, time
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import patch
from zoneinfo import ZoneInfo

from django.test import TestCase as DjangoTestCase

from pubtv.operations.active_station import SESSION_KEY
from pubtv.operations.connected_intake import (
    add_source_paths,
    create_draft,
    inspect_source,
    suggest_metadata,
)
from pubtv.operations.media_intake import safe_return_url
from pubtv.operations.models import (
    Device,
    Episode,
    MediaAsset,
    MediaIntakeDraft,
    MediaIntakeReview,
    Occurrence,
    PreparationBatch,
    PreparationJob,
    RecurrenceSlot,
    Show,
    Station,
    UltraNexusTargetSettings,
    WeeklyEpisodeAssignment,
)


class ConnectedIntakeHelpersTests(TestCase):
    def test_filename_suggestion_is_editable_and_keeps_episode_number(self):
        result = suggest_metadata("Morning_Show_Episode_12.mxf")
        self.assertEqual(result["episode_number"], 12)
        self.assertIn("Morning Show", result["episode_title"])
        self.assertEqual(result["confidence"], "medium")

    def test_inspection_records_cheap_stable_file_facts_without_hashing_bytes(self):
        with TemporaryDirectory() as directory:
            path = Path(directory) / "episode.mp4"
            path.write_bytes(b"test media")
            facts = inspect_source(str(path), probe_runner=lambda _path: '{"format":{"duration":"3.2"}}')
        self.assertEqual(facts["size"], 10)
        self.assertEqual(facts["runtime_seconds"], 4)
        self.assertEqual(len(facts["key"]), 64)
        self.assertNotIn("sha256", facts)

    def test_inspection_bounds_the_best_effort_probe(self):
        with TemporaryDirectory() as directory:
            path = Path(directory) / "episode.mp4"
            path.write_bytes(b"test media")
            with patch("pubtv.operations.connected_intake.measured_runtime", return_value=None) as probe:
                inspect_source(str(path))
        probe.assert_called_once_with(path.resolve(), timeout_seconds=8)

    def test_safe_return_accepts_canonical_library_context(self):
        self.assertEqual(safe_return_url("/library/?q=id&view=reusable"),
                         "/library/?q=id&view=reusable")
        self.assertEqual(safe_return_url("https://outside.example/"), "/library/")


class ConnectedIntakeRequestTests(DjangoTestCase):
    def setUp(self):
        self.station = Station.objects.create(name="PUB-TV")
        self.other = Station.objects.create(name="GOV-TV")
        self.show = Show.objects.create(station=self.station, title="Morning", code="morning", slot_duration_seconds=1800)
        self.slot = RecurrenceSlot.objects.create(station=self.station, show=self.show, weekday=0,
                                                   start_time=time(7), duration_seconds=1800, is_premiere=True)
        session = self.client.session
        session[SESSION_KEY] = self.station.pk
        session.save()
        self.directory = TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)

    def source(self, name="Morning_Episode_1.mp4", content=b"media"):
        path = Path(self.directory.name) / name
        path.write_bytes(content)
        return path

    def inspect(self, path=None):
        path = path or self.source()
        response = self.client.post("/intake/", {"action": "inspect", "paths": str(path),
                                                  "originating_station": self.station.pk})
        self.assertEqual(response.status_code, 302)
        return MediaIntakeDraft.objects.get(station=self.station)

    def resolve(self, draft, **overrides):
        data = {"action": "resolve", "revision": draft.revision, "originating_station": self.station.pk,
                "kind_1": "episode", "show_id_1": self.show.pk, "episode_id_1": "new",
                "episode_title_1": "Morning One", "episode_number_1": "1",
                "encoding_mode_1": "needs_encoding", "target_id_1": "",
                "duplicate_action_1": "", "premiere_date_1": ""}
        data.update(overrides)
        return self.client.post(f"/intake/{draft.token}/", data)

    def confirm(self, draft, revision=None):
        return self.client.post(f"/intake/{draft.token}/", {"action": "confirm",
            "revision": draft.revision if revision is None else revision,
            "originating_station": self.station.pk})

    def test_landing_get_does_not_create_draft_and_multiline_post_does(self):
        self.assertEqual(self.client.get("/intake/").status_code, 200)
        self.assertFalse(MediaIntakeDraft.objects.exists())
        first, second = self.source("one.mp4"), self.source("two.mp4", b"two")
        response = self.client.post("/intake/", {"action": "inspect", "paths": f"{first}\n{second}"})
        self.assertEqual(response.status_code, 302)
        self.assertEqual(len(MediaIntakeDraft.objects.get().payload["rows"]), 2)

    def test_invalid_initial_path_redirects_to_same_durable_draft_and_can_be_corrected(self):
        missing = Path(self.directory.name) / "not-there.mp4"
        response = self.client.post(f"/intake/?show={self.show.pk}", {
            "action": "inspect", "paths": str(missing), "show": self.show.pk,
            "originating_station": self.station.pk,
        })
        self.assertEqual(response.status_code, 302)
        draft = MediaIntakeDraft.objects.get()
        self.assertEqual(response.url, f"/intake/{draft.token}/")
        self.assertEqual(draft.payload["pending_paths"], [str(missing)])
        self.assertEqual(draft.payload["intake_context"]["show"], self.show.pk)
        recovery = self.client.get(response.url)
        self.assertContains(recovery, str(missing))
        self.assertContains(recovery, "No such file or directory")
        self.assertContains(recovery, "Show context:")
        self.assertContains(recovery, self.show.title)

        corrected = self.source("corrected.mp4")
        corrected_response = self.client.post(response.url, {
            "action": "inspect", "revision": draft.revision, "paths": str(corrected),
            "originating_station": self.station.pk,
        })
        self.assertEqual(corrected_response.status_code, 302)
        self.assertEqual(corrected_response.url, response.url)
        draft.refresh_from_db()
        self.assertEqual(len(draft.payload["rows"]), 1)
        self.assertEqual(draft.payload["rows"][0]["file"]["path"], str(corrected.resolve()))
        self.assertEqual(draft.payload["pending_paths"], [])

        self.client.post(response.url, {"action": "inspect", "revision": draft.revision,
            "paths": str(corrected), "originating_station": self.station.pk})
        draft.refresh_from_db()
        self.assertEqual(len(draft.payload["rows"]), 1)
        self.assertEqual(MediaIntakeDraft.objects.count(), 1)

    def test_source_paths_are_saved_before_probe_interruption(self):
        draft, _created = create_draft(station=self.station)
        path = self.source("interrupt.mp4")
        with patch("pubtv.operations.connected_intake.inspect_source", side_effect=KeyboardInterrupt), self.assertRaises(KeyboardInterrupt):
            add_source_paths(draft, [str(path)], intake_context={"show": self.show})
        draft.refresh_from_db()
        self.assertEqual(draft.payload["pending_paths"], [str(path)])
        self.assertEqual(draft.payload["intake_context"]["show"], self.show.pk)

    def test_canonical_existing_asset_link_preserves_context_through_inspection(self):
        target = Device.objects.create(station=self.station, name="Context target")
        UltraNexusTargetSettings.objects.create(target=target, is_current=True, host="controller")
        episode = Episode.objects.create(show=self.show, title="Linked episode")
        path = self.source("linked.mp4")
        asset = MediaAsset.objects.create(station=self.station, episode=episode, file_name=path.name,
            kind="encoded", version="v1", local_path=str(path))
        response = self.client.get(f"/intake/?asset={asset.pk}&target={target.pk}")
        self.assertContains(response, asset.local_path)
        self.assertContains(response, f'name="asset" value="{asset.pk}"')
        response = self.client.post("/intake/", {"action": "inspect", "paths": str(path),
            "asset": asset.pk, "target": target.pk, "originating_station": self.station.pk})
        self.assertEqual(response.status_code, 302)
        row = MediaIntakeDraft.objects.get().payload["rows"][0]
        self.assertEqual(row["metadata"]["episode_id"], episode.pk)
        self.assertEqual(row["metadata"]["target_id"], target.pk)
        self.assertEqual(row["duplicate_action"], "reuse")
        self.assertEqual(row["encoding_mode"], "already_encoded")

    def test_no_target_saves_catalog_and_exact_premiere_plan(self):
        draft = self.inspect()
        response = self.resolve(draft, plan_1="on", premiere_date_1="2026-10-19")
        self.assertEqual(response.status_code, 302)
        draft.refresh_from_db()
        review = self.client.get(f"/intake/{draft.token}/")
        self.assertContains(review, "2026-10-19 at 07:00")
        self.assertContains(review, "Preparation blocked")
        response = self.confirm(draft)
        self.assertEqual(response.status_code, 302)
        self.assertEqual(Episode.objects.get().title, "Morning One")
        self.assertEqual(MediaAsset.objects.get().local_path, str((Path(self.directory.name) / "Morning_Episode_1.mp4").resolve()))
        self.assertTrue(WeeklyEpisodeAssignment.objects.filter(premiere_date="2026-10-19").exists())
        self.assertFalse(PreparationBatch.objects.exists())
        draft.refresh_from_db()
        self.assertTrue(draft.payload["results"][0]["preparation_blocked"])

    def test_configured_target_stages_review_then_queues_real_job(self):
        target = Device.objects.create(station=self.station, name="Controller")
        UltraNexusTargetSettings.objects.create(target=target, is_current=True, host="controller")
        draft = self.inspect()
        response = self.resolve(draft, target_id_1=target.pk, encoding_mode_1="already_encoded")
        self.assertEqual(response.status_code, 302)
        draft.refresh_from_db()
        self.assertEqual(draft.payload["step"], "device_review")
        review = MediaIntakeReview.objects.get()
        self.assertEqual(review.payload["connected_draft_token"], str(draft.token))
        response = self.confirm(draft)
        self.assertEqual(response.status_code, 302)
        batch = PreparationBatch.objects.get()
        self.assertEqual(batch.status, "approved")
        self.assertFalse(batch.items.get().encode_before_transfer)
        self.assertTrue(PreparationJob.objects.filter(batch=batch, status="queued").exists())

    def test_reusable_without_file_is_a_normal_catalog_only_path(self):
        response = self.client.post("/intake/", {"action": "add_reusable",
            "originating_station": self.station.pk})
        draft = MediaIntakeDraft.objects.get()
        self.assertEqual(response.status_code, 302)
        response = self.client.post(f"/intake/{draft.token}/", {"action": "resolve",
            "revision": draft.revision, "originating_station": self.station.pk,
            "kind_1": "reusable", "asset_name_1": "Station ID", "target_id_1": ""})
        self.assertEqual(response.status_code, 302)
        draft.refresh_from_db()
        self.assertEqual(self.confirm(draft).status_code, 302)
        asset = MediaAsset.objects.get()
        self.assertIsNone(asset.episode)
        self.assertEqual(asset.file_name, "Station ID")
        self.assertEqual(asset.local_path, "")

    def test_reusable_file_can_enter_the_same_real_preparation_queue(self):
        target = Device.objects.create(station=self.station, name="Reusable target")
        UltraNexusTargetSettings.objects.create(target=target, is_current=True, host="controller")
        draft = self.inspect(self.source("station-id.mp4"))
        response = self.client.post(f"/intake/{draft.token}/", {"action": "resolve",
            "revision": draft.revision, "originating_station": self.station.pk,
            "kind_1": "reusable", "asset_name_1": "Station ID", "target_id_1": target.pk,
            "encoding_mode_1": "already_encoded"})
        self.assertEqual(response.status_code, 302)
        draft.refresh_from_db(); self.assertEqual(self.confirm(draft).status_code, 302)
        asset = MediaAsset.objects.get()
        self.assertIsNone(asset.episode)
        batch = PreparationBatch.objects.get()
        self.assertEqual(batch.items.get().asset, asset)
        self.assertTrue(PreparationJob.objects.filter(batch=batch, status="queued").exists())

    def test_duplicate_requires_explicit_choice_and_corrected_version_preserves_prior(self):
        path = self.source("correction.mp4")
        target = Device.objects.create(station=self.station, name="Correction target")
        UltraNexusTargetSettings.objects.create(target=target, is_current=True, host="controller")
        episode = Episode.objects.create(show=self.show, title="Existing")
        old = MediaAsset.objects.create(station=self.station, episode=episode, file_name=path.name,
                                        kind="source", version="v1", local_path=str(path))
        future = Occurrence.objects.create(station=self.station, show=self.show, episode=episode,
            item_type="episode", label=self.show.title,
            starts_at=datetime(2026, 11, 2, 7, tzinfo=ZoneInfo("America/Detroit")), planned_duration_seconds=1800)
        draft = self.inspect(path)
        response = self.resolve(draft, episode_id_1=episode.pk, episode_title_1=episode.title)
        self.assertEqual(response.status_code, 400)
        draft.refresh_from_db()
        response = self.resolve(draft, episode_id_1=episode.pk, episode_title_1=episode.title,
                                duplicate_action_1="new_version", target_id_1=target.pk)
        self.assertEqual(response.status_code, 302)
        draft.refresh_from_db()
        preview = self.client.get(f"/intake/{draft.token}/")
        self.assertContains(preview, "Future plays affected by a corrected version")
        self.assertContains(preview, future.starts_at.isoformat())
        self.assertContains(preview, f"/occurrences/{future.pk}/")
        self.assertEqual(self.confirm(draft).status_code, 302)
        self.assertTrue(MediaAsset.objects.filter(pk=old.pk).exists())
        self.assertEqual(list(MediaAsset.objects.filter(episode=episode).values_list("version", flat=True)), ["v1", "v2"])
        draft.refresh_from_db()
        self.assertIn(future.pk, draft.payload["results"][0]["republication_occurrence_ids"])
        complete = self.client.get(f"/intake/{draft.token}/")
        self.assertContains(complete, f"/occurrences/{future.pk}/")
        self.assertContains(complete, "prepare this version first")

    def test_episode_match_and_repeated_file_require_explicit_existing_record(self):
        path = self.source("already-known.mp4")
        episode = Episode.objects.create(show=self.show, title="Known episode")
        MediaAsset.objects.create(station=self.station, episode=episode, file_name=path.name,
                                  kind="source", version="v1", local_path=str(path))
        draft = self.inspect(path)
        blank = self.resolve(draft, episode_id_1="")
        self.assertEqual(blank.status_code, 400)
        draft.refresh_from_db()
        repeated_as_new = self.resolve(draft, episode_id_1="new")
        self.assertEqual(repeated_as_new.status_code, 400)
        self.assertContains(repeated_as_new, "already catalogued for Known episode", status_code=400)
        self.assertEqual(Episode.objects.count(), 1)

    def test_stale_confirmation_and_cross_station_resume_are_rejected(self):
        draft = self.inspect(); self.assertEqual(self.resolve(draft).status_code, 302)
        draft.refresh_from_db()
        stale = draft.revision - 1
        response = self.confirm(draft, revision=stale)
        self.assertEqual(response.status_code, 400)
        self.assertFalse(Episode.objects.exists())
        session = self.client.session; session[SESSION_KEY] = self.other.pk; session.save()
        self.assertEqual(self.client.get(f"/intake/{draft.token}/").status_code, 404)

    def test_stale_metadata_post_cannot_overwrite_newer_draft_payload(self):
        draft = self.inspect()
        stale_revision = draft.revision
        payload = draft.payload
        payload["newer_tab_marker"] = "keep"
        draft.payload = payload; draft.revision += 1
        draft.save(update_fields=["payload", "revision", "updated_at"])
        response = self.resolve(draft, revision=stale_revision, episode_title_1="Stale overwrite")
        self.assertEqual(response.status_code, 400)
        draft.refresh_from_db()
        self.assertEqual(draft.payload["newer_tab_marker"], "keep")
        self.assertNotEqual(draft.payload["rows"][0].get("metadata", {}).get("episode_title"), "Stale overwrite")

    def test_archived_episode_is_excluded_and_cannot_confirm_from_old_review(self):
        episode = Episode.objects.create(show=self.show, title="Archive after review")
        draft = self.inspect(self.source("archive.mp4"))
        self.assertEqual(self.resolve(draft, episode_id_1=episode.pk,
                                     episode_title_1=episode.title).status_code, 302)
        draft.refresh_from_db()
        episode.archived_at = datetime.now(tz=ZoneInfo("America/Detroit"))
        episode.save(update_fields=["archived_at"])
        response = self.confirm(draft)
        self.assertEqual(response.status_code, 400)
        self.assertContains(response, "archived or moved", status_code=400)
        self.assertFalse(MediaAsset.objects.exists())

    def test_occurrence_workbench_uses_normal_review_actions_and_keeps_manual_evidence_in_admin(self):
        episode = Episode.objects.create(show=self.show, title="Workbench episode")
        occurrence = Occurrence.objects.create(station=self.station, show=self.show, episode=episode,
            item_type="episode", label=self.show.title,
            starts_at=datetime(2026, 11, 9, 7, tzinfo=ZoneInfo("America/Detroit")),
            planned_duration_seconds=1800)
        response = self.client.get(f"/occurrences/{occurrence.pk}/")
        self.assertContains(response, f"/intake/?show={self.show.pk}&amp;episode={episode.pk}")
        self.assertContains(response, "Review publication")
        self.assertContains(response, "Change play")
        self.assertContains(response, "Open episode")
        self.assertContains(response, "Administration: manual evidence and historical records")
        content = response.content.decode()
        self.assertLess(
            content.index("Administration: manual evidence and historical records"),
            content.index("Record device programming"),
        )

    def test_reusable_library_has_relink_review_and_canonical_intake_action(self):
        path = self.source("reusable.mp4")
        asset = MediaAsset.objects.create(station=self.station, file_name=path.name,
            kind="source", version="v1", local_path=str(path))
        response = self.client.get("/library/?view=reusable")
        self.assertContains(response, f'action="/media/assets/{asset.pk}/relink/"')
        self.assertContains(response, f'href="/intake/?asset={asset.pk}"')
        self.assertContains(response, "Matching bytes retain this reusable version")
