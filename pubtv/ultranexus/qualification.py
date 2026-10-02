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


REQUIRED_CASE_CHECKS = {
    "geometry": {"geometry"},
    "frame_rate": {"frame_rate"},
    "audio": {"audio_layout", "loudness"},
    "illegal_levels": {"video_levels", "limiter"},
    "fractional_duration": {"duration"},
    "loudness_limiter": {"loudness", "limiter"},
    "controller_playback": {"controller_playback"},
}


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
    case_kind: str
    source: EvidenceFile
    ame_output: EvidenceFile
    ffmpeg_output: EvidenceFile
    preset_identity: str
    ffmpeg_build_identity: str
    ffmpeg_profile_identity: str
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
               case_kind: str = "",
               ffmpeg_profile_identity: str = "",
               comparison_report: dict[str, Any] | None = None) -> "QualificationPair":
        if case_kind not in REQUIRED_CASE_CHECKS:
            raise ValueError("case kind is not part of the required qualification matrix")
        if not all(value.strip() for value in (case_id, preset_identity, ffmpeg_build_identity,
                                               ffmpeg_profile_identity)):
            raise ValueError("case, preset, FFmpeg build, and profile identities are required")
        ame = parse_ffprobe_json(ame_probe)
        ffmpeg = parse_ffprobe_json(ffmpeg_probe)
        report = comparison_report or compare_probes(ame, ffmpeg)
        return cls(case_id, case_kind, EvidenceFile.capture(source), EvidenceFile.capture(ame_output),
                   EvidenceFile.capture(ffmpeg_output), preset_identity, ffmpeg_build_identity,
                   ffmpeg_profile_identity,
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
        "checks": {},
    }


def pair_acceptance_blockers(pair: dict[str, Any]) -> list[str]:
    blockers = []
    kind = pair.get("case_kind")
    if kind not in REQUIRED_CASE_CHECKS:
        return ["unknown qualification case kind"]
    report = pair.get("comparison_report") or {}
    if report.get("status") != "comparable" or report.get("differences"):
        blockers.append("AME/FFmpeg observable results differ")
    if report.get("unmeasured"):
        blockers.append("comparison retains unmeasured requirements")
    checks = report.get("checks") or {}
    missing = sorted(name for name in REQUIRED_CASE_CHECKS[kind] if checks.get(name) != "passed")
    if missing:
        blockers.append("required checks are not passed: " + ", ".join(missing))
    return blockers


def validate_qualification_manifest(data: dict[str, Any], *, build_hash: str,
                                    preset_hash: str, profile_hash: str) -> list[str]:
    blockers = []
    if data.get("schema") != 2:
        blockers.append("qualification manifest schema 2 is required")
    pairs = data.get("pairs")
    if not isinstance(pairs, list) or not pairs:
        return blockers + ["qualification comparisons are required"]
    kinds = {pair.get("case_kind") for pair in pairs}
    missing_kinds = sorted(set(REQUIRED_CASE_CHECKS) - kinds)
    if missing_kinds:
        blockers.append("required fixture cases are missing: " + ", ".join(missing_kinds))
    for pair in pairs:
        case_id = pair.get("case_id") or "unnamed"
        if pair.get("owner_review") != "accepted":
            blockers.append(f"{case_id}: owner review is not accepted")
        if pair.get("ffmpeg_build_identity") != build_hash:
            blockers.append(f"{case_id}: FFmpeg build identity differs")
        if pair.get("preset_identity") != preset_hash:
            blockers.append(f"{case_id}: AME preset identity differs")
        if pair.get("ffmpeg_profile_identity") != profile_hash:
            blockers.append(f"{case_id}: FFmpeg argument profile differs")
        blockers.extend(f"{case_id}: {message}" for message in pair_acceptance_blockers(pair))
    return blockers


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
            return {"schema": 2, "status": "experimental", "pairs": []}
        return json.loads(self.manifest_path.read_text(encoding="utf-8"))

    def owner_review(self, case_id: str, decision: str, note: str = "") -> Path:
        if decision not in {"pending", "accepted", "rejected"}:
            raise ValueError("owner decision must be pending, accepted, or rejected")
        data = self.read()
        for pair in data["pairs"]:
            if pair["case_id"] == case_id:
                if decision == "accepted":
                    blockers = pair_acceptance_blockers(pair)
                    if blockers:
                        raise ValueError("comparison cannot be accepted: " + "; ".join(blockers))
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
