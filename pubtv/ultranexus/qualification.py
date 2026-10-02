"""Offline evidence pairing for experimental AME/FFmpeg qualification.

The workspace stores references and hashes, never media bytes. It is an aid to
owner review; a recorded comparison does not qualify FFmpeg automatically.
"""
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Any

from .media import parse_ffprobe_json
from .encoding import FFmpegEncoder


def _hash_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


@dataclass(frozen=True)
class EvidenceFile:
    path: str
    sha256: str
    size: int

    @classmethod
    def capture(cls, path: str | Path) -> "EvidenceFile":
        candidate = Path(path).expanduser()
        if not candidate.is_file():
            raise FileNotFoundError(candidate)
        return cls(str(candidate), _hash_file(candidate), candidate.stat().st_size)


@dataclass(frozen=True)
class QualificationPair:
    case_id: str
    source: EvidenceFile
    ame_output: EvidenceFile
    ffmpeg_output: EvidenceFile
    preset_identity: str
    ffmpeg_build_identity: str
    ame_probe: dict[str, Any]
    ffmpeg_probe: dict[str, Any]
    comparison_report: dict[str, Any]
    status: str = "experimental"
    owner_review: str = "pending"

    @classmethod
    def create(cls, case_id: str, source: str | Path, ame_output: str | Path,
               ffmpeg_output: str | Path, *, preset_identity: str,
               ffmpeg_build_identity: str, ame_probe: str | bytes | dict,
               ffmpeg_probe: str | bytes | dict,
               comparison_report: dict[str, Any] | None = None) -> "QualificationPair":
        if not case_id.strip() or not preset_identity.strip() or not ffmpeg_build_identity.strip():
            raise ValueError("case, preset, and FFmpeg build identities are required")
        ame = parse_ffprobe_json(ame_probe)
        ffmpeg = parse_ffprobe_json(ffmpeg_probe)
        report = comparison_report or compare_probes(ame, ffmpeg)
        return cls(case_id, EvidenceFile.capture(source), EvidenceFile.capture(ame_output),
                   EvidenceFile.capture(ffmpeg_output), preset_identity, ffmpeg_build_identity,
                   ame.raw, ffmpeg.raw, report)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def compare_probes(ame_probe, ffmpeg_probe) -> dict[str, Any]:
    """Compare observable technical fields; unknown processing remains open."""
    fields = ("duration", "width", "height", "video_codec", "audio_codec", "nominal_frames")
    left = {field: str(getattr(ame_probe, field)) for field in fields}
    right = {field: str(getattr(ffmpeg_probe, field)) for field in fields}
    return {
        "status": "comparable" if left == right else "differences_found",
        "ame": left, "ffmpeg": right,
        "differences": {key: (left[key], right[key]) for key in fields if left[key] != right[key]},
        "unmeasured": ["limiter", "loudness_equivalence", "legalization", "controller_playback"],
        "qualification": "experimental",
    }


class QualificationWorkspace:
    """A deterministic JSON manifest for owner-supplied qualification evidence."""
    manifest_name = "ffmpeg-qualification.json"

    def __init__(self, directory: str | Path):
        self.directory = Path(directory).expanduser()
        self.manifest_path = self.directory / self.manifest_name

    def _write(self, data):
        self.directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        temporary = None
        try:
            with NamedTemporaryFile("w", encoding="utf-8", dir=self.directory,
                                    prefix=".ffmpeg-qualification-", delete=False) as handle:
                temporary = Path(handle.name)
                os.chmod(temporary, 0o600)
                json.dump(data, handle, indent=2, sort_keys=True)
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, self.manifest_path)
        finally:
            if temporary and temporary.exists():
                temporary.unlink()
        return self.manifest_path

    def add_pair(self, pair: QualificationPair) -> Path:
        self.directory.mkdir(parents=True, exist_ok=True)
        data = self.read()
        pairs = {entry["case_id"]: entry for entry in data["pairs"]}
        if pair.case_id in pairs and pairs[pair.case_id] != pair.to_dict():
            raise ValueError(f"qualification case already exists: {pair.case_id}")
        pairs[pair.case_id] = pair.to_dict()
        data["pairs"] = [pairs[key] for key in sorted(pairs)]
        data["updated_at"] = datetime.now(timezone.utc).isoformat()
        return self._write(data)

    def read(self) -> dict[str, Any]:
        if not self.manifest_path.exists():
            return {"schema": 1, "status": "experimental", "pairs": []}
        return json.loads(self.manifest_path.read_text(encoding="utf-8"))

    def owner_review(self, case_id: str, decision: str, note: str = "") -> Path:
        if decision not in {"pending", "accepted", "rejected"}:
            raise ValueError("owner decision must be pending, accepted, or rejected")
        data = self.read()
        for pair in data["pairs"]:
            if pair["case_id"] == case_id:
                pair["owner_review"] = decision
                pair["owner_note"] = note
                break
        else:
            raise KeyError(case_id)
        data["updated_at"] = datetime.now(timezone.utc).isoformat()
        return self._write(data)


def build_ffmpeg_command(source: str, output: str, *, video_stream=0, audio_stream=0,
                         ffmpeg_executable="ffmpeg"):
    return FFmpegEncoder(ffmpeg_executable, video_stream=video_stream,
                         audio_stream=audio_stream).command(source, output)
