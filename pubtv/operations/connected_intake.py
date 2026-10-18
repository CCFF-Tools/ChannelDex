"""Durable, file-first media intake.

The browser records local file references; it never uploads or copies the
selected bytes. Inspection is intentionally cheap. Complete-file hashing and
output-profile checks remain worker work after preparation confirmation.
"""
from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Iterable
from datetime import date
from pathlib import Path
from types import SimpleNamespace
from uuid import UUID, uuid4, uuid5
from zoneinfo import ZoneInfo

from django.contrib import messages
from django.core.exceptions import ValidationError
from django.db import transaction
from django.http import HttpResponseBadRequest
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import NoReverseMatch, reverse
from django.utils import timezone

from .active_station import resolve_active_station
from .media_identity import measured_runtime
from .models import AuditEvent, Device, Episode, MediaAsset, Occurrence, Show


def _draft_model():
    from .models import MediaIntakeDraft
    return MediaIntakeDraft


def _draft_url(draft):
    try:
        return reverse("connected-intake-draft", kwargs={"token": draft.token})
    except NoReverseMatch:
        return f"/intake/{draft.token}/"


def inspect_source(path: str, *, probe_runner=None) -> dict:
    """Record cheap facts for an existing file without reading all its bytes."""
    candidate = Path(path).expanduser().resolve(strict=True)
    if not candidate.is_file():
        raise ValueError("Choose a local media file, not a folder.")
    stat = candidate.stat()
    if stat.st_size <= 0:
        raise ValueError("Choose a non-empty local media file.")
    facts = {"path": str(candidate), "name": candidate.name, "size": stat.st_size,
             "mtime_ns": stat.st_mtime_ns}
    # Row identity only. The preparation worker computes a complete-file hash.
    facts["key"] = hashlib.sha256(
        f"{candidate}\0{stat.st_size}\0{stat.st_mtime_ns}".encode()
    ).hexdigest()
    # A missing or wedged probe must not strand a durable intake request.
    runtime = (measured_runtime(candidate, probe_runner=probe_runner, timeout_seconds=8)
               if probe_runner else measured_runtime(candidate, timeout_seconds=8))
    if runtime is not None:
        facts["runtime_seconds"] = runtime
    return facts


def suggest_metadata(filename: str, *, show: Show | None = None, context: str = "") -> dict:
    """Return editable filename suggestions; never assert an episode match."""
    stem = Path(filename).stem
    match = re.search(r"(?:episode|ep|e)[ ._-]*(\d{1,5})\b", stem, re.IGNORECASE)
    number = int(match.group(1)) if match else None
    title_stem = stem if not match else stem[:match.start()] + stem[match.end():]
    cleaned = re.sub(r"[._-]+", " ", title_stem).strip()
    return {"show_id": show.pk if show else None, "show_title": show.title if show else "",
            "episode_title": cleaned[:160], "episode_number": number,
            "context": context[:500], "confidence": "medium" if number else "low"}


def _payload(draft):
    value = dict(draft.payload or {})
    value.setdefault("step", "select")
    value.setdefault("rows", [])
    value.setdefault("status", {"state": "draft", "message": ""})
    return value


def _save_payload(draft, payload, *, expected_revision=None, state="draft"):
    if expected_revision not in (None, ""):
        try:
            expected = int(expected_revision)
        except (TypeError, ValueError) as exc:
            raise ValueError("This intake revision is invalid; reload the draft.") from exc
        if draft.revision != expected:
            raise ValueError("This intake changed in another tab; reload the draft before saving.")
    payload = dict(payload)
    status = payload.setdefault("status", {})
    status["state"] = state
    if state == "failed":
        status.setdefault("message", "")
    else:
        status["message"] = ""
    draft.payload = payload
    draft.state = state
    draft.revision = (draft.revision or 1) + 1
    draft.save(update_fields=["payload", "state", "revision", "updated_at"])
    return draft


@transaction.atomic
def _save_error(draft, payload, message, *, expected_revision=None):
    locked = _draft_model().objects.select_for_update().get(pk=draft.pk)
    if expected_revision not in (None, ""):
        try:
            expected = int(expected_revision)
        except (TypeError, ValueError):
            return locked, False
        if locked.revision != expected:
            return locked, False
    payload = dict(payload)
    payload.setdefault("status", {}).update(state="failed", message=str(message))
    locked.payload = payload
    locked.state = "failed"
    locked.revision = (locked.revision or 1) + 1
    locked.save(update_fields=["payload", "state", "revision", "updated_at"])
    return locked, True


