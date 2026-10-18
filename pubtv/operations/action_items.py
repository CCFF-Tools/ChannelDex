"""Derived operator actions for the Today workspace.

This module deliberately contains no task model or completion state.  The
projection is rebuilt from the canonical schedule, preparation, and upload
records each time it is requested.
"""

from collections import OrderedDict
from datetime import date, datetime, time, timedelta
from pathlib import Path
from urllib.parse import urlencode
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from django.urls import reverse
from django.utils import timezone

from .automation import schedule_ready_binding
from .media_queue import effective_preparation_status
from .models import (
    Episode,
    Occurrence,
    PreparationBatchItem,
    RecurrenceSlot,
    UploadedOccurrenceCoverage,
    WeeklyEpisodeAssignment,
)
from .services import calendar_capacity, schedule_alerts

WAITING_STATES = {"queued", "encoding", "validating", "transferring", "verifying"}
ACTIONABLE_STATES = {"failed", "blocked"}


def _zone(station):
    try:
        return ZoneInfo(station.timezone)
    except (AttributeError, ZoneInfoNotFoundError):
        return ZoneInfo("America/Detroit")


def _url(name, *args, **kwargs):
    """Reverse a known local action, retaining a useful fallback in tests."""
    try:
        return reverse(name, args=args, kwargs=kwargs)
    except Exception:  # pragma: no cover - only reached by stripped test URLConfs
        return "/"


def _context_url(name, selected_date, *args, **kwargs):
    path = _url(name, *args, **kwargs)
    query = urlencode({"date": selected_date.isoformat(), "return": "today"})
    return f"{path}?{query}"


def _when(value, station):
    if value is None:
        return None
    if timezone.is_aware(value):
        value = timezone.localtime(value, _zone(station))
    return value.strftime("%Y-%m-%d %-I:%M %p")


def _item(key, title, reason, url, *, needed_at=None, destination=None):
    result = {"key": key, "title": title, "reason": reason, "url": url}
    if needed_at:
        result["needed_at"] = needed_at
    if destination:
        result["destination"] = destination
    return result


def _add(items, key, title, reason, url, *, needed_at=None, destination=None, group="upcoming", sort_at=None):
    current = items.get((group, key))
    if current is None:
        current = _item(key, title, reason, url, needed_at=needed_at, destination=destination)
        if sort_at is not None:
            current["sort_at"] = sort_at
        if sort_at is None and needed_at:
            try:
                sort_at = datetime.combine(date.fromisoformat(needed_at[:10]), time.min, ZoneInfo("America/Detroit"))
            except ValueError:
                pass
        current["_sort"] = (0, sort_at, key) if sort_at is not None else (1, "", key)
        current["_uses"] = []
        items[(group, key)] = current
    return current


def _finish(items):
    output = []
    for item in items.values():
        uses = item.pop("_uses", [])
        if uses:
            item["reason"] += " Affected uses: " + ", ".join(dict.fromkeys(uses)) + "."
        item.pop("_sort", None)
        output.append(item)
    return output


def _active_coverage(occurrence):
    return UploadedOccurrenceCoverage.objects.filter(
        occurrence=occurrence,
        occurrence_revision=occurrence.revision,
        upload__state="active",
    )


def _week_end(selected_date):
    monday = selected_date - timedelta(days=selected_date.weekday())
    return monday + timedelta(days=13)


