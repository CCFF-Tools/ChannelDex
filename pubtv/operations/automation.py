"""Pure, fail-closed helpers for the two-approval UltraNEXUS workflow."""
from __future__ import annotations

import hashlib
import shutil
import json
import os
import posixpath
import re
import subprocess
import struct
import uuid
from collections import Counter
from datetime import datetime, timedelta
from pathlib import Path
from tempfile import NamedTemporaryFile
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from django.conf import settings as django_settings
from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from .models import (PreparationBatch, PreparationBatchItem, ResearchGate, MediaInspection,
                     MediaBinding, MediaIdAllocation, TransferAttempt, UltraNexusTargetSettings, ArtifactRevision,
                     SchedulePublicationBatch, Device)
from pubtv.ultranexus.encoding import AdobeMediaEncoder, FFmpegEncoder, discover_ame
from pubtv.ultranexus.media import parse_ffprobe_json, validate_nexus_mono
from pubtv.ultranexus.ftp import StdlibFTPAdapter
from pubtv.ultranexus.secrets import KeychainSecretStore, SecretReference
from pubtv.ultranexus.nmg import NMGImage, SCHEDULE_BASE, SCHEDULE_STRIDE
from pubtv.ultranexus.bin import BinImage, SCHEDULE_BASE as BIN_SCHEDULE_BASE, SCHEDULE_STRIDE as BIN_SCHEDULE_STRIDE
from pubtv.ultranexus.bin import RESOURCE_BASE as BIN_RESOURCE_BASE, RESOURCE_STRIDE as BIN_RESOURCE_STRIDE


def canonical_json(value) -> str:
    """Return the one JSON representation used for approval snapshots."""
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True, default=str)


def canonical_hash(value) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def approval_snapshot(value):
    return {"snapshot": value, "hash": canonical_hash(value)}


def approve_snapshot(instance, payload, *, approval=1, actor="owner", at=None):
    """Persist an approval only when its exact snapshot and digest are present."""
    at = at or timezone.now()
    prefix = f"approval_{approval}_"
    setattr(instance, prefix + "snapshot", payload)
    setattr(instance, prefix + "hash", canonical_hash(payload))
    setattr(instance, prefix + "status", "approved")
    setattr(instance, prefix + "at", at)
    setattr(instance, prefix + "actor", actor)
    instance.save()
    return instance


def invalidate_snapshot(instance, *, approval=1, reason="changed"):
    prefix = f"approval_{approval}_"
    setattr(instance, prefix + "status", "stale")
    notes = getattr(instance, "notes", "")
    if notes and reason not in notes:
        instance.notes = f"{notes}\n{reason}"
    elif not notes and hasattr(instance, "notes"):
        instance.notes = reason
    instance.save(update_fields=[prefix + "status"] + (["notes"] if hasattr(instance, "notes") else []))
    return instance


SCHEDULE_RESEARCH_GATES = {
    "nmg_bin_relationship",
    "schedule_bin_format",
    "schedule_bin_activation",
}


def _gate_status(target, required=SCHEDULE_RESEARCH_GATES):
    gates = list(ResearchGate.objects.filter(target=target)) if target is not None else []
    by_key = {gate.key: gate for gate in gates}
    open_keys = [key for key in sorted(required) if key not in by_key or by_key[key].status != "passed"]
    return gates, open_keys


def schedule_ready_binding(asset, target):
    if not asset or not target:
        return None
    return asset.ultranexus_bindings.filter(target=target, binding_type="encoded").filter(
        Q(verification_basis="locally_verified", local_inspection__status="passed")
        | Q(verification_basis="legacy", legacy_attestation_hash__gt="", legacy_attested_at__isnull=False,
            legacy_attested_by__gt="", external_reference__gt="")
    ).order_by("pk").first()


def preview_schedule(occurrences, *, target=None, research_passed=False, workflow_mode="selected_changes"):
    """Build a reviewable schedule preview without writing uploads or coverage."""
    gates, open_gates = _gate_status(target)
    rows, blockers = [], ([f"research gate: {key}" for key in open_gates] if not research_passed else [])
    ordered = sorted(occurrences, key=lambda item: item.starts_at)
    week_start = week_end = None
    if workflow_mode == "full_week":
        if not ordered:
            blockers.append("a full-week preview requires at least one planned occurrence")
        else:
            try:
                station_zone = ZoneInfo(ordered[0].station.timezone)
            except (AttributeError, ZoneInfoNotFoundError):
                station_zone = ZoneInfo("America/Detroit")
            first_local = ordered[0].starts_at.astimezone(station_zone)
            week_date = first_local.date() - timedelta(days=first_local.weekday())
            week_start = datetime.combine(week_date, datetime.min.time(), station_zone)
            week_end = datetime.combine(week_date + timedelta(days=7), datetime.min.time(), station_zone)
            if week_start.utcoffset() != week_end.utcoffset():
                blockers.append("DST-transition week")
            if any(not week_start <= item.starts_at.astimezone(station_zone) < week_end for item in ordered):
                blockers.append("full-week selections span more than one scheduling week")
    for item in ordered:
        item_blockers = []
        if item.item_type == "live": item_blockers.append("live item requires manual review")
        if item.item_type == "episode" and not item.episode_id: item_blockers.append("unassigned episode")
        if not item.asset_id and item.item_type in {"episode", "media", "filler", "psa", "station_id"}:
            item_blockers.append("media asset is not assigned")
        if item.asset_id and target is not None and not schedule_ready_binding(item.asset, target):
            item_blockers.append("verified media transfer is missing")
        if item.starts_at.date() != item.ends_at.date(): item_blockers.append("midnight-crossing item")
        # A timezone offset change between endpoints is an explicit DST review.
        if item.starts_at.utcoffset() != item.ends_at.utcoffset(): item_blockers.append("DST-transition item")
        previous = next((row for row in reversed(rows) if row.get("occurrence") is not None), None)
        if previous and item.starts_at < previous["ends_at"]:
            item_blockers.append("Switchback/overlap")
        elif previous and item.starts_at > previous["ends_at"]:
            rows.append({
                "occurrence": None,
                "starts_at": previous["ends_at"],
                "ends_at": item.starts_at,
                "readiness": "switchback",
                "blockers": [],
                "is_gap": True,
            })
        readiness = "ready" if not item_blockers else "blocked"
        rows.append({"occurrence": item, "starts_at": item.starts_at, "ends_at": item.ends_at, "readiness": readiness, "blockers": item_blockers, "is_gap": False})
        blockers.extend(f"{item.label}: {reason}" for reason in item_blockers)
    if workflow_mode == "full_week" and week_start is not None:
        if rows and rows[0]["starts_at"] > week_start:
            rows.insert(0, {"occurrence": None, "starts_at": week_start, "ends_at": rows[0]["starts_at"], "readiness": "switchback", "blockers": [], "is_gap": True})
        if rows and rows[-1]["ends_at"] < week_end:
            rows.append({"occurrence": None, "starts_at": rows[-1]["ends_at"], "ends_at": week_end, "readiness": "switchback", "blockers": [], "is_gap": True})
    return {"rows": rows, "blockers": blockers, "research_gates": gates, "ready": not blockers}