@transaction.atomic
def create_draft(*, station, payload=None, token=None, actor="owner"):
    if station is None:
        raise ValueError("Create a station before starting media intake.")
    Model = _draft_model()
    token = UUID(str(token)) if token else uuid4()
    existing = Model.objects.filter(token=token).first()
    if existing:
        if existing.station_id != station.pk:
            raise ValueError("This intake token belongs to another station.")
        return existing, False
    initial = {"step": "select", "rows": [], "status": {"state": "draft", "message": ""}, **(payload or {})}
    return Model.objects.create(station=station, token=token, payload=initial, state="draft"), True


def _split_paths(values):
    result = []
    for value in values:
        result.extend(line.strip() for line in str(value or "").splitlines() if line.strip())
    return result


def add_source_paths(draft, paths: Iterable[str], *, expected_revision=None, probe_runner=None,
                     intake_context=None):
    """Persist the selection first, then inspect it without holding a DB transaction.

    A crash, cancelled probe, or unavailable path therefore leaves a resumable draft
    containing the exact paths the operator selected.
    """
    Model = _draft_model()
    selected_paths = _split_paths(paths)
    if not selected_paths:
        raise ValueError("Select one or more local files.")
    with transaction.atomic():
        locked = Model.objects.select_for_update().get(pk=draft.pk)
        payload = _payload(locked)
        payload["pending_paths"] = selected_paths
        context = intake_context or {}
        if context:
            payload["intake_context"] = {
                key: getattr(context.get(key), "pk", None)
                for key in ("show", "episode", "asset", "target")
            }
        locked = _save_payload(locked, payload, expected_revision=expected_revision)
        staged_revision = locked.revision

    inspected, failures = [], []
    for path in selected_paths:
        try:
            inspected.append(inspect_source(path, probe_runner=probe_runner))
        except (ValueError, OSError, RuntimeError) as exc:
            failures.append((path, str(exc)))

    with transaction.atomic():
        locked = Model.objects.select_for_update().get(pk=draft.pk)
        if locked.revision != staged_revision:
            raise ValueError("This intake changed during inspection; reload the draft before saving.")
        payload = _payload(locked)
        rows = list(payload["rows"])
        known = {(row.get("file") or {}).get("key") for row in rows}
        context = intake_context or _draft_context(payload, locked.station)
        for facts in inspected:
            if facts["key"] in known:
                continue
            suggestion = suggest_metadata(facts["name"])
            episode = context.get("episode")
            asset = context.get("asset")
            show = context.get("show") or (episode.show if episode else None)
            rows.append({"kind": "reusable" if asset and not asset.episode_id else "episode",
                         "file": facts, "suggestion": suggestion,
                         "metadata": {"show_id": show.pk if show else None,
                                      "episode_id": episode.pk if episode else ("new" if show else ""),
                                      "episode_title": episode.title if episode else suggestion["episode_title"],
                                      "episode_number": episode.episode_number if episode and episode.episode_number else (suggestion["episode_number"] or ""),
                                      "target_id": context.get("target").pk if context.get("target") else None,
                                      "asset_name": asset.file_name if asset else ""},
                         "dates": {}, "encoding_mode": "needs_encoding", "duplicate_action": "",
                         "status": "inspected"})
            if asset:
                rows[-1]["encoding_mode"] = "already_encoded" if asset.kind == "encoded" else "needs_encoding"
                rows[-1]["duplicate_action"] = "reuse"
                rows[-1]["existing_asset_id"] = asset.pk
            known.add(facts["key"])
        payload.update(step="inspect" if rows else "select", rows=rows,
                       pending_paths=[path for path, _message in failures])
        if failures:
            message = "; ".join(f"{path}: {message}" for path, message in failures)
            payload.setdefault("status", {}).update(state="failed", message=message)
            locked = _save_payload(locked, payload, expected_revision=staged_revision, state="failed")
        else:
            locked = _save_payload(locked, payload, expected_revision=staged_revision)
    return locked


@transaction.atomic
def add_reusable_row(draft, *, expected_revision=None):
    Model = _draft_model()
    locked = Model.objects.select_for_update().get(pk=draft.pk)
    payload = _payload(locked)
    payload["rows"] = [*payload["rows"], {"kind": "reusable", "file": {}, "suggestion": {},
        "metadata": {"episode_title": ""}, "dates": {}, "encoding_mode": "catalog_only",
        "duplicate_action": "", "status": "inspected"}]
    payload["step"] = "inspect"
    return _save_payload(locked, payload, expected_revision=expected_revision)


