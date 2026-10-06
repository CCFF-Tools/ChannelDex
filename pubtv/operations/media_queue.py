"""Private, durable multi-file media intake and preparation queue.

The queue deliberately owns intake and approval only.  Encoding and transfer
remain in :mod:`automation`, where the qualified adapters and gates live.
"""
from __future__ import annotations

import os
import json
import re
import uuid
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from django.conf import settings
from django.contrib import messages
from django.db import IntegrityError, transaction
from django.core.exceptions import ValidationError
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone

from .automation import approve_snapshot, canonical_hash, preparation_batch_snapshot, schedule_ready_binding, _sha256_file
from .media_identity import asset_claim, file_metadata
from .models import AuditEvent, Device, Episode, MediaAsset, MediaIntakeReview, MediaRelinkReview, PreparationBatch, PreparationBatchItem, PreparationJob, Show, UltraNexusTargetSettings


QUEUE_STATES = ("queued", "encoding", "validating", "transferring", "verifying", "ready", "blocked", "failed", "cancelled")
MAX_UPLOAD_BYTES = 50 * 1024 * 1024 * 1024
SUPPORTED_VIDEO_SUFFIXES = {".mp4", ".mov", ".mxf", ".mpeg", ".mpg", ".m4v", ".avi", ".mkv"}


def accepted_media_batches():
    """Return accepted batches, retaining cancelled ones until explicitly hidden."""
    return (
        PreparationBatch.objects.filter(removed_from_list=False)
        .select_related("target")
        .prefetch_related("items__asset__episode")
        .order_by("-created_at")[:100]
    )


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


class IntakeValidationError(ValueError):
    def __init__(self, errors):
        self.row_errors = errors
        super().__init__("; ".join(f"Row {index + 1}: {'; '.join(messages)}" for index, messages in errors.items()))


def validate_rows(rows, *, show):
    """Validate all rows without writing; preserve individual error locations."""
    rows = list(rows or [])
    if not rows:
        raise ValueError("Add at least one source file.")
    seen, seen_paths, validated, errors = set(), set(), [], {}
    for index, row in enumerate(rows):
        problems = []
        source_path = row.get("source_path") or row.get("path")
        upload = row.get("file") or row.get("source_video") or row.get("upload")
        path, name = None, ""
        try:
            if source_path:
                path = Path(str(source_path))
                if not path.is_absolute():
                    raise ValueError("Enter an absolute local file path.")
                try:
                    path = path.resolve(strict=True)
                    size, mtime = file_metadata(path)
                except (OSError, RuntimeError):
                    raise ValueError("file is unavailable.")
                if len(str(path)) > 500:
                    raise ValueError("selected path is too long for intake.")
                name = path.name
                if str(path) in seen_paths:
                    raise ValueError("Each local source file may appear only once in a batch.")
                seen_paths.add(str(path))
            else:
                if upload is None or not getattr(upload, "name", ""):
                    raise ValueError("Every queue row requires a source file.")
                name, size = upload.name, getattr(upload, "size", 1)
            if size == 0:
                raise ValueError("empty source files are not allowed.")
            if size > MAX_UPLOAD_BYTES:
                raise ValueError("files larger than 50 GiB are not allowed.")
            if Path(name).suffix.casefold() not in SUPPORTED_VIDEO_SUFFIXES:
                raise ValueError("unsupported video file type.")
        except ValueError as exc:
            problems.append(str(exc))
        episode, title = None, ""
        try:
            if source_path and row.get("episode_id") in (None, "") and row.get("episode") is None:
                raise ValueError("Choose create a new episode or an explicit existing episode.")
            episode, title = _row_episode(row, show)
            key = f"episode:{episode.pk}" if episode else f"new:{title.casefold()}"
            if key in seen:
                raise ValueError("Each episode may appear only once in a batch.")
            seen.add(key)
        except (ValueError, TypeError) as exc:
            problems.append(str(exc))
        mode = row.get("encoding_mode")
        # Legacy upload callers already carry an explicit boolean. The new
        # local-path browser rows must carry an explicit mode or boolean.
        if mode not in ("needs_encoding", "already_encoded") and "encode_before_transfer" not in row:
            problems.append("Choose already encoded or needs encoding for this file.")
        for key, label in (("episode_number", "Episode number"), ("runtime_seconds", "Runtime seconds")):
            value = row.get(key)
            if value not in (None, "") and (not str(value).isdigit() or int(value) > 2147483647 or (key == "runtime_seconds" and int(value) == 0)):
                problems.append(f"{label} must be a positive whole number.")
        if problems:
            errors[index] = problems
        validated.append({"upload": upload, "source_path": str(path) if path else None, "name": name,
                          "episode": episode, "title": title,
                          "encode_before_transfer": mode == "needs_encoding" if mode else bool(row.get("encode_before_transfer")),
                          "episode_number": row.get("episode_number", ""), "runtime_seconds": row.get("runtime_seconds", "")})
    if errors:
        raise IntakeValidationError(errors)
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
                asset = MediaAsset.objects.create(episode=episode, file_name=_safe_name(row["upload"].name), kind="source" if row["encode_before_transfer"] else "encoded", version=f"v{max(numbers or [0]) + 1}", smb_reference=str(path), local_path=str(path.resolve()), content_identity=digest)
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
        state = "cancelled" if item.batch.status == "cancelled" else ("queued" if item.execution_status == "pending" else item.execution_status)
        if state == "ready" and not schedule_ready_binding(item.asset, item.batch.target):
            state = "blocked"
        if state not in QUEUE_STATES:
            state = "blocked"
        if state != "cancelled":
            counts[state] = counts.get(state, 0) + 1
        rows.append({"id": item.pk, "batch_id": item.batch_id, "target_id": item.batch.target_id, "target": item.batch.target.name, "episode": getattr(item.asset.episode, "title", ""), "file_name": item.asset.file_name, "state": state, "blocker": item.blocker})
    return {"counts": counts, "items": rows, "updated_at": timezone.now().isoformat()}


