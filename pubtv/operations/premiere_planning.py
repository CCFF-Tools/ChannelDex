"""Preview and commit ordered, multi-cycle premiere plans.

The planner deliberately uses the existing WeeklyEpisodeAssignment and
materialize_assignment contracts.  A preview is only a signed description of
the rows to be created; it never creates schedule or media records.
"""

from datetime import date, timedelta
from zoneinfo import ZoneInfo
import hashlib
import json

from django.core.exceptions import ValidationError
from django.core.signing import BadSignature, TimestampSigner
from django.db import IntegrityError, transaction
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone

from .models import Episode, RecurrenceSlot, Show, Station, WeeklyEpisodeAssignment
from .services import active_premiere_slot, audit, materialize_assignment, occurrences_for_cycle


_SIGNER = TimestampSigner(salt="pubtv-bulk-premiere-planner")


def premiere_candidates(show, *, start=None, page=1, page_size=12):
    """Return the next valid premiere boundaries with occupied explanations."""
    start = start or timezone.localdate(timezone=ZoneInfo("America/Detroit"))
    try:
        page = max(1, int(page))
    except (TypeError, ValueError):
        page = 1
    candidates, cursor = [], start
    limit = page * page_size
    while len(candidates) < limit and (cursor - start).days < 370:
        slot = active_premiere_slot(show, cursor)
        if slot is not None:
            assignment = WeeklyEpisodeAssignment.objects.filter(show=show, week_start=cursor - timedelta(days=cursor.weekday())).first()
            candidates.append({"date": cursor, "slot": slot, "occupied": bool(assignment),
                "occupied_by": assignment, "reason": "Already assigned" if assignment else "Available"})
        cursor += timedelta(days=1)
    offset = (page - 1) * page_size
    page_candidates = candidates[offset:offset + page_size]
    recommended = False
    for candidate in page_candidates:
        candidate["recommended"] = not recommended and not candidate["occupied"]
        recommended = recommended or candidate["recommended"]
    return page_candidates


def _fingerprint(show, episodes, assignments, slots, *, first_date, episode_ids):
    """Return a deterministic fingerprint of every input to a preview."""
    payload = {
        "show": show.pk,
        "slot_duration_seconds": show.slot_duration_seconds,
        "station_timezone": show.station.timezone,
        "first_date": first_date.isoformat(),
        "episode_ids": [int(value) for value in episode_ids],
        "episodes": [
            [e.pk, e.title, e.status, e.intended_air_order, e.intended_premiere_date.isoformat() if e.intended_premiere_date else None, e.runtime_seconds]
            for e in episodes
        ],
        "assignments": [
            [a.pk, a.week_start.isoformat(), a.premiere_date.isoformat() if a.premiere_date else None, a.episode_id, a.selection_type, a.is_automatic_carry_forward]
            for a in assignments
        ],
        "slots": [
            [s.pk, s.station_id, s.weekday, s.start_time.isoformat(), s.duration_seconds, s.is_premiere, s.active_from.isoformat() if s.active_from else None, s.active_until.isoformat() if s.active_until else None]
            for s in slots
        ],
    }
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(raw).hexdigest()


def _active_premiere_dates(show, start, count):
    slots = list(show.slots.all())
    first_matches = [
        slot for slot in slots
        if slot.is_premiere and slot.weekday == start.weekday()
        and (not slot.active_from or slot.active_from <= start)
        and (not slot.active_until or start <= slot.active_until)
    ]
    if len(first_matches) != 1:
        raise ValidationError(f"{start} must match exactly one active premiere recurrence.")
    dates = []
    cursor = start
    for _ in range(370 * max(1, count)):
        configured_week = []
        for offset in range(7):
            day = cursor + timedelta(days=offset)
            configured_week.extend(
                slot for slot in slots
                if slot.is_premiere and slot.weekday == day.weekday()
                and (not slot.active_from or slot.active_from <= day)
                and (not slot.active_until or day <= slot.active_until)
            )
        if len({slot.pk for slot in configured_week}) > 1:
            raise ValidationError("More than one active premiere weekday is configured in a seven-day cycle.")
        slot_matches = [
            slot for slot in slots
            if slot.is_premiere and slot.weekday == cursor.weekday()
            and (not slot.active_from or slot.active_from <= cursor)
            and (not slot.active_until or cursor <= slot.active_until)
        ]
        if len(slot_matches) > 1:
            raise ValidationError(f"Premiere date {cursor} has ambiguous active premiere recurrence.")
        if len(slot_matches) == 1:
            dates.append(cursor)
            if len(dates) == count:
                return dates
        cursor += timedelta(days=1)
    raise ValidationError("There are not enough active premiere dates configured after the selected date.")


