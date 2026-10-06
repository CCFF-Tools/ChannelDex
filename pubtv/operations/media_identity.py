"""Local byte identity and cross-process asset claims.

File IO stays outside database transactions. flock also works across the web
process and worker on SQLite, whose select_for_update does not claim rows.
"""
from contextlib import contextmanager, ExitStack
from decimal import Decimal, InvalidOperation
from pathlib import Path
import fcntl
import json
import subprocess

from django.conf import settings


@contextmanager
def asset_claim(asset_id):
    root = Path(settings.DATA_DIR) / "ultranexus" / "asset-claims"
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    with (root / f"{int(asset_id)}.lock").open("a+") as handle:
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise ValueError("This media asset has active preparation or review; try again when it finishes.") from exc
        try:
            yield
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


@contextmanager
def batch_asset_claims(batch):
    with ExitStack() as stack:
        for asset_id in sorted(set(batch.items.values_list("asset_id", flat=True))):
            stack.enter_context(asset_claim(asset_id))
        yield


def file_metadata(path):
    candidate = Path(path)
    stat = candidate.stat()
    if not candidate.is_file():
        raise ValueError("Selected path is not a regular file.")
    return stat.st_size, stat.st_mtime_ns


def measured_runtime(path, *, probe_runner=None, executable="ffprobe", runner=subprocess.run):
    """Best-effort source runtime; source need not satisfy output profile."""
    try:
        raw = probe_runner(str(path)) if probe_runner else runner(
            (executable, "-v", "error", "-show_format", "-show_streams", "-of", "json", str(path)),
            check=False, capture_output=True, text=True, timeout=30)
        if hasattr(raw, "returncode"):
            if raw.returncode:
                return None
            raw = json.loads(raw.stdout)
        elif isinstance(raw, str):
            raw = json.loads(raw)
        values = [raw.get("format", {}).get("duration")] + [s.get("duration") for s in raw.get("streams", [])]
        durations = [Decimal(str(value)) for value in values if value not in (None, "", "N/A")]
        duration = max(durations) if durations else None
        return max(1, int(duration.to_integral_value(rounding="ROUND_CEILING"))) if duration is not None and duration.is_finite() and duration > 0 else None
    except (OSError, subprocess.TimeoutExpired, ValueError, TypeError, AttributeError, InvalidOperation):
        return None
