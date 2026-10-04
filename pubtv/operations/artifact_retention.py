"""Explicit retention of generated schedule files; immutable metadata survives."""
from collections import defaultdict
import json
import os
from pathlib import Path

from django.conf import settings
from django.db import transaction
from .models import (ArtifactRevision, AuditEvent, ControllerSnapshot, Device,
                     MediaInspection, PreparationBatchItem, ScheduleDeliveryOperation,
                     SchedulePublicationBatch, UltraNexusTargetSettings)
from pubtv.ultranexus.utils import sha256_file

DEFAULT_KEEP_SETS = 20


def _audit(action, payload, actor="owner", entity_id=None, entity="UltraNEXUSArtifact"):
    return AuditEvent.objects.create(actor=actor, action=action, entity=entity,
                                    entity_id=entity_id, summary=json.dumps(payload, sort_keys=True))


def _pin_state():
    pins = {}
    for event in AuditEvent.objects.filter(entity="UltraNEXUSArtifactSet", action__in=("pin", "unpin")).order_by("occurred_at", "pk"):
        pins[event.entity_id] = event.action == "pin"
    return pins


def set_pinned(batch_id, *, pinned, actor="owner"):
    with transaction.atomic():
        batch = SchedulePublicationBatch.objects.get(pk=batch_id)
        Device.objects.select_for_update().get(pk=batch.target_id)
        return _audit("pin" if pinned else "unpin", {"pinned": bool(pinned)}, actor,
                      batch_id, "UltraNEXUSArtifactSet")


def _path(value):
    return Path(value).expanduser().absolute() if value else None


def _safe(path):
    base = Path(settings.DATA_DIR).absolute()
    root = base / "ultranexus" / "schedules"
    try:
        relative = path.relative_to(root)
        children = []
        current = path
        while current != base:
            children.append(current)
            current = current.parent
        # Refuse aliases, symlinks at any level, and shared file inodes.
        # macOS /var -> /private/var above DATA_DIR is an OS-owned alias.
        return (path.resolve() == base.resolve() / "ultranexus" / "schedules" / relative
                and not any(p.is_symlink() for p in children)
                and path.is_file() and path.stat().st_nlink == 1)
    except (ValueError, OSError):
        return False


def plan_cleanup(*, keep_sets=DEFAULT_KEEP_SETS):
    if keep_sets < DEFAULT_KEEP_SETS:
        raise ValueError("Retain at least 20 unpinned successful sets per target")
    artifacts = list(ArtifactRevision.objects.select_related("publication_batch"))
    pins = _pin_state()
    protected = set()
    protected_hashes = set()
    # All historical bases still participate in permanent identifier allocation.
    for row in UltraNexusTargetSettings.objects.all():
        protected.update(filter(None, (_path(row.base_nmg_path), _path(row.base_bin_path))))
    for row in ScheduleDeliveryOperation.objects.select_related("artifact"):
        protected.update(filter(None, (_path(row.rollback_path), _path(row.artifact.file_reference))))
    for reference in ControllerSnapshot.objects.values_list("source_reference", flat=True):
        protected.add(_path(reference))
    for reference in MediaInspection.objects.values_list("local_path", flat=True):
        protected.add(_path(reference))
    for reference in PreparationBatchItem.objects.values_list("selected_input_path", flat=True):
        protected.add(_path(reference))
    for snapshot in SchedulePublicationBatch.objects.values_list("approval_2_snapshot", flat=True):
        for row in snapshot.get("artifacts", []) if isinstance(snapshot, dict) else []:
            protected_hashes.add(row.get("content_hash"))
    active_batches = set(SchedulePublicationBatch.objects.filter(jobs__status__in=("queued", "running")).values_list("pk", flat=True))
    # Keep all referenced source artifacts, including source NMG hashes in BIN evidence.
    for artifact in artifacts:
        for data in (artifact.manifest, artifact.validation):
            if isinstance(data, dict) and data.get("source_nmg_hash"):
                protected_hashes.add(data["source_nmg_hash"])
    by_target = defaultdict(dict)
    for artifact in artifacts:
        batch = artifact.publication_batch
        if batch.status == "verified" and artifact.validation.get("status") == "passed" and not pins.get(batch.pk):
            by_target[batch.target_id][batch.pk] = batch.activated_at or batch.created_at
    retained = set()
    for batches in by_target.values():
        retained.update(sorted(batches, key=lambda pk: (batches[pk], pk), reverse=True)[:keep_sets])
    paths = defaultdict(list)
    for artifact in artifacts:
        paths[_path(artifact.file_reference)].append(artifact)
    actions, skipped = [], []
    for path, refs in paths.items():
        reason = None
        if path in protected or any(a.content_hash in protected_hashes for a in refs):
            reason = "protected evidence reference"
        elif any(pins.get(a.publication_batch_id) for a in refs):
            reason = "pinned"
        elif any(a.publication_batch_id in retained | active_batches for a in refs):
            reason = "retained set or active job"
        elif any(a.publication_batch.status != "verified" or a.validation.get("status") != "passed" for a in refs):
            reason = "uncompleted or unsuccessful artifact"
        elif path is None or not _safe(path):
            reason = "missing, external, symlink or shared path"
        else:
            try:
                digest = sha256_file(path)
                if any(a.content_hash != digest for a in refs):
                    reason = "changed artifact bytes"
                else:
                    actions.append({"path": str(path), "sha256": digest, "size": path.stat().st_size,
                                    "artifact_ids": [a.pk for a in refs]})
            except OSError:
                reason = "unreadable artifact"
        if reason:
            skipped.append({"path": str(path), "reason": reason})
    return {"keep_sets": keep_sets, "actions": actions, "skipped": skipped}


def cleanup(*, execute=False, keep_sets=DEFAULT_KEEP_SETS, actor="owner"):
    report = plan_cleanup(keep_sets=keep_sets)
    report.update(dry_run=not execute, completed=[], failed=[])
    if not execute:
        return report
    for item in report["actions"]:
        # Intent is durable even if the process stops immediately after unlink.
        _audit("retention_delete_requested", item, actor)
        try:
            with transaction.atomic():
                # The audit write acquires SQLite's writer lock; row locks protect
                # the same target mutation boundary on other supported databases.
                _audit("retention_delete_check", item, actor)
                list(Device.objects.select_for_update().order_by("pk"))
                eligible = {row["path"]: row for row in plan_cleanup(keep_sets=keep_sets)["actions"]}
                if eligible.get(item["path"]) != item:
                    raise OSError("artifact references or bytes changed after cleanup preview")
                source = Path(item["path"])
                if not _safe(source) or sha256_file(source) != item["sha256"]:
                    raise OSError("artifact changed before deletion")
                os.unlink(source)
                _audit("retention_delete", item, actor)
            report["completed"].append(item)
        except OSError as exc:
            failure = {**item, "error": str(exc)}
            _audit("retention_delete_failed", failure, actor)
            report["failed"].append(failure)
    return report
