"""Record a private AME/FFmpeg comparison without declaring equivalence."""
import json
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from pubtv.ultranexus.qualification import EvidenceFile, QualificationPair, QualificationWorkspace


class Command(BaseCommand):
    help = "Record an experimental, exact-hash AME/FFmpeg Nexus Mono comparison."

    def add_arguments(self, parser):
        parser.add_argument("case_id")
        parser.add_argument("source")
        parser.add_argument("ame_output")
        parser.add_argument("ffmpeg_output")
        parser.add_argument("preset")
        parser.add_argument("ffmpeg_executable")
        parser.add_argument("ame_probe_json")
        parser.add_argument("ffmpeg_probe_json")
        parser.add_argument("--workspace", default=None)

    def handle(self, *args, **options):
        try:
            preset = EvidenceFile.capture(options["preset"])
            ffmpeg = EvidenceFile.capture(options["ffmpeg_executable"])
            ame_probe = json.loads(Path(options["ame_probe_json"]).read_text(encoding="utf-8"))
            ffmpeg_probe = json.loads(Path(options["ffmpeg_probe_json"]).read_text(encoding="utf-8"))
            pair = QualificationPair.create(
                options["case_id"], options["source"], options["ame_output"],
                options["ffmpeg_output"], preset_identity=preset.sha256,
                ffmpeg_build_identity=ffmpeg.sha256,
                ame_probe=ame_probe, ffmpeg_probe=ffmpeg_probe,
            )
            workspace = QualificationWorkspace(options["workspace"] or
                                               Path(settings.DATA_DIR) / "ultranexus" / "qualification")
            path = workspace.add_pair(pair)
        except (OSError, ValueError, KeyError, TypeError) as exc:
            raise CommandError(f"Qualification evidence could not be recorded: {type(exc).__name__}") from exc
        self.stdout.write(f"Experimental comparison recorded: {path}")
