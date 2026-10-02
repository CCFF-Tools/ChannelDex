"""Record a private AME/FFmpeg comparison without declaring equivalence."""
import json
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from pubtv.ultranexus.encoding import FFmpegEncoder
from pubtv.ultranexus.qualification import (EvidenceFile, QualificationPair,
                                            QualificationWorkspace, REQUIRED_CASE_CHECKS)


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
        parser.add_argument("--case-kind", required=True, choices=sorted(REQUIRED_CASE_CHECKS))
        parser.add_argument("--comparison-report-json")
        parser.add_argument("--run-analysis", action="store_true",
                            help="Run signalstats and ebur128 on both outputs.")
        parser.add_argument("--workspace", default=None)

    def handle(self, *args, **options):
        try:
            preset = EvidenceFile.capture(options["preset"])
            ffmpeg = EvidenceFile.capture(options["ffmpeg_executable"])
            ame_probe = json.loads(Path(options["ame_probe_json"]).read_text(encoding="utf-8"))
            ffmpeg_probe = json.loads(Path(options["ffmpeg_probe_json"]).read_text(encoding="utf-8"))
            comparison_report = (json.loads(Path(options["comparison_report_json"]).read_text(encoding="utf-8"))
                                 if options.get("comparison_report_json") else None)
            encoder = FFmpegEncoder(options["ffmpeg_executable"])
            measurements = {}
            if options["run_analysis"]:
                import subprocess
                measurements = {
                    "ame": encoder.measure(options["ame_output"], runner=subprocess.run),
                    "ffmpeg": encoder.measure(options["ffmpeg_output"], runner=subprocess.run),
                }
            pair = QualificationPair.create(
                options["case_id"], options["source"], options["ame_output"],
                options["ffmpeg_output"], preset_identity=preset.sha256,
                ffmpeg_build_identity=ffmpeg.sha256,
                ffmpeg_profile_identity=encoder.profile_identity(),
                case_kind=options["case_kind"], ame_probe=ame_probe,
                ffmpeg_probe=ffmpeg_probe, comparison_report=comparison_report,
                measurements=measurements,
            )
            workspace = QualificationWorkspace(options["workspace"] or
                                               Path(settings.DATA_DIR) / "ultranexus" / "qualification")
            path = workspace.add_pair(pair)
        except (OSError, RuntimeError, ValueError, KeyError, TypeError) as exc:
            raise CommandError(f"Qualification evidence could not be recorded: {type(exc).__name__}") from exc
        self.stdout.write(f"Experimental comparison recorded: {path}")
