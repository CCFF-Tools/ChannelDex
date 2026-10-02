import json
from dataclasses import dataclass
from decimal import Decimal, ROUND_CEILING
from .exceptions import CompatibilityError, ValidationError

@dataclass(frozen=True)
class Probe:
    raw: dict
    video: dict
    audio: dict | None
    duration: Decimal
    width: int
    height: int
    video_codec: str
    audio_codec: str | None
    nominal_frames: int

    @property
    def duration_units(self) -> int:
        """Qualified nominal-30 units: ceil(probed duration seconds × 30)."""
        return self.nominal_frames

def parse_ffprobe_json(data: str | bytes | dict) -> Probe:
    try:
        raw = json.loads(data) if isinstance(data, (str, bytes)) else data
        streams = raw["streams"]
        video = next(s for s in streams if s.get("codec_type") == "video")
    except (ValueError, KeyError, TypeError, StopIteration) as exc:
        raise ValidationError("ffprobe JSON does not contain a video stream") from exc
    audio = next((s for s in streams if s.get("codec_type") == "audio"), None)
    duration_raw = video.get("duration") or raw.get("format", {}).get("duration")
    if duration_raw is None:
        raise ValidationError("ffprobe JSON has no duration")
    try:
        duration = Decimal(str(duration_raw))
        frames = int((duration * Decimal(30)).to_integral_value(rounding=ROUND_CEILING))
        width, height = int(video["width"]), int(video["height"])
    except (ValueError, ArithmeticError, KeyError) as exc:
        raise ValidationError("invalid ffprobe dimensions or duration") from exc
    return Probe(raw, video, audio, duration, width, height, str(video.get("codec_name", "")), audio and str(audio.get("codec_name", "")), frames)

def validate_nexus_mono(probe: Probe, *, require_h264=True) -> None:
    """Validate the supplied probe against the reviewed Nexus Mono profile."""
    errors = []
    if (probe.width, probe.height) != (1920, 1080): errors.append("dimensions must be 1920x1080")
    if require_h264 and probe.video_codec not in {"h264", "avc1"}: errors.append("video codec must be H.264")
    if probe.video.get("profile") not in {None, "Main"}: errors.append("video profile must be Main")
    if probe.video.get("level") not in {None, 41, "4.1", "41"}: errors.append("video level must be 4.1")
    if probe.video.get("field_order") not in {None, "progressive", "unknown"}: errors.append("video must be progressive")
    if probe.video.get("pix_fmt") not in {None, "yuv420p"}: errors.append("pixel format must be yuv420p")
    if probe.video.get("sample_aspect_ratio") not in {None, "1:1"}: errors.append("SAR must be 1:1")
    if probe.video.get("color_space") not in {None, "bt709"}: errors.append("color space must be BT.709")
    if probe.video.get("color_range") not in {None, "tv", "mpeg"}: errors.append("color range must be television/legal")
    if probe.audio and probe.audio_codec not in {"aac", "mp4a"}: errors.append("audio codec must be AAC")
    if probe.audio:
        if probe.audio.get("sample_rate") not in {None, "48000", 48000}: errors.append("audio sample rate must be 48 kHz")
        if probe.audio.get("channels") not in {None, 1}: errors.append("audio must be mono")
    if probe.video.get("avg_frame_rate") and probe.video.get("r_frame_rate") and probe.video["avg_frame_rate"] != probe.video["r_frame_rate"]:
        errors.append("variable frame rate contradiction")
    rate = probe.video.get("r_frame_rate") or probe.video.get("avg_frame_rate")
    if rate and rate not in {"30000/1001", "30/1", "30"}: errors.append("frame rate is outside nominal 30 fps profile")
    if errors: raise CompatibilityError("Nexus Mono incompatibility: " + "; ".join(errors))
