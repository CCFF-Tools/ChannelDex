"""Signed, atomic review/commit helpers for schedule changes."""
import copy
from datetime import date, datetime, timedelta
from itertools import pairwise
from zoneinfo import ZoneInfo

from django.core.exceptions import ValidationError
from django.core.signing import BadSignature, TimestampSigner
from django.db import transaction
from django.db.models import F
from django.utils import timezone

from .models import Episode, Occurrence, RecurrenceSlot, Show, WeeklyEpisodeAssignment


def _unsign(salt, token, message, max_age):
    try:
        return TimestampSigner(salt=salt).unsign_object(token, max_age=max_age)
    except (BadSignature, ValueError, TypeError):
        raise ValueError(message)


def _occurrence_snapshot(occurrences):
    return [[item.pk, item.revision] for item in sorted(occurrences, key=lambda item: item.pk)]


def _assignment_snapshot(assignment):
    return {
        "show": assignment.show_id,
        "week_start": assignment.week_start.isoformat(),
        "premiere_date": assignment.premiere_date.isoformat() if assignment.premiere_date else None,
        "episode": assignment.episode_id,
        "selection_type": assignment.selection_type,
        "state": assignment.state,
    }


def _slot_snapshot(slot):
    return {
        "show": slot.show_id, "weekday": slot.weekday,
        "start_time": slot.start_time.isoformat(),
        "duration_seconds": slot.duration_seconds,
        "is_premiere": slot.is_premiere,
        "active_from": slot.active_from.isoformat() if slot.active_from else None,
        "active_until": slot.active_until.isoformat() if slot.active_until else None,
    }


def _validate_pairs(pairs, current, message):
    try:
        expected = {int(pk): int(revision) for pk, revision in pairs}
    except (TypeError, ValueError):
        raise ValueError(message)
    if len(expected) != len(pairs) or {item.pk: item.revision for item in current} != expected:
        raise ValueError(message)


def _validate_exact_scope(queryset, pairs, message):
    current = list(queryset.order_by("pk"))
    _validate_pairs(pairs, current, message)
    return current


def _validate_no_overlaps(candidates, *, station_id, exclude_ids):
    candidates = [item for item in candidates if item.status == "planned"]
    if not candidates:
        return
    earliest = min(item.starts_at for item in candidates) - timedelta(days=1)
    latest = max(item.ends_at for item in candidates) + timedelta(days=1)
    others = list(Occurrence.objects.filter(
        station_id=station_id, status="planned", starts_at__lt=latest,
    ).exclude(pk__in=exclude_ids))
    intervals = [(item.starts_at, item.ends_at, item.label) for item in candidates]
    intervals.extend((item.starts_at, item.ends_at, item.label) for item in others if item.ends_at > earliest)
    intervals.sort(key=lambda row: row[0])
    for left, right in pairwise(intervals):
        if right[0] < left[1]:
            raise ValidationError(
                f"The proposed schedule overlaps {left[2]} and {right[2]}. Resolve the conflict before committing."
            )


def _preview_result(rows, candidates, *, station_id, exclude_ids):
    blockers = []
    try:
        _validate_no_overlaps(candidates, station_id=station_id, exclude_ids=exclude_ids)
    except ValidationError as exc:
        blockers.extend(exc.messages)
    return {
        "rows": sorted(
            rows,
            key=lambda row: (row.get("before_starts_at") or row.get("after_starts_at"), row.get("occurrence_id") or 0),
        ),
        "blockers": blockers,
        "requires_republication": any(row["uploaded"] and row["changes_plan"] for row in rows),
    }