def process_due_job_state(job, *, now=None):
    """Advance a queued publication job once; repeated calls are harmless.

    This accepts the optional PublicationJob model without coupling the UI to a
    worker implementation. Jobs remain blocked until both research and Approval 2.
    """
    now = now or timezone.now()
    if getattr(job, "status", None) in {"succeeded", "failed", "cancelled"}:
        return job
    requested = getattr(getattr(job, "publication_batch", None), "requested_activation_at", None)
    if requested and requested > now:
        return job
    publication = getattr(job, "publication_batch", None)
    if publication and (publication.approval_2_status != "approved" or publication.approval_2_hash == ""):
        if hasattr(job, "status"):
            job.status = "failed"; job.error = "Approval 2 is required"
            fields = ["status", "error"]
            if hasattr(job, "finished_at"): job.finished_at = now; fields.append("finished_at")
            job.save(update_fields=fields)
        return job
    if hasattr(job, "status"):
        if getattr(job, "kind", "") in {"deliver", "reconcile", "verify"}:
            job.status = "failed"; job.error = "Delivery and activation remain blocked pending qualified capability"; job.finished_at = now; job.save(update_fields=["status", "error", "finished_at"]); return job
        job.status = "succeeded"
        if hasattr(job, "finished_at"): job.finished_at = now
        fields = ["status"] + (["finished_at"] if hasattr(job, "finished_at") else [])
        job.save(update_fields=fields)
    return job


def preparation_snapshot(item: PreparationBatchItem):
    asset = item.asset
    return {"asset_id": str(asset.asset_id), "file_name": asset.file_name, "version": asset.version,
            "encode_before_transfer": item.encode_before_transfer, "occurrence_id": item.occurrence_id,
            "selected_input_path": item.selected_input_path, "selected_input_hash": item.selected_input_hash}


def settings_record_snapshot(target_settings):
    settings_snapshot = None
    if target_settings:
        settings_snapshot = {
            "id": target_settings.pk,
            "version": target_settings.version,
            "settings_hash": target_settings.settings_hash,
            "settings": target_settings.settings,
            "host": target_settings.host,
            "port": target_settings.port,
            "media_directory": target_settings.media_directory,
            "schedule_path": target_settings.schedule_path,
            "secret_reference": target_settings.secret_reference,
            "reconciliation_mode": target_settings.reconciliation_mode,
            "capability_flags": target_settings.capability_flags,
            "base_nmg_path": target_settings.base_nmg_path,
            "base_nmg_hash": target_settings.base_nmg_hash,
            "base_bin_path": target_settings.base_bin_path,
            "base_bin_hash": target_settings.base_bin_hash,
            "command_port": target_settings.command_port,
            "command_username": target_settings.command_username,
            "command_secret_reference": target_settings.command_secret_reference,
        }
    return settings_snapshot


def target_settings_snapshot(target):
    return settings_record_snapshot(UltraNexusTargetSettings.objects.filter(target=target, is_current=True).first())


def preparation_batch_snapshot(batch: PreparationBatch):
    return {"target_id": batch.target_id, "target_settings": target_settings_snapshot(batch.target),
            "items": [preparation_snapshot(item) for item in batch.items.select_related("asset").order_by("position", "pk")]}


def _sha256_file(path):
    digest = hashlib.sha256()
    with open(path, "rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _gate_passed(target, key):
    return ResearchGate.objects.filter(target=target, key=key, status="passed").exists()


def _qualified_ffmpeg(config, target):
    """Bind a passed equivalence gate to the reviewed build and comparisons."""
    if not _gate_passed(target, "ffmpeg_equivalence"):
        raise RuntimeError("FFmpeg equivalence gate is not passed")
    executable = Path(config.get("ffmpeg_executable") or "")
    manifest_path = Path(config.get("ffmpeg_qualification_manifest") or "")
    build_hash = (config.get("ffmpeg_build_sha256") or "").lower()
    manifest_hash = (config.get("ffmpeg_qualification_sha256") or "").lower()
    if (not executable.is_file() or not manifest_path.is_file() or
            len(build_hash) != 64 or len(manifest_hash) != 64):
        raise RuntimeError("Exact FFmpeg build and reviewed comparison manifest are required")
    if _sha256_file(executable) != build_hash or _sha256_file(manifest_path) != manifest_hash:
        raise RuntimeError("FFmpeg build or qualification manifest changed")
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, ValueError) as exc:
        raise RuntimeError("FFmpeg qualification manifest is unreadable") from exc
    pairs = manifest.get("pairs", [])
    if not pairs or any(pair.get("owner_review") != "accepted" or
                        pair.get("ffmpeg_build_identity") != build_hash for pair in pairs):
        raise RuntimeError("FFmpeg comparisons are not accepted for this exact build")
    return str(executable)


def _run_ffprobe(path, *, runner=subprocess.run, executable="ffprobe"):
    result = runner(
        (executable, "-v", "error", "-show_streams", "-show_format", "-of", "json", str(path)),
        check=False,
        capture_output=True,
        text=True,
    )
    if getattr(result, "returncode", 1) != 0:
        raise RuntimeError("ffprobe failed")
    return parse_ffprobe_json(result.stdout)


def _ame_process_running(executable):
    try:
        result = subprocess.run(("pgrep", "-f", str(executable)), check=False, capture_output=True, text=True)
    except OSError as exc:
        raise RuntimeError("Unable to determine whether Adobe Media Encoder is already running") from exc
    if result.returncode == 0:
        return True
    if result.returncode == 1:
        return False
    raise RuntimeError("Unable to determine whether Adobe Media Encoder is already running")