def _active_asset_work(asset_id):
    return PreparationJob.objects.filter(batch__items__asset_id=asset_id, status="running").exists()


def begin_relink_review(asset_id, path, *, actor="owner"):
    candidate = Path(str(path))
    if not candidate.is_absolute():
        raise ValueError("Relink requires an absolute local file path.")
    candidate = candidate.resolve(strict=True)
    size, mtime = file_metadata(candidate)
    if len(str(candidate)) > 500:
        raise ValueError("Relink path is too long.")
    with asset_claim(asset_id):
        asset = MediaAsset.objects.get(pk=asset_id)
        if _active_asset_work(asset_id):
            raise ValueError("Wait for active preparation before reviewing a new location.")
        return MediaRelinkReview.objects.create(asset=asset, proposed_path=str(candidate),
            file_size=size, mtime_ns=mtime, original_path=asset.local_path,
            original_identity=asset.content_identity, actor=actor)


def process_relink_review(review):
    """Background candidate identity review, with no inherited verification."""
    if review.status not in {"queued", "checking", "confirm_queued", "confirming"}:
        return review
    try:
        with asset_claim(review.asset_id):
            review.refresh_from_db()
            if review.status not in {"queued", "checking", "confirm_queued", "confirming"}:
                return review
            if _active_asset_work(review.asset_id):
                raise ValueError("Active preparation must finish before relink review.")
            finalizing = review.status in {"confirm_queued", "confirming"}
            review.status = "confirming" if finalizing else "checking"
            review.save(update_fields=["status"])
            candidate = Path(review.proposed_path)
            expected = (review.file_size, review.mtime_ns)
            if file_metadata(candidate) != expected:
                raise ValueError("The candidate changed; start a fresh relink review.")
            digest = _sha256_file(candidate)
            if file_metadata(candidate) != expected:
                raise ValueError("The candidate changed during review; start a fresh relink review.")
            asset = MediaAsset.objects.get(pk=review.asset_id)
            if (asset.local_path, asset.content_identity) != (review.original_path, review.original_identity):
                raise ValueError("The asset changed in another tab; start a fresh relink review.")
            review.observed_hash = digest
            review.status = "matched" if review.original_identity and digest == review.original_identity else "new_version"
            review.error = "" if review.status == "matched" else "These bytes have a different or unknown identity. Add them as a reviewed new version with fresh preparation."
            if finalizing and review.status == "matched":
                review.status = "confirming"
                review.save(update_fields=["observed_hash", "status", "error"])
                _commit_relink_review(review)
                review.refresh_from_db()
            else:
                review.save(update_fields=["observed_hash", "status", "error"])
    except (OSError, RuntimeError, ValueError) as exc:
        review.status = "failed"
        review.error = str(exc)
        review.save(update_fields=["status", "error"])
    return review


def relink_review_result(review):
    from urllib.parse import urlencode
    result = {"review_token": str(review.token), "asset_id": review.asset_id,
              "status": review.status, "error": review.error,
              "file_size": review.file_size, "mtime_ns": review.mtime_ns,
              "requires_review": review.status != "confirmed"}
    if review.status == "new_version":
        params = {"path": review.proposed_path}
        if review.asset.episode_id:
            params.update(show=review.asset.episode.show_id, episode=review.asset.episode_id)
        result["new_intake_url"] = "/media/?" + urlencode(params)
    return result


def confirm_relink_review(token, *, actor="owner", asset_id=None):
    """Record explicit intent; final byte verification stays in the worker."""
    review = MediaRelinkReview.objects.select_related("asset").get(token=token)
    if asset_id is not None and review.asset_id != asset_id:
        raise ValueError("This review belongs to a different media asset.")
    with asset_claim(review.asset_id):
        review.refresh_from_db()
        asset = MediaAsset.objects.get(pk=review.asset_id)
        if review.status in {"confirmed", "confirm_queued", "confirming"}:
            return asset, False
        if review.status != "matched" or not review.original_identity or review.observed_hash != review.original_identity:
            raise ValueError("Background identity review must match before confirmation. Different or unknown bytes require new-version intake.")
        if _active_asset_work(asset.pk):
            raise ValueError("Wait for active preparation before confirming a new location.")
        if (asset.local_path, asset.content_identity) != (review.original_path, review.original_identity):
            raise ValueError("The asset changed; start a fresh relink review.")
        if file_metadata(review.proposed_path) != (review.file_size, review.mtime_ns):
            raise ValueError("The reviewed file changed; start a fresh relink review.")
        queued = MediaRelinkReview.objects.filter(pk=review.pk, status="matched").update(status="confirm_queued", actor=actor)
        if not queued:
            raise ValueError("This review changed; reload before confirming.")
        return asset, True


