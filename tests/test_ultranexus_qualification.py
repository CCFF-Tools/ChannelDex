import json
import tempfile
import unittest
from pathlib import Path

from pubtv.ultranexus.encoding import FFmpegEncoder
from pubtv.ultranexus.qualification import (QualificationPair, QualificationWorkspace,
                                            REQUIRED_CASE_CHECKS,
                                            validate_qualification_manifest)


PROBE = {"streams": [{"codec_type": "video", "codec_name": "h264", "width": 1920,
                       "height": 1080, "duration": "2", "profile": "Main",
                       "level": "4.1", "r_frame_rate": "30000/1001"},
                      {"codec_type": "audio", "codec_name": "aac", "sample_rate": "48000",
                       "channels": 1}], "format": {"duration": "2"}}


class QualificationTests(unittest.TestCase):
    def report(self, *checks):
        return {"status": "comparable", "differences": {}, "unmeasured": [],
                "qualification": "experimental",
                "checks": {check: "passed" for check in checks}}

    def test_command_maps_selected_streams(self):
        encoder = FFmpegEncoder(video_stream=1, audio_stream=2)
        argv = encoder.command("in", "out").argv
        self.assertIn("0:v:1", argv)
        self.assertIn("0:a:2", argv)
        video, audio = encoder.analysis_commands("encoded.mp4")
        self.assertIn("signalstats,metadata=print", video.argv)
        self.assertIn("0:v:1", video.argv)
        self.assertIn("ebur128=peak=true", audio.argv)
        self.assertIn("0:a:2", audio.argv)

    def test_pairs_hashes_and_stays_experimental_until_review(self):
        with tempfile.TemporaryDirectory() as directory:
            paths = [Path(directory) / name for name in ("source", "ame", "ffmpeg")]
            for path in paths:
                path.write_bytes(path.name.encode())
            pair = QualificationPair.create("case-1", *paths, preset_identity="preset-v2",
                                            ffmpeg_build_identity="ffmpeg 7", ame_probe=PROBE,
                                            ffmpeg_probe=json.dumps(PROBE), case_kind="geometry",
                                            ffmpeg_profile_identity="profile-v1",
                                            comparison_report=self.report("geometry"))
            workspace = QualificationWorkspace(Path(directory) / "evidence")
            manifest = workspace.add_pair(pair)
            data = json.loads(manifest.read_text())
            self.assertEqual(data["status"], "experimental")
            self.assertEqual(data["pairs"][0]["owner_review"], "pending")
            self.assertEqual(len(data["pairs"][0]["source"]["sha256"]), 64)
            workspace.owner_review("case-1", "accepted", "owner compared samples")
            self.assertEqual(workspace.read()["pairs"][0]["owner_review"], "accepted")

    def test_duplicate_case_cannot_be_replaced(self):
        with tempfile.TemporaryDirectory() as directory:
            paths = [Path(directory) / name for name in ("source", "ame", "ffmpeg")]
            for path in paths: path.write_bytes(path.name.encode())
            pair = QualificationPair.create("case", *paths, preset_identity="p", ffmpeg_build_identity="f",
                                            ffmpeg_profile_identity="profile", case_kind="geometry",
                                            comparison_report=self.report("geometry"),
                                            ame_probe=PROBE, ffmpeg_probe=PROBE)
            workspace = QualificationWorkspace(Path(directory) / "evidence")
            workspace.add_pair(pair)
            changed = QualificationPair.create("case", *paths, preset_identity="other", ffmpeg_build_identity="f",
                                               ffmpeg_profile_identity="profile", case_kind="geometry",
                                               comparison_report=self.report("geometry"),
                                               ame_probe=PROBE, ffmpeg_probe=PROBE)
            with self.assertRaises(ValueError): workspace.add_pair(changed)

    def test_manifest_requires_complete_measured_matrix(self):
        pairs = []
        for kind, checks in REQUIRED_CASE_CHECKS.items():
            pairs.append({"case_id": kind, "case_kind": kind, "owner_review": "accepted",
                          "ffmpeg_build_identity": "b", "preset_identity": "p",
                          "ffmpeg_profile_identity": "f",
                          "comparison_report": self.report(*checks)})
        self.assertEqual(validate_qualification_manifest(
            {"schema": 2, "pairs": pairs}, build_hash="b", preset_hash="p", profile_hash="f"), [])
        pairs[0]["comparison_report"]["unmeasured"] = ["video_levels"]
        self.assertTrue(validate_qualification_manifest(
            {"schema": 2, "pairs": pairs}, build_hash="b", preset_hash="p", profile_hash="f"))
