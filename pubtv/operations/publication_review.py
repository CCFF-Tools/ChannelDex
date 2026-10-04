"""Local, immutable review of the controller BIN before Approval 2."""
from __future__ import annotations

import hashlib
import uuid
from pathlib import Path

from django.conf import settings as django_settings
from django.db import transaction
from django.core import signing

from .automation import canonical_hash, publication_snapshot, restricted_target_blockers, settings_record_snapshot
from .models import ControllerSnapshot, SchedulePublicationBatch, ScheduleDeliveryOperation, Device
from pubtv.ultranexus.bin import BinImage
from pubtv.ultranexus.command import LOADSCH_PATH
from pubtv.ultranexus.utils import sha256_file
from pubtv.ultranexus.exceptions import UltraNexusError


def _record(image, record):
    size = 574 if hasattr(record, "occurrence") else 518
    raw = image.data[record.offset:record.offset + size]
    return {"slot": record.slot, "reference": record.reference,
            "sha256": hashlib.sha256(raw).hexdigest(),
            "title": record.title, "filename": record.filename,
            **({"day": record.day, "start": record.start, "end": record.end,
                "duration": record.duration, "in_point": record.in_point,
                "out_point": record.out_point, "occurrence": record.occurrence} if hasattr(record, "occurrence") else {})}


def _manifest(image):
    return {"sha256": image.manifest()["sha256"],
            "resources": [_record(image, row) for row in image.resources],
            "schedules": [_record(image, row) for row in image.schedules]}


def _diff(actual, proposed):
    """Match exact bytes first, then unique occurrence identities, never resources."""
    result = {"adds": [], "moves": [], "changes": [], "deletes": [], "unchanged": []}
    old, new = list(actual["schedules"]), list(proposed["schedules"])
    for before in list(old):
        match = next((row for row in new if row["sha256"] == before["sha256"]), None)
        if match is not None:
            result["unchanged"].append(match)
            old.remove(before)
            new.remove(match)
    for before in old:
        identity = before.get("occurrence")
        matches = [row for row in new if identity and row.get("occurrence") == identity]
        if len(matches) == 1 and sum(row.get("occurrence") == identity for row in old) == 1:
            after = matches[0]
            kind = "moves" if (before.get("day"), before.get("start")) != (after.get("day"), after.get("start")) else "changes"
            result[kind].append({"before": before, "after": after})
            new.remove(after)
        else:
            result["deletes"].append(before)
    result["adds"] = new
    result["resource_changes"] = _diff_resources(actual["resources"], proposed["resources"])
    return result


def _diff_resources(actual, proposed):
    old = {row["reference"]: row for row in actual}
    new = {row["reference"]: row for row in proposed}
    return {"adds": [new[k] for k in sorted(set(new) - set(old))],
            "deletes": [old[k] for k in sorted(set(old) - set(new))],
            "changed": [{"before": old[k], "after": new[k]} for k in sorted(set(old) & set(new))
                        if old[k]["sha256"] != new[k]["sha256"]],
            "unchanged": [new[k] for k in sorted(set(old) & set(new))
                          if old[k]["sha256"] == new[k]["sha256"]]}


def _target_period(image):
    rows = [row for row in image.schedules if not row.title.startswith("Switchback")]
    if not rows:
        return {"first_day": None, "last_day": None, "first_start": None, "last_end": None}
    return {"first_day": min(row.day for row in rows), "last_day": max(row.day for row in rows),
            "first_start": min(row.start for row in rows), "last_end": max(row.end for row in rows)}


def _publication_binding(batch):
    value = dict(publication_snapshot(batch))
    value.pop("review_hash", None)
    # Include live occurrence revisions, not only the originally selected ones.
    value["live_occurrences"] = list(batch.occurrence_selections.order_by("pk").values(
        "occurrence_id", "occurrence__revision", "occurrence__status",
        "occurrence__asset_id", "occurrence__starts_at", "occurrence__planned_duration_seconds"))
    for row in value["live_occurrences"]:
        row["occurrence__starts_at"] = row["occurrence__starts_at"].isoformat()
    return value


def capture_controller_snapshot(batch, *, adapter=None):
    """Download only the qualified schedule path and persist an immutable snapshot."""
    current = batch.target.ultranexus_settings.filter(is_current=True).first()
    if batch.status == "cancelled":
        raise ValueError("Cancelled publications cannot capture a new review")
    blockers = restricted_target_blockers(current)
    if blockers:
        raise ValueError("Qualified target contract is incomplete: " + "; ".join(blockers))
    if current.schedule_path != LOADSCH_PATH:
        raise ValueError("Only the qualified controller schedule path may be captured")
    root = Path(django_settings.DATA_DIR) / "ultranexus" / "snapshots"
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    path = root / f"target-{batch.target_id}-batch-{batch.pk}-{uuid.uuid4().hex}.bin"
    if adapter is None:
        from .publication_delivery import _ftp
        adapter = _ftp(current)
    download = getattr(adapter, "download", None) or getattr(getattr(adapter, "adapter", None), "download", None)
    if download is None:
        raise ValueError("FTP adapter cannot capture the qualified controller schedule")
    download(current.schedule_path, str(path))
    actual = BinImage.from_file(path)
    payload = {"target_id": batch.target_id, "schedule_path": current.schedule_path,
               "captured_sha256": sha256_file(path), "captured_manifest": _manifest(actual),
               "captured_settings": settings_record_snapshot(current),
               "captured_settings_hash": canonical_hash(settings_record_snapshot(current)),
               "expected_base_hash": current.base_bin_hash}
    digest = canonical_hash(payload)
    payload["snapshot_hash"] = digest
    with transaction.atomic():
        Device.objects.select_for_update().get(pk=batch.target_id)
        latest = ControllerSnapshot.objects.select_for_update().filter(target=batch.target).order_by("-revision").first()
        revision = (latest.revision if latest else 0) + 1
        return ControllerSnapshot.objects.create(target=batch.target, revision=revision,
            snapshot_hash=digest, payload=payload, source_reference=str(path))