def _commit_relink_review(review):
    """Finalize only under the asset claim after worker rehash and metadata check."""
    with transaction.atomic():
        # The first SQL write reserves finalization on SQLite. No expensive IO
        # holds this writer transaction or runs in the confirmation request.
        reserved = MediaRelinkReview.objects.filter(pk=review.pk, status="confirming").update(status="confirmed")
        if not reserved:
            raise ValueError("This review changed; reload before confirming.")
        asset = MediaAsset.objects.get(pk=review.asset_id)
        if _active_asset_work(asset.pk):
            raise ValueError("Active preparation prevents relink finalization.")
        if (asset.local_path, asset.content_identity) != (review.original_path, review.original_identity):
            raise ValueError("The asset changed; start a fresh relink review.")
        if file_metadata(review.proposed_path) != (review.file_size, review.mtime_ns):
            raise ValueError("The reviewed file changed; start a fresh relink review.")
        updated = MediaAsset.objects.filter(pk=asset.pk, local_path=review.original_path, content_identity=review.original_identity).update(local_path=review.proposed_path)
        if updated != 1:
            raise ValueError("The asset changed; start a fresh relink review.")
        affected = PreparationBatchItem.objects.filter(asset=asset).exclude(execution_status="ready").exclude(batch__status="cancelled")
        affected_ids = list(affected.values_list("pk", flat=True))
        affected_batch_ids = list(affected.values_list("batch_id", flat=True))
        affected.update(execution_status="blocked", blocker="Source location changed; review this input again before retrying.", approval_1_status="stale")
        for job in PreparationJob.objects.filter(batch_id__in=affected_batch_ids, status="queued"):
            selected = (job.result or {}).get("item_ids")
            # Selective sibling jobs remain eligible. Whole batch jobs are
            # cancelled; untouched pending siblings get a separate claim.
            if selected and not set(selected).intersection(affected_ids):
                continue
            job.status = "cancelled"
            job.error = "Input location changed; fresh owner review required."
            job.finished_at = timezone.now()
            job.save(update_fields=["status", "error", "finished_at"])
            pending = list(job.batch.items.filter(execution_status="pending").values_list("pk", flat=True))
            if pending:
                PreparationJob.objects.get_or_create(idempotency_key=f"relink-siblings:{job.pk}", defaults={"batch": job.batch, "result": {"item_ids": pending}})
        AuditEvent.objects.create(actor=review.actor, action="relink", entity="MediaAsset", entity_id=asset.pk,
            summary=json.dumps({"event": "Matching media bytes moved to a reviewed local path", "old_path": review.original_path, "new_path": review.proposed_path, "identity": review.observed_hash}, ensure_ascii=False))
        asset.refresh_from_db()


def relink_media_asset(asset_id, path, *, actor="owner", expected_identity="", confirm=False):
    # Compatibility entry point deliberately cannot bypass background review.
    if confirm:
        raise ValueError("Create a background relink review and confirm its matched token.")
    return relink_review_result(begin_relink_review(asset_id, path, actor=actor))


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


def intake_context(request):
    """Resolve show-context links without trusting unrelated query values."""
    show = Show.objects.filter(pk=request.GET.get("show")).first() if request.GET.get("show") else None
    if show is None and not request.GET.get("show") and Show.objects.count() == 1:
        show = Show.objects.first()
    episode = Episode.objects.filter(pk=request.GET.get("episode"), show=show).first() if show and request.GET.get("episode") else None
    asset = MediaAsset.objects.filter(pk=request.GET.get("asset"), episode=episode).first() if episode and request.GET.get("asset") else None
    targets = Device.objects.order_by("name")
    target = Device.objects.filter(pk=request.GET.get("target")).first() if request.GET.get("target") else None
    if target is None:
        configured = Device.objects.filter(ultranexus_settings__is_current=True).order_by("name")
        target = configured.first() if configured.count() == 1 else (targets.first() if targets.count() == 1 else None)
    try:
        order = max(0, int(request.GET.get("order", "0")))
    except (TypeError, ValueError):
        order = 0
    return {"show": show, "episode": episode, "asset": asset, "target": target, "order": order,
            "item": request.GET.get("item", ""), "has_context": bool(request.GET), "accepted": request.COOKIES.get("media_intake_accepted") == "1", "draft_rows": ([{"source_path": request.GET["path"], "episode_id": str(episode.pk) if episode else "new", "new_title": Path(request.GET["path"]).stem, "encoding_mode": "", "episode_number": "", "runtime_seconds": ""}] if request.GET.get("path") else [])}