def cycle_change_preview(assignment, occurrences, proposal):
    """Build the exact human-readable before/after review for a cycle edit."""
    from .services import occurrences_for_cycle

    persisted = WeeklyEpisodeAssignment.all_objects.select_related("show", "episode").get(pk=assignment.pk)
    premiere_date, episode, selection_type = _parse_cycle_proposal({"proposal": proposal}, persisted)
    persisted.premiere_date = premiere_date
    persisted.week_start = premiere_date - timedelta(days=premiere_date.weekday())
    persisted.episode = episode
    persisted.selection_type = selection_type
    desired = [item for item in occurrences_for_cycle(persisted) if item.starts_at >= timezone.now()]
    current = list(occurrences)
    canonical = {item.recurrence_slot_id: item for item in current if not item.is_exception and item.recurrence_slot_id}
    exceptions = {item.recurrence_slot_id for item in current if item.is_exception and item.recurrence_slot_id}
    desired_by_slot = {item.recurrence_slot_id: item for item in desired}
    rows, candidates = [], []
    for item in current:
        uploaded = item.uploaded_revisions.exists()
        if item.is_exception:
            rows.append({
                "occurrence_id": item.pk, "action": "Preserve exception",
                "before_starts_at": item.starts_at, "after_starts_at": item.starts_at,
                "before_episode": item.episode, "after_episode": item.episode,
                "before_duration": item.planned_duration_seconds, "after_duration": item.planned_duration_seconds,
                "is_exception": True, "uploaded": uploaded, "changes_plan": False,
            })
            continue
        wanted = desired_by_slot.get(item.recurrence_slot_id)
        if selection_type == "none" or not wanted:
            rows.append({
                "occurrence_id": item.pk, "action": "Cancel play",
                "before_starts_at": item.starts_at, "after_starts_at": None,
                "before_episode": item.episode, "after_episode": None,
                "before_duration": item.planned_duration_seconds, "after_duration": None,
                "is_exception": False, "uploaded": uploaded, "changes_plan": item.status == "planned",
            })
            continue
        changed_time = item.starts_at != wanted.starts_at
        changed_episode = item.episode_id != wanted.episode_id
        changed_duration = item.planned_duration_seconds != wanted.planned_duration_seconds
        action = "No change"
        if changed_time and changed_episode:
            action = "Move play and change episode"
        elif changed_time:
            action = "Move play"
        elif changed_episode:
            action = "Change episode"
        elif changed_duration:
            action = "Change duration"
        if item.status != "planned":
            action = "Restore play with reviewed changes" if (
                changed_time or changed_episode or changed_duration
            ) else "Restore planned play"
        candidate = copy.copy(wanted)
        candidate.pk = item.pk
        candidates.append(candidate)
        rows.append({
            "occurrence_id": item.pk, "action": action,
            "before_starts_at": item.starts_at, "after_starts_at": wanted.starts_at,
            "before_episode": item.episode, "after_episode": wanted.episode,
            "before_duration": item.planned_duration_seconds, "after_duration": wanted.planned_duration_seconds,
            "is_exception": False, "uploaded": uploaded,
            "changes_plan": changed_time or changed_episode or changed_duration or item.status != "planned",
        })
    for slot_id, wanted in desired_by_slot.items():
        if slot_id in canonical or slot_id in exceptions:
            continue
        candidates.append(wanted)
        rows.append({
            "occurrence_id": None, "action": "Add play",
            "before_starts_at": None, "after_starts_at": wanted.starts_at,
            "before_episode": None, "after_episode": wanted.episode,
            "before_duration": None, "after_duration": wanted.planned_duration_seconds,
            "is_exception": False, "uploaded": False, "changes_plan": True,
        })
    return _preview_result(
        rows, candidates, station_id=persisted.show.station_id,
        exclude_ids=[item.pk for item in current if not item.is_exception],
    )


