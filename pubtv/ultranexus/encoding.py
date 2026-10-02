from dataclasses import dataclass
from pathlib import Path
import hashlib
import os
import json
import re
import shutil
import glob
from .exceptions import CapabilityError

@dataclass(frozen=True)
class EncodeCommand:
    argv: tuple[str, ...]
    output: str

class Encoder:
    def command(self, source: str, output: str) -> EncodeCommand: raise NotImplementedError

class AdobeMediaEncoder(Encoder):
    def __init__(self, executable=None, preset=None):
        self.executable = executable or os.environ.get("AME_EXECUTABLE", "Adobe Media Encoder")
        self.preset = preset or os.environ.get("AME_PRESET")
    def command(self, source, output):
        if not self.preset: raise ValueError("AME preset is required")
        raise CapabilityError("AME execution is unavailable; use extend_script() with the reviewed console bridge")
    def extend_script(self, source, output):
        # Deterministic, safely quoted bridge payload; execution remains explicit.
        s, d, p = (json.dumps(str(v), ensure_ascii=True) for v in (source, output, self.preset))
        return (f"var exporter = app.getExporter();\n"
                f"var item = exporter.exportItem({s}, {d}, {p});\n"
                "item.onEncodeFinished = function() { app.quit(); };\n"
                "item.onError = function(error) { $.writeln(String(error)); app.quit(); };\n")
    def bridge_command(self, script_path):
        return (self.executable, "--console", "es.processFile", script_path)

def discover_ame(candidates=()):
    paths = tuple(candidates) or tuple(
        sorted(glob.glob("/Applications/Adobe Media Encoder*.app/Contents/MacOS/Adobe Media Encoder"), reverse=True)
    )
    return next((str(p) for p in paths if Path(p).is_file()), None)


def discover_ffmpeg(candidates=()):
    """Find FFmpeg in PATH and common macOS package locations."""
    paths = []
    if candidates:
        paths.extend(str(p) for p in candidates)
    found = shutil.which("ffmpeg")
    if found:
        paths.append(found)
    paths.extend(("/opt/homebrew/bin/ffmpeg", "/usr/local/bin/ffmpeg", "/usr/bin/ffmpeg"))
    return next((str(p) for p in paths if Path(p).is_file()), None)


def discover_executables(*, ame_candidates=(), ffmpeg_candidates=()):
    return {"ame": discover_ame(ame_candidates), "ffmpeg": discover_ffmpeg(ffmpeg_candidates)}