def create_intake_review(*, target, show, episode=None, asset=None, action="prepare_only", order=0, item="", actor="owner", submission_token=None):
    """Persist the connected intake choice before any preparation is queued."""
    token = uuid.UUID(str(submission_token)) if submission_token else uuid.uuid4()
    review, created = MediaIntakeReview.objects.get_or_create(token=token, defaults={
        "target": target, "show": show, "episode": episode, "asset": asset, "order": int(order or 0), "action": action,
        "payload": {"item": str(item or ""), "episode_id": episode.pk if episode else None,
                    "asset_id": asset.pk if asset else None, "target_id": target.pk}, "created_by": actor})
    if (review.target_id, review.show_id, review.action) != (target.pk, show.pk, action):
        raise ValueError("This review token belongs to different intake choices. Start a fresh review.")
    return review


def _stage_review_rows(review, rows):
    staged = []
    for position, row in enumerate(validate_rows(rows, show=review.show)):
        if not row.get("source_path"):
            raise ValueError("Select local source paths with the file chooser.")
        path = Path(row["source_path"])
        stat = path.stat()
        staged.append({"path": str(path), "name": row["name"],
                       "size": stat.st_size, "mtime_ns": stat.st_mtime_ns,
                       "episode_id": row["episode"].pk if row["episode"] else None, "new_title": row["title"],
                       "encode_before_transfer": row["encode_before_transfer"], "position": position,
                       "content_identity": "", "episode_number": row.get("episode_number", ""),
                       "runtime_seconds": row.get("runtime_seconds", "")})
    if review.payload.get("rows"):
        if review.payload["rows"] != staged:
            raise ValueError("The draft changed after this review. Start a fresh intake review.")
        return staged
    if review.status != "pending":
        raise ValueError("This intake review is no longer pending.")
    initial = review.payload
    payload = {**initial, "rows": staged}
    updated = MediaIntakeReview.objects.filter(pk=review.pk, status="pending", payload=initial).update(payload=payload)
    if not updated:
        review.refresh_from_db()
        if review.payload.get("rows") != staged:
            raise ValueError("This draft was reviewed in another tab. Start a fresh review.")
    else:
        review.payload = payload
    return staged


def _intake_premiere_proposal(review, first_date):
    """Build the exact run the owner will review without creating episodes."""
    from .premiere_planning import _active_premiere_dates

    rows = sorted(review.payload.get("rows") or [], key=lambda value: (value.get("position", 0), value.get("name", "")))
    if not rows:
        raise ValueError("This intake review has no staged files; review the files again.")
    dates = _active_premiere_dates(review.show, first_date, len(rows))
    week_starts = [value - timedelta(days=value.weekday()) for value in dates]
    assignments = list(review.show.weekly_assignments.order_by("week_start", "pk"))
    if any(assignment.week_start in week_starts for assignment in assignments):
        raise ValidationError("One or more proposed premiere cycles is already occupied, including an explicit No program cycle.")
    slots = list(review.show.slots.order_by("weekday", "start_time", "pk"))
    existing_episode_ids = [row["episode_id"] for row in rows if row.get("episode_id")]
    episodes = {
        episode.pk: episode for episode in Episode.objects.filter(show=review.show, pk__in=existing_episode_ids)
    }
    if len(episodes) != len(set(existing_episode_ids)):
        raise ValidationError("An episode selected during intake is no longer available; review the intake again.")

    cycles = []
    tz = ZoneInfo(review.show.station.timezone)
    for row, premiere_date in zip(rows, dates):
        premiere_slot = next((
            slot for slot in slots
            if slot.is_premiere and slot.weekday == premiere_date.weekday()
            and (not slot.active_from or slot.active_from <= premiere_date)
            and (not slot.active_until or premiere_date <= slot.active_until)
        ), None)
        if premiere_slot is None:
            raise ValidationError(
                f"No active premiere slot is configured for {premiere_date.strftime('%A, %B %-d, %Y')}."
            )
        cycle_start = datetime.combine(premiere_date, premiere_slot.start_time, tzinfo=tz)
        cycle_end = cycle_start + timedelta(days=7)
        occurrences = []
        for slot in slots:
            for offset in range(8):
                day = premiere_date + timedelta(days=offset)
                if day.weekday() != slot.weekday or (slot.active_from and day < slot.active_from) or (slot.active_until and day > slot.active_until):
                    continue
                starts_at = datetime.combine(day, slot.start_time, tzinfo=tz)
                if cycle_start <= starts_at < cycle_end:
                    occurrences.append({
                        "date": day.isoformat(), "time": slot.start_time.isoformat(timespec="minutes"),
                        "role": "premiere" if slot.pk == premiere_slot.pk else "replay", "slot_id": slot.pk,
                    })
        episode = episodes.get(row.get("episode_id"))
        cycles.append({
            "title": episode.title if episode else row.get("new_title", ""),
            "premiere_date": premiere_date.isoformat(),
            "week_start": (premiere_date - timedelta(days=premiere_date.weekday())).isoformat(),
            "occurrences": sorted(occurrences, key=lambda item: (item["date"], item["time"], item["slot_id"])),
        })

    inputs = {
        "show": [review.show_id, review.show.title, review.show.slot_duration_seconds, review.show.station.timezone],
        "first_premiere_date": first_date.isoformat(),
        "rows": [{key: row.get(key) for key in ("hash", "name", "episode_id", "new_title", "encode_before_transfer", "position")} for row in rows],
        "episodes": [[episode.pk, episode.title, episode.status, episode.intended_air_order,
                      episode.intended_premiere_date.isoformat() if episode.intended_premiere_date else None,
                      episode.runtime_seconds] for episode in sorted(episodes.values(), key=lambda value: value.pk)],
        "assignments": [[assignment.pk, assignment.week_start.isoformat(),
                         assignment.premiere_date.isoformat() if assignment.premiere_date else None,
                         assignment.episode_id, assignment.selection_type, assignment.is_automatic_carry_forward]
                        for assignment in assignments],
        "slots": [[slot.pk, slot.station_id, slot.weekday, slot.start_time.isoformat(), slot.duration_seconds,
                   slot.is_premiere, slot.active_from.isoformat() if slot.active_from else None,
                   slot.active_until.isoformat() if slot.active_until else None] for slot in slots],
        "cycles": cycles,
    }
    return {"snapshot": inputs, "fingerprint": canonical_hash(inputs), "cycles": cycles}