def build_daily_work(station, selected_date):
    """Return the current derived work projection for ``station`` and date.

    The horizon starts at ``selected_date`` and ends on the Sunday of the
    following Monday-based week.  Items before that horizon are reported only
    when they are past premiere exceptions.
    """
    empty = {"action_groups": [], "waiting_items": [], "action_count": 0}
    if station is None:
        return empty
    if not isinstance(selected_date, date):
        selected_date = date.fromisoformat(str(selected_date))

    # Work must keep unfinished items visible beyond the short near-term
    # horizon.  Capacity and calendar warnings below remain bounded, while
    # schedule, preparation, and coverage records are projected until done.
    horizon_end = _week_end(selected_date)
    tz = _zone(station)
    start = timezone.make_aware(datetime.combine(selected_date, time.min), tz)
    occurrences = list(
        Occurrence.objects.filter(station=station, episode__archived_at__isnull=True,
                                  show__archived_at__isnull=True)
        .select_related("show", "episode", "asset", "weekly_assignment", "recurrence_slot")
        .prefetch_related("programming")
        .order_by("starts_at", "pk")
    )
    horizon_occurrences = [o for o in occurrences if o.ends_at > start]
    by_asset = {}
    for occurrence in horizon_occurrences:
        if occurrence.asset_id:
            by_asset.setdefault(occurrence.asset_id, []).append(occurrence)

    devices = list(station.devices.all().order_by("name", "pk"))
    actions = OrderedDict()
    waiting = []

    # Failed, blocked, and stale preparations are the canonical recovery state.
    batch_items = PreparationBatchItem.objects.filter(
        batch__target__station=station,
        batch__removed_from_list=False,
    ).select_related("asset", "batch__target", "asset__episode")
    for batch_item in batch_items:
        state, blocker = effective_preparation_status(batch_item)
        target = batch_item.batch.target
        destination = target.destination_label
        asset = batch_item.asset
        if getattr(getattr(asset, "episode", None), "archived_at", None) is not None or getattr(getattr(getattr(asset, "episode", None), "show", None), "archived_at", None) is not None:
            continue
        uses = [o.display_label for o in by_asset.get(asset.pk, [])]
        asset_label = asset.file_name or asset.legacy_label or str(asset)
        if state in WAITING_STATES:
            waiting.append(_item(
                f"preparation:{asset.pk}:{target.pk}",
                f"Preparing {asset_label}",
                "Preparation is in progress and will remain visible here until it needs operator attention.",
                _context_url("media-queue", selected_date) + f"&asset={asset.pk}", destination=destination,
            ))
        elif state in ACTIONABLE_STATES or batch_item.approval_1_status == "stale":
            reason = blocker or "Preparation needs review before this destination can use the media."
            item = _add(actions, f"preparation:{asset.pk}:{target.pk}",
                        f"Review preparation for {asset_label}", reason,
                        _context_url("media-queue", selected_date) + f"&asset={asset.pk}",
                        destination=destination, group="attention")
            item["_uses"].extend(uses)

    # Planned media and exact-revision upload coverage.
    for occurrence in horizon_occurrences:
        if occurrence.status != "planned":
            continue
        asset = occurrence.asset
        label = occurrence.display_label
        when = _when(occurrence.starts_at, station)
        if occurrence.item_type in {"episode", "media"} and asset is None:
            identity = f"episode:{occurrence.episode_id}" if occurrence.episode_id else f"occurrence:{occurrence.pk}"
            item = _add(actions, f"asset:{identity}", f"Add media for {label}",
                 "This planned item has no media asset attached.",
                 _context_url("occurrence-workbench", selected_date, occurrence.pk),
                     needed_at=when, group="attention", sort_at=occurrence.starts_at)
            item["_uses"].append(f"{label} at {when}")
        if asset is None:
            # Missing media is the next meaningful action. Delivery cannot be
            # reviewed yet, and a replay must not create a second card.
            continue
        asset_prep = getattr(asset, "preparation_record", None)
        relevant_items = list(PreparationBatchItem.objects.filter(
            asset=asset, batch__target__station=station, batch__removed_from_list=False,
        ).exclude(batch__status="cancelled").select_related("batch__target"))
        relevant_device_ids = set(occurrence.programming.values_list("device_id", flat=True))
        relevant_device_ids.update(item.batch.target_id for item in relevant_items)
        relevant_devices = [device for device in devices if device.pk in relevant_device_ids]
        target_ready = {device.pk: bool(schedule_ready_binding(asset, device)) for device in relevant_devices}
        source_missing = not asset.local_path or not Path(asset.local_path).is_file() or (asset_prep and asset_prep.source_available == "no")
        unfinished_relevant_prep = bool(relevant_devices) and any(not ready for ready in target_ready.values())
        if source_missing and (not relevant_devices or unfinished_relevant_prep):
            item = _add(actions, f"source:{asset.pk}", f"Locate source for {asset.file_name or asset.legacy_label or label}",
                         "A local source is required for unfinished preparation.",
                         _context_url("asset-edit", selected_date, asset.pk),
                         needed_at=when, group="attention", sort_at=occurrence.starts_at)
            item["_uses"].append(f"{label} at {when}")

        # A known local source still needs a destination-specific preparation
        # action before delivery coverage can be meaningful. Existing queue
        # states already produce their own waiting or recovery item above.
        if not source_missing:
            for device in relevant_devices:
                if target_ready.get(device.pk):
                    continue
                target_items = [item for item in relevant_items if item.batch.target_id == device.pk]
                states = {effective_preparation_status(item)[0] for item in target_items}
                if states & (WAITING_STATES | ACTIONABLE_STATES) or any(item.approval_1_status == "stale" for item in target_items):
                    continue
                _add(actions, f"preparation:{asset.pk}:{device.pk}", f"Prepare media for {label}",
                     f"The local source is available but is not prepared for {device.name}.",
                     _context_url("media-queue", selected_date) + f"&asset={asset.pk}&target={device.pk}",
                     needed_at=when, destination=device.destination_label, group="attention", sort_at=occurrence.starts_at)

        covered = _active_coverage(occurrence)
        covered_devices = set(covered.values_list("upload__device_id", flat=True))
        relevant_device_ids = set(occurrence.programming.values_list("device_id", flat=True))
        relevant_device_ids.update(item.batch.target_id for item in relevant_items)
        relevant_devices = relevant_devices or [None]
        for device in relevant_devices:
            if device is None:
                if covered_devices:
                    continue
                destination = None
                destination_text = "No active upload coverage exists for this exact schedule revision."
                identity = f"episode:{occurrence.episode_id}" if occurrence.episode_id else f"asset:{asset.pk}"
                action_key = f"coverage:{identity}:unscoped"
            elif device.pk not in covered_devices:
                destination = device.destination_label
                destination_text = f"No active upload coverage exists for this exact schedule revision on {device.name}."
                identity = f"episode:{occurrence.episode_id}" if occurrence.episode_id else f"asset:{asset.pk}"
                action_key = f"coverage:{identity}:{device.pk}"
            else:
                continue
            item = _add(actions, action_key,
                     f"Review delivery for {label}",
                     destination_text,
                     _context_url("occurrence-workbench", selected_date, occurrence.pk),
                     needed_at=when, destination=destination, group="upcoming", sort_at=occurrence.starts_at)
            item["_uses"].append(f"{label} at {when}")

    # Capacity marks genuinely unplanned time as a separate schedule action.
    # Virtual-channel filler inside a reservation is intentionally not included.
    capacity_days = calendar_capacity(station, selected_date, days=(horizon_end - selected_date).days + 1)
    unplanned_days = []
    for day_capacity in capacity_days:
        if any(interval.get("status") == "available" for interval in day_capacity.get("intervals", [])):
            unplanned_days.append(day_capacity["date"])
    if unplanned_days:
        first_day = min(unplanned_days)
        labels = ", ".join(day.strftime("%b %-d") for day in unplanned_days)
        _add(actions, "unplanned:summary", "Review unplanned time",
             f"Coverage intervals without an explicit schedule item: {labels}.",
             _context_url("day-view", first_day), needed_at=first_day.isoformat(), group="upcoming",
             sort_at=timezone.make_aware(datetime.combine(first_day, time.min), tz))

    # Runtime and schedule conflicts are distinct from intentional virtual-channel time.
    for occurrence in horizon_occurrences:
        if occurrence.status != "planned":
            continue
        when = _when(occurrence.starts_at, station)
        if occurrence.item_type in {"episode", "media"} and occurrence.asset_id and occurrence.actual_runtime_seconds is None:
            identity = f"episode:{occurrence.episode_id}" if occurrence.episode_id else f"occurrence:{occurrence.pk}"
            item = _add(actions, f"runtime:{identity}", f"Record runtime for {occurrence.display_label}",
                 "Runtime is unknown; review the plan before relying on this fixed slot.",
                 _context_url("occurrence-workbench", selected_date, occurrence.pk), needed_at=when, group="upcoming", sort_at=occurrence.starts_at)
            item["_uses"].append(f"{occurrence.display_label} at {when}")
        if occurrence.over_slot:
            _add(actions, f"overrun:{occurrence.pk}", f"Review overrun for {occurrence.display_label}",
                 "The recorded runtime exceeds the fixed schedule slot.",
                 _context_url("occurrence-workbench", selected_date, occurrence.pk), needed_at=when, group="upcoming", sort_at=occurrence.starts_at)
    for kind, first, second in schedule_alerts(horizon_occurrences):
        if kind == "overlap":
            conflict_date = timezone.localtime(first.starts_at, tz).date() if timezone.is_aware(first.starts_at) else first.starts_at.date()
            _add(actions, f"conflict:{first.pk}:{second.pk}", "Review overlapping schedule items",
                 f"{first.display_label} overlaps {second.display_label}; fixed starts remain unchanged.",
                 _context_url("day-view", conflict_date), needed_at=_when(first.starts_at, station), group="attention", sort_at=first.starts_at)

    # Reserved premiere capacity without a choice needs a planner decision. No-program
    # assignments intentionally stop carry-forward and therefore do not become actions.
    slots = RecurrenceSlot.objects.filter(station=station, is_premiere=True).select_related("show")
    for slot in slots:
        if slot.show.archived_at is not None:
            continue
        cursor = selected_date
        while cursor <= horizon_end:
            if cursor.weekday() == slot.weekday and (not slot.active_from or cursor >= slot.active_from) and (not slot.active_until or cursor <= slot.active_until):
                week_start = cursor - timedelta(days=cursor.weekday())
                assignment = WeeklyEpisodeAssignment.objects.filter(show=slot.show, week_start=week_start).first()
                if assignment is None:
                    key = f"assignment:{slot.show_id}:{week_start.isoformat()}"
                    _add(actions, key, f"Choose episode for {slot.show.title}",
                         "This reserved premiere time has no episode or explicit No program choice.",
                         _context_url("bulk-premiere-planner", selected_date, slot.show_id),
                         needed_at=cursor.strftime("%Y-%m-%d"), group="upcoming",
                         sort_at=timezone.make_aware(datetime.combine(cursor, time.min), tz))
            cursor += timedelta(days=1)

    # Past premiere exceptions are kept separate and never described as airing failures.
    past = Occurrence.objects.filter(
        station=station, item_type="episode", schedule_role="premiere", status__in=("planned", "cancelled", "preempted"), starts_at__lt=start,
        episode__archived_at__isnull=True, show__archived_at__isnull=True,
    ).select_related("episode")
    for occurrence in past:
        if occurrence.status in {"cancelled", "preempted"} or not _active_coverage(occurrence).exists():
            _add(actions, f"past-premiere:{occurrence.pk}", f"Review past premiere for {occurrence.display_label}",
                 "Owner review is needed for this past premiere's schedule and evidence; no airing conclusion is inferred.",
                 _context_url("occurrence-workbench", selected_date, occurrence.pk),
                 needed_at=_when(occurrence.starts_at, station), group="past", sort_at=occurrence.starts_at)

    # Optional lower-priority catalog housekeeping for episodes with no scheduled use.
    scheduled_episode_ids = set(Occurrence.objects.filter(station=station, episode__isnull=False).values_list("episode_id", flat=True))
    for episode in Episode.objects.filter(show__station=station, archived_at__isnull=True,
                                          show__archived_at__isnull=True).prefetch_related("assets"):
        if episode.pk not in scheduled_episode_ids and not episode.assets.exists():
            _add(actions, f"catalog:{episode.pk}", f"Add media for {episode.show.title} · {episode.title}",
                 "No scheduled date; catalog work is lower priority and has no invented deadline.",
                 _context_url("asset-create", selected_date) + f"&episode={episode.pk}", group="housekeeping")

    groups = []
    titles = (("attention", "Needs attention"), ("upcoming", "Upcoming work"), ("past", "Past exceptions"), ("housekeeping", "Library housekeeping"))
    for group, title in titles:
        items = sorted((item for (kind, _), item in actions.items() if kind == group), key=lambda value: value.get("_sort", ("9999", "")))
        for item in items:
            uses = item.pop("_uses", [])
            if uses:
                item["reason"] += " Affected uses: " + ", ".join(dict.fromkeys(uses)) + "."
            item.pop("_sort", None)
        if items:
            groups.append({"title": title, "items": items})
    return {"action_groups": groups, "waiting_items": waiting, "action_count": sum(len(group["items"]) for group in groups)}