class FFmpegEncoder(Encoder):
    def __init__(self, executable="ffmpeg", *, video_bitrate="6500000", audio_bitrate="256000",
                 video_stream=0, audio_stream=0):
        self.executable, self.video_bitrate, self.audio_bitrate = executable, video_bitrate, audio_bitrate
        self.video_stream, self.audio_stream = int(video_stream), int(audio_stream)
        if self.video_stream < 0 or self.audio_stream < 0:
            raise ValueError("stream indexes must be non-negative")
    def command(self, source, output):
        # Explicit maps prevent a commentary, alternate-angle, or attached
        # picture stream from becoming the deliverable by ffmpeg's heuristics.
        argv = (self.executable, "-n", "-i", source, "-map", f"0:v:{self.video_stream}",
                "-map", f"0:a:{self.audio_stream}", "-vf", "scale=1920:1080:force_original_aspect_ratio=decrease,pad=1920:1080:(ow-iw)/2:(oh-ih)/2,fps=30000/1001,setsar=1", "-c:v", "libx264", "-profile:v", "main", "-level:v", "4.1", "-pix_fmt", "yuv420p", "-colorspace", "bt709", "-color_primaries", "bt709", "-color_trc", "bt709", "-color_range", "tv", "-b:v", self.video_bitrate, "-c:a", "aac", "-ac", "1", "-ar", "48000", "-b:a", self.audio_bitrate, output)
        return EncodeCommand(argv, output)

    def profile_identity(self) -> str:
        """Hash the exact qualified argument profile independently of file paths."""
        argv = self.command("{source}", "{output}").argv[1:]
        return hashlib.sha256(json.dumps(argv, separators=(",", ":")).encode("utf-8")).hexdigest()

    def analysis_commands(self, source: str) -> tuple[EncodeCommand, EncodeCommand]:
        """Build explicit post-encode analysis commands for qualification evidence."""
        video = (self.executable, "-hide_banner", "-nostats", "-loglevel", "info", "-i", source,
                 "-map", f"0:v:{self.video_stream}", "-vf", "signalstats,metadata=print",
                 "-an", "-f", "null", "-")
        audio = (self.executable, "-hide_banner", "-nostats", "-loglevel", "info", "-i", source,
                 "-map", f"0:a:{self.audio_stream}", "-af", "ebur128=peak=true",
                 "-vn", "-f", "null", "-")
        return EncodeCommand(video, "-"), EncodeCommand(audio, "-")

    def measure(self, source: str, *, runner) -> dict:
        """Execute qualification analyzers and retain compact numeric evidence."""
        video_command, audio_command = self.analysis_commands(source)

        def execute(command):
            result = runner(command.argv, check=False, capture_output=True, text=True)
            if getattr(result, "returncode", 1) != 0:
                raise RuntimeError("FFmpeg qualification analysis failed")
            return "\n".join((getattr(result, "stdout", "") or "",
                              getattr(result, "stderr", "") or ""))

        signal_values = {}
        for name, raw in re.findall(r"lavfi\.signalstats\.([A-Za-z0-9_]+)=(-?[0-9]+(?:\.[0-9]+)?)",
                                    execute(video_command)):
            signal_values.setdefault(name, []).append(float(raw))
        if not signal_values:
            raise RuntimeError("FFmpeg signalstats output was not measurable")
        video = {name: {"minimum": min(values), "maximum": max(values),
                        "last": values[-1], "samples": len(values)}
                 for name, values in sorted(signal_values.items())}

        audio_text = execute(audio_command)
        def last_value(pattern):
            matches = re.findall(pattern, audio_text, flags=re.MULTILINE)
            return float(matches[-1]) if matches else None
        audio = {
            "integrated_lufs": last_value(r"^\s*I:\s*(-?[0-9]+(?:\.[0-9]+)?)\s+LUFS"),
            "loudness_range_lu": last_value(r"^\s*LRA:\s*([0-9]+(?:\.[0-9]+)?)\s+LU"),
            "true_peak_dbtp": last_value(r"^\s*Peak:\s*(-?[0-9]+(?:\.[0-9]+)?)\s+dBFS"),
        }
        if audio["integrated_lufs"] is None or audio["true_peak_dbtp"] is None:
            raise RuntimeError("FFmpeg ebur128 output was not measurable")
        return {"video_signalstats": video, "audio_ebur128": audio}

    def post_encode_measurements(self, probe):
        """Return bounded, observable measurements from an ffprobe result.

        This deliberately reports only values present in the probe. It does
        not infer AME limiter, loudness, or legalisation equivalence.
        """
        video = probe.video
        audio = probe.audio or {}
        return {
            "duration": str(probe.duration),
            "duration_units": probe.duration_units,
            "nominal_frames": probe.nominal_frames,
            "width": probe.width, "height": probe.height,
            "video_codec": probe.video_codec,
            "video_bitrate": video.get("bit_rate"),
            "frame_rate": video.get("avg_frame_rate") or video.get("r_frame_rate"),
            "audio_codec": probe.audio_codec,
            "audio_bitrate": audio.get("bit_rate"),
            "audio_sample_rate": audio.get("sample_rate"),
            "audio_channels": audio.get("channels"),
            "loudness": audio.get("loudness") or probe.raw.get("format", {}).get("loudness"),
            "loudness_status": "measured" if (audio.get("loudness") or probe.raw.get("format", {}).get("loudness")) is not None else "not_measured",
        }

def build_encoder_command(encoder: Encoder, source, output):
    return encoder.command(source, output).argv