def _intake_candidate_context(review):
    """Attach persisted exact-run proposals to the next configured dates."""
    from .premiere_planning import premiere_candidates

    candidates = premiere_candidates(review.show)
    proposals = review.payload.get("premiere_proposals") or {}
    for candidate in candidates:
        candidate["proposal"] = proposals.get(candidate["date"].isoformat())
    return candidates


def _store_intake_premiere_proposals(review):
    """Persist the reviewable proposals for each currently available candidate."""
    from .premiere_planning import premiere_candidates

    candidates = premiere_candidates(review.show)
    proposals = {}
    for candidate in candidates:
        if candidate["occupied"]:
            continue
        try:
            proposal = _intake_premiere_proposal(review, candidate["date"])
        except ValidationError as exc:
            candidate["reason"] = str(exc)
            candidate["proposal"] = None
            continue
        proposals[candidate["date"].isoformat()] = proposal
        candidate["proposal"] = proposal
    review.payload = {**review.payload, "premiere_proposals": proposals}
    review.save(update_fields=["payload"])
    return candidates


@transaction.atomic
def confirm_intake_review(review, *, first_premiere_date=None, actor="owner"):
    """Materialize one reviewed intake exactly once, preserving Approval 1."""
    # Reserve with the first SQL statement. SQLite has no row locks; a read
    # before this CAS can otherwise race into a read-to-write lock upgrade.
    reserved = MediaIntakeReview.objects.filter(pk=review.pk, status="pending").update(status="confirming")
    review = MediaIntakeReview.objects.select_related("show", "target").get(pk=review.pk)
    if not reserved:
        if review.status == "confirmed":
            return PreparationBatch.objects.get(submission_token=review.token), False
        raise ValueError("This intake is no longer pending; reload to see its result.")
    rows = review.payload.get("rows") or []
    if not rows:
        raise ValueError("This intake review has no staged files; review the files again.")
    if not UltraNexusTargetSettings.objects.filter(target=review.target, is_current=True).exists():
        raise ValueError("Media intake requires current device settings")
    Show.objects.select_for_update().get(pk=review.show_id)
    proposal = None
    if review.action == "prepare_and_plan":
        if not first_premiere_date:
            raise ValueError("Choose the first valid premiere date.")
        try:
            first_date = date.fromisoformat(str(first_premiere_date))
        except (TypeError, ValueError) as exc:
            raise ValueError("Choose a valid first premiere date.") from exc
        reviewed = (review.payload.get("premiere_proposals") or {}).get(first_date.isoformat())
        if not reviewed:
            raise ValueError("That premiere date was not part of this intake review; review the intake again.")
        list(review.show.weekly_assignments.select_for_update())
        list(review.show.slots.select_for_update())
        selected_ids = [row["episode_id"] for row in rows if row.get("episode_id")]
        list(Episode.objects.select_for_update().filter(show=review.show, pk__in=selected_ids))
        proposal = _intake_premiere_proposal(review, first_date)
        if proposal["fingerprint"] != reviewed.get("fingerprint") or proposal["cycles"] != reviewed.get("cycles"):
            raise ValidationError("The show, queue, recurrence, or assignments changed after review; review the intake again.")

    batch = PreparationBatch.objects.create(target=review.target, show=review.show,
        submission_token=review.token, label=f"{review.show.title} media queue", created_by=actor)
    AuditEvent.objects.create(actor=actor, action="create", entity="PreparationBatch", entity_id=batch.pk, summary="Reviewed media intake batch created")
    episodes = []
    for row in sorted(rows, key=lambda value: (value.get("position", 0), value.get("name", ""))):
        path = Path(row["path"])
        try:
            stat = path.stat()
        except OSError:
            raise ValueError("A staged source file changed or is unavailable; review the intake again.")
        metadata = (stat.st_size, stat.st_mtime_ns)
        expected = (row.get("size"), row.get("mtime_ns"))
        if not path.is_file() or len(str(path)) > 500 or metadata != expected:
            raise ValueError("A staged source file changed or is unavailable; review the intake again.")
        episode = Episode.objects.filter(pk=row.get("episode_id"), show=review.show).first() if row.get("episode_id") else None
        if row.get("episode_id") and episode is None:
            raise ValueError("The selected existing episode changed; review the intake again.")
        if episode is None:
            episode = Episode.objects.create(show=review.show, title=row.get("new_title", "").strip(),
                episode_number=int(row["episode_number"]) if str(row.get("episode_number", "")).isdigit() else None,
                runtime_seconds=int(row["runtime_seconds"]) if str(row.get("runtime_seconds", "")).isdigit() else None,
                runtime_provenance="manual" if str(row.get("runtime_seconds", "")).isdigit() else "")
            AuditEvent.objects.create(actor=actor, action="create", entity="Episode", entity_id=episode.pk, summary="Episode created from reviewed local media intake")
        elif str(row.get("episode_number", "")).isdigit() or str(row.get("runtime_seconds", "")).isdigit():
            if str(row.get("episode_number", "")).isdigit():
                episode.episode_number = int(row["episode_number"])
            if str(row.get("runtime_seconds", "")).isdigit():
                episode.runtime_seconds = int(row["runtime_seconds"])
                episode.runtime_provenance = "manual"
            episode.save(update_fields=["episode_number", "runtime_seconds", "runtime_provenance"])
            AuditEvent.objects.create(actor=actor, action="update", entity="Episode", entity_id=episode.pk, summary="Manual episode number/runtime recorded during media intake")
        episodes.append(episode)
        prior = [int(v[1:]) for v in MediaAsset.objects.filter(episode=episode).values_list("version", flat=True) if v.startswith("v") and v[1:].isdigit()]
        encode = bool(row.get("encode_before_transfer"))
        asset = MediaAsset.objects.create(episode=episode, file_name=row["name"], kind="source" if encode else "encoded",
            version=f"v{max(prior or [0]) + 1}", local_path=str(path.resolve()),
            runtime_seconds=int(row["runtime_seconds"]) if str(row.get("runtime_seconds", "")).isdigit() else None,
            runtime_provenance="manual" if str(row.get("runtime_seconds", "")).isdigit() else "")
        AuditEvent.objects.create(actor=actor, action="create", entity="MediaAsset", entity_id=asset.pk, summary="Reviewed local file reference added to library")
        PreparationBatchItem.objects.create(batch=batch, asset=asset, selected_input_path=str(path),
            reviewed_file_size=row["size"], reviewed_mtime_ns=row["mtime_ns"],
            encode_before_transfer=encode, position=row.get("position", 0))
    approve_snapshot(batch, preparation_batch_snapshot(batch), approval=1, actor=actor)
    batch.status = "approved"; batch.save(update_fields=["status"])
    PreparationJob.objects.get_or_create(batch=batch, idempotency_key=f"preparation:{batch.pk}:{batch.approval_1_hash}")
    assignment_ids = []
    if review.action == "prepare_and_plan":
        from .services import audit, materialize_assignment
        from .models import WeeklyEpisodeAssignment
        for episode, cycle in zip(episodes, proposal["cycles"]):
            premiere_date = date.fromisoformat(cycle["premiere_date"])
            assignment = WeeklyEpisodeAssignment(
                show=review.show, week_start=date.fromisoformat(cycle["week_start"]),
                premiere_date=premiere_date, episode=episode, selection_type="premiere",
            )
            assignment.full_clean()
            assignment.save()
            materialize_assignment(assignment)
            audit("create", "WeeklyEpisodeAssignment", assignment, f"Reviewed intake premiere planned: {episode.title} on {premiere_date}", actor)
            assignment_ids.append(assignment.pk)
    review.payload = {**review.payload, "assignment_ids": assignment_ids,
                      "first_premiere_date": str(first_premiere_date or "")}
    review.status = "confirmed"
    review.save(update_fields=["payload", "status"])
    return batch, True


