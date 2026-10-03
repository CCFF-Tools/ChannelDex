"""Private, durable multi-file media intake and preparation queue.

The queue deliberately owns intake and approval only.  Encoding and transfer
remain in :mod:`automation`, where the qualified adapters and gates live.
"""
from __future__ import annotations

import os
import re
import uuid
from pathlib import Path

from django.conf import settings
from django.contrib import messages
from django.db import IntegrityError, transaction
from django.core.exceptions import ValidationError
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone

from .automation import approve_snapshot, preparation_batch_snapshot, schedule_ready_binding, _sha256_file
from .models import AuditEvent, Device, Episode, MediaAsset, PreparationBatch, PreparationBatchItem, PreparationJob, Show, UltraNexusTargetSettings


QUEUE_STATES = ("queued", "encoding", "validating", "transferring", "verifying", "ready", "blocked", "failed")
MAX_UPLOAD_BYTES = 50 * 1024 * 1024 * 1024


def _safe_name(name: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "_", Path(name or "source-video").name)[:180] or "source-video"


def _row_episode(row, show):
    episode = row.get("episode")
    if episode is None and row.get("episode_id") not in (None, "", "new"):
        episode = Episode.objects.filter(pk=row["episode_id"], show=show).first()
        if episode is None:
            raise ValueError("The selected episode does not belong to this show.")
    title = (row.get("new_title") or row.get("episode_title") or "").strip()
    if episode is None and not title:
        raise ValueError("Choose an existing episode or enter a title for a new episode.")
    if len(title) > 160:
        raise ValueError("New episode titles must be 160 characters or fewer.")
    if episode is not None and episode.show_id != show.pk:
        raise ValueError("The selected episode does not belong to this show.")
    return episode, title


def validate_rows(rows, *, show):
    """Validate every row before any episode, asset, batch, or file is written."""
    rows = list(rows or [])
    if not rows:
        raise ValueError("Add at least one source file.")
    seen = set()
    validated = []
    for row in rows:
        upload = row.get("file") or row.get("source_video") or row.get("upload")
        if upload is None or not getattr(upload, "name", ""):
            raise ValueError("Every queue row requires a source file.")
        if getattr(upload, "size", 1) == 0:
            raise ValueError(f"{upload.name}: empty source files are not allowed.")
        if getattr(upload, "size", 0) > MAX_UPLOAD_BYTES:
            raise ValueError(f"{upload.name}: files larger than 50 GiB are not allowed.")
        if Path(upload.name).suffix.casefold() not in {".mp4", ".mov", ".mxf", ".mpeg", ".mpg", ".m4v", ".avi", ".mkv"}:
            raise ValueError(f"{upload.name}: unsupported video file type.")
        episode, title = _row_episode(row, show)
        key = f"episode:{episode.pk}" if episode else f"new:{title.casefold()}"
        if key in seen:
            raise ValueError("Each episode may appear only once in a batch.")
        seen.add(key)
        validated.append({"upload": upload, "episode": episode, "title": title, "encode_before_transfer": bool(row.get("encode_before_transfer", True))})
    return validated


def _store_upload(upload) -> Path:
    root = Path(getattr(settings, "DATA_DIR", settings.BASE_DIR)) / "imports"
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    root.chmod(0o700)
    path = root / f"{uuid.uuid4().hex[:16]}-{_safe_name(upload.name)}"
    try:
        descriptor = os.fdopen(os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "wb")
        try:
            for chunk in upload.chunks() if hasattr(upload, "chunks") else iter((upload.read(),)):
                descriptor.write(chunk)
        finally:
            descriptor.close()
    except Exception:
        path.unlink(missing_ok=True)
        raise
    return path


