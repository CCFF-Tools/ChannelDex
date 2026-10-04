"""Private, durable multi-file media intake and preparation queue.

The queue deliberately owns intake and approval only.  Encoding and transfer
remain in :mod:`automation`, where the qualified adapters and gates live.
"""
from __future__ import annotations

import os
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
from .models import AuditEvent, Device, Episode, MediaAsset, MediaIntakeReview, PreparationBatch, PreparationBatchItem, PreparationJob, Show, UltraNexusTargetSettings


QUEUE_STATES = ("queued", "encoding", "validating", "transferring", "verifying", "ready", "blocked", "failed", "cancelled")
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
        state = "cancelled" if item.batch.status == "cancelled" else ("queued" if item.execution_status == "pending" else item.execution_status)
        if state == "ready" and not schedule_ready_binding(item.asset, item.batch.target):
            state = "blocked"
        if state not in QUEUE_STATES:
            state = "blocked"
        if state != "cancelled":
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


def intake_context(request):
    """Resolve show-context links without trusting unrelated query values."""
    show = Show.objects.filter(pk=request.GET.get("show")).first() if request.GET.get("show") else None
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
            "item": request.GET.get("item", "")}


def create_intake_review(*, target, show, episode=None, asset=None, action="prepare_only", order=0, item="", actor="owner"):
    """Persist the connected intake choice before any preparation is queued."""
    return MediaIntakeReview.objects.create(
        target=target, show=show, episode=episode, asset=asset, order=int(order or 0), action=action,
        payload={"item": str(item or ""), "episode_id": episode.pk if episode else None,
                 "asset_id": asset.pk if asset else None, "target_id": target.pk}, created_by=actor,
    )