def media_queue(request):
    connected = intake_context(request)
    if request.method == "POST":
        action = request.POST.get("action", "prepare_only")
        if action in {"review", "review_intake"}:
            show = Show.objects.filter(pk=request.POST.get("show")).first()
            target = Device.objects.filter(pk=request.POST.get("target")).first()
            episode_id = request.POST.get("episode") or None
            episode = Episode.objects.filter(pk=episode_id, show=show).first() if show and episode_id else None
            asset_id = request.POST.get("asset") or None
            asset = MediaAsset.objects.filter(pk=asset_id, episode=episode).first() if episode and asset_id else None
            response_status = 200
            connected.update(show=show, target=target)
            if request.POST.get("draft_rows"):
                try:
                    draft = json.loads(request.POST["draft_rows"])
                    connected["draft_rows"] = draft if isinstance(draft, list) else []
                except (ValueError, TypeError):
                    pass
            if show and target:
                source_paths = request.POST.getlist("source_paths")
                manual_path = (request.POST.get("source_path_manual") or "").strip()
                if manual_path:
                    source_paths.append(manual_path)
                rows = []
                if request.POST.get("draft_rows"):
                    try:
                        rows = json.loads(request.POST["draft_rows"])
                        if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
                            raise ValueError("Invalid draft rows.")
                    except (ValueError, TypeError):
                        connected["error"] = "The intake draft could not be read. Reload your saved draft and try again."
                        rows = []
                else:
                    for index, source_path in enumerate(source_paths):
                        row = {"source_path": source_path, "episode_id": request.POST.get(f"episode_id_{index}"), "new_title": request.POST.get(f"new_title_{index}", ""), "episode_number": request.POST.get(f"episode_number_{index}"), "runtime_seconds": request.POST.get(f"runtime_seconds_{index}")}
                        mode = request.POST.get(f"encoding_mode_{index}")
                        if mode:
                            row["encoding_mode"] = mode
                        elif f"encode_before_transfer_{index}" in request.POST or "encode_before_transfer" in request.POST:
                            row["encode_before_transfer"] = request.POST.get(f"encode_before_transfer_{index}", request.POST.get("encode_before_transfer")) == "on"
                        rows.append(row)
                connected.update(show=show, target=target, draft_rows=rows)

                review = None
                try:
                    review = create_intake_review(target=target, show=show, episode=episode, asset=asset,
                        action="prepare_and_plan" if request.POST.get("next_action") == "prepare_and_plan" else "prepare_only", order=request.POST.get("order", 0), item=request.POST.get("item"), submission_token=request.POST.get("submission_token") or None)
                    _stage_review_rows(review, rows)
                    connected["show"] = show
                    connected["target"] = target
                    connected["review"] = review
                    connected["review_rows"] = review.payload.get("rows", [])
                    if review.action == "prepare_and_plan":
                        connected["premiere_candidates"] = _store_intake_premiere_proposals(review)
                    connected["reviewed"] = True
                except (ValueError, ValidationError, OSError) as exc:
                    if review is not None and not review.payload.get("rows") and review.status == "pending":
                        MediaIntakeReview.objects.filter(pk=review.pk, status="pending", payload=review.payload).delete()
                    connected["error"] = str(exc) if isinstance(exc, (ValueError, ValidationError)) else "Unable to stage this intake. Select the files and try again."
                    connected["row_errors"] = getattr(exc, "row_errors", {})
                    response_status = 400
            else:
                connected["error"] = "Choose a show and destination before reviewing this intake."
                response_status = 400
            batches = accepted_media_batches()
            episodes = list(Episode.objects.order_by("show_id", "title").values("id", "show_id", "title"))
            return render(request, "media_queue.html", {"batches": batches, "queue": queue_summary(), "targets": Device.objects.all(),
                "shows": Show.objects.all(), "episodes": episodes, "submission_token": uuid.uuid4() if connected.get("reviewed") else request.POST.get("submission_token") or uuid.uuid4(), "connected": connected}, status=response_status)
        if action == "confirm_intake":
            review = get_object_or_404(MediaIntakeReview, token=request.POST.get("review_token"))
            try:
                batch, created = confirm_intake_review(
                    review, first_premiere_date=request.POST.get("first_premiere_date"),
                )
            except (ValueError, ValidationError) as exc:
                connected.update(
                    show=review.show, target=review.target, review=review,
                    review_rows=review.payload.get("rows", []), draft_rows=[{**row, "episode_id": row["episode_id"] or "new", "source_path": row["path"], "encoding_mode": "needs_encoding" if row["encode_before_transfer"] else "already_encoded"} for row in review.payload.get("rows", [])], reviewed=True, error=str(exc),
                )
                if review.action == "prepare_and_plan":
                    connected["premiere_candidates"] = _intake_candidate_context(review)
                return render(request, "media_queue.html", {
                    "batches": accepted_media_batches(),
                    "queue": queue_summary(), "targets": Device.objects.all(), "shows": Show.objects.all(),
                    "episodes": list(Episode.objects.order_by("show_id", "title").values("id", "show_id", "title")),
                    "submission_token": uuid.uuid4(), "connected": connected,
                }, status=400)
            review.refresh_from_db()
            if review.action == "prepare_and_plan":
                from urllib.parse import urlencode
                params = [("target", review.target_id)] + [
                    ("assignment", assignment_id) for assignment_id in review.payload.get("assignment_ids", [])
                ]
                return redirect(f"/schedule/prepare/?{urlencode(params)}")
            messages.success(request, f"Batch {batch.pk} queued." if created else f"Batch {batch.pk} was already accepted.")
            response = redirect("media-queue")
            response.set_cookie("media_intake_accepted", "1", httponly=True, samesite="Strict")
            return response
        # Keep the show/episode/asset context when the owner chooses the
        # connected premiere planning branch. No schedule is created here.
        if action in {"prepare_and_plan", "prepare_plan", "plan_premieres"}:
            from urllib.parse import urlencode
            params = {key: request.POST.get(key) for key in ("show", "episode", "asset", "target", "item", "order") if request.POST.get(key)}
            return redirect(f"/shows/{request.POST.get('show')}/premieres/?{urlencode(params)}")
        # Browser intake accepts local references only. Historical preparation
        # helpers remain available to old records/callers, but this endpoint
        # never copies uploads or hashes video during an HTTP request.
        connected["error"] = "Use Add episode files or an absolute local path, then review the intake."
        return render(request, "media_queue.html", {"batches": accepted_media_batches(), "queue": queue_summary(),
            "targets": Device.objects.all(), "shows": Show.objects.all(),
            "episodes": list(Episode.objects.order_by("show_id", "title").values("id", "show_id", "title")),
            "submission_token": request.POST.get("submission_token") or uuid.uuid4(), "connected": connected}, status=400)
    batches = accepted_media_batches()
    episodes = list(Episode.objects.order_by("show_id", "title").values("id", "show_id", "title"))
    response = render(request, "media_queue.html", {"batches": batches, "queue": queue_summary(), "targets": Device.objects.all(), "shows": Show.objects.all(), "episodes": episodes, "submission_token": uuid.uuid4(), "connected": connected})
    if connected["accepted"]:
        response.delete_cookie("media_intake_accepted")
    return response


