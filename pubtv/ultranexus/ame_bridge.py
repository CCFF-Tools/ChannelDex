"""Filesystem bridge for the supported Adobe Media Encoder UXP API."""
from __future__ import annotations

from dataclasses import dataclass
import json
import os
from pathlib import Path
import shutil
import time
import uuid


PLUGIN_NAME = "channeldex-ame-uxp"


def bridge_root(data_dir) -> Path:
    return Path(data_dir) / "ame-bridge"


def prepare_bridge(data_dir) -> Path:
    root = bridge_root(data_dir)
    for name in ("requests", "results"):
        (root / name).mkdir(parents=True, exist_ok=True, mode=0o700)
    return root


def install_plugin(data_dir) -> Path:
    """Install the bundled panel at a stable user-data path for Adobe UDT."""
    source = Path(__file__).resolve().parents[1] / "integrations" / PLUGIN_NAME
    destination = Path(data_dir) / "integrations" / PLUGIN_NAME
    if source.is_dir():
        destination.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        shutil.copytree(source, destination, dirs_exist_ok=True)
    prepare_bridge(data_dir)
    return destination / "manifest.json"


def plugin_manifest_path(data_dir) -> Path:
    installed = Path(data_dir) / "integrations" / PLUGIN_NAME / "manifest.json"
    if installed.is_file():
        return installed
    return Path(__file__).resolve().parents[1] / "integrations" / PLUGIN_NAME / "manifest.json"


@dataclass(frozen=True)
class AMERenderResult:
    returncode: int
    stdout: str = ""
    stderr: str = ""
    details: dict | None = None


class AMEBridge:
    """Submit one encode to the ChannelDex UXP panel and await its final state."""

    def __init__(self, data_dir, *, heartbeat_max_age=10, timeout=12 * 60 * 60,
                 clock=time.time, sleeper=time.sleep):
        self.root = prepare_bridge(data_dir)
        self.heartbeat_max_age = heartbeat_max_age
        self.timeout = timeout
        self.clock = clock
        self.sleeper = sleeper

    @property
    def status_path(self) -> Path:
        return self.root / "status.json"

    def status(self) -> dict:
        try:
            status = json.loads(self.status_path.read_text(encoding="utf-8"))
            age = self.clock() - self.status_path.stat().st_mtime
        except (OSError, ValueError, TypeError):
            return {"ready": False, "detail": "ChannelDex AME panel is not connected."}
        ready = bool(status.get("ready") and status.get("api") == "RenderQueue.renderFile"
                     and age <= self.heartbeat_max_age)
        return {**status, "ready": ready, "age_seconds": max(0, age)}

    def available(self) -> bool:
        return bool(self.status().get("ready"))

    def render(self, source, preset, output, *, source_sha256="", preset_sha256="") -> AMERenderResult:
        if not self.available():
            return AMERenderResult(1, stderr="ChannelDex AME panel is not connected.")
        job_id = uuid.uuid4().hex
        request_path = self.root / "requests" / f"{job_id}.json"
        result_path = self.root / "results" / f"{job_id}.json"
        payload = {
            "version": 1,
            "id": job_id,
            "source": str(Path(source).resolve()),
            "preset": str(Path(preset).resolve()),
            "output": str(Path(output).resolve()),
            "source_sha256": source_sha256,
            "preset_sha256": preset_sha256,
            "created_at": self.clock(),
            "expires_at": self.clock() + self.timeout,
        }
        temporary = request_path.with_suffix(".tmp")
        temporary.write_text(json.dumps(payload, sort_keys=True), encoding="utf-8")
        os.replace(temporary, request_path)
        deadline = self.clock() + self.timeout
        last = None
        while self.clock() < deadline:
            try:
                last = json.loads(result_path.read_text(encoding="utf-8"))
            except FileNotFoundError:
                last = None
            except (OSError, ValueError, TypeError):
                last = None
            if (last and last.get("version") == 1 and last.get("id") == job_id
                    and last.get("state") in {"succeeded", "failed"}):
                request_path.unlink(missing_ok=True)
                result_path.unlink(missing_ok=True)
                if last["state"] == "succeeded":
                    return AMERenderResult(0, stdout=json.dumps(last, sort_keys=True), details=last)
                return AMERenderResult(1, stderr=str(last.get("error") or "Adobe Media Encoder failed"), details=last)
            self.sleeper(1)
        request_path.unlink(missing_ok=True)
        result_path.unlink(missing_ok=True)
        return AMERenderResult(1, stderr="Adobe Media Encoder did not finish before the ChannelDex timeout.", details=last)
