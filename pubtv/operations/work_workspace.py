"""The derived Work workspace.

Work is intentionally a projection.  It does not create a task record or a
manual completion flag; the canonical schedule, media, and intake records
remain the source of truth.
"""
from __future__ import annotations

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from django.utils import timezone

from .action_items import build_daily_work
from .models import Episode, Occurrence


def _local_date(value, station):
    if value is None:
        return None
    if timezone.is_aware(value):
        try:
            zone = ZoneInfo(station.timezone)
        except (AttributeError, ZoneInfoNotFoundError):
            zone = ZoneInfo("America/Detroit")
        return timezone.localtime(value, zone).date()
    return value.date() if isinstance(value, datetime) else value


def _episode_items(station, selected_date):
    """Return pending episodes without a scheduled occurrence as work items."""
    scheduled = set(Occurrence.objects.filter(station=station, episode__isnull=False,
                                               status="planned", starts_at__gte=timezone.now())
                    .values_list("episode_id", flat=True))
    result = []
    projections = {}
    for episode in Episode.objects.filter(show__station=station, status="pending", archived_at__isnull=True,
                                         show__archived_at__isnull=True).select_related("show"):
        if episode.pk in scheduled:
            continue
        from .episode_schedule import project_episode_schedule
        if episode.show_id not in projections:
            projections[episode.show_id] = project_episode_schedule(
                episode.show, selected_date, selected_date + timedelta(days=366))
        proposed = next((row for row in projections[episode.show_id]["suggestions"]
                         if row["episode"].pk == episode.pk and row["slot"].is_premiere), None)
        due = proposed["starts_at"] if proposed else None
        result.append({
            "key": f"episode:{episode.pk}", "kind": "episode", "title": f"{episode.show.title} · {episode.title}",
            "label": f"{episode.show.title} · {episode.title}",
            "next_airtime": due.strftime("%a %b %d, %-I:%M %p") if due else None,
            "suggested_airtime": due.isoformat() if due else None,
            "sort_date": due.date().isoformat() if due else "9999-12-31",
            "sort_at": due,
            "blocker": "Choose a premiere cycle" if due is None else "Suggested premiere – confirm its dates to add it to the schedule",
            "action_label": "Review episode dates",
            "url": f"/episodes/{episode.pk}/?return=/work/",
            "url_name": "episode-detail", "url_args": (episode.pk,),
            "derived": True,
        })
    return result


def _draft_items(station):
    """Read optional MediaIntakeDraft records without coupling to migrations."""
    try:
        from .models import MediaIntakeDraft
    except ImportError:
        return []
    fields = {f.name for f in MediaIntakeDraft._meta.get_fields()}
    query = MediaIntakeDraft.objects.all()
    if "station" in fields:
        query = query.filter(station=station)
    if "state" in fields:
        query = query.exclude(state__in=["submitted", "complete", "completed", "cancelled"])
    return [{
        "key": f"intake:{draft.pk}", "kind": "intake", "title": "Finish media intake draft",
        "label": (draft.payload or {}).get("title", "Untitled intake draft") if "payload" in fields else "Untitled intake draft",
        "next_airtime": None, "blocker": "Complete intake details before media can be prepared",
        "url": f"/intake/{draft.token}/", "action_label": "Resume intake", "derived": True,
    } for draft in query.order_by("updated_at", "pk")]


def build_work_workspace(station, selected_date=None):
    """Build the complete Work projection, including queues beyond the near horizon."""
    selected_date = selected_date or timezone.localdate()
    derived = build_daily_work(station, selected_date) if station else {"action_groups": [], "waiting_items": [], "action_count": 0}
    actions = [dict(item, kind="action", derived=True) for group in derived["action_groups"] for item in group["items"]]
    waiting = [dict(item, kind="waiting", derived=True) for item in derived["waiting_items"]]
    queued = _episode_items(station, selected_date) + _draft_items(station) if station else []
    for item in actions:
        if item.get("action_label"):
            continue
        title = item.get("title", "").lower()
        if title.startswith("add media"):
            item["action_label"] = "Add media"
        elif title.startswith("record runtime"):
            item["action_label"] = "Review runtime"
        elif title.startswith("review delivery"):
            item["action_label"] = "Review delivery"
        elif title.startswith("review unplanned"):
            item["action_label"] = "Review schedule coverage"
        elif title.startswith("choose episode"):
            item["action_label"] = "Choose episode"
        elif title.startswith("locate source"):
            item["action_label"] = "Locate source"
        elif title.startswith("prepare media"):
            item["action_label"] = "Prepare media"
        else:
            item["action_label"] = "Resolve issue" if item.get("kind") == "action" else "Review plan"
    for item in waiting:
        item.setdefault("action_label", "View progress")
    # An unscheduled episode with no media has one meaningful next action.
    # Fold the suggested premiere into the media action instead of presenting
    # two cards that send the operator to the same record.
    for queued_item in list(queued):
        if queued_item.get("kind") != "episode":
            continue
        label = queued_item.get("label", "")
        matching = next((item for item in actions
                         if item.get("title") == f"Add media for {label}"), None)
        if matching:
            matching["next_airtime"] = queued_item.get("next_airtime")
            matching["suggested_airtime"] = queued_item.get("suggested_airtime")
            matching["sort_at"] = queued_item.get("sort_at")
            matching["blocker"] = queued_item.get("blocker")
            matching["action_label"] = "Add media"
            queued.remove(queued_item)

    def chronological(item):
        value = item.get("sort_at")
        if isinstance(value, datetime):
            return (0, value.timestamp(), item.get("title", ""))
        if value:
            return (0, str(value), item.get("title", ""))
        raw = item.get("needed_at") or item.get("next_airtime")
        for fmt in ("%Y-%m-%d %-I:%M %p", "%Y-%m-%d"):
            try:
                return (0, datetime.strptime(str(raw), fmt).timestamp(), item.get("title", ""))
            except (TypeError, ValueError):
                continue
        return (1, "9999-12-31", item.get("title", ""))

    attention = actions + queued
    attention.sort(key=chronological)
    return {
        "selected_date": selected_date,
        "action_items": actions,
        "waiting_items": waiting,
        "queue_items": queued,
        "groups": [{"title": "Incoming and unfinished – earliest airtime first", "items": attention},
                   {"title": "Running or waiting", "items": waiting}],
        "action_count": len(actions) + len(queued),
    }


# Friendly aliases for view and integration code.
build_work = build_work_workspace
work_projection = build_work_workspace