def recurring_change_review(slot, occurrences, changes):
    """Build before/after rows and preflight overlap checks for a recurring edit."""
    current = list(occurrences)
    rows, candidates = [], []
    end_mode = changes.get("mode") == "end"
    tz = ZoneInfo(slot.station.timezone)
    if not end_mode:
        effective = date.fromisoformat(changes["effective_date"])
        weekday = int(changes["weekday"])
        start_time = datetime.strptime(changes["start_time"], "%H:%M:%S").time()
        duration = int(changes["duration_seconds"])
    for item in current:
        uploaded = item.uploaded_revisions.exists()
        if item.is_exception:
            after_starts, after_duration, action, changes_plan = item.starts_at, item.planned_duration_seconds, "Preserve exception", False
        elif end_mode and item.status == "planned":
            after_starts, after_duration, action, changes_plan = None, None, "Cancel play", True
        elif end_mode:
            after_starts, after_duration, action, changes_plan = item.starts_at, item.planned_duration_seconds, "No change", False
        elif item.status != "planned":
            after_starts, after_duration, action, changes_plan = item.starts_at, item.planned_duration_seconds, "No change – already cancelled", False
        else:
            local = timezone.localtime(item.starts_at, tz)
            new_day = local.date() - timedelta(days=local.weekday()) + timedelta(days=weekday)
            if new_day < effective:
                new_day += timedelta(days=7)
            after_starts = datetime.combine(new_day, start_time, tzinfo=tz)
            after_duration = duration
            moved = after_starts != item.starts_at
            resized = after_duration != item.planned_duration_seconds
            action = "Move play" if moved else ("Change duration" if resized else "No change")
            if moved and resized:
                action = "Move play and change duration"
            changes_plan = moved or resized
            if item.status == "planned":
                candidate = copy.copy(item)
                candidate.starts_at, candidate.planned_duration_seconds = after_starts, after_duration
                candidates.append(candidate)
        rows.append({
            "occurrence_id": item.pk, "action": action,
            "before_starts_at": item.starts_at, "after_starts_at": after_starts,
            "before_episode": item.episode, "after_episode": item.episode if after_starts else None,
            "before_duration": item.planned_duration_seconds, "after_duration": after_duration,
            "is_exception": item.is_exception, "uploaded": uploaded, "changes_plan": changes_plan,
        })
    return _preview_result(rows, candidates, station_id=slot.station_id, exclude_ids=[item.pk for item in candidates])


def duration_change_review(show, occurrences, duration_seconds):
    rows, candidates = [], []
    for item in occurrences:
        eligible = not item.is_exception and item.status == "planned"
        after_duration = duration_seconds if eligible else item.planned_duration_seconds
        changed = eligible and after_duration != item.planned_duration_seconds
        candidate = copy.copy(item)
        if eligible:
            candidate.planned_duration_seconds = after_duration
            candidates.append(candidate)
        rows.append({
            "occurrence_id": item.pk,
            "action": "Preserve exception" if item.is_exception else ("Change duration" if changed else "No change"),
            "before_starts_at": item.starts_at, "after_starts_at": item.starts_at,
            "before_episode": item.episode, "after_episode": item.episode,
            "before_duration": item.planned_duration_seconds, "after_duration": after_duration,
            "is_exception": item.is_exception, "uploaded": item.uploaded_revisions.exists(),
            "changes_plan": changed,
        })
    return _preview_result(rows, candidates, station_id=show.station_id, exclude_ids=[item.pk for item in candidates])


def payload_requires_republication(payload):
    ids = [int(pk) for pk in payload.get("changed_occurrences", [])]
    return Occurrence.objects.filter(pk__in=ids, uploaded_revisions__isnull=False).exists()


def sign_cycle_review(assignment, occurrences, proposal=None, changed_occurrence_ids=None):
    now = timezone.now()
    selected = list(occurrences)
    persisted = WeeklyEpisodeAssignment.all_objects.get(pk=assignment.pk)
    proposal = proposal or {
        "show": assignment.show_id,
        "premiere_date": assignment.premiere_date.isoformat() if assignment.premiere_date else None,
        "episode": assignment.episode_id,
        "selection_type": assignment.selection_type,
    }
    return TimestampSigner(salt="pubtv-cycle-review").sign_object({
        "assignment": assignment.pk, "original": _assignment_snapshot(persisted),
        "proposal": proposal, "occurrences": _occurrence_snapshot(selected),
        "changed_occurrences": sorted(int(pk) for pk in (changed_occurrence_ids or [])),
        "scope_cutoff": now.isoformat(),
        "exact_future": all(item.starts_at >= now for item in selected),
    })