def create_media_queue_batch(*, target, show, rows, submission_token=None, label="", actor="owner"):
    """Create one immutable, approved batch in file/row order.

    Validation happens before private files or database records are created;
    the submission token makes browser retries return the original batch.
    """
    validated = validate_rows(rows, show=show)
    token = uuid.UUID(str(submission_token)) if submission_token else uuid.uuid4()
    existing = PreparationBatch.objects.filter(submission_token=token).first()
    if existing:
        if existing.target_id != target.pk or existing.show_id != show.pk:
            raise ValueError("This submission token is already bound to another show or target.")
        return existing, False
    paths = []
    try:
        # Large file IO must not hold SQLite's writer lock while the owner plans.
        prepared = []
        for row in validated:
            path = _store_upload(row["upload"])
            paths.append(path)
            prepared.append((row, path, _sha256_file(path)))
        with transaction.atomic():
            batch = PreparationBatch.objects.create(target=target, show=show,
                submission_token=token, label=label or f"{show.title} media queue", created_by=actor)
            Show.objects.select_for_update().get(pk=show.pk)
            AuditEvent.objects.create(actor=actor, action="create", entity="PreparationBatch", entity_id=batch.pk, summary="Media queue batch created and awaiting approval")
            for position, (row, path, digest) in enumerate(prepared):
                episode = row["episode"]
                if episode is None:
                    episode = Episode.objects.create(show=show, title=row["title"])
                    AuditEvent.objects.create(actor=actor, action="create", entity="Episode", entity_id=episode.pk, summary="Episode created from approved media queue intake")
                prior_versions = list(MediaAsset.objects.filter(episode=episode).values_list("version", flat=True))
                numbers = [int(value[1:]) for value in prior_versions if value.startswith("v") and value[1:].isdigit()]
                asset = MediaAsset.objects.create(episode=episode, file_name=_safe_name(row["upload"].name), kind="source", version=f"v{max(numbers or [0]) + 1}", smb_reference=str(path))
                AuditEvent.objects.create(actor=actor, action="create", entity="MediaAsset", entity_id=asset.pk, summary="Source asset created from private media queue intake")
                item = PreparationBatchItem.objects.create(batch=batch, asset=asset, selected_input_path=str(path.resolve()), selected_input_hash=digest, encode_before_transfer=row["encode_before_transfer"], position=position)
            if not UltraNexusTargetSettings.objects.filter(target=target, is_current=True).exists():
                raise ValueError("Media intake requires current device settings")
            approve_snapshot(batch, preparation_batch_snapshot(batch), approval=1, actor=actor)
            batch.status = "approved"
            batch.save(update_fields=["status"])
            PreparationJob.objects.get_or_create(batch=batch, idempotency_key=f"preparation:{batch.pk}:{batch.approval_1_hash}")
            return batch, True
    except Exception as exc:
        for path in paths:
            path.unlink(missing_ok=True)
        if isinstance(exc, IntegrityError):
            # The first write reserves the token. A concurrent duplicate may
            # finish after our optimistic lookup but before that write.
            existing = PreparationBatch.objects.filter(submission_token=token).first()
            if existing:
                if existing.target_id != target.pk or existing.show_id != show.pk:
                    raise ValueError("This submission token is already bound to another show or target.") from exc
                return existing, False
        raise


def queue_summary():
    """Return a polling-safe global summary without exposing private paths."""
    items = PreparationBatchItem.objects.select_related("batch", "asset", "asset__episode").order_by("batch__created_at", "position", "pk")
    counts = {state: 0 for state in QUEUE_STATES}
    rows = []
    for item in items:
        state = "queued" if item.execution_status == "pending" else item.execution_status
        if state == "ready" and not schedule_ready_binding(item.asset, item.batch.target):
            state = "blocked"
        if state not in QUEUE_STATES:
            state = "blocked"
        counts[state] = counts.get(state, 0) + 1
        rows.append({"id": item.pk, "batch_id": item.batch_id, "target_id": item.batch.target_id, "target": item.batch.target.name, "episode": getattr(item.asset.episode, "title", ""), "file_name": item.asset.file_name, "state": state, "blocker": item.blocker})
    return {"counts": counts, "items": rows, "updated_at": timezone.now().isoformat()}