def _next_media_id(target):
    used = set(MediaBinding.objects.filter(target=target, media_id_uint16__isnull=False).values_list("media_id_uint16", flat=True))
    used.update(MediaIdAllocation.objects.filter(target=target).values_list("media_id_uint16", flat=True))
    for settings_record in UltraNexusTargetSettings.objects.filter(target=target):
        if settings_record.base_nmg_path and settings_record.base_nmg_hash:
            image = NMGImage.from_file(settings_record.base_nmg_path, expected_sha256=settings_record.base_nmg_hash)
            used.update(resource.media_id for resource in image.resources() if resource.media_id)
        if settings_record.base_bin_path and settings_record.base_bin_hash:
            image = BinImage.from_file(settings_record.base_bin_path, expected_sha256=settings_record.base_bin_hash)
            used.update(resource.media_id for resource in image.resources if resource.media_id)
    return next((value for value in range(1, 65536) if value not in used), None)


def _resource_reference(target, digest):
    used = set(MediaBinding.objects.filter(target=target, resource_reference_uint32__isnull=False).values_list("resource_reference_uint32", flat=True))
    used.update(MediaIdAllocation.objects.filter(target=target).values_list("resource_reference_uint32", flat=True))
    for settings_record in UltraNexusTargetSettings.objects.filter(target=target):
        if settings_record.base_nmg_path and settings_record.base_nmg_hash:
            image = NMGImage.from_file(settings_record.base_nmg_path, expected_sha256=settings_record.base_nmg_hash)
            used.update(resource.reference for resource in image.resources())
        if settings_record.base_bin_path and settings_record.base_bin_hash:
            image = BinImage.from_file(settings_record.base_bin_path, expected_sha256=settings_record.base_bin_hash)
            used.update(resource.reference for resource in image.resources)
    value = int(digest[:8], 16) or 1
    while value in used:
        value = 1 if value == 0xFFFFFFFF else value + 1
    return value


def controller_filename(original_name, digest, *, encoded):
    """Return a deterministic, ASCII UltraNEXUS filename with a <=27-byte stem."""
    original = Path(original_name)
    suffix = ".mp4" if encoded else (original.suffix.lower() or ".mp4")
    stem = original.stem.encode("ascii", "ignore").decode("ascii")
    stem = re.sub(r"[^A-Za-z0-9_-]+", "-", stem).strip("-_") or "media"
    marker = f"-{digest[:8]}"
    stem = stem[: max(1, 27 - len(marker))] + marker
    return stem + suffix


def _target_config(target):
    record = UltraNexusTargetSettings.objects.filter(target=target, is_current=True).first()
    if not record:
        raise RuntimeError("current UltraNEXUS target settings are unavailable")
    return record, {
        **record.settings,
        "host": record.host,
        "port": record.port or 21,
        "media_directory": record.media_directory,
        "schedule_path": record.schedule_path,
        "secret_reference": record.secret_reference,
        "base_nmg_path": record.base_nmg_path,
        "base_nmg_hash": record.base_nmg_hash,
        "base_bin_path": record.base_bin_path,
        "base_bin_hash": record.base_bin_hash,
        "command_port": record.command_port,
        "command_username": record.command_username,
        "command_secret_reference": record.command_secret_reference,
    }


def _keychain_password(reference):
    try:
        service, account = reference.split(":", 1)
    except ValueError as exc:
        raise RuntimeError("secret reference must use service:account") from exc
    return KeychainSecretStore().get(SecretReference(service, account))


def approve_preparation_batch(batch, *, actor="owner", at=None):
    """Capture local source paths and hashes before the immutable Approval 1."""
    items = list(batch.items.select_related("asset"))
    if not items:
        raise ValueError("Approval 1 requires at least one media item.")
    if not UltraNexusTargetSettings.objects.filter(target=batch.target, is_current=True).exists():
        raise ValueError("Approval 1 requires current UltraNEXUS target settings.")
    for item in items:
        path = item.selected_input_path or item.asset.smb_reference
        if not path or not Path(path).is_file():
            raise ValueError(f"{item.asset}: selected local source file is unavailable")
        item.selected_input_path = str(Path(path).resolve())
        item.selected_input_hash = _sha256_file(item.selected_input_path)
        item.save(update_fields=["selected_input_path", "selected_input_hash"])
    return approve_snapshot(batch, preparation_batch_snapshot(batch), approval=1, actor=actor, at=at)