def _ordered_episodes(show, episode_ids):
    try:
        ids = [int(value) for value in episode_ids]
    except (TypeError, ValueError) as exc:
        raise ValidationError("Episode selections must be numeric episode ids.") from exc
    if not ids or len(ids) > 100 or len(ids) != len(set(ids)):
        raise ValidationError("Choose each pending episode once, in the order it should premiere.")
    episodes = list(Episode.objects.filter(show=show, pk__in=ids))
    by_id = {episode.pk: episode for episode in episodes}
    if len(by_id) != len(ids) or any(by_id[pk].status != "pending" for pk in ids):
        raise ValidationError("Bulk premiere planning only accepts pending episodes from this show.")
    if WeeklyEpisodeAssignment.objects.filter(show=show, episode_id__in=ids, selection_type="premiere").exists():
        raise ValidationError("An episode already has a confirmed premiere assignment and cannot be planned again.")
    return [by_id[pk] for pk in ids]


def build_bulk_premiere_preview(*, show, episode_ids, first_premiere_date):
    """Validate inputs and return a signed, reviewable planning proposal."""
    if not isinstance(first_premiere_date, date):
        raise ValidationError("Enter a valid first premiere date.")
    episodes = _ordered_episodes(show, episode_ids)
    slots = list(show.slots.all().order_by("weekday", "start_time", "pk"))
    dates = _active_premiere_dates(show, first_premiere_date, len(episodes))
    week_starts = [d - timedelta(days=d.weekday()) for d in dates]
    if len(week_starts) != len(set(week_starts)) or any((later - earlier).days < 7 for earlier, later in zip(dates, dates[1:])):
        raise ValidationError("Premiere cycles must use one non-overlapping premiere boundary per seven-day cycle.")
    existing = list(WeeklyEpisodeAssignment.objects.filter(show=show).order_by("week_start", "pk"))
    by_week = {assignment.week_start: assignment for assignment in existing}
    if any(d - timedelta(days=d.weekday()) in by_week for d in dates):
        raise ValidationError("One or more proposed premiere cycles is already occupied, including an explicit No program cycle.")

    occurrences = []
    for episode, premiere_date in zip(episodes, dates):
        # The date must resolve to exactly one active premiere slot.  This is
        # also checked by WeeklyEpisodeAssignment.full_clean at confirmation.
        if active_premiere_slot(show, premiere_date) is None:
            raise ValidationError(f"{premiere_date} is not a uniquely configured active premiere date.")
        assignment = WeeklyEpisodeAssignment(
            show=show,
            week_start=premiere_date - timedelta(days=premiere_date.weekday()),
            premiere_date=premiere_date,
            episode=episode,
            selection_type="premiere",
        )
        cycle = [
            {
                "date": occurrence.starts_at.date().isoformat(),
                "time": occurrence.starts_at.time().isoformat(timespec="minutes"),
                "premiere": occurrence.schedule_role == "premiere",
                "slot_id": occurrence.recurrence_slot_id,
            }
            for occurrence in occurrences_for_cycle(assignment)
        ]
        occurrences.append({"episode_id": episode.pk, "episode_title": episode.title, "premiere_date": premiere_date.isoformat(), "week_start": (premiere_date - timedelta(days=premiere_date.weekday())).isoformat(), "occurrences": sorted(cycle, key=lambda item: (item["date"], item["time"]))})

    fingerprint = _fingerprint(show, episodes, existing, slots, first_date=first_premiere_date, episode_ids=episode_ids)
    payload = {"show_id": show.pk, "episode_ids": [episode.pk for episode in episodes], "first_premiere_date": first_premiere_date.isoformat(), "dates": [item["premiere_date"] for item in occurrences], "fingerprint": fingerprint}
    return {"show": show, "episodes": episodes, "cycles": occurrences, "fingerprint": fingerprint, "token": _SIGNER.sign_object(payload)}


def _load_preview(token):
    try:
        return _SIGNER.unsign_object(token, max_age=3600)
    except (BadSignature, ValueError, TypeError):
        raise ValidationError("This premiere proposal is invalid or expired; preview it again.")


