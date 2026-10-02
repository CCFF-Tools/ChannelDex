from dataclasses import dataclass
from pathlib import Path
import os
import json
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
    paths = tuple(candidates) or ("/Applications/Adobe Media Encoder.app/Contents/MacOS/Adobe Media Encoder",)
    return next((p for p in paths if Path(p).exists()), None)

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

    def post_encode_measurements(self, probe):
        """Return bounded, observable measurements from an ffprobe result.

        This deliberately reports only values present in the probe. It does
        not infer AME limiter, loudness, or legalisation equivalence.
        """
        video = probe.video
        audio = probe.audio or {}
        return {
            "duration": str(probe.duration),
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