def process_preparation_batch(batch, *, runner=subprocess.run, credential_resolver=None,
                              ftp_factory=StdlibFTPAdapter, ame_discoverer=discover_ame,
                              ffmpeg_executable="ffmpeg", probe_runner=None,
                              ame_process_checker=_ame_process_running):
    """Execute a prepared batch through injected, inspectable boundaries.

    Missing paths/tools/credentials/capabilities become durable blockers; this
    function never infers success from environment variables alone.
    """
    blockers = []
    try:
        target_settings, config = _target_config(batch.target)
    except RuntimeError as exc:
        batch.status = "blocked"
        batch.notes = str(exc)
        batch.save(update_fields=["status", "notes"])
        return {"batch": batch, "blockers": [str(exc)], "complete": False}
    artifact_root = Path(django_settings.DATA_DIR) / "ultranexus" / "renditions"
    artifact_root.mkdir(parents=True, exist_ok=True, mode=0o700)
    approved_items = batch.approval_1_snapshot.get("items", [])
    approved_ids = {entry.get("asset_id") for entry in approved_items}
    current_ids = {str(value) for value in batch.items.values_list("asset__asset_id", flat=True)}
    approval_invalid = (
        batch.approval_1_status != "approved"
        or batch.approval_1_hash != canonical_hash(batch.approval_1_snapshot)
        or batch.approval_1_snapshot.get("target_id") != batch.target_id
        or batch.approval_1_snapshot.get("target_settings") != preparation_batch_snapshot(batch).get("target_settings")
        or approved_ids != current_ids
    )
    if approval_invalid:
        message = "Approval 1 is missing, invalid, or stale"
        if batch.approval_1_status == "approved":
            batch.approval_1_status = "stale"
        batch.status = "blocked"
        batch.notes = message
        batch.save(update_fields=["approval_1_status", "status", "notes"])
        batch.items.update(execution_status="blocked", blocker=message)
        return {"batch": batch, "blockers": [message], "complete": False}
    items = list(batch.items.select_related("asset"))
    live_snapshots = {}
    for item in items:
        path = item.selected_input_path or item.asset.smb_reference
        live_snapshots[str(item.asset.asset_id)] = {
            **preparation_snapshot(item),
            "selected_input_path": str(Path(path).resolve()) if path else "",
            "selected_input_hash": _sha256_file(path) if path and Path(path).is_file() else "",
        }
    if any(entry != live_snapshots.get(entry.get("asset_id")) for entry in approved_items):
        message = "Approval 1 snapshot is stale"
        batch.approval_1_status = "stale"
        batch.status = "blocked"
        batch.notes = message
        batch.save(update_fields=["approval_1_status", "status", "notes"])
        batch.items.update(execution_status="blocked", blocker=message)
        return {"batch": batch, "blockers": [message], "complete": False}
    for item in items:
        attempt = None
        input_copy = None
        retain_input = False
        path = item.selected_input_path or item.asset.smb_reference
        current = live_snapshots[str(item.asset.asset_id)]
        if not path or not Path(path).is_file(): blockers.append(f"{item.asset}: source path unavailable"); continue
        secret_ref = config.get("secret_reference")
        if not config.get("host") or not config.get("media_directory") or not secret_ref:
            blockers.append(f"{item.asset}: target host, media directory, or secret reference is unavailable")
            continue
        approved_digest = current["selected_input_hash"]
        remote_filename = controller_filename(item.asset.file_name, approved_digest, encoded=item.encode_before_transfer)
        output = artifact_root / f"{item.asset.asset_id}-{remote_filename}"
        try:
            if item.resulting_inspection_id and item.resulting_inspection.status == "passed" and item.resulting_binding_id:
                continue
            input_copy = artifact_root / f"approved-input-{uuid.uuid4().hex}"
            fd = os.open(input_copy, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, "wb") as destination, open(path, "rb") as source:
                shutil.copyfileobj(source, destination, length=1024 * 1024)
            if _sha256_file(input_copy) != approved_digest:
                raise RuntimeError("approved source changed while preparing its private copy")
            if item.encode_before_transfer:
                output.unlink(missing_ok=True)
                ame = ame_discoverer((config.get("ame_executable"),) if config.get("ame_executable") else ())
                if ame and _gate_passed(batch.target, "ame_scripting"):
                    if not config.get("ame_preset"):
                        raise RuntimeError("qualified AME preset path is unavailable")
                    if ame_process_checker(ame):
                        raise RuntimeError("Adobe Media Encoder is already running; refusing to disturb unrelated work")
                    encoder_backend = "adobe_media_encoder"
                    encoder = AdobeMediaEncoder(executable=ame, preset=config.get("ame_preset"))
                    script = encoder.extend_script(str(input_copy), str(output))
                    with NamedTemporaryFile("w", suffix=".jsx", delete=False, dir=artifact_root, encoding="utf-8") as handle:
                        handle.write(script)
                        script_path = handle.name
                    command = encoder.bridge_command(script_path)
                elif _gate_passed(batch.target, "ffmpeg_equivalence"):
                    encoder_backend = "ffmpeg"
                    executable = _qualified_ffmpeg(config, batch.target)
                    command = FFmpegEncoder(executable=executable).command(str(input_copy), str(output)).argv
                else: raise RuntimeError("qualified AME unavailable and ffmpeg equivalence research is not passed")
                try:
                    result = runner(command, check=False, capture_output=True, text=True)
                finally:
                    if "script_path" in locals():
                        Path(script_path).unlink(missing_ok=True)
                        del script_path
                if getattr(result, "returncode", 1) != 0 or not output.is_file(): raise RuntimeError("encoder failed or produced no output")
            else:
                encoder_backend = "bypass"
                command = ()
                output = input_copy
                if not _gate_passed(batch.target, "nexus_mono_bypass"):
                    raise RuntimeError("direct-transfer Nexus Mono qualification is not passed")
            probe = parse_ffprobe_json(probe_runner(str(output))) if probe_runner else _run_ffprobe(output, runner=runner)
            validate_nexus_mono(probe)
            digest = _sha256_file(output)
            if _sha256_file(input_copy) != approved_digest:
                raise RuntimeError("approved private source changed before transfer")
            if _sha256_file(output) != digest:
                raise RuntimeError("prepared media changed before transfer")
            inspection = MediaInspection.objects.create(
                asset=item.asset,
                target=batch.target,
                local_path=str(output),
                file_size=output.stat().st_size,
                probe_json=probe.raw,
                tool="ffprobe",
                profile="Nexus Mono",
                status="passed",
                media_hash=digest,
                details={"nominal_frames": probe.nominal_frames, "duration": str(probe.duration),
                         "encoder_backend": encoder_backend, "encoder_command": list(command)},
            )
            retain_input = not item.encode_before_transfer
            resolver = credential_resolver or _keychain_password
            secret = resolver(secret_ref)
            if not secret:
                raise RuntimeError("target credentials unavailable")
            credentials = secret if isinstance(secret, dict) else {"password": secret}
            adapter = ftp_factory(
                config["host"],
                port=config.get("port") or 21,
                username=credentials.get("username") or config.get("ftp_username"),
                password=credentials.get("password"),
            )
            remote_path = posixpath.join(config["media_directory"].rstrip("/"), remote_filename)
            attempt = TransferAttempt.objects.create(item=item, target=batch.target, status="started")
            transfer = adapter.upload(str(output), remote_path, overwrite=False)
            if transfer.sha256.lower() != digest.lower():
                raise RuntimeError("remote transfer verification hash does not match the approved local bytes")
            with transaction.atomic():
                Device.objects.select_for_update().get(pk=batch.target_id)
                media_id = _next_media_id(batch.target)
                if media_id is None:
                    raise RuntimeError("target Media ID space is exhausted")
                reference = _resource_reference(batch.target, digest)
                MediaIdAllocation.objects.create(
                    target=batch.target, media_id_uint16=media_id,
                    resource_reference_uint32=reference,
                    casefold_key=remote_filename.casefold(), media_hash=digest,
                )
                binding = MediaBinding(
                    asset=item.asset,
                    target=batch.target,
                    binding_type="encoded",
                    media_id_uint16=media_id,
                    resource_reference_uint32=reference,
                    bare_filename=remote_filename,
                    profile="Nexus Mono",
                    verification_basis="locally_verified",
                    local_inspection=inspection,
                    external_reference=transfer.remote_path,
                    notes="Verified FTP transfer",
                )
                binding.full_clean()
                binding.save()
            attempt.status = "succeeded"
            attempt.external_reference = transfer.remote_path
            attempt.artifact_hash = transfer.sha256
            attempt.evidence = {"reused": transfer.reused, "verified_sha256": transfer.sha256}
            attempt.save(update_fields=["status", "external_reference", "artifact_hash", "evidence"])
            item.resulting_inspection = inspection; item.resulting_binding = binding; item.execution_status = "ready"; item.save(update_fields=["resulting_inspection", "resulting_binding", "execution_status"])
        except Exception as exc:
            if attempt is not None and attempt.status == "started":
                attempt.status = "failed"
                attempt.notes = str(exc)
                attempt.save(update_fields=["status", "notes"])
            item.execution_status = "blocked"; item.blocker = str(exc); item.save(update_fields=["execution_status", "blocker"]); blockers.append(f"{item.asset}: {exc}")
        finally:
            if input_copy is not None and not retain_input:
                input_copy.unlink(missing_ok=True)
    batch.status = "blocked" if blockers else "complete"; batch.notes = "; ".join(dict.fromkeys(blockers)); batch.save(update_fields=["status", "notes"])
    return {"batch": batch, "blockers": blockers, "complete": not blockers}


