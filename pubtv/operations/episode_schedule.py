"""Read-only episode schedule projection used by Library and Schedule views."""
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from django.utils import timezone

from .models import Occurrence, WeeklyEpisodeAssignment
from .services import occurrences_for_cycle, pending_episode_sort_key


def project_episode_schedule(show, start_date, end_date):
    """Return materialized plays plus unmaterialized queue suggestions.

    Suggestions are deliberately plain dictionaries: rendering them must not
    create plans or consume the pending-new queue.
    """
    tz = ZoneInfo(show.station.timezone)
    start = timezone.make_aware(datetime.combine(start_date, datetime.min.time()), tz)
    end = timezone.make_aware(datetime.combine(end_date + timedelta(days=1), datetime.min.time()), tz)
    plays = list(Occurrence.objects.filter(
        station=show.station, show=show, starts_at__gte=start, starts_at__lt=end,
        status="planned", episode__isnull=False, episode__archived_at__isnull=True,
    ).select_related("episode", "recurrence_slot").order_by("starts_at", "pk"))
    pending = sorted(show.episodes.filter(status="pending", archived_at__isnull=True), key=pending_episode_sort_key)
    suggestions = []
    if show.archived_at:
        return {"plays": plays, "suggestions": [], "pending": pending}
    # Explicit cycles, including No program, reserve their week. Suggestions
    # never reinterpret an existing choice or consume an already planned episode.
    assignments = list(show.weekly_assignments.all())
    occupied_weeks = {item.week_start for item in assignments}
    planned_ids = set(Occurrence.objects.filter(
        show=show, status="planned", starts_at__gte=timezone.now(), episode__isnull=False,
    ).values_list("episode_id", flat=True))
    planned_ids.update(item.episode_id for item in assignments
                       if item.episode_id and (item.premiere_date or item.week_start) >= start_date)
    used_pending = set(planned_ids)
    slots = list(show.slots.all())
    occupied_starts = {item.starts_at for item in plays}
    for offset in range((end_date - start_date).days + 1):
        day = start_date + timedelta(days=offset)
        week = day - timedelta(days=day.weekday())
        if week in occupied_weeks:
            continue
        premieres = [slot for slot in slots if slot.is_premiere and slot.weekday == day.weekday()
                     and (not slot.active_from or slot.active_from <= day)
                     and (not slot.active_until or slot.active_until >= day)]
        if len(premieres) != 1:
            continue
        local_start = datetime.combine(day, premieres[0].start_time, tzinfo=tz)
        if local_start < timezone.now() or local_start in occupied_starts:
            continue
        # A producer's specific future date is not pulled earlier to fill a gap.
        dated = [item for item in pending if item.pk not in used_pending and item.intended_premiere_date == day]
        episode = dated[0] if dated else next((item for item in pending
            if item.pk not in used_pending and item.intended_premiere_date is None), None)
        if episode is None:
            continue
        candidate = WeeklyEpisodeAssignment(show=show, episode=episode, week_start=week,
                                             premiere_date=day, selection_type="premiere")
        cycle_plays = occurrences_for_cycle(candidate)
        # A suggestion that overlaps an explicit play is not an available cycle.
        if any(item.starts_at in occupied_starts for item in cycle_plays):
            continue
        used_pending.add(episode.pk)
        occupied_weeks.add(week)
        for item in cycle_plays:
            if start <= item.starts_at < end:
                suggestions.append({"slot": item.recurrence_slot, "starts_at": item.starts_at,
                                    "episode": episode, "schedule_role": item.schedule_role,
                                    "premiere_date": day})
    suggestions.sort(key=lambda item: item["starts_at"])
    return {"plays": plays, "suggestions": suggestions, "pending": pending}
