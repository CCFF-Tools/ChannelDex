"""Reviewed cycle/media selection, independent of the media worker."""
from __future__ import annotations

from dataclasses import dataclass
import uuid

from django.contrib import messages
from django.core import signing
from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import F
from django.shortcuts import render, redirect
from django.utils import timezone

from .automation import canonical_hash, schedule_ready_binding, target_settings_snapshot
from .models import (Device, MediaAsset, Occurrence, OccurrenceRevisionSelection,
    PreparationBatchItem, PublicationCycleSelection, SchedulePublicationBatch,
    UploadedOccurrenceCoverage, WeeklyEpisodeAssignment)
from .services import audit
from .active_station import resolve_active_station

SALT = "channeldex.schedule-cycle-review.v1"


class SchedulePreparationError(ValueError):
    pass


class SchedulePreparationStale(SchedulePreparationError):
    pass


@dataclass
class PreparationRow:
    assignment: WeeklyEpisodeAssignment
    asset: MediaAsset | None
    occurrences: tuple
    excluded: tuple
    readiness: str
    blockers: tuple
    entries: tuple
    job: PreparationBatchItem | None


def _assignment_snapshot(assignment):
    return {"id": assignment.pk, "show_id": assignment.show_id,
        "week_start": str(assignment.week_start), "premiere_date": str(assignment.premiere_date),
        "episode_id": assignment.episode_id, "selection_type": assignment.selection_type,
        "timezone": assignment.show.station.timezone}


def _asset_snapshot(asset):
    if asset is None:
        return None
    return {"id": asset.pk, "asset_id": str(asset.asset_id), "episode_id": asset.episode_id,
        "version": asset.version, "file_name": asset.file_name, "kind": asset.kind,
        "source_reference_hash": canonical_hash(asset.smb_reference),
        "inputs": list(asset.preparation_batch_items.order_by("pk").values(
            "pk", "selected_input_hash", "batch__target_id"))}


def cycle_occurrences(assignment, **kwargs):
    return list(assignment.occurrences.select_related("asset", "station").order_by("starts_at", "pk"))


def _active_covered_ids(occurrences, target):
    return set(UploadedOccurrenceCoverage.objects.filter(
        occurrence__in=occurrences, upload__device=target, upload__state="active",
        occurrence_revision=F("occurrence__revision"),
    ).values_list("occurrence_id", flat=True))


def media_readiness(asset, target):
    binding = schedule_ready_binding(asset, target) if asset else None
    return {"state": "ready" if binding else "waiting", "label": "Ready" if binding else "Waiting for media", "binding": binding}


def prepared_asset_suggestions(assignment, target):
    """Return exact prepared assets first; never infer a newer unrelated file."""
    return list(MediaAsset.objects.filter(
        episode_id=assignment.episode_id,
        preparation_batch_items__batch__target=target,
    ).distinct().order_by("-preparation_batch_items__pk", "-pk"))