def _posted_rows(request, current_rows):
    rows = []
    for index, old in enumerate(current_rows, 1):
        row = dict(old)
        metadata = dict(row.get("metadata") or {})
        metadata.update({"show_id": request.POST.get(f"show_id_{index}", ""),
            "episode_id": request.POST.get(f"episode_id_{index}", ""),
            "episode_title": request.POST.get(f"episode_title_{index}", "").strip(),
            "episode_number": request.POST.get(f"episode_number_{index}", "").strip(),
            "target_id": request.POST.get(f"target_id_{index}", ""),
            "asset_name": request.POST.get(f"asset_name_{index}", "").strip()})
        row.update(kind=request.POST.get(f"kind_{index}", row.get("kind", "episode")),
            metadata=metadata,
            encoding_mode=request.POST.get(f"encoding_mode_{index}", row.get("encoding_mode", "needs_encoding")),
            duplicate_action=request.POST.get(f"duplicate_action_{index}", ""),
            dates={"premiere_date": request.POST.get(f"premiere_date_{index}", "").strip(),
                   "plan": request.POST.get(f"plan_{index}") == "on"})
        rows.append(row)
    return rows


def _existing_asset(row, station, episode=None):
    file_path = (row.get("file") or {}).get("path", "")
    if not file_path:
        return None
    query = MediaAsset.objects.filter(station=station).exclude(local_path="")
    query = query.filter(episode=episode) if episode else query.filter(episode__isnull=True)
    selected = Path(file_path).resolve()
    for asset in query.order_by("-pk"):
        try:
            if Path(asset.local_path).resolve() == selected:
                return asset
        except (OSError, RuntimeError):
            continue
    return None


def _assets_at_path(row, station):
    file_path = (row.get("file") or {}).get("path", "")
    if not file_path:
        return []
    selected = Path(file_path).resolve()
    matches = []
    for asset in MediaAsset.objects.filter(station=station).exclude(local_path="").select_related("episode"):
        try:
            if Path(asset.local_path).resolve() == selected:
                matches.append(asset)
        except (OSError, RuntimeError):
            continue
    return matches


def _validate_rows(rows, station):
    if not rows:
        raise ValueError("Select a file or add a reusable catalog item.")
    validated = []
    for index, row in enumerate(rows, 1):
        item = dict(row)
        metadata = dict(item.get("metadata") or {})
        kind = item.get("kind")
        if kind not in {"episode", "reusable"}:
            raise ValueError(f"Row {index}: choose episode media or reusable station media.")
        episode = None
        if kind == "episode":
            show_id = str(metadata.get("show_id") or "")
            show = Show.objects.filter(pk=show_id, station=station, archived_at__isnull=True).first() if show_id.isdigit() else None
            if show is None:
                raise ValueError(f"Row {index}: choose a show from the active station.")
            episode_id = str(metadata.get("episode_id") or "")
            if not episode_id:
                raise ValueError(f"Row {index}: explicitly choose an existing episode or create a new episode.")
            episode = Episode.objects.filter(pk=episode_id, show=show, archived_at__isnull=True).first() if episode_id.isdigit() else None
            if episode_id != "new" and episode is None:
                raise ValueError(f"Row {index}: the selected episode is unavailable in this station.")
            title = str(metadata.get("episode_title") or (episode.title if episode else "")).strip()
            if episode is None and not title:
                raise ValueError(f"Row {index}: enter an episode title or choose an existing episode.")
            metadata.update(episode_title=title[:160], show_id=show.pk,
                            episode_id=episode.pk if episode else None)
        else:
            metadata.update(show_id=None, episode_id=None)
            name = str(metadata.get("asset_name") or (item.get("file") or {}).get("name") or "").strip()
            if not name:
                raise ValueError(f"Row {index}: enter a reusable asset name.")
            metadata["asset_name"] = Path(name).name[:255]
            item["dates"] = {"premiere_date": "", "plan": False}
        target_id = str(metadata.get("target_id") or "")
        if target_id:
            target = Device.objects.filter(pk=target_id, station=station).first() if target_id.isdigit() else None
            if target is None:
                raise ValueError(f"Row {index}: the destination is unavailable in this station.")
            metadata["target_id"] = target.pk
        else:
            metadata["target_id"] = None
        facts = item.get("file") or {}
        if kind == "episode" and not facts.get("path"):
            raise ValueError(f"Row {index}: episode media requires a local file.")
        if facts.get("path"):
            try:
                path = Path(facts["path"]).resolve(strict=True); stat = path.stat()
            except (OSError, RuntimeError) as exc:
                raise ValueError(f"Row {index}: the selected file is unavailable; choose it again.") from exc
            if (stat.st_size, stat.st_mtime_ns) != (facts.get("size"), facts.get("mtime_ns")):
                raise ValueError(f"Row {index}: the source changed after inspection; inspect it again.")
            if item.get("encoding_mode") not in {"needs_encoding", "already_encoded"}:
                raise ValueError(f"Row {index}: choose whether the file needs encoding.")
        path_matches = _assets_at_path(item, station)
        if episode is None and any(asset.episode_id for asset in path_matches):
            matched = next(asset for asset in path_matches if asset.episode_id)
            raise ValueError(
                f"Row {index}: this file is already catalogued for {matched.episode.title}; "
                "choose that existing episode and then choose reuse or corrected version."
            )
        if kind == "reusable" and any(asset.episode_id for asset in path_matches):
            raise ValueError(f"Row {index}: this file is already episode media; choose its existing episode.")
        existing = _existing_asset(item, station, episode)
        item["existing_asset_id"] = existing.pk if existing else None
        if existing and item.get("duplicate_action") not in {"reuse", "new_version"}:
            raise ValueError(f"Row {index}: this exact path is already catalogued; choose reuse or add a corrected version.")
        if episode and item.get("duplicate_action") != "reuse":
            item["future_plays"] = [{"id": occurrence.pk,
                "starts_at": occurrence.starts_at.astimezone(ZoneInfo(station.timezone)).isoformat(),
                "role": occurrence.schedule_role,
                "label": occurrence.label} for occurrence in Occurrence.objects.filter(
                    station=station, episode=episode, starts_at__gte=timezone.now(),
                    status="planned", is_exception=False).order_by("starts_at", "pk")]
        else:
            item["future_plays"] = []
        item["metadata"] = metadata; item["status"] = "resolved"
        validated.append(item)
    return validated


