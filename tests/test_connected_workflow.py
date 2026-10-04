from datetime import timedelta, time
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
from zoneinfo import ZoneInfo

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client, TestCase, override_settings
from django.utils import timezone

from pubtv.operations.media_queue import confirm_intake_review
from pubtv.operations.models import (
    Device, Episode, MediaIntakeReview, PreparationBatch, PreparationJob,
    RecurrenceSlot, Show, Station, UltraNexusTargetSettings,
    WeeklyEpisodeAssignment,
)


class ConnectedWorkflowTests(TestCase):
    def setUp(self):
        self.data_dir = TemporaryDirectory()
        self.settings = override_settings(DATA_DIR=Path(self.data_dir.name))
        self.settings.enable()
        self.station = Station.objects.create(name="PUB-TV", timezone="America/Detroit")
        self.show = Show.objects.create(station=self.station, title="Civic", code="civic")
        self.target = Device.objects.create(name="Nexus")
        UltraNexusTargetSettings.objects.create(target=self.target, is_current=True, host="controller")
        RecurrenceSlot.objects.create(
            station=self.station, show=self.show, weekday=0, start_time=time(19),
            duration_seconds=1800, is_premiere=True,
        )
        RecurrenceSlot.objects.create(
            station=self.station, show=self.show, weekday=2, start_time=time(19),
            duration_seconds=1800, is_premiere=False,
        )

    def tearDown(self):
        self.settings.disable()
        self.data_dir.cleanup()

    def _first_monday(self):
        today = timezone.localdate(timezone=ZoneInfo("America/Detroit"))
        return today + timedelta(days=(7 - today.weekday()) % 7)

    def _review(self, action="prepare_and_plan", files=1):
        uploads = [SimpleUploadedFile(f"episode-{index + 1}.mp4", b"video" + bytes([index])) for index in range(files)]
        data = {
            "action": "review_intake", "next_action": action,
            "show": self.show.pk, "target": self.target.pk,
            "source_files": uploads, "encode_before_transfer": "on",
        }
        for index in range(files):
            data[f"episode_id_{index}"] = "new"
            data[f"new_title_{index}"] = f"Episode {index + 1}"
        response = Client().post("/media/", data)
        self.assertEqual(response.status_code, 200)
        return MediaIntakeReview.objects.get()

    def test_review_stages_files_without_starting_work_then_confirms_once(self):
        review = self._review(files=2)
        self.assertFalse(PreparationBatch.objects.exists())
        self.assertFalse(PreparationJob.objects.exists())
        self.assertEqual([row["position"] for row in review.payload["rows"]], [0, 1])
        self.assertTrue(all(Path(row["path"]).is_file() for row in review.payload["rows"]))

        first_date = self._first_monday().isoformat()
        response = Client().post("/media/", {
            "action": "confirm_intake", "review_token": review.token,
            "first_premiere_date": first_date,
        })
        self.assertEqual(response.status_code, 302)
        self.assertTrue(response.url.startswith("/schedule/prepare/?target="))
        self.assertEqual(PreparationBatch.objects.count(), 1)
        self.assertEqual(PreparationJob.objects.count(), 1)
        assignments = list(WeeklyEpisodeAssignment.objects.order_by("week_start"))
        self.assertEqual([item.episode.title for item in assignments], ["Episode 1", "Episode 2"])
        self.assertTrue(all(item.occurrences.count() == 2 for item in assignments))

        repeated = Client().post("/media/", {
            "action": "confirm_intake", "review_token": review.token,
        })
        self.assertEqual(repeated.status_code, 302)
        self.assertEqual(PreparationBatch.objects.count(), 1)
        self.assertEqual(PreparationJob.objects.count(), 1)
        self.assertEqual(WeeklyEpisodeAssignment.objects.count(), 2)

    def test_prepare_only_does_not_create_schedule_cycles(self):
        review = self._review(action="prepare_only")
        response = Client().post("/media/", {"action": "confirm_intake", "review_token": review.token})
        self.assertRedirects(response, "/media/")
        self.assertEqual(PreparationJob.objects.count(), 1)
        self.assertFalse(WeeklyEpisodeAssignment.objects.exists())

    def test_changed_staged_file_rejects_confirmation_atomically(self):
        review = self._review(action="prepare_only")
        Path(review.payload["rows"][0]["path"]).write_bytes(b"tampered")
        with self.assertRaisesMessage(ValueError, "changed or is unavailable"):
            confirm_intake_review(review)
        self.assertFalse(Episode.objects.exists())
        self.assertFalse(PreparationBatch.objects.exists())
        self.assertFalse(PreparationJob.objects.exists())

    def test_changed_recurrence_rejects_reviewed_plan_atomically(self):
        review = self._review()
        self.assertTrue(review.payload["premiere_proposals"])
        slot = self.show.slots.get(is_premiere=False)
        slot.start_time = time(20)
        slot.save()

        first_date = next(iter(review.payload["premiere_proposals"]))
        response = Client().post("/media/", {
            "action": "confirm_intake", "review_token": review.token,
            "first_premiere_date": first_date,
        })

        self.assertEqual(response.status_code, 400)
        self.assertContains(response, "changed after review", status_code=400)
        self.assertFalse(PreparationBatch.objects.exists())
        self.assertFalse(PreparationJob.objects.exists())
        self.assertFalse(WeeklyEpisodeAssignment.objects.exists())

    def test_invalid_review_does_not_leave_pending_review(self):
        response = Client().post("/media/", {
            "action": "review_intake", "next_action": "prepare_only",
            "show": self.show.pk, "target": self.target.pk,
            "source_files": [SimpleUploadedFile("notes.txt", b"not video")],
            "episode_id_0": "new", "new_title_0": "Invalid",
        })

        self.assertEqual(response.status_code, 400)
        self.assertContains(response, "unsupported video file type", status_code=400)
        self.assertFalse(MediaIntakeReview.objects.exists())
        self.assertFalse(PreparationJob.objects.exists())

    def test_show_context_prefills_the_media_intake(self):
        response = Client().get(f"/media/?show={self.show.pk}")
        self.assertContains(response, f'value="{self.show.pk}" selected')
        self.assertContains(response, f'value="{self.target.pk}" selected')

    def test_review_with_missing_active_premiere_slot_returns_validation_page(self):
        candidate_date = self._first_monday()
        with patch("pubtv.operations.premiere_planning._active_premiere_dates", return_value=[candidate_date + timedelta(days=1)]):
            response = Client().post("/media/", {
                "action": "review_intake", "next_action": "prepare_and_plan",
                "show": self.show.pk, "target": self.target.pk,
                "source_files": [SimpleUploadedFile("episode.mp4", b"video")],
                "episode_id_0": "new", "new_title_0": "Missing slot",
            })
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "No active premiere slot is configured")