def preview_schedule_preparation(target, selections, *, now=None):
    now = now or timezone.now()
    rows, included, excluded, blockers, snapshots = [], [], [], [], []
    seen = set()
    for choice in selections:
        assignment, asset = choice["assignment"], choice.get("asset")
        errors = []
        if assignment.pk in seen:
            errors.append("Choose each cycle only once")
        seen.add(assignment.pk)
        if assignment.selection_type not in {"premiere", "rerun"} or not assignment.episode_id:
            errors.append("Choose a confirmed episode cycle")
        if asset is None:
            errors.append("an explicit media version is required")
        elif asset.episode_id != assignment.episode_id:
            errors.append("Media must belong to this cycle's episode")
        occurrences = cycle_occurrences(assignment)
        covered = _active_covered_ids(occurrences, target)
        use, skip, entries = [], [], []
        for item in occurrences:
            reason = (item.get_status_display() if item.status != "planned" else
                "Past" if item.starts_at <= now else "Already covered" if item.pk in covered else "Included")
            (use if reason == "Included" else skip).append(item)
            mappings = list(item.publication_selections.filter(publication_batch__target=target)
                .order_by("-pk").values("publication_batch_id", "operation", "source_bin_slot", "source_bin_record_hash")[:3])
            entries.append({"occurrence": item, "reason": reason, "mappings": mappings,
                "replacement": bool(item.asset_id and asset and item.asset_id != asset.pk)})
            if reason == "Included" and (item.episode_id != assignment.episode_id or item.item_type != "episode"):
                errors.append("A linked occurrence no longer matches this cycle's episode")
        if not use:
            errors.append("no future uncovered planned occurrences")
        ready = bool(asset and schedule_ready_binding(asset, target))
        job = PreparationBatchItem.objects.filter(asset=asset, batch__target=target).order_by("-pk").first() if asset else None
        rows.append(PreparationRow(assignment, asset, tuple(use), tuple(skip),
            "ready" if ready else "waiting", tuple(errors), tuple(entries), job))
        included.extend(use); excluded.extend(skip); blockers.extend(errors)
        snapshots.append({"assignment": _assignment_snapshot(assignment), "asset": _asset_snapshot(asset),
            "occurrences": [{"id": entry["occurrence"].pk, "revision": entry["occurrence"].revision,
                "reason": entry["reason"], "mappings": entry["mappings"]} for entry in entries]})
    if not rows:
        blockers.append("Choose at least one confirmed cycle")
    snapshot = {"target_id": target.pk, "target_settings": target_settings_snapshot(target), "cycles": snapshots}
    snapshot["hash"] = canonical_hash(snapshot)
    return {"rows": rows, "included": tuple(included), "excluded": tuple(excluded),
        "blockers": tuple(dict.fromkeys(blockers)), "ready": bool(rows) and not blockers and all(r.readiness == "ready" for r in rows),
        "snapshot": snapshot}


@transaction.atomic
def confirm_schedule_preparation(target, selections, reviewed_snapshot, *, actor="owner",
        reconciliation_mode="preserve", confirm_replacements=False, review_token=None):
    if reconciliation_mode != "preserve":
        raise SchedulePreparationError("Cycle preparation requires preservation of unrelated controller records")
    target = Device.objects.select_for_update().get(pk=target.pk)
    if review_token:
        existing = SchedulePublicationBatch.objects.filter(review_token=review_token).first()
        if existing:
            original = existing.cycle_selections.first()
            if existing.target_id != target.pk or not original or original.reviewed_snapshot.get("review_hash") != reviewed_snapshot.get("hash"):
                raise SchedulePreparationStale("Review token belongs to another proposal")
            return existing
    fresh = []
    for choice in selections:
        assignment = WeeklyEpisodeAssignment.objects.select_for_update().select_related("show__station").get(pk=choice["assignment"].pk)
        asset = MediaAsset.objects.select_for_update().get(pk=choice["asset"].pk) if choice.get("asset") else None
        list(Occurrence.objects.select_for_update().filter(weekly_assignment=assignment))
        fresh.append({"assignment": assignment, "asset": asset})
    preview = preview_schedule_preparation(target, fresh)
    if reviewed_snapshot != preview["snapshot"]:
        raise SchedulePreparationStale("Cycles, media, target settings or occurrences changed; preview again")
    if preview["blockers"]:
        raise SchedulePreparationError("; ".join(preview["blockers"]))
    if any(e["replacement"] for row in preview["rows"] for e in row.entries if e["reason"] == "Included") and not confirm_replacements:
        raise SchedulePreparationError("Replacing existing cycle media requires explicit confirmation")
    batch = SchedulePublicationBatch.objects.create(target=target, review_token=review_token,
        workflow_mode="selected_changes", reconciliation_mode="preserve", created_by=actor)
    for row in preview["rows"]:
        for occurrence in row.occurrences:
            if occurrence.asset_id != row.asset.pk:
                occurrence.asset = row.asset
                occurrence.save(update_fields=["asset", "revision"])
                occurrence.refresh_from_db()
                audit("update", "Occurrence", occurrence, f"Reviewed cycle media version {row.asset.pk}", actor)
            OccurrenceRevisionSelection.objects.create(publication_batch=batch, occurrence=occurrence,
                occurrence_revision=occurrence.revision)
        PublicationCycleSelection.objects.create(publication_batch=batch, assignment=row.assignment,
            asset=row.asset, preparation_item=row.job, reviewed_snapshot={
                "review_hash": reviewed_snapshot["hash"], "assignment": _assignment_snapshot(row.assignment),
                "target_settings": reviewed_snapshot["target_settings"],
                "asset": _asset_snapshot(row.asset),
                "occurrences": [{"id": o.pk, "revision": o.revision} for o in row.occurrences]})
    audit("create", "SchedulePublicationBatch", batch, "Reviewed confirmed cycles and exact media versions", actor)
    return batch