def process_preparation_job(job, **kwargs):
    """Run one durable Approval-1 job and retain its terminal result."""
    if job.status in {"succeeded", "failed", "cancelled"}:
        return job
    now = timezone.now()
    job.status = "running"
    job.started_at = now
    job.save(update_fields=["status", "started_at"])
    try:
        result = process_preparation_batch(job.batch, **kwargs)
    except Exception as exc:
        result = {"complete": False, "blockers": [str(exc)]}
    job.finished_at = timezone.now()
    job.result = {"complete": result["complete"], "blockers": result["blockers"]}
    job.status = "succeeded" if result["complete"] else "failed"
    job.error = "" if result["complete"] else "; ".join(result["blockers"])
    job.save(update_fields=["status", "finished_at", "result", "error"])
    return job


def generate_nmg_artifact(batch):
    """Generate one immutable, locally validated selected-change NMG revision."""
    if batch.workflow_mode != "selected_changes":
        raise ValueError("Full-week NMG generation remains disabled pending qualification.")
    target_settings, config = _target_config(batch.target)
    generation_settings = settings_record_snapshot(target_settings)
    base_path = Path(config.get("base_nmg_path") or "")
    if not base_path.is_file() or not config.get("base_nmg_hash"):
        raise ValueError("A readable, hash-qualified base NMG is required.")
    template_reference = target_settings.settings.get("nmg_resource_template_reference")
    schedule_template_base = target_settings.settings.get("nmg_schedule_template_base")
    if not template_reference or schedule_template_base is None:
        raise ValueError("Qualified NMG resource and schedule template identifiers are required.")
    schedule_template_base = int(schedule_template_base)
    if schedule_template_base < SCHEDULE_BASE or (schedule_template_base - SCHEDULE_BASE) % SCHEDULE_STRIDE:
        raise ValueError("The NMG schedule template offset is not aligned.")

    selections = list(batch.occurrence_selections.select_related(
        "occurrence", "occurrence__station", "occurrence__asset"
    ).order_by("occurrence__starts_at", "pk"))
    stale = [entry.occurrence.label for entry in selections if entry.occurrence_revision != entry.occurrence.revision]
    if stale:
        raise ValueError("Occurrence revisions changed: " + ", ".join(stale))
    preview = preview_schedule([entry.occurrence for entry in selections if entry.operation != "delete"],
                               target=batch.target, research_passed=True)
    if preview["blockers"]:
        raise ValueError("NMG generation blocked: " + "; ".join(preview["blockers"]))
    image = NMGImage.from_file(base_path, expected_sha256=config["base_nmg_hash"])
    image.validate_restricted()
    bin_base = None
    if any(item.operation != "add" for item in selections):
        if not config.get("base_bin_path") or not config.get("base_bin_hash"):
            raise ValueError("Selected edits require the exact qualified controller BIN base")
        bin_base = BinImage.from_file(config["base_bin_path"], expected_sha256=config["base_bin_hash"])
    schedule_template_record = image.data[schedule_template_base:schedule_template_base+SCHEDULE_STRIDE]
    template_resource = next((item for item in image.resources() if item.reference == int(template_reference)), None)
    if not template_resource:
        raise ValueError("Qualified Nexus Mono resource template is absent from the base NMG.")
    next_occurrence_id = max((item.occurrence_id for item in image.schedules()), default=0) + 1
    manifest_items = []
    for selection in selections:
        occurrence = selection.occurrence
        source_info = {}
        if selection.operation != "add":
            slot = selection.source_bin_slot
            source = next((item for item in bin_base.schedules if item.slot == slot), None)
            if source is None:
                raise ValueError(f"{occurrence.label}: selected source BIN slot is not active")
            bin_record = bin_base.data[source.offset:source.offset + BIN_SCHEDULE_STRIDE]
            if hashlib.sha256(bin_record).hexdigest() != selection.source_bin_record_hash.lower():
                raise ValueError(f"{occurrence.label}: selected source BIN record changed")
            matches = [item for item in image.schedules() if not item.title.startswith("Switchback")
                       and (item.reference, item.day, item.start) == (source.reference, source.day, source.start)]
            if len(matches) != 1:
                raise ValueError(f"{occurrence.label}: source event cannot be mapped unambiguously to NMG")
            source_nmg = matches[0]
            nmg_record = image.data[source_nmg.base:source_nmg.base + SCHEDULE_STRIDE]
            image = image.remove_schedule(target_base=source_nmg.base,
                                          expected_record_sha256=hashlib.sha256(nmg_record).hexdigest())
            source_info = {"source_bin_slot": slot, "source_bin_record_hash": selection.source_bin_record_hash.lower(),
                           "source_nmg_record_hash": hashlib.sha256(nmg_record).hexdigest()}
            if selection.operation == "delete":
                manifest_items.append({"occurrence_id": occurrence.pk,
                                       "occurrence_revision": selection.occurrence_revision,
                                       "operation": "delete", **source_info})
                continue
        binding = schedule_ready_binding(occurrence.asset, batch.target)
        if not binding or binding.resource_reference_uint32 is None or binding.media_id_uint16 is None:
            raise ValueError(f"{occurrence.label}: a schedule-ready binding with controller IDs is required")
        resource = next((item for item in image.resources() if item.reference == binding.resource_reference_uint32), None)
        if resource and (resource.filename != binding.bare_filename or
                         (resource.media_id not in (0, binding.media_id_uint16))):
            raise ValueError(f"{occurrence.label}: controller resource identity differs from its ChannelDex binding")
        if not resource:
            if binding.verification_basis != "locally_verified" or not binding.local_inspection_id:
                raise ValueError(f"{occurrence.label}: attested legacy resource is absent from the base NMG")
            if any(item.media_id == binding.media_id_uint16 for item in image.resources() if item.media_id):
                raise ValueError(f"{occurrence.label}: Media ID collides with the controller base")
            if any(item.filename.casefold() == binding.bare_filename.casefold() for item in image.resources() if item.filename):
                raise ValueError(f"{occurrence.label}: media filename collides with the controller base")
            probe = parse_ffprobe_json(binding.local_inspection.probe_json)
            bitrate = probe.video.get("bit_rate") or probe.raw.get("format", {}).get("bit_rate")
            image = image.append_resource(
                template=template_resource,
                title=occurrence.label,
                filename=binding.bare_filename,
                storage=template_resource.storage,
                comment="ChannelDex",
                reference=binding.resource_reference_uint32,
                media_id=binding.media_id_uint16,
                duration_units=probe.nominal_frames,
                rounded_seconds=(probe.nominal_frames + 29) // 30,
                video_bitrate=int(bitrate) if bitrate else None,
                width=probe.width,
                height=probe.height,
            )
            resource = next(item for item in image.resources() if item.reference == binding.resource_reference_uint32)
        zone = ZoneInfo(occurrence.station.timezone)
        local_start = occurrence.starts_at.astimezone(zone)
        day = (local_start.weekday() + 1) % 7
        start = local_start.hour * 3600 + local_start.minute * 60 + local_start.second
        play_duration = resource.rounded_seconds or ((resource.duration_units + 29) // 30)
        if play_duration <= 0:
            raise ValueError(f"{occurrence.label}: controller resource duration is unavailable")
        if play_duration > occurrence.planned_duration_seconds:
            raise ValueError(f"{occurrence.label}: media exceeds its fixed schedule slot")
        gap = next((item for item in image.schedules()
                    if item.day == day and item.start <= start
                    and item.end >= start + play_duration
                    and item.title.startswith("Switchback")), None)
        if not gap:
            raise ValueError(f"{occurrence.label}: no containing Switchback exists in the qualified base")
        image = image.insert_schedule(
            template_base=schedule_template_base,
            target_base=gap.base,
            resource=resource,
            occurrence_id=next_occurrence_id,
            day=day,
            start=start,
            duration=play_duration,
            title=occurrence.label,
            filename=binding.bare_filename,
            comment="ChannelDex",
            out_frames=resource.duration_units or play_duration * 30,
            template_record=schedule_template_record,
        )
        manifest_items.append({"occurrence_id": occurrence.pk, "occurrence_revision": selection.occurrence_revision,
                               "operation": selection.operation, **source_info,
                               "nmg_occurrence_id": next_occurrence_id,
                               "resource_reference": binding.resource_reference_uint32, "play_duration_seconds": play_duration})
        next_occurrence_id += 1
    image.validate_restricted()
    output_root = Path(django_settings.DATA_DIR) / "ultranexus" / "schedules"
    output_root.mkdir(parents=True, exist_ok=True, mode=0o700)
    manifest = {**image.manifest(), "base_sha256": config["base_nmg_hash"], "items": manifest_items,
                "target_id": batch.target_id, "target_settings": generation_settings,
                "workflow_mode": batch.workflow_mode, "reconciliation_mode": batch.reconciliation_mode}
    temp_path = None
    output = None
    try:
        with NamedTemporaryFile("wb", suffix=".nmg.part", delete=False, dir=output_root) as handle:
            handle.write(image.data)
            handle.flush()
            os.fsync(handle.fileno())
            temp_path = Path(handle.name)
        with transaction.atomic():
            Device.objects.select_for_update().get(pk=batch.target_id)
            locked = SchedulePublicationBatch.objects.select_for_update().get(pk=batch.pk)
            current_settings = UltraNexusTargetSettings.objects.select_for_update().filter(
                target_id=batch.target_id, is_current=True
            ).first()
            if settings_record_snapshot(current_settings) != generation_settings:
                raise ValueError("UltraNEXUS target settings changed during NMG generation.")
            revision = (locked.artifact_revisions.filter(artifact_type="nmg").order_by("-revision").values_list("revision", flat=True).first() or 0) + 1
            output_candidate = output_root / f"publication-{batch.pk}-r{revision}-{manifest['sha256']}-{uuid.uuid4().hex}.nmg"
            if output_candidate.exists():
                raise ValueError("Refusing to replace an existing immutable NMG artifact.")
            os.replace(temp_path, output_candidate)
            temp_path = None
            output = output_candidate
            artifact = ArtifactRevision.objects.create(
                publication_batch=locked, artifact_type="nmg", revision=revision,
                file_reference=str(output), content_hash=manifest["sha256"], manifest=manifest,
                validation={"status": "passed", "layout": image.header.version},
            )
            if locked.approval_2_status == "approved":
                invalidate_snapshot(locked, approval=2, reason="generated schedule artifact changed")
        return artifact
    except Exception:
        if temp_path:
            temp_path.unlink(missing_ok=True)
        if output:
            output.unlink(missing_ok=True)
        raise


def generate_bin_artifact(batch):
    """Build a paired target-specific BIN from a reviewed NMG mutation set."""
    if batch.workflow_mode != "selected_changes" or batch.reconciliation_mode != "preserve":
        raise ValueError("Restricted BIN creation supports selected preserved changes only")
    target_settings, config = _target_config(batch.target)
    settings_snapshot = settings_record_snapshot(target_settings)
    if config.get("schedule_path") != "/internal/schedule/schedule.bin":
        raise ValueError("Qualified controller schedule path must be reviewed and saved")
    if not config.get("base_bin_path") or not config.get("base_bin_hash"):
        raise ValueError("An exact qualified controller BIN base is required")
    nmg_artifact = batch.artifact_revisions.filter(artifact_type="nmg").order_by("-revision").first()
    if not nmg_artifact or nmg_artifact.manifest.get("target_settings") != settings_snapshot:
        raise ValueError("Generate a current NMG artifact before its paired BIN")
    if _sha256_file(nmg_artifact.file_reference) != nmg_artifact.content_hash:
        raise ValueError("NMG artifact bytes changed")
    nmg = NMGImage.from_file(nmg_artifact.file_reference, expected_sha256=nmg_artifact.content_hash)
    nmg.validate_restricted()
    base = BinImage.from_file(config["base_bin_path"], expected_sha256=config["base_bin_hash"])
    image = base
    items = nmg_artifact.manifest.get("items", [])
    selections = list(batch.occurrence_selections.order_by("occurrence__starts_at", "pk"))
    if [(x.occurrence_id, x.occurrence_revision, x.operation) for x in selections] != [
        (x.get("occurrence_id"), x.get("occurrence_revision"), x.get("operation", "add")) for x in items
    ]:
        raise ValueError("NMG selected changes no longer match this publication batch")
    removed = set()
    for selection, item in zip(selections, items):
        if selection.operation == "add":
            continue
        slot = selection.source_bin_slot
        old = next((record for record in base.schedules if record.slot == slot), None)
        if old is None or old.title.startswith("Switchback") or slot in removed:
            raise ValueError("Selected source BIN record is absent or ambiguous")
        old_bytes = base.data[old.offset:old.offset + BIN_SCHEDULE_STRIDE]
        if hashlib.sha256(old_bytes).hexdigest() != selection.source_bin_record_hash.lower():
            raise ValueError("Selected source BIN record hash changed")
        removed.add(slot)
    template_ref = target_settings.settings.get("bin_resource_template_reference")
    template = next((r for r in image.resources if r.reference == template_ref), None)
    if template is None:
        raise ValueError("Qualified BIN resource template is unavailable")
    new_events = []
    for selection, item in zip(selections, items):
        if selection.operation == "delete":
            continue
        ref = item.get("resource_reference")
        nmg_resource = next((r for r in nmg.resources() if r.reference == ref), None)
        if nmg_resource is None:
            raise ValueError("Selected NMG resource is missing")
        current = next((r for r in image.resources if r.reference == ref), None)
        if current is None:
            source = nmg.data[nmg_resource.base:nmg_resource.base + BIN_RESOURCE_STRIDE]
            if image.data[template.offset + 0x3e:template.offset + 0x43] != source[0x3e:0x43]:
                raise ValueError("NMG and BIN technical templates are not profile-compatible")
            image, _ = image.append_resource_from_template(
                template, reference=ref, title=nmg_resource.title,
                filename=nmg_resource.filename, media_id=nmg_resource.media_id,
                storage=nmg_resource.storage, comment=nmg_resource.comment,
                group=struct.unpack_from("<I", source)[0],
                duration_units=nmg_resource.duration_units,
                rounded_seconds=nmg_resource.rounded_seconds,
                bitrate=struct.unpack_from("<I", source, 0x3a)[0],
                width=struct.unpack_from("<H", source, 0x150)[0],
                height=struct.unpack_from("<H", source, 0x152)[0],
            )
            current = next(r for r in image.resources if r.reference == ref)
        if (current.filename, current.media_id) != (nmg_resource.filename, nmg_resource.media_id):
            raise ValueError("BIN resource identity differs from reviewed NMG")
        lineage = item.get("nmg_occurrence_id")
        matched = [s for s in nmg.schedules() if s.occurrence_id == lineage and not s.title.startswith("Switchback")]
        if len(matched) != 1 or matched[0].reference != ref:
            raise ValueError("Selected NMG schedule event is missing or ambiguous")
        event = matched[0]
        new_events.append(nmg.data[event.base:event.base + BIN_SCHEDULE_STRIDE])
    retained = [base.data[s.offset:s.offset + BIN_SCHEDULE_STRIDE] for s in base.schedules if s.slot not in removed]
    all_events = retained + new_events
    if len(all_events) > 3000:
        raise ValueError("BIN schedule capacity exhausted")
    # Keep retained records in their original order. A global sort would move
    # unrelated controller records for an ordinary selected insertion.
    data = bytearray(image.data)
    for index, raw in enumerate(all_events):
        offset = BIN_SCHEDULE_BASE + index * BIN_SCHEDULE_STRIDE
        data[offset:offset + BIN_SCHEDULE_STRIDE] = raw
    tail = BIN_SCHEDULE_BASE + len(all_events) * BIN_SCHEDULE_STRIDE
    data[tail:BIN_SCHEDULE_BASE + 3000 * BIN_SCHEDULE_STRIDE] = b"\0" * (3000 - len(all_events)) * BIN_SCHEDULE_STRIDE
    image, audit = image._schedule_result(data, "selected-changes")
    nmg_events = Counter(
        (s.reference, s.day, s.start, s.end, s.duration)
        for s in nmg.schedules() if not s.title.startswith("Switchback")
    )
    bin_events = Counter(
        (s.reference, s.day, s.start, s.end, s.duration)
        for s in image.executable_schedules
    )
    if bin_events != nmg_events:
        raise ValueError("NMG/BIN executable events are not logically equivalent")
    expected = {(s.reference, s.day, s.start, s.in_point, s.out_point) for s in image.executable_schedules}
    for raw in new_events:
        key = (struct.unpack_from("<I", raw, 4)[0], raw[0x1a],
               struct.unpack_from("<I", raw, 0x1b)[0],
               struct.unpack_from("<I", raw, 0x3b)[0], struct.unpack_from("<I", raw, 0x3f)[0])
        if key not in expected:
            raise ValueError("Selected NMG event did not survive BIN serialization")
    manifest = {**image.manifest(), "base_sha256": config["base_bin_hash"],
                "source_nmg_hash": nmg_artifact.content_hash, "items": items,
                "target_settings": settings_snapshot, "audit": audit,
                "removed_source_slots": sorted(removed)}
    root = Path(django_settings.DATA_DIR) / "ultranexus" / "schedules"
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    temporary = output = None
    try:
        with NamedTemporaryFile("wb", suffix=".bin.part", delete=False, dir=root) as stream:
            stream.write(image.data); stream.flush(); os.fsync(stream.fileno())
            temporary = Path(stream.name)
        BinImage.from_file(temporary, expected_sha256=manifest["sha256"])
        with transaction.atomic():
            Device.objects.select_for_update().get(pk=batch.target_id)
            locked = SchedulePublicationBatch.objects.select_for_update().get(pk=batch.pk)
            if settings_record_snapshot(UltraNexusTargetSettings.objects.filter(target=batch.target, is_current=True).first()) != settings_snapshot:
                raise ValueError("Target settings changed during BIN generation")
            revision = (locked.artifact_revisions.filter(artifact_type="bin").order_by("-revision").values_list("revision", flat=True).first() or 0) + 1
            output = root / f"publication-{batch.pk}-r{revision}-{manifest['sha256']}-{uuid.uuid4().hex}.bin"
            if output.exists():
                raise ValueError("Refusing to replace an existing BIN artifact")
            os.replace(temporary, output); temporary = None
            artifact = ArtifactRevision.objects.create(
                publication_batch=locked, artifact_type="bin", revision=revision,
                file_reference=str(output), content_hash=manifest["sha256"], manifest=manifest,
                validation={"status": "passed", "target_id": batch.target_id,
                            "source_nmg_hash": nmg_artifact.content_hash,
                            "occurrences": items, "audit": audit},
            )
            if locked.approval_2_status == "approved":
                invalidate_snapshot(locked, approval=2, reason="generated schedule artifact changed")
        return artifact
    except Exception:
        if temporary: temporary.unlink(missing_ok=True)
        if output: output.unlink(missing_ok=True)
        raise


def publication_artifact_blockers(batch):
    """Rehash artifacts and confirm the generated schedule matches this batch."""
    blockers = []
    artifacts = []
    for artifact_type in ("nmg", "bin"):
        artifact = batch.artifact_revisions.filter(artifact_type=artifact_type).order_by("-revision").first()
        if artifact:
            artifacts.append(artifact)
    if not artifacts:
        return ["validated NMG/BIN artifact is required"]
    if not any(item.artifact_type == "nmg" for item in artifacts):
        blockers.append("a validated NMG review artifact is required")
    if not any(item.artifact_type == "bin" for item in artifacts):
        blockers.append("a separately validated BIN delivery artifact is required")
    expected_items = [
        {"occurrence_id": entry.occurrence_id, "occurrence_revision": entry.occurrence_revision,
         "operation": entry.operation, "source_bin_slot": entry.source_bin_slot,
         "source_bin_record_hash": entry.source_bin_record_hash.lower()}
        for entry in batch.occurrence_selections.order_by("occurrence__starts_at", "pk")
    ]
    current_settings = target_settings_snapshot(batch.target)
    selected_nmg = next((item for item in artifacts if item.artifact_type == "nmg"), None)
    for artifact in artifacts:
        path = Path(artifact.file_reference)
        if not path.is_file():
            blockers.append(f"{artifact.artifact_type.upper()} revision {artifact.revision} file is unavailable")
            continue
        if _sha256_file(path) != artifact.content_hash:
            blockers.append(f"{artifact.artifact_type.upper()} revision {artifact.revision} hash changed")
        if artifact.validation.get("status") != "passed":
            blockers.append(f"{artifact.artifact_type.upper()} revision {artifact.revision} lacks passed validation")
        if artifact.artifact_type == "nmg":
            actual_items = [
                {"occurrence_id": item.get("occurrence_id"), "occurrence_revision": item.get("occurrence_revision"),
                 "operation": item.get("operation", "add"), "source_bin_slot": item.get("source_bin_slot"),
                 "source_bin_record_hash": item.get("source_bin_record_hash", "").lower()}
                for item in artifact.manifest.get("items", [])
            ]
            if actual_items != expected_items:
                blockers.append("NMG occurrence revisions do not match the publication batch")
            if artifact.manifest.get("target_id") != batch.target_id or artifact.manifest.get("target_settings") != current_settings:
                blockers.append("NMG target settings changed after generation")
            if artifact.manifest.get("workflow_mode") != batch.workflow_mode or artifact.manifest.get("reconciliation_mode") != batch.reconciliation_mode:
                blockers.append("NMG workflow or reconciliation mode changed after generation")
        if artifact.artifact_type == "bin":
            actual_items = [
                {"occurrence_id": item.get("occurrence_id"), "occurrence_revision": item.get("occurrence_revision"),
                 "operation": item.get("operation", "add"), "source_bin_slot": item.get("source_bin_slot"),
                 "source_bin_record_hash": item.get("source_bin_record_hash", "").lower()}
                for item in artifact.validation.get("occurrences", [])
            ]
            if actual_items != expected_items:
                blockers.append("BIN occurrence revisions do not match the publication batch")
            if artifact.validation.get("target_id") != batch.target_id:
                blockers.append("BIN target does not match the publication batch")
            if not selected_nmg or artifact.validation.get("source_nmg_hash") != selected_nmg.content_hash:
                blockers.append("BIN validation is not linked to the selected NMG revision")
    return blockers


def publication_snapshot(batch):
    selections = batch.occurrence_selections.select_related("occurrence").order_by("occurrence__starts_at", "pk")
    controller = batch.target.controller_snapshots.order_by("-revision").first()
    controller_hash = controller.snapshot_hash if controller else ""
    artifacts = []
    for artifact_type in ("nmg", "bin"):
        artifact = batch.artifact_revisions.filter(artifact_type=artifact_type).order_by("-revision").values(
            "artifact_type", "revision", "content_hash"
        ).first()
        if artifact:
            artifacts.append(artifact)
    media = []
    for selection in selections:
        binding = schedule_ready_binding(selection.occurrence.asset, batch.target)
        if binding:
            media.append({"asset_id": str(binding.asset.asset_id), "binding_id": binding.pk,
                          "verification_basis": binding.verification_basis,
                          "inspection_hash": binding.local_inspection.media_hash if binding.local_inspection_id else "",
                          "legacy_attestation_hash": binding.legacy_attestation_hash})
    return {"target_id": batch.target_id, "target_settings": target_settings_snapshot(batch.target),
            "workflow_mode": batch.workflow_mode,
            "reconciliation_mode": batch.reconciliation_mode,
            "requested_activation_at": batch.requested_activation_at.isoformat() if batch.requested_activation_at else None,
            "controller_snapshot_hash": controller_hash,
            "artifacts": artifacts,
            "media": media,
            "occurrences": [{"id": s.occurrence_id, "revision": s.occurrence_revision,
                             "operation": s.operation, "source_bin_slot": s.source_bin_slot,
                             "source_bin_record_hash": s.source_bin_record_hash.lower()} for s in selections]}


# Descriptive aliases keep callers decoupled from the storage field names.
canonical_snapshot_hash = canonical_hash
approve_approval_snapshot = approve_snapshot
invalidate_approval_snapshot = invalidate_snapshot
preview_schedule_batch = preview_schedule
process_due_job = process_due_job_state