def media_queue_summary(request):
    from .models import SchedulePublicationBatch
    from .schedule_preparation import publication_readiness
    result = queue_summary()
    result["publications"] = [{"id": batch.pk, **{
        key: value for key, value in publication_readiness(batch).items() if key != "waiting"
    }} for batch in SchedulePublicationBatch.objects.select_related("target").order_by("-pk")[:100]]
    response = JsonResponse(result)
    response["Cache-Control"] = "no-store"
    return response


def media_queue(request):
    if request.method == "POST":
        try:
            target = get_object_or_404(Device, pk=request.POST.get("target"))
            show = get_object_or_404(Show, pk=request.POST.get("show"))
            uploads = request.FILES.getlist("source_files")
            rows = [{"file": upload, "episode_id": request.POST.get(f"episode_id_{index}"), "new_title": request.POST.get(f"new_title_{index}", ""), "encode_before_transfer": request.POST.get("encode_before_transfer") == "on"} for index, upload in enumerate(uploads)]
            batch, created = create_media_queue_batch(target=target, show=show, rows=rows, submission_token=request.POST.get("submission_token") or None)
            messages.success(request, f"Batch {batch.pk} queued." if created else f"Batch {batch.pk} was already accepted; its original approved files are unchanged.")
        except (ValueError, IntegrityError, ValidationError, OSError) as exc:
            batches = PreparationBatch.objects.select_related("target").prefetch_related("items__asset__episode").order_by("-created_at")[:100]
            return render(request, "media_queue.html", {"batches": batches, "queue": queue_summary(), "error": str(exc) if isinstance(exc, (ValueError, ValidationError)) else "Unable to retain this intake. No batch was queued; select the files and try again.", "targets": Device.objects.all(), "shows": Show.objects.all(), "episodes": list(Episode.objects.order_by("show_id", "title").values("id", "show_id", "title")), "submission_token": request.POST.get("submission_token") or uuid.uuid4()}, status=400)
        return redirect("media-queue")
    batches = PreparationBatch.objects.select_related("target").prefetch_related("items__asset__episode").order_by("-created_at")[:100]
    episodes = list(Episode.objects.order_by("show_id", "title").values("id", "show_id", "title"))
    return render(request, "media_queue.html", {"batches": batches, "queue": queue_summary(), "targets": Device.objects.all(), "shows": Show.objects.all(), "episodes": episodes, "submission_token": uuid.uuid4()})


def retry_media_queue_item(item_id):
    """Explicitly requeue one failed/blocked item; siblings are untouched."""
    with transaction.atomic():
        item = PreparationBatchItem.objects.select_for_update().select_related("batch").get(pk=item_id)
        PreparationBatch.objects.select_for_update().get(pk=item.batch_id)
        if item.execution_status not in {"failed", "blocked"}:
            raise ValueError("Only failed or blocked items can be retried explicitly.")
        if PreparationJob.objects.filter(batch=item.batch, status__in=("queued", "running")).exists():
            raise ValueError("This batch already has active work.")
        item.execution_status = "pending"
        item.blocker = ""
        item.save(update_fields=["execution_status", "blocker"])
        job = PreparationJob.objects.create(batch=item.batch, idempotency_key=f"preparation:{item.batch_id}:{item.batch.approval_1_hash}:retry:{item.pk}:{uuid.uuid4().hex}", result={"item_ids": [item.pk]})
        return job


# Names used by the parent URL/view integration.
queue_preparation = create_media_queue_batch
media_queue_view = media_queue
queue_summary_view = media_queue_summary
media_queue_status = media_queue_summary


def media_item_retry(request, item_id):
    if request.method != "POST":
        return JsonResponse({"error": "POST required"}, status=405)
    try:
        job = retry_media_queue_item(item_id)
    except PreparationBatchItem.DoesNotExist:
        return JsonResponse({"error": "Item not found"}, status=404)
    except ValueError as exc:
        messages.error(request, str(exc))
    else:
        messages.success(request, "This item was queued for an explicit retry.")
    return redirect("media-queue")