def _stage_review_rows(review, rows):
    staged = []
    paths = []
    try:
        for position, row in enumerate(validate_rows(rows, show=review.show)):
            path = _store_upload(row["upload"])
            paths.append(path)
            staged.append({"path": str(path.resolve()), "hash": _sha256_file(path), "name": _safe_name(row["upload"].name),
                           "episode_id": row["episode"].pk if row["episode"] else None, "new_title": row["title"],
                           "encode_before_transfer": row["encode_before_transfer"], "position": position})
    except Exception:
        for path in paths:
            path.unlink(missing_ok=True)
        raise
    review.payload = {**review.payload, "rows": staged}
    review.save(update_fields=["payload"])
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
    review = MediaIntakeReview.objects.select_for_update().select_related("show", "target").get(pk=review.pk)
    if review.status == "confirmed":
        return PreparationBatch.objects.get(submission_token=review.token), False
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
        if not path.is_file() or _sha256_file(path) != row.get("hash"):
            raise ValueError("A staged source file changed or is unavailable; review the intake again.")
        episode = Episode.objects.filter(pk=row.get("episode_id"), show=review.show).first() if row.get("episode_id") else None
        if episode is None:
            episode = Episode.objects.create(show=review.show, title=row.get("new_title", "").strip())
        episodes.append(episode)
        prior = [int(v[1:]) for v in MediaAsset.objects.filter(episode=episode).values_list("version", flat=True) if v.startswith("v") and v[1:].isdigit()]
        asset = MediaAsset.objects.create(episode=episode, file_name=row["name"], kind="source", version=f"v{max(prior or [0]) + 1}", smb_reference=str(path))
        PreparationBatchItem.objects.create(batch=batch, asset=asset, selected_input_path=str(path), selected_input_hash=row["hash"], encode_before_transfer=bool(row.get("encode_before_transfer", True)), position=row.get("position", 0))
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
            episode = Episode.objects.filter(pk=request.POST.get("episode"), show=show).first() if show else None
            asset = MediaAsset.objects.filter(pk=request.POST.get("asset"), episode=episode).first() if episode else None
            response_status = 200
            if show and target:
                rows = [{"file": upload, "episode_id": request.POST.get(f"episode_id_{index}"), "new_title": request.POST.get(f"new_title_{index}", ""), "encode_before_transfer": request.POST.get("encode_before_transfer") == "on"} for index, upload in enumerate(request.FILES.getlist("source_files"))]
                review = None
                try:
                    validate_rows(rows, show=show)
                    review = create_intake_review(target=target, show=show, episode=episode, asset=asset,
                        action=request.POST.get("next_action", "prepare_only"), order=request.POST.get("order", 0), item=request.POST.get("item"))
                    _stage_review_rows(review, rows)
                    connected["review"] = review
                    connected["review_rows"] = review.payload.get("rows", [])
                    if review.action == "prepare_and_plan":
                        connected["premiere_candidates"] = _store_intake_premiere_proposals(review)
                    connected["reviewed"] = True
                except (ValueError, ValidationError, OSError) as exc:
                    if review is not None:
                        review.delete()
                    connected["error"] = str(exc) if isinstance(exc, (ValueError, ValidationError)) else "Unable to stage this intake. Select the files and try again."
                    response_status = 400
            else:
                connected["error"] = "Choose a show and destination before reviewing this intake."
                response_status = 400
            batches = PreparationBatch.objects.select_related("target").prefetch_related("items__asset__episode").order_by("-created_at")[:100]
            episodes = list(Episode.objects.order_by("show_id", "title").values("id", "show_id", "title"))
            return render(request, "media_queue.html", {"batches": batches, "queue": queue_summary(), "targets": Device.objects.all(),
                "shows": Show.objects.all(), "episodes": episodes, "submission_token": request.POST.get("submission_token") or uuid.uuid4(), "connected": connected}, status=response_status)
        if action == "confirm_intake":
            review = get_object_or_404(MediaIntakeReview, token=request.POST.get("review_token"))
            try:
                batch, created = confirm_intake_review(
                    review, first_premiere_date=request.POST.get("first_premiere_date"),
                )
            except (ValueError, ValidationError) as exc:
                connected.update(
                    show=review.show, target=review.target, review=review,
                    review_rows=review.payload.get("rows", []), reviewed=True, error=str(exc),
                )
                if review.action == "prepare_and_plan":
                    connected["premiere_candidates"] = _intake_candidate_context(review)
                return render(request, "media_queue.html", {
                    "batches": PreparationBatch.objects.select_related("target").prefetch_related("items__asset__episode").order_by("-created_at")[:100],
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
            return redirect("media-queue")
        # Keep the show/episode/asset context when the owner chooses the
        # connected premiere planning branch. No schedule is created here.
        if action in {"prepare_and_plan", "prepare_plan", "plan_premieres"}:
            from urllib.parse import urlencode
            params = {key: request.POST.get(key) for key in ("show", "episode", "asset", "target", "item", "order") if request.POST.get(key)}
            return redirect(f"/shows/{request.POST.get('show')}/premieres/?{urlencode(params)}")
        try:
            target = get_object_or_404(Device, pk=request.POST.get("target"))
            show = get_object_or_404(Show, pk=request.POST.get("show"))
            uploads = request.FILES.getlist("source_files")
            rows = [{"file": upload, "episode_id": request.POST.get(f"episode_id_{index}"), "new_title": request.POST.get(f"new_title_{index}", ""), "encode_before_transfer": request.POST.get("encode_before_transfer") == "on"} for index, upload in enumerate(uploads)]
            batch, created = create_media_queue_batch(target=target, show=show, rows=rows, submission_token=request.POST.get("submission_token") or None)
            messages.success(request, f"Batch {batch.pk} queued." if created else f"Batch {batch.pk} was already accepted; its original approved files are unchanged.")
        except (ValueError, IntegrityError, ValidationError, OSError) as exc:
            batches = PreparationBatch.objects.select_related("target").prefetch_related("items__asset__episode").order_by("-created_at")[:100]
            return render(request, "media_queue.html", {"batches": batches, "queue": queue_summary(), "error": str(exc) if isinstance(exc, (ValueError, ValidationError)) else "Unable to retain this intake. No batch was queued; select the files and try again.", "targets": Device.objects.all(), "shows": Show.objects.all(), "episodes": list(Episode.objects.order_by("show_id", "title").values("id", "show_id", "title")), "submission_token": request.POST.get("submission_token") or uuid.uuid4(), "connected": connected}, status=400)
        return redirect("media-queue")
    batches = PreparationBatch.objects.select_related("target").prefetch_related("items__asset__episode").order_by("-created_at")[:100]
    episodes = list(Episode.objects.order_by("show_id", "title").values("id", "show_id", "title"))
    return render(request, "media_queue.html", {"batches": batches, "queue": queue_summary(), "targets": Device.objects.all(), "shows": Show.objects.all(), "episodes": episodes, "submission_token": uuid.uuid4(), "connected": connected})


def retry_media_queue_item(item_id):
    """Explicitly requeue one failed/blocked item; siblings are untouched."""
    with transaction.atomic():
        item = PreparationBatchItem.objects.select_for_update().select_related("batch").get(pk=item_id)
        PreparationBatch.objects.select_for_update().get(pk=item.batch_id)
        if item.batch.status == "cancelled":
            raise ValueError("Cancelled media batches cannot be retried.")
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