def _groups(rows):
    groups = {}
    for position, row in enumerate(rows):
        metadata = row.get("metadata") or {}
        if row.get("kind") == "episode":
            groups.setdefault((metadata.get("show_id"), metadata.get("target_id")), []).append((position, row))
    return groups


def _review_rows(rows):
    result = []
    for position, row in rows:
        metadata, facts = row.get("metadata") or {}, row.get("file") or {}
        result.append({"source_path": facts.get("path", ""),
            "episode_id": metadata.get("episode_id") or "new", "new_title": metadata.get("episode_title", ""),
            "episode_number": metadata.get("episode_number", ""), "runtime_seconds": facts.get("runtime_seconds", ""),
            "encoding_mode": row.get("encoding_mode"),
            "asset_id": row.get("existing_asset_id") if row.get("duplicate_action") == "reuse" else None,
            "position": position})
    return result


def _binding(rows):
    stable = [{"file": {key: (row.get("file") or {}).get(key) for key in ("path", "size", "mtime_ns", "key")},
        "kind": row.get("kind"), "metadata": row.get("metadata"), "dates": row.get("dates"),
        "encoding_mode": row.get("encoding_mode"), "duplicate_action": row.get("duplicate_action")} for row in rows]
    return hashlib.sha256(json.dumps(stable, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def _selected_group_date(group):
    planned = [bool(row.get("dates", {}).get("plan")) for _position, row in group]
    if any(planned) and not all(planned):
        raise ValueError("Choose premiere planning for every file in a show/destination group, or for none of them.")
    dates = [row.get("dates", {}).get("premiere_date") for _position, row in group if row.get("dates", {}).get("plan")]
    if not dates:
        return None
    if any(not value for value in dates):
        raise ValueError("Choose a premiere date for every episode marked for planning.")
    return dates[0]


def _validate_requested_cycles(group, proposal):
    if proposal is None:
        return
    requested = [row.get("dates", {}).get("premiere_date") for _position, row in group]
    proposed = [cycle["premiere_date"] for cycle in proposal.get("cycles", [])]
    if requested != proposed:
        raise ValueError("The selected premiere dates do not form this show's next exact premiere run; use the reviewed suggested dates.")


@transaction.atomic
def resolve_rows(draft, rows, *, expected_revision=None):
    Model = _draft_model()
    locked = Model.objects.select_for_update().get(pk=draft.pk)
    validated = _validate_rows(rows, locked.station)
    payload = _payload(locked)
    payload.update(rows=validated, step="resolve", review_tokens=[], review_groups=[])
    locked = _save_payload(locked, payload, expected_revision=expected_revision)
    from .media_queue import (
        _stage_review_rows,
        _store_intake_premiere_proposals,
        create_intake_review,
    )
    binding = _binding(validated)
    review_groups = []
    for (show_id, target_id), group in _groups(validated).items():
        first_date = _selected_group_date(group)
        if target_id is None:
            proposal = _no_target_preview(group, locked.station) if first_date else None
            _validate_requested_cycles(group, proposal)
            review_groups.append({"show_id": show_id, "target_id": None, "review_token": "",
                "first_premiere_date": first_date, "positions": [p for p, _r in group]})
            continue
        show = Show.objects.get(pk=show_id, station=locked.station)
        target = Device.objects.get(pk=target_id, station=locked.station)
        token = uuid5(locked.token, f"review:{locked.revision}:{show_id}:{target_id}")
        review = create_intake_review(target=target, show=show,
            action="prepare_and_plan" if first_date else "prepare_only", submission_token=token)
        _stage_review_rows(review, _review_rows(group))
        candidates = _store_intake_premiere_proposals(review) if first_date else []
        selected_proposal = next((candidate.get("proposal") for candidate in candidates
                                  if candidate["date"].isoformat() == first_date), None)
        if first_date and selected_proposal is None:
            raise ValueError("The selected premiere date is unavailable; choose one of the reviewed suggested dates.")
        _validate_requested_cycles(group, selected_proposal)
        review.payload = {**review.payload, "connected_draft_token": str(locked.token),
                          "draft_revision": locked.revision + 1, "metadata_binding": binding}
        review.save(update_fields=["payload"])
        review_groups.append({"show_id": show_id, "target_id": target_id, "review_token": str(review.token),
            "first_premiere_date": first_date, "positions": [p for p, _r in group],
            "premiere_candidates": [{"date": candidate["date"].isoformat(), "reason": candidate["reason"],
                "recommended": candidate.get("recommended", False), "proposal": candidate.get("proposal")}
                for candidate in candidates]})
    payload = _payload(locked)
    payload.update(step="device_review", review_groups=review_groups,
        review_tokens=[g["review_token"] for g in review_groups if g["review_token"]], metadata_binding=binding)
    return _save_payload(locked, payload, expected_revision=locked.revision)


def _next_version(episode):
    versions = MediaAsset.objects.filter(episode=episode).values_list("version", flat=True)
    numbers = [int(value[1:]) for value in versions if value.startswith("v") and value[1:].isdigit()]
    return f"v{max(numbers or [0]) + 1}"


def _assert_file_unchanged(row):
    facts = row.get("file") or {}
    if not facts.get("path"):
        return None
    try:
        path = Path(facts["path"]).resolve(strict=True); stat = path.stat()
    except (OSError, RuntimeError) as exc:
        raise ValueError("A reviewed source file is unavailable; choose it and review again.") from exc
    if (stat.st_size, stat.st_mtime_ns) != (facts.get("size"), facts.get("mtime_ns")):
        raise ValueError("A reviewed source file changed; inspect and review it again.")
    return path


def _save_catalog_row(station, row, *, actor="owner"):
    metadata = row.get("metadata") or {}
    path = _assert_file_unchanged(row)
    episode = Episode.objects.filter(pk=metadata.get("episode_id"), show__station=station,
                                     show__archived_at__isnull=True, archived_at__isnull=True).first() if metadata.get("episode_id") else None
    if row.get("kind") == "episode" and episode is None:
        number = metadata.get("episode_number")
        episode = Episode.objects.create(show_id=metadata["show_id"], title=metadata["episode_title"],
            episode_number=int(number) if str(number).isdigit() else None,
            runtime_seconds=(row.get("file") or {}).get("runtime_seconds"), runtime_provenance="measured")
        AuditEvent.objects.create(station=station, actor=actor, action="create", entity="Episode", entity_id=episode.pk,
                                  summary="Episode created from reviewed connected intake")
    existing = MediaAsset.objects.filter(pk=row.get("existing_asset_id"), station=station).first() if row.get("existing_asset_id") else None
    if existing and row.get("duplicate_action") == "reuse":
        return episode, existing, False
    name = metadata.get("asset_name") or (row.get("file") or {}).get("name")
    asset = MediaAsset.objects.create(station=station, episode=episode, file_name=name,
        kind="encoded" if row.get("encoding_mode") == "already_encoded" else "source",
        version=_next_version(episode) if episode else "v1", local_path=str(path) if path else "",
        runtime_seconds=(row.get("file") or {}).get("runtime_seconds"),
        runtime_provenance="measured" if (row.get("file") or {}).get("runtime_seconds") else "")
    AuditEvent.objects.create(station=station, actor=actor, action="create", entity="MediaAsset", entity_id=asset.pk,
        summary="Reviewed local reference added as a new media version" if episode else "Reusable media saved from connected intake")
    return episode, asset, True


def _no_target_preview(group, station):
    from .media_queue import _intake_premiere_proposal
    show = Show.objects.get(pk=group[0][1]["metadata"]["show_id"], station=station)
    first = _selected_group_date(group)
    if not first:
        return None
    rows = [{"position": position, "name": row["file"]["name"],
             "episode_id": row["metadata"].get("episode_id"), "new_title": row["metadata"]["episode_title"],
             "encode_before_transfer": row.get("encoding_mode") == "needs_encoding"} for position, row in group]
    review = SimpleNamespace(show=show, show_id=show.pk, payload={"rows": rows})
    return _intake_premiere_proposal(review, date.fromisoformat(first))


@transaction.atomic
def confirm_draft(draft, *, expected_revision, actor="owner"):
    Model = _draft_model()
    locked = Model.objects.select_for_update().select_related("station").get(pk=draft.pk)
    if locked.state == "complete":
        return _payload(locked).get("results", []), False
    try:
        revision = int(expected_revision)
    except (TypeError, ValueError) as exc:
        raise ValueError("This intake revision is invalid; reload the reviewed draft.") from exc
    if revision != locked.revision:
        raise ValueError("This intake changed after review; reload and review it again.")
    payload = _payload(locked); rows = payload.get("rows") or []
    if payload.get("step") != "device_review" or _binding(rows) != payload.get("metadata_binding"):
        raise ValueError("Metadata or dates changed after review; review the intake again.")
    _assert_active_bindings(rows, locked.station)
    from .media_queue import MediaIntakeReview, confirm_intake_review
    results, completed_positions = [], set()
    for group_info in payload.get("review_groups", []):
        positions = group_info["positions"]
        if group_info.get("review_token"):
            review = MediaIntakeReview.objects.select_related("show", "target").get(token=group_info["review_token"])
            if review.show.station_id != locked.station_id or review.target.station_id != locked.station_id:
                raise ValueError("A preparation review belongs to another station.")
            if (review.payload.get("connected_draft_token") != str(locked.token)
                    or review.payload.get("draft_revision") != locked.revision
                    or review.payload.get("metadata_binding") != payload.get("metadata_binding")):
                raise ValueError("The preparation review no longer matches this draft.")
            batch, created = confirm_intake_review(review,
                first_premiere_date=group_info.get("first_premiere_date"), actor=actor)
            review.refresh_from_db()
            episode_ids = list(batch.items.exclude(asset__episode=None).order_by("position", "pk")
                               .values_list("asset__episode_id", flat=True))
            republication_ids = sorted({
                int(play["id"])
                for position in positions
                for play in rows[position].get("future_plays", [])
                if play.get("id")
            })
            results.append({"kind": "prepared", "batch_id": batch.pk, "created": created,
                            "episode_ids": episode_ids,
                            "assignment_ids": review.payload.get("assignment_ids", []),
                            "republication_occurrence_ids": republication_ids})
            completed_positions.update(positions); continue
        group = [(position, rows[position]) for position in positions]
        proposal = _no_target_preview(group, locked.station)
        planned_cycles = proposal["cycles"] if proposal else []
        saved = [_save_catalog_row(locked.station, row, actor=actor) for _position, row in group]
        assignment_ids = []
        if planned_cycles:
            from .models import WeeklyEpisodeAssignment
            from .services import audit, materialize_assignment
            for (episode, _asset, _created), cycle in zip(saved, planned_cycles):
                assignment = WeeklyEpisodeAssignment(show=episode.show, week_start=date.fromisoformat(cycle["week_start"]),
                    premiere_date=date.fromisoformat(cycle["premiere_date"]), episode=episode, selection_type="premiere")
                assignment.full_clean(); assignment.save(); materialize_assignment(assignment)
                audit("create", "WeeklyEpisodeAssignment", assignment,
                      f"Reviewed connected intake premiere planned: {episode.title}", actor)
                assignment_ids.append(assignment.pk)
        for episode, asset, created in saved:
            future = list(Occurrence.objects.filter(station=locked.station, episode=episode,
                starts_at__gte=timezone.now(), status="planned", is_exception=False).values_list("pk", flat=True)) if episode else []
            results.append({"kind": "catalogued", "episode_id": episode.pk if episode else None,
                "episode_ids": [episode.pk] if episode else [],
                "asset_id": asset.pk, "created": created, "preparation_blocked": True,
                "assignment_ids": assignment_ids, "republication_occurrence_ids": future})
        completed_positions.update(positions)
    for position, row in enumerate(rows):
        if position in completed_positions:
            continue
        _episode, asset, created = _save_catalog_row(locked.station, row, actor=actor)
        target_id = (row.get("metadata") or {}).get("target_id")
        batch_id = None
        preparation_blocked = True
        if target_id and asset.local_path:
            from .media_queue import confirm_existing_asset_review, create_existing_asset_review
            target = Device.objects.get(pk=target_id, station=locked.station)
            batch, _item = create_existing_asset_review(target=target, asset=asset,
                submission_token=uuid5(locked.token, f"reusable:{position}:{target.pk}"), actor=actor)
            batch, _created_batch = confirm_existing_asset_review(batch, actor=actor)
            batch_id = batch.pk; preparation_blocked = False
        results.append({"kind": "prepared" if batch_id else "catalogued", "episode_id": None,
            "asset_id": asset.pk, "episode_ids": [], "batch_id": batch_id,
            "created": created, "preparation_blocked": preparation_blocked,
            "assignment_ids": [], "republication_occurrence_ids": []})
    payload.update(step="complete", results=results, completed_at=timezone.now().isoformat(),
                   status={"state": "complete", "message": ""})
    locked.payload = payload; locked.state = "complete"; locked.revision += 1
    locked.save(update_fields=["payload", "state", "revision", "updated_at"])
    return results, True


def _suggested_dates(rows, station):
    from .premiere_planning import premiere_candidates
    result, offsets = {}, {}
    for index, row in enumerate(rows, 1):
        if row.get("kind") != "episode" or row.get("dates", {}).get("premiere_date"):
            continue
        show_id = str((row.get("metadata") or {}).get("show_id") or "")
        show = Show.objects.filter(pk=show_id, station=station).first() if show_id.isdigit() else None
        if not show:
            continue
        candidates = [candidate for candidate in premiere_candidates(show) if not candidate["occupied"]]
        offset = offsets.get(show.pk, 0)
        if offset < len(candidates):
            result[index] = candidates[offset]["date"].isoformat(); offsets[show.pk] = offset + 1
    return result


def _initial_context(request, station):
    """Resolve canonical intake links without trusting cross-station IDs."""
    source = request.POST if request.method == "POST" else request.GET
    def numeric(key):
        value = source.get(key)
        return value if str(value or "").isdigit() else None
    show = (Show.objects.filter(pk=numeric("show"), station=station, archived_at__isnull=True).first()
            if numeric("show") else None)
    episode = (Episode.objects.filter(pk=numeric("episode"), show__station=station,
                                      show__archived_at__isnull=True, archived_at__isnull=True)
               .select_related("show").first() if numeric("episode") else None)
    asset = (MediaAsset.objects.filter(pk=numeric("asset"), station=station)
             .select_related("episode__show").first() if numeric("asset") else None)
    if asset and asset.episode_id:
        if asset.episode.archived_at or asset.episode.show.archived_at:
            asset = None
        else:
            episode = asset.episode
            show = episode.show
    elif episode:
        show = episode.show
    target = (Device.objects.filter(pk=numeric("target"), station=station,
                                    ultranexus_settings__is_current=True).distinct().first()
              if numeric("target") else None)
    manual_path = str(source.get("path") or "").strip()
    path = asset.local_path if asset and asset.local_path else manual_path
    return {"show": show, "episode": episode, "asset": asset, "target": target,
            "path": path, "asset_missing_path": bool(asset and not asset.local_path)}


def _draft_context(payload, station):
    """Rehydrate only active, station-bound context saved with a draft."""
    saved = payload.get("intake_context") or {}
    show = Show.objects.filter(pk=saved.get("show"), station=station,
                               archived_at__isnull=True).first()
    episode = (Episode.objects.filter(pk=saved.get("episode"), show__station=station,
                                      show__archived_at__isnull=True, archived_at__isnull=True)
               .select_related("show").first())
    asset = (MediaAsset.objects.filter(pk=saved.get("asset"), station=station)
             .select_related("episode__show").first())
    if asset and asset.episode_id:
        if asset.episode.archived_at or asset.episode.show.archived_at:
            asset = None
        else:
            episode, show = asset.episode, asset.episode.show
    elif episode:
        show = episode.show
    target = (Device.objects.filter(pk=saved.get("target"), station=station,
                                    ultranexus_settings__is_current=True).distinct().first())
    return {"show": show, "episode": episode, "asset": asset, "target": target}


def _assert_active_bindings(rows, station):
    for row in rows:
        metadata = row.get("metadata") or {}
        show_id, episode_id = metadata.get("show_id"), metadata.get("episode_id")
        if show_id and not Show.objects.filter(pk=show_id, station=station,
                                                archived_at__isnull=True).exists():
            raise ValueError("A reviewed show was archived or moved; start a fresh intake review.")
        if episode_id and not Episode.objects.filter(pk=episode_id, show_id=show_id,
                archived_at__isnull=True, show__archived_at__isnull=True).exists():
            raise ValueError("A reviewed episode was archived or moved; start a fresh intake review.")


def connected_intake_view(request, token=None):
    """Select, review and confirm local references with durable resume state."""
    station = resolve_active_station(request)
    if station is None:
        return render(request, "connected_intake.html", {"station_missing": True, "draft": None,
            "rows": [], "error": "Create a station before starting media intake."}, status=400)
    Model = _draft_model(); draft = None
    initial = _initial_context(request, station) if token is None else {}
    if token:
        try:
            draft = get_object_or_404(Model, token=UUID(str(token)), station=station)
        except (ValueError, TypeError):
            return HttpResponseBadRequest("Invalid intake token.")
    if request.method == "GET" and draft is None:
        recent = Model.objects.filter(station=station).exclude(state="complete").order_by("-updated_at")[:12]
        return render(request, "connected_intake.html", {"draft": None, "rows": [], "recent_drafts": recent,
            "error": "", "shows": Show.objects.filter(station=station, archived_at__isnull=True),
            "initial": initial, "initial_path": initial.get("path", "")})
    if request.method == "POST" and draft is None:
        draft, _created = create_draft(station=station)
    payload = _payload(draft); error = payload.get("status", {}).get("message", ""); status = 200
    if request.method == "POST":
        action = request.POST.get("action", "inspect")
        try:
            if action == "inspect":
                paths = _split_paths(request.POST.getlist("paths"))
                if not paths:
                    raise ValueError("Select one or more local files.")
                draft = add_source_paths(draft, paths, expected_revision=request.POST.get("revision") or None,
                                         intake_context=initial)
            elif action == "add_reusable":
                draft = add_reusable_row(draft, expected_revision=request.POST.get("revision") or None)
            elif action == "resolve":
                posted = _posted_rows(request, payload.get("rows", [])); payload["rows"] = posted
                draft = resolve_rows(draft, posted, expected_revision=request.POST.get("revision") or None)
            elif action == "confirm":
                confirm_draft(draft, expected_revision=request.POST.get("revision"))
                messages.success(request, "Intake confirmed. Catalog and schedule records are saved; preparation remains visible separately.")
                return redirect(_draft_url(draft))
            else:
                raise ValueError("Unknown intake action.")
            return redirect(_draft_url(draft))
        except (ValueError, ValidationError, OSError, KeyError) as exc:
            payload = _payload(draft); message = "; ".join(getattr(exc, "messages", [str(exc)]))
            if action == "resolve":
                payload["rows"] = locals().get("posted", payload.get("rows", []))
            draft, _preserved = _save_error(draft, payload, message,
                expected_revision=request.POST.get("revision") or None)
            error = message; status = 400
    draft.refresh_from_db(); payload = _payload(draft)
    review_groups = payload.get("review_groups", [])
    for group_info in review_groups:
        if not group_info.get("review_token"):
            group = [(position, payload["rows"][position]) for position in group_info["positions"]]
            try:
                group_info["selected_proposal"] = _no_target_preview(group, station)
            except (ValueError, ValidationError) as exc:
                group_info["proposal_error"] = "; ".join(getattr(exc, "messages", [str(exc)]))
        else:
            group_info["selected_proposal"] = next((item.get("proposal") for item in group_info.get("premiere_candidates", [])
                if item.get("date") == group_info.get("first_premiere_date")), None)
    rows = payload.get("rows", [])
    draft_context = _draft_context(payload, station)
    suggestions = _suggested_dates(rows, station)
    for index, row in enumerate(rows, 1):
        row["suggested_premiere_date"] = suggestions.get(index, "")
    context = {"draft": draft, "rows": rows,
        "shows": Show.objects.filter(station=station, archived_at__isnull=True).order_by("title"),
        "targets": Device.objects.filter(station=station, ultranexus_settings__is_current=True).distinct().order_by("name"),
        "episodes": Episode.objects.filter(show__station=station, show__archived_at__isnull=True,
            archived_at__isnull=True).select_related("show").order_by("show__title", "title"),
        "error": error or payload.get("status", {}).get("message", ""),
        "review_groups": review_groups, "results": payload.get("results", []),
        "draft_context": draft_context,
        "pending_paths_text": "\n".join(payload.get("pending_paths") or []),
        "complete": draft.state == "complete", "browse_url": reverse("browse-path"),
        "setup_url": "/settings/devices/", "publish_url": "/schedule/publish/"}
    return render(request, "connected_intake_review.html", context, status=status)


connected_intake = connected_intake_view
