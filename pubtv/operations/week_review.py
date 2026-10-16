"""Read-only, whole-week planning review; never prepares or publishes a schedule."""
from datetime import date, datetime, timedelta, time
from zoneinfo import ZoneInfo

from django.db.models import F
from django.http import HttpResponseBadRequest
from django.shortcuts import render
from django.utils import timezone
from django.views.decorators.http import require_GET

from .active_station import resolve_active_station
from .models import Device, Occurrence, UploadedOccurrenceCoverage, WeeklyEpisodeAssignment
from .services import calendar_capacity, preparation_readiness, schedule_alerts, active_premiere_slot


def no_program_windows(station, start, end):
    """Return the station-local premiere-to-premiere no-program windows.

    The assignment's ``week_start`` is a planning bucket, while the cycle is
    anchored at the configured premiere date/time.  Compare the resulting
    window with the requested range instead of assuming calendar-week
    boundaries; this preserves a prior cycle's Monday replay when the next
    premiere falls later in that week.
    """
    windows = []
    for assignment in WeeklyEpisodeAssignment.objects.filter(
        show__station=station, selection_type="none", week_start__gte=start - timedelta(days=14),
        week_start__lte=end,
    ).select_related("show", "show__station"):
        premiere = assignment.premiere_date
        if premiere is None:
            slot = assignment.show.slots.filter(is_premiere=True).order_by("pk").first()
            if slot is None:
                continue
            premiere = assignment.week_start + timedelta(days=slot.weekday)
        slot = active_premiere_slot(assignment.show, premiere)
        if slot:
            begins = datetime.combine(premiere, slot.start_time, ZoneInfo(station.timezone))
            finishes = begins + timedelta(days=7)
            requested_start = datetime.combine(start, time.min, ZoneInfo(station.timezone))
            requested_end = datetime.combine(end, time.min, ZoneInfo(station.timezone))
            if begins < requested_end and finishes > requested_start:
                windows.append((assignment.show_id, begins, finishes))
    return windows


def planned_columns(station, selected, occurrences, capacity):
    """Keep detailed gaps, unassigned reservations and intentional returns visible."""
    zone = ZoneInfo(station.timezone)
    no_program = no_program_windows(station, selected, selected + timedelta(days=7))
    from .models import RecurrenceSlot
    slots = list(RecurrenceSlot.objects.filter(station=station).select_related("show"))
    columns = []
    for day in capacity:
        begins = datetime.combine(day["date"], time.min, zone)
        ends = datetime.combine(day["date"] + timedelta(days=1), time.min, zone)
        items = [{"kind": "occurrence", "occurrence": item,
                  "start": max(item.starts_at, begins), "end": min(item.ends_at, ends)}
                 for item in occurrences if item.starts_at < ends and item.ends_at > begins]
        for interval in day["intervals"]:
            if interval["status"] == "reserved" and interval.get("item"):
                continue
            row = {**interval, "kind": "capacity"}
            if row["status"] == "available":
                row["label"] = "Unplanned time · coverage unknown"
            elif row["status"] == "reserved":
                row["label"] = "Reserved · content not assigned"
                for slot in slots:
                    # ``calendar_capacity`` clips cross-midnight reservations
                    # to the current day.  Check both possible local dates so
                    # a Sunday 23:30 slot remains identifiable on Monday.
                    row_day = row["start"].astimezone(zone).date()
                    candidate_days = (row_day - timedelta(days=1), row_day)
                    if any(
                        slot.weekday == slot_day.weekday()
                        and (not slot.active_from or slot.active_from <= slot_day)
                        and (not slot.active_until or slot.active_until >= slot_day)
                        and (slot_start < row["end"] and slot_start + timedelta(seconds=slot.duration_seconds) > row["start"])
                        and any(show == slot.show_id and a <= slot_start < b for show, a, b in no_program)
                        for slot_day in candidate_days
                        for slot_start in (datetime.combine(slot_day, slot.start_time, zone),)
                    ):
                        row["label"] = "No program · explicit cycle choice"
                        row["status"] = "no-program"
                        break
            items.append(row)
        items.sort(key=lambda item: (item["start"], item["kind"] != "occurrence"))
        columns.append({"date": day["date"], "items": items})
    return columns


@require_GET
def week_review(request):
    station = resolve_active_station(request)
    try:
        selected = date.fromisoformat(request.GET["date"]) if request.GET.get("date") else timezone.localdate()
    except ValueError:
        return HttpResponseBadRequest("Invalid date")
    selected -= timedelta(days=selected.weekday())
    context = {"selected_date": selected, "week_end": selected + timedelta(days=6),
               "station": station, "week_columns": [], "rows": [], "warnings": []}
    if station is None:
        return render(request, "week_review.html", context)
    zone = ZoneInfo(station.timezone)
    begins = datetime.combine(selected, time.min, zone)
    ends = datetime.combine(selected + timedelta(days=7), time.min, zone)
    occurrences = [item for item in Occurrence.objects.filter(station=station, status="planned", starts_at__lt=ends)
                   .select_related("show", "episode", "asset", "weekly_assignment", "recurrence_slot", "preparation")
                   .order_by("starts_at", "pk") if item.ends_at > begins]
    targets = list(Device.objects.filter(station=station).order_by("name", "pk"))
    target_id = request.GET.get("target", "")
    target = next((item for item in targets if str(item.pk) == target_id), None)
    if target_id and target is None:
        return HttpResponseBadRequest("Choose a destination in the current channel")
    covered = set(UploadedOccurrenceCoverage.objects.filter(
        occurrence__in=occurrences, upload__state="active",
        upload__device__station=station,
        occurrence_revision=F("occurrence__revision"),
        **({"upload__device": target} if target else {}),
    ).values_list("occurrence_id", flat=True))
    from .schedule_preparation import media_readiness
    for item in occurrences:
        item.readiness = preparation_readiness(item)
        item.upload_covered = item.pk in covered
        item.review_preparation = (media_readiness(item.asset, target)["label"] if target and item.item_type != "live"
                                   else "Live entry · check programming" if item.item_type == "live"
                                   else "Choose a destination to check media readiness")
        item.cycle_delivery_supported = bool(item.weekly_assignment_id and item.episode_id)
        # A fixed reservation is not evidence of the actual media runtime.
        # For media-bearing items, preserve an unknown value when the recorded
        # runtime is absent; zero remains a deliberate recorded runtime.
        runtime = item.asset.runtime_seconds if item.asset_id else item.episode.runtime_seconds if item.episode_id else item.planned_duration_seconds
        item.runtime_unknown = runtime is None
    warnings = []
    for kind, first, second in schedule_alerts(occurrences):
        warnings.append({"item": first, "text": f"Overlap with {second.display_label}" if kind == "overlap"
                         else "Less than the ideal 60-second gap (advisory)" if kind == "short_gap"
                         else "Runtime exceeds the fixed slot; starts will not be moved"})
    capacity = calendar_capacity(station, selected, 7)
    context.update(rows=occurrences, warnings=warnings, targets=targets, target=target,
                   week_columns=planned_columns(station, selected, occurrences, capacity))
    return render(request, "week_review.html", context)
