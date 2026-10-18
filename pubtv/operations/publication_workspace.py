"""Context and guardrails for the unified schedule publication workspace.

This module composes the existing publication review and delivery services.
It never performs device exchange and never replaces the immutable review
token, snapshot, or operation gates owned by those services.
"""
from __future__ import annotations

from datetime import date
from zoneinfo import ZoneInfo

from django.utils import timezone

from .models import Occurrence, SchedulePublicationBatch
from .publication_review import publication_review

UNSUPPORTED_TYPES = {"live"}


def occurrence_blockers(occurrence):
    blockers = []
    if occurrence.item_type in UNSUPPORTED_TYPES:
        blockers.append("Live entries require attended handling and are not supported by schedule publication")
    if occurrence.item_type in {"episode", "media", "station_id", "psa", "filler"} and not occurrence.asset_id:
        blockers.append("No media is attached to this occurrence")
    zone = ZoneInfo(getattr(getattr(occurrence, "station", None), "timezone", "America/Detroit"))
    start = occurrence.starts_at.astimezone(zone)
    end = occurrence.ends_at.astimezone(zone)
    if start.date() != end.date():
        blockers.append("Occurrence crosses midnight")
    if getattr(start, "fold", 0) or getattr(end, "fold", 0) or start.utcoffset() != end.utcoffset():
        blockers.append("Occurrence falls in an ambiguous DST hour")
    return blockers


def selection_binding(batch):
    """Return stale exact-occurrence selections before any review is shown."""
    stale = []
    for selection in batch.occurrence_selections.select_related("occurrence").order_by("pk"):
        if selection.occurrence_revision != selection.occurrence.revision:
            stale.append({"selection": selection.pk, "occurrence": selection.occurrence_id,
                          "selected_revision": selection.occurrence_revision,
                          "current_revision": selection.occurrence.revision,
                          "reason": "Schedule occurrence changed after selection"})
    return stale


def publication_state(batch):
    """Expose operator-facing completion stages without implying airing."""
    evidence = getattr(batch, "activation_evidence", None)
    latest = evidence.order_by("-recorded_at", "-pk").first() if evidence is not None else None
    if batch.status == "verified" and latest and latest.status == "activation_observed":
        return "independently verified"
    if latest and latest.status in {"rolled_back", "failed", "ambiguous"}:
        return {"rolled_back": "rolled back – publication needed", "failed": "failed – needs attention",
                "ambiguous": "verification inconclusive – needs attention"}[latest.status]
    if batch.status in {"blocked", "failed"}:
        return "needs attention"
    if batch.status in {"delivering", "awaiting_activation"}:
        return "sent"
    if batch.status in {"approved", "previewed"}:
        return "prep complete" if batch.status == "approved" else "app planned"
    if batch.status == "cancelled":
        return "cancelled"
    return "app planned"


def occurrence_publication_state(occurrence):
    """Describe current-revision publication, not actual broadcast history."""
    selections = occurrence.publication_selections.filter(
        occurrence_revision=occurrence.revision,
    ).exclude(publication_batch__status="cancelled").select_related("publication_batch").order_by("-pk")
    latest = selections.first()
    if latest is None:
        return "Planned – publication needed"
    state = publication_state(latest.publication_batch)
    if state == "independently verified":
        return "Verified on device"
    if "attention" in state or "rolled back" in state:
        return state.capitalize()
    return "Publication in progress – verification pending"


def publication_view(request):
    """Unified publication entry point.

    The established automation dashboard owns every guarded POST transition
    (review, approval, staging, activation, and observation).  Delegating
    keeps those transaction and token checks in one place while giving the
    parent router a stable `/schedule/publish/` endpoint.
    """
    from . import views
    if request.GET.get("guided") or request.POST.get("delivery"):
        from .guided_delivery import guided_delivery_view
        return guided_delivery_view(request)
    return views.automation_dashboard(request)


def work_view(request):
    """Render the Work projection for the active station."""
    from django.shortcuts import render

    from .active_station import resolve_active_station
    from .work_workspace import build_work_workspace
    station = resolve_active_station(request)
    selected = request.GET.get("date") or None
    try:
        selected = date.fromisoformat(selected) if selected else None
    except ValueError:
        selected = None
    context = build_work_workspace(station, selected)
    context.update({"station": station, "groups": context["groups"]})
    return render(request, "work.html", context)


def build_publication_workspace(*, station=None, selected_dates=None, device=None):
    """Return pending publication reviews and explicit unsupported blockers."""
    if station is None:
        return {"batches": [], "unbatched_occurrences": [], "has_blockers": False}
    qs = SchedulePublicationBatch.objects.select_related("target").prefetch_related("occurrence_selections__occurrence")
    qs = qs.filter(target__station=station)
    if device is not None:
        qs = qs.filter(target=device, target__station=station)
    batches = []
    selected_dates = ({d if isinstance(d, date) else date.fromisoformat(str(d)) for d in selected_dates}
                      if selected_dates else None)
    # Surface planned occurrences that are not yet bound to a publication
    # batch.  The existing create_publication form remains responsible for
    # creating the immutable selection records.
    from django.db.models import F
    bound = set(Occurrence.objects.filter(station=station,
        publication_selections__occurrence_revision=F("revision"),
        publication_selections__publication_batch__target=device,
        publication_selections__publication_batch__status__in=("previewed", "approved", "awaiting_activation", "delivering", "verified"),
    ).values_list("pk", flat=True)) if device else set()
    pending = Occurrence.objects.filter(starts_at__gte=timezone.now())
    if station is not None:
        pending = pending.filter(station=station)
    # Pending changes include revised/cancelled plays; the operation review
    # decides add/move/replace/delete against the captured controller schedule.
    unbatched = [o for o in pending.select_related("station", "show", "episode", "asset").order_by("starts_at", "pk")
                 if o.pk not in bound and (selected_dates is None or o.starts_at.date() in selected_dates)]
    for occurrence in unbatched:
        occurrence.publication_blockers = occurrence_blockers(occurrence) if occurrence.status == "planned" else []
    for batch in qs.exclude(status="cancelled").order_by("created_at", "pk"):
        selections = list(batch.occurrence_selections.all())
        if selected_dates:
            selections = [s for s in selections if s.occurrence.starts_at.date() in selected_dates]
        blockers = selection_binding(batch)
        for selection in selections:
            if selection.operation != "delete":
                blockers.extend({"occurrence": selection.occurrence_id, "reason": reason}
                                for reason in occurrence_blockers(selection.occurrence))
        review = publication_review(batch)
        batches.append({"batch": batch, "selections": selections, "state": publication_state(batch),
                        "blockers": blockers + [{"reason": reason} for reason in review.get("blockers", [])],
                        "review": review, "review_hash": review.get("review_hash")})
    return {"batches": batches, "unbatched_occurrences": unbatched,
            "has_blockers": any(item["blockers"] for item in batches) or any(o.publication_blockers for o in unbatched),
            "completion_note": "Sent or activated does not prove that the schedule aired."}


publication_projection = build_publication_workspace