def load_cycle_review(token, max_age=3600):
    return _unsign("pubtv-cycle-review", token, "Schedule review is invalid or expired.", max_age)


def _parse_cycle_proposal(payload, assignment):
    proposal = payload.get("proposal") or {}
    if int(proposal.get("show") or 0) != assignment.show_id:
        raise ValueError("The reviewed show cannot be changed for this cycle.")
    try:
        premiere_date = date.fromisoformat(proposal["premiere_date"])
    except (KeyError, TypeError, ValueError):
        raise ValueError("The reviewed premiere date is invalid.")
    if premiere_date != assignment.premiere_date and premiere_date < timezone.localdate():
        raise ValidationError("A premiere cycle cannot be moved into the past.")
    episode_id = proposal.get("episode")
    if episode_id in ("", None):
        episode, selection_type = None, "none"
    else:
        episode = Episode.objects.filter(
            pk=int(episode_id), show_id=assignment.show_id, archived_at__isnull=True,
            show__archived_at__isnull=True,
        ).first()
        if not episode:
            raise ValueError("The reviewed episode is unavailable or belongs to another show.")
        selection_type = "premiere" if episode.status == "pending" else "rerun"
    if proposal.get("selection_type") not in (None, selection_type):
        raise ValueError("The reviewed episode selection is inconsistent.")
    return premiere_date, episode, selection_type


@transaction.atomic
def commit_cycle_review(assignment, *, token_payload, episode=None, actor="owner"):
    """Commit only the proposal and exact future set captured by preview."""
    from .services import audit, occurrences_for_cycle

    if int(token_payload.get("assignment") or 0) != assignment.pk:
        raise ValueError("Schedule review does not match this cycle.")
    WeeklyEpisodeAssignment.all_objects.filter(pk=assignment.pk).update(state=F("state"))
    assignment = WeeklyEpisodeAssignment.all_objects.select_related("show", "episode").get(pk=assignment.pk)
    if _assignment_snapshot(assignment) != token_payload.get("original"):
        raise ValueError("The cycle assignment changed after preview; review it again.")
    cutoff = datetime.fromisoformat(token_payload["scope_cutoff"])
    if token_payload.get("exact_future"):
        current = _validate_exact_scope(
            Occurrence.objects.filter(weekly_assignment=assignment, starts_at__gte=cutoff),
            token_payload.get("occurrences", []),
            "The schedule changed after preview; review it again.",
        )
    else:
        ids = [pair[0] for pair in token_payload.get("occurrences", [])]
        current = list(Occurrence.objects.filter(weekly_assignment=assignment, pk__in=ids))
        _validate_pairs(token_payload.get("occurrences", []), current, "The schedule changed after preview; review it again.")

    premiere_date, reviewed_episode, selection_type = _parse_cycle_proposal(token_payload, assignment)
    if episode is not None and reviewed_episode and episode.pk != reviewed_episode.pk:
        raise ValueError("Submitted episode does not match the reviewed change.")
    if assignment.show.archived_at:
        raise ValueError("Archived shows cannot be scheduled.")
    assignment.premiere_date = premiere_date
    assignment.week_start = premiere_date - timedelta(days=premiere_date.weekday())
    assignment.episode = reviewed_episode
    assignment.selection_type = selection_type
    assignment.is_automatic_carry_forward = False
    assignment.full_clean()
    assignment.save()

    canonical = [item for item in current if not item.is_exception and item.starts_at >= timezone.now()]
    by_slot = {item.recurrence_slot_id: item for item in canonical if item.recurrence_slot_id}
    desired = [item for item in occurrences_for_cycle(assignment) if item.starts_at >= timezone.now()]
    candidates = []
    for wanted in desired:
        existing = by_slot.get(wanted.recurrence_slot_id)
        wanted.full_clean()
        if existing:
            wanted.pk, wanted.revision = existing.pk, existing.revision
            wanted.asset_id = existing.asset_id if existing.episode_id == wanted.episode_id else None
        candidates.append(wanted)
    _validate_no_overlaps(candidates, station_id=assignment.show.station_id, exclude_ids=[item.pk for item in canonical])

    desired_slots = {item.recurrence_slot_id for item in desired}
    for item in canonical:
        if ((selection_type == "none" or item.recurrence_slot_id not in desired_slots)
                and Occurrence.objects.filter(pk=item.pk, revision=item.revision).update(
                status="cancelled",
                reason="Weekly selection changed to no program." if selection_type == "none" else "No longer in the reviewed premiere cycle.",
                revision=F("revision") + 1,
            ) != 1):
            raise ValueError("The schedule changed after preview; review it again.")
    if selection_type != "none":
        for wanted in candidates:
            if wanted.pk:
                existing = by_slot[wanted.recurrence_slot_id]
                if Occurrence.objects.filter(pk=existing.pk, revision=existing.revision).update(
                    starts_at=wanted.starts_at, planned_duration_seconds=wanted.planned_duration_seconds,
                    show_id=wanted.show_id, episode_id=wanted.episode_id, asset_id=wanted.asset_id,
                    item_type="episode", label=wanted.label, status="planned", reason="",
                    recurrence_slot_id=wanted.recurrence_slot_id, schedule_role=wanted.schedule_role,
                    cycle_key=wanted.cycle_key, revision=F("revision") + 1,
                ) != 1:
                    raise ValueError("The schedule changed after preview; review it again.")
            elif not any(item.recurrence_slot_id == wanted.recurrence_slot_id for item in current if item.is_exception):
                wanted.save(skip_revision=True)
    audit("update", "WeeklyEpisodeAssignment", assignment, "Cycle review committed", actor=actor)
    return assignment