def retry_media_queue_item(item_id):
    """Explicitly requeue one failed/blocked item; siblings are untouched."""
    with transaction.atomic():
        item = PreparationBatchItem.objects.select_for_update().select_related("batch").get(pk=item_id)
        PreparationBatch.objects.select_for_update().get(pk=item.batch_id)
        if item.batch.status == "cancelled":
            raise ValueError("Cancelled media batches cannot be retried.")
        if item.approval_1_status == "stale":
            raise ValueError("Source input changed; a fresh intake review is required before resuming.")
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


def cancel_media_queue_batch(batch_id, *, actor="owner"):
    """Cancel a media batch while retaining its durable records and audit trail."""
    allowed = {"approved", "blocked", "failed"}
    with transaction.atomic():
        batch = PreparationBatch.objects.select_for_update().get(pk=batch_id)
        if batch.status == "cancelled":
            return batch, False
        if batch.status == "in_progress":
            raise ValueError("In-progress work cannot be removed safely.")
        if batch.status not in allowed:
            raise ValueError("Only approved, blocked, or failed media batches can be removed from the active queue.")
        if batch.jobs.filter(status="running").exists():
            raise ValueError("In-progress work cannot be removed safely while a preparation job is running.")
        batch.status = "cancelled"
        batch.save(update_fields=["status"])
        batch.jobs.filter(status="queued").update(
            status="cancelled", finished_at=timezone.now(), error="Cancelled by owner"
        )
        AuditEvent.objects.create(
            actor=actor, action="cancel", entity="PreparationBatch", entity_id=batch.pk,
            summary="Media queue batch cancelled by owner",
        )
        return batch, True