@transaction.atomic
def confirm_bulk_premiere_plan(*, token, actor="owner"):
    """Create all assignments atomically after rechecking the signed preview."""
    payload = _load_preview(token)
    show = Show.objects.select_for_update().get(pk=int(payload["show_id"]))
    try:
        episode_ids = [int(value) for value in payload["episode_ids"]]
    except (TypeError, ValueError) as exc:
        raise ValidationError("The premiere proposal contains invalid episode ids; preview again.") from exc
    list(Episode.objects.select_for_update().filter(show=show, pk__in=episode_ids))
    episodes = _ordered_episodes(show, episode_ids)
    assignments = list(WeeklyEpisodeAssignment.objects.select_for_update().filter(show=show).order_by("week_start", "pk"))
    slots = list(RecurrenceSlot.objects.select_for_update().filter(show=show).order_by("weekday", "start_time", "pk"))
    first_date = date.fromisoformat(payload["first_premiere_date"])
    current = _fingerprint(show, episodes, assignments, slots, first_date=first_date, episode_ids=episode_ids)
    if current != payload.get("fingerprint"):
        raise ValidationError("The show, queue, recurrence, or assignments changed after preview; preview again.")
    dates = [date.fromisoformat(value) for value in payload["dates"]]
    if len(dates) != len(episodes):
        raise ValidationError("The proposal is incomplete; preview again.")
    if any(WeeklyEpisodeAssignment.objects.filter(show=show, week_start=d - timedelta(days=d.weekday())).exists() for d in dates):
        raise ValidationError("A proposed premiere cycle was occupied while confirming; nothing was changed.")
    created = []
    try:
        for episode, premiere_date in zip(episodes, dates):
            assignment = WeeklyEpisodeAssignment(show=show, week_start=premiere_date - timedelta(days=premiere_date.weekday()), premiere_date=premiere_date, episode=episode, selection_type="premiere")
            assignment.full_clean()
            assignment.save()
            materialize_assignment(assignment)
            audit("create", "WeeklyEpisodeAssignment", assignment, f"Bulk premiere planned: {episode.title} on {premiere_date}", actor)
            created.append(assignment)
    except IntegrityError as exc:
        raise ValidationError("A proposed premiere cycle was occupied while confirming; nothing was changed.") from exc
    return created


def bulk_premiere_planner(request, show_id):
    station = Station.objects.first()
    show = get_object_or_404(Show, pk=show_id, station=station)
    episodes = list(show.episodes.filter(status="pending").order_by("intended_air_order", "intended_premiere_date", "legacy_received_at", "pk"))
    selected_ids = request.POST.getlist("episode_ids") or request.GET.getlist("episode_ids")
    if selected_ids:
        order = {str(value): index for index, value in enumerate(selected_ids)}
        episodes.sort(key=lambda episode: order.get(str(episode.pk), len(order) + episode.pk))
    first_value = request.POST.get("first_premiere_date") or request.GET.get("first_premiere_date", "")
    try:
        candidate_start = date.fromisoformat(first_value) if first_value else timezone.localdate(timezone=ZoneInfo("America/Detroit"))
    except ValueError:
        candidate_start = timezone.localdate(timezone=ZoneInfo("America/Detroit"))
    try:
        candidate_page = int(request.GET.get("page", "1"))
    except ValueError:
        candidate_page = 1
    context = {"show": show, "episodes": episodes, "first_premiere_date": first_value, "selected_ids": selected_ids,
               "candidates": premiere_candidates(show, start=candidate_start, page=candidate_page), "candidate_page": candidate_page,
               "intake_context": {key: request.GET.get(key, "") for key in ("item", "episode", "asset", "target", "order")}}
    if request.method == "POST":
        if request.POST.get("action") == "confirm":
            try:
                payload = _load_preview(request.POST.get("preview_token", ""))
                if int(payload.get("show_id", -1)) != show.pk:
                    raise ValidationError("This premiere proposal belongs to another show.")
                created = confirm_bulk_premiere_plan(token=request.POST.get("preview_token", ""))
            except (ValidationError, Show.DoesNotExist) as exc:
                context["error"] = str(exc)
                return render(request, "pubtv/bulk_premiere_planner.html", context, status=400)
            return redirect("week-view")
        try:
            proposal = build_bulk_premiere_preview(show=show, episode_ids=request.POST.getlist("episode_ids"), first_premiere_date=date.fromisoformat(request.POST.get("first_premiere_date", "")))
        except (ValueError, ValidationError) as exc:
            context["error"] = str(exc)
        else:
            context["proposal"] = proposal
    return render(request, "pubtv/bulk_premiere_planner.html", context)