def sign_recurring_review(slot, occurrences, changes, changed_occurrence_ids=None):
    persisted = RecurrenceSlot.objects.get(pk=slot.pk)
    selected = list(occurrences)
    assignment_ids = sorted({item.weekly_assignment_id for item in selected if item.weekly_assignment_id})
    assignments = [
        [item.pk, _assignment_snapshot(item)]
        for item in WeeklyEpisodeAssignment.all_objects.filter(pk__in=assignment_ids).order_by("pk")
    ]
    return TimestampSigner(salt="pubtv-recurring-review").sign_object({
        "slot": slot.pk, "original": _slot_snapshot(persisted),
        "occurrences": _occurrence_snapshot(selected), "assignments": assignments,
        "changed_occurrences": sorted(int(pk) for pk in (changed_occurrence_ids or [])),
        "changes": changes,
    })


def load_recurring_review(token, max_age=3600):
    return _unsign("pubtv-recurring-review", token, "Recurring schedule review is invalid or expired.", max_age)


@transaction.atomic
def commit_recurring_review(slot, *, token_payload, actor="owner"):
    from .services import audit

    if int(token_payload.get("slot") or 0) != slot.pk:
        raise ValueError("Review does not match this weekly time.")
    RecurrenceSlot.objects.filter(pk=slot.pk).update(weekday=F("weekday"))
    slot = RecurrenceSlot.objects.select_related("show", "station").get(pk=slot.pk)
    if _slot_snapshot(slot) != token_payload.get("original"):
        raise ValueError("The weekly time changed after preview; review it again.")
    reviewed_assignments = {int(pk): snapshot for pk, snapshot in token_payload.get("assignments", [])}
    current_assignments = list(WeeklyEpisodeAssignment.all_objects.filter(pk__in=reviewed_assignments))
    if (
        len(current_assignments) != len(reviewed_assignments)
        or any(_assignment_snapshot(item) != reviewed_assignments[item.pk] for item in current_assignments)
    ):
        raise ValueError("A premiere cycle changed after preview; review the weekly time again.")
    changes = token_payload.get("changes") or {}
    if changes.get("mode") == "end":
        try:
            end_date = date.fromisoformat(changes["active_until"])
        except (KeyError, TypeError, ValueError):
            raise ValueError("The reviewed weekly-time end date is invalid.")
        if slot.active_from and end_date < slot.active_from:
            raise ValidationError("The end date cannot be before this weekly time begins.")
        current = _validate_exact_scope(
            Occurrence.objects.filter(recurrence_slot=slot, starts_at__date__gt=end_date),
            token_payload.get("occurrences", []),
            "The schedule changed after preview; review it again.",
        )
        revisions = {int(pk): int(revision) for pk, revision in token_payload["occurrences"]}
        for item in current:
            if item.is_exception or item.status != "planned":
                continue
            if Occurrence.objects.filter(pk=item.pk, revision=revisions[item.pk]).update(
                status="cancelled", reason="Weekly time ended by reviewed schedule change.",
                revision=F("revision") + 1,
            ) != 1:
                raise ValueError("The schedule changed after preview; review it again.")
        slot.active_until = end_date
        slot.full_clean()
        slot.save(update_fields=["active_until"])
        audit("end", "RecurrenceSlot", slot, f"Weekly time ended {end_date}", actor=actor)
        return None
    try:
        effective = date.fromisoformat(changes["effective_date"])
        weekday, duration = int(changes["weekday"]), int(changes["duration_seconds"])
        start_time = datetime.strptime(changes["start_time"], "%H:%M:%S").time()
        active_until = date.fromisoformat(changes["active_until"]) if changes.get("active_until") else None
        is_premiere = bool(changes["is_premiere"])
    except (KeyError, TypeError, ValueError):
        raise ValueError("The reviewed weekly time is invalid.")
    if effective < timezone.localdate():
        raise ValidationError("The effective date cannot be in the past.")
    if slot.active_from and effective <= slot.active_from:
        raise ValidationError("The replacement must start after the preserved slot version begins.")
    if active_until and active_until < effective:
        raise ValidationError("The replacement end date cannot be before its effective date.")
    current = _validate_exact_scope(
        Occurrence.objects.filter(recurrence_slot=slot, starts_at__date__gte=effective),
        token_payload.get("occurrences", []),
        "The schedule changed after preview; review it again.",
    )
    replacement = RecurrenceSlot(
        station=slot.station, show=slot.show, weekday=weekday, start_time=start_time,
        duration_seconds=duration, is_premiere=is_premiere,
        active_from=effective, active_until=active_until,
    )
    tz = ZoneInfo(slot.station.timezone)
    candidates = []
    for item in current:
        if item.is_exception or item.status != "planned":
            continue
        local = timezone.localtime(item.starts_at, tz)
        new_day = local.date() - timedelta(days=local.weekday()) + timedelta(days=weekday)
        if new_day < effective:
            new_day += timedelta(days=7)
        item.starts_at = datetime.combine(new_day, start_time, tzinfo=tz)
        item.planned_duration_seconds = duration
        item.full_clean()
        candidates.append(item)
    _validate_no_overlaps(candidates, station_id=slot.station_id, exclude_ids=[item.pk for item in candidates])
    slot.active_until = effective - timedelta(days=1)
    slot.full_clean()
    slot.save(update_fields=["active_until"])
    replacement.full_clean()
    replacement.save()
    assignment_by_id = {item.pk: item for item in current_assignments}
    if slot.is_premiere and replacement.is_premiere:
        for item in candidates:
            if item.schedule_role != "premiere" or not item.weekly_assignment_id:
                continue
            assignment = assignment_by_id[item.weekly_assignment_id]
            new_date = timezone.localtime(item.starts_at, tz).date()
            assignment.premiere_date = new_date
            assignment.week_start = new_date - timedelta(days=new_date.weekday())
            assignment.full_clean()
            assignment.save(update_fields=["premiere_date", "week_start"])
    revisions = {int(pk): int(revision) for pk, revision in token_payload["occurrences"]}
    for item in candidates:
        if Occurrence.objects.filter(pk=item.pk, revision=revisions[item.pk]).update(
            starts_at=item.starts_at, planned_duration_seconds=duration,
            recurrence_slot=replacement, revision=F("revision") + 1,
        ) != 1:
            raise ValueError("The schedule changed after preview; review it again.")
    audit("supersede", "RecurrenceSlot", slot, f"Weekly time changed effective {effective}", actor=actor)
    return replacement