def publication_readiness(batch):
    choices = list(batch.cycle_selections.select_related("asset", "assignment__episode", "preparation_item"))
    waiting = [choice for choice in choices if not schedule_ready_binding(choice.asset, batch.target)]
    return {"label": "Waiting for media" if waiting else "Media ready" if choices else "Selected changes",
        "waiting": waiting, "ready": not waiting}


def _choices_from_ids(ids, asset_ids):
    if not ids or len(ids) != len(set(ids)):
        raise SchedulePreparationError("Choose one or more distinct confirmed cycles")
    choices = []
    for pk in ids:
        assignment = WeeklyEpisodeAssignment.objects.select_related("show__station").get(pk=pk)
        asset = MediaAsset.objects.get(pk=asset_ids[str(pk)], episode_id=assignment.episode_id)
        choices.append({"assignment": assignment, "asset": asset})
    return choices


def schedule_prepare(request):
    station = resolve_active_station(request)
    context = {"targets": Device.objects.filter(station=station).order_by("name")}
    try:
        if request.method == "POST":
            if request.POST.get("action") == "confirm":
                payload = signing.loads(request.POST.get("review_token", ""), salt=SALT, max_age=3600)
                target = Device.objects.get(pk=payload["target_id"], station=station)
                choices = _choices_from_ids(payload["ids"], payload["assets"])
                batch = confirm_schedule_preparation(target, choices, payload["snapshot"],
                    review_token=payload["uuid"], confirm_replacements=request.POST.get("confirm_replacements") == "on")
                messages.success(request, "Reviewed schedule plan saved. Waiting media can finish independently; delivery remains attended.")
                return redirect(f"/schedule/delivery/?batch={batch.pk}#publication-{batch.pk}")
            target = Device.objects.get(pk=request.POST.get("target"), station=station)
            ids = request.POST.getlist("assignment")
            assets = {str(pk): request.POST.get(f"asset_{pk}") for pk in ids}
            choices = _choices_from_ids(ids, assets)
            review = preview_schedule_preparation(target, choices)
            context.update(target=target, review=review, rows=review["rows"], review_token=signing.dumps({
                "target_id": target.pk, "ids": ids, "assets": assets,
                "snapshot": review["snapshot"], "uuid": str(uuid.uuid4())}, salt=SALT))
            return render(request, "schedule_preparation.html", context)
    except (SchedulePreparationError, signing.BadSignature, ValidationError, ValueError, TypeError,
            KeyError, Device.DoesNotExist, WeeklyEpisodeAssignment.DoesNotExist, MediaAsset.DoesNotExist) as exc:
        context["error"] = str(exc) if isinstance(exc, SchedulePreparationError) else "The proposal or selection is invalid or expired. Select all cycles and media versions again."
    assignments = WeeklyEpisodeAssignment.objects.filter(selection_type__in=("premiere", "rerun"), episode__isnull=False).select_related("show", "episode").order_by("-week_start", "pk")
    requested_target = Device.objects.filter(pk=request.GET.get("target"), station=station).first() if request.method == "GET" else None
    if requested_target:
        context["target"] = requested_target
    context["cycle_options"] = [(a, prepared_asset_suggestions(a, requested_target) if requested_target else MediaAsset.objects.filter(episode=a.episode).order_by("-pk")) for a in assignments]
    context["connected_assignments"] = request.GET.getlist("assignment")
    return render(request, "schedule_preparation.html", context)


build_schedule_preparation_review = preview_schedule_preparation
review_schedule_preparation = preview_schedule_preparation
commit_schedule_preparation = confirm_schedule_preparation
schedule_preparation_readiness = media_readiness