def publication_review(batch, snapshot=None):
    """Return the exact actual/proposed diff and media readiness for rendering."""
    snapshot = snapshot or batch.target.controller_snapshots.order_by("-revision").first()
    artifact = batch.artifact_revisions.filter(artifact_type="bin").order_by("-revision").first()
    if not snapshot or not artifact:
        return {"blockers": ["Controller BIN capture and generated BIN are required before Approval 2"]}
    actual = snapshot.payload.get("captured_manifest")
    current_settings = batch.target.ultranexus_settings.filter(is_current=True).first()
    blockers = []
    if not actual:
        return {"blockers": ["Controller snapshot predates parsed BIN capture; capture again"]}
    if batch.status == "cancelled":
        return {"blockers": ["Publication is cancelled"]}
    captured_hash = snapshot.payload.get("captured_sha256", "")
    payload = dict(snapshot.payload)
    payload.pop("snapshot_hash", None)
    if canonical_hash(payload) != snapshot.snapshot_hash:
        return {"blockers": ["Controller capture metadata changed"]}
    try:
        actual_image = BinImage.from_file(snapshot.source_reference, expected_sha256=captured_hash)
        proposed = BinImage.from_file(artifact.file_reference, expected_sha256=artifact.content_hash)
    except (OSError, ValueError):
        return {"blockers": ["Controller capture or proposed BIN is missing, changed, or invalid"]}
    if _manifest(actual_image) != actual:
        return {"blockers": ["Controller capture manifest does not match its bytes"]}
    if not current_settings or captured_hash.lower() != current_settings.base_bin_hash.lower():
        blockers.append("Controller BIN differs from the qualified approved base; review cannot auto-rebase")
    if current_settings and snapshot.payload.get("captured_settings_hash") != canonical_hash(settings_record_snapshot(current_settings)):
        blockers.append("Target settings changed after controller capture")
    current_publication_hash = canonical_hash(_publication_binding(batch))
    diff = _diff(actual, _manifest(proposed))
    full_snapshot = publication_snapshot(batch)
    gaps = []
    nmg_artifact = batch.artifact_revisions.filter(artifact_type="nmg").order_by("-revision").first()
    if nmg_artifact:
        try:
            from pubtv.ultranexus.nmg import NMGImage
            nmg = NMGImage.from_file(nmg_artifact.file_reference, expected_sha256=nmg_artifact.content_hash)
            gaps = [{"title": row.title, "day": row.day, "start": row.start, "end": row.end}
                    for row in nmg.schedules() if row.title.startswith("Switchback")]
        except (OSError, ValueError, UltraNexusError):
            # Invalid local artifacts must be shown as blockers on the review page.
            blockers.append("Proposed NMG could not be read and verified")
    review = {"snapshot_hash": snapshot.snapshot_hash, "captured_hash": captured_hash,
              "proposed_hash": artifact.content_hash, "target_period": _target_period(proposed),
              "target": str(batch.target), "mode": batch.reconciliation_mode,
              "activation": batch.requested_activation_at.isoformat() if batch.requested_activation_at else "Immediate attended commitment",
              "occurrences": _publication_binding(batch)["live_occurrences"],
              "diff": diff, "switchbacks": gaps, "media": full_snapshot.get("media", []),
              "publication_snapshot_hash": current_publication_hash, "blockers": blockers}
    review["review_hash"] = canonical_hash(review)
    if not blockers:
        review["review_token"] = signing.dumps({"batch": batch.pk, "hash": review["review_hash"]}, salt="ultranexus-publication-review")
    return review


def validate_review_token(batch, token):
    try:
        signed = signing.loads(token, salt="ultranexus-publication-review", max_age=86400)
    except (signing.BadSignature, TypeError) as exc:
        raise ValueError("Review the current controller diff before approving") from exc
    review = publication_review(batch)
    if review.get("blockers") or signed != {"batch": batch.pk, "hash": review.get("review_hash")}:
        raise ValueError("Displayed review changed; review the new diff before approving")
    return review


def cancel_publication(batch_id):
    """Cancel local commitment only; retain staged remote bytes and all media."""
    from .services import audit
    with transaction.atomic():
        initial = SchedulePublicationBatch.objects.get(pk=batch_id)
        Device.objects.select_for_update().get(pk=initial.target_id)
        batch = SchedulePublicationBatch.objects.select_for_update().get(pk=batch_id)
        operations = list(batch.delivery_operations.select_for_update())
        if batch.activated_at or any(op.state not in {"staged", "failed", "cancelled"} for op in operations):
            raise ValueError("Publication has started delivery or needs recovery; cancellation is blocked")
        for operation in operations:
            if operation.state == "staged":
                operation.state = "cancelled"
                operation.result = {**operation.result, "cancelled": True, "retained_staging_path": operation.staging_path}
                operation.save(update_fields=["state", "result", "updated_at"])
        batch.status = "cancelled"
        batch.approval_2_status = "stale"
        batch.save(update_fields=["status", "approval_2_status"])
        batch.jobs.filter(status="queued").update(status="cancelled")
        audit("cancel", "SchedulePublicationBatch", batch, "Publication cancelled; media and remote staged bytes retained")
    return batch