def sign_duration_review(show, occurrences, duration_seconds, effective_date=None, changed_occurrence_ids=None):
    effective_date = effective_date or timezone.localdate()
    persisted = Show.objects.get(pk=show.pk)
    return TimestampSigner(salt="pubtv-duration-review").sign_object({
        "show": show.pk, "original_duration": persisted.slot_duration_seconds,
        "occurrences": _occurrence_snapshot(occurrences),
        "changed_occurrences": sorted(int(pk) for pk in (changed_occurrence_ids or [])),
        "duration_seconds": int(duration_seconds), "effective_date": effective_date.isoformat(),
    })


def load_duration_review(token, max_age=3600):
    return _unsign("pubtv-duration-review", token, "Duration review is invalid or expired.", max_age)


@transaction.atomic
def commit_duration_review(show, *, token_payload, actor="owner"):
    from .services import audit

    if int(token_payload.get("show") or 0) != show.pk:
        raise ValueError("Review does not match this show.")
    Show.objects.filter(pk=show.pk).update(slot_duration_seconds=F("slot_duration_seconds"))
    show.refresh_from_db()
    if show.slot_duration_seconds != token_payload.get("original_duration"):
        raise ValueError("The show changed after preview; review it again.")
    try:
        duration = int(token_payload["duration_seconds"])
        effective = date.fromisoformat(token_payload["effective_date"])
    except (KeyError, TypeError, ValueError):
        raise ValueError("The reviewed duration is invalid.")
    if effective < timezone.localdate():
        raise ValidationError("The effective date cannot be in the past.")
    current = _validate_exact_scope(
        Occurrence.objects.filter(show=show, starts_at__date__gte=effective),
        token_payload.get("occurrences", []),
        "The schedule changed after preview; review it again.",
    )
    candidates = []
    for item in current:
        if item.is_exception or item.status != "planned":
            continue
        item.planned_duration_seconds = duration
        candidates.append(item)
    _validate_no_overlaps(candidates, station_id=show.station_id, exclude_ids=[item.pk for item in candidates])
    Show.objects.filter(pk=show.pk).update(slot_duration_seconds=duration)
    RecurrenceSlot.objects.filter(show=show, active_until__isnull=True).update(duration_seconds=duration)
    revisions = {int(pk): int(revision) for pk, revision in token_payload["occurrences"]}
    for item in candidates:
        if Occurrence.objects.filter(pk=item.pk, revision=revisions[item.pk]).update(
            planned_duration_seconds=duration, revision=F("revision") + 1,
        ) != 1:
            raise ValueError("The schedule changed after preview; review it again.")
    audit("update", "Show", show, f"Show time slot length changed to {duration} seconds effective {effective}", actor=actor)
    return show


def preview_occurrence_change(occurrence, *, starts_at=None, episode=None, duration_seconds=None):
    if starts_at is None and episode is None and duration_seconds is None:
        raise ValidationError("Provide at least one schedule change.")
    return {
        "occurrence": occurrence,
        "original": {"starts_at": occurrence.starts_at, "episode": occurrence.episode, "duration_seconds": occurrence.planned_duration_seconds},
        "proposed": {"starts_at": starts_at or occurrence.starts_at, "episode": episode or occurrence.episode, "duration_seconds": duration_seconds if duration_seconds is not None else occurrence.planned_duration_seconds},
        "preserve_evidence": True, "requires_revision": True,
    }


def recurring_change_preview(slot, *, start_time=None, duration_seconds=None, effective_date=None):
    if start_time is None and duration_seconds is None:
        raise ValidationError("Provide a recurring time or duration change.")
    return {"slot": slot, "effective_date": effective_date, "start_time": start_time or slot.start_time, "duration_seconds": duration_seconds if duration_seconds is not None else slot.duration_seconds, "preserve_past": True, "preserve_exceptions": True}