def media_batch_cancel(request, batch_id):
    if request.method != "POST":
        return JsonResponse({"error": "POST required"}, status=405)
    try:
        _batch, cancelled = cancel_media_queue_batch(batch_id)
    except PreparationBatch.DoesNotExist:
        return JsonResponse({"error": "Batch not found"}, status=404)
    except ValueError as exc:
        messages.error(request, str(exc))
    else:
        messages.success(request, "This media batch was cancelled." if cancelled else "This media batch was already cancelled.")
    return redirect("media-queue")


def remove_cancelled_media_batch(batch_id, *, actor="owner"):
    """Hide a cancelled batch from the accepted list without deleting history."""
    with transaction.atomic():
        batch = PreparationBatch.objects.select_for_update().get(pk=batch_id)
        if batch.status != "cancelled":
            raise ValueError("Only cancelled media batches can be removed from the list.")
        if batch.removed_from_list:
            return batch, False
        batch.removed_from_list = True
        batch.save(update_fields=["removed_from_list"])
        AuditEvent.objects.create(
            actor=actor, action="remove_from_list", entity="PreparationBatch", entity_id=batch.pk,
            summary="Cancelled media queue batch removed from accepted list",
        )
        return batch, True


def media_batch_remove(request, batch_id):
    wants_json = (
        request.headers.get("X-Requested-With") == "XMLHttpRequest"
        or "application/json" in request.headers.get("Accept", "")
    )
    if request.method != "POST":
        return JsonResponse({"error": "POST required"}, status=405)
    try:
        batch, removed = remove_cancelled_media_batch(batch_id)
    except PreparationBatch.DoesNotExist:
        return JsonResponse({"error": "Batch not found"}, status=404)
    except ValueError as exc:
        if wants_json:
            return JsonResponse({"error": str(exc)}, status=400)
        messages.error(request, str(exc))
    else:
        if wants_json:
            return JsonResponse({"removed": removed, "batch_id": batch.pk})
        messages.success(request, "This cancelled media batch was removed from the accepted list." if removed else "This media batch was already removed from the accepted list.")
    return redirect("media-queue")
