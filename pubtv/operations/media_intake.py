"""Catalog-only media intake and preparation monitor views.

The normal intake flow records an operator's catalog choice and references. It
does not inspect, copy, hash, encode, transfer, or queue a source. The older
connected queue remains available in media_queue for legacy callers.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import uuid
from urllib.parse import parse_qs, urlencode, urlparse

from django.contrib import messages
from django.core.exceptions import ValidationError
from django.db import IntegrityError, OperationalError, transaction
from django.http import HttpResponseBadRequest, HttpResponseNotAllowed
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse

from .active_station import resolve_active_station
from .forms import AssetForm
from .models import AuditEvent, CatalogIntakeSubmission, Delivery, Device, Episode, MediaAsset, Show

PURPOSES = {"new_episode", "existing_episode", "reusable"}
SAFE_RETURN_PATHS = {
    "/library/", "/intake/", "/schedule/publish/",
    "/media/", "/media/library/", "/media/preparation/",
}

def safe_return_url(value: str | None, fallback: str = "/library/") -> str:
    """Keep only same-origin collection/context URLs supplied by the browser."""
    parsed = urlparse(value or "")
    if parsed.scheme or parsed.netloc or not parsed.path.startswith("/") or parsed.path.startswith("//"):
        return fallback
    if parsed.path not in SAFE_RETURN_PATHS:
        return fallback
    allowed = {"q", "show", "readiness", "scope", "view", "archived",
               "episode", "asset", "target", "date"}
    query = {key: values[-1] for key, values in parse_qs(parsed.query, keep_blank_values=True).items()
             if key in allowed and values}
    return parsed.path + (("?" + urlencode(query)) if query else "")

def _int_field(value, label, *, positive=False):
    value = (value or "").strip()
    if not value:
        return None
    if not value.isdigit() or (positive and int(value) <= 0):
        raise ValueError(f"{label} must be a positive whole number.")
    return int(value)

def _context(station, request):
    source = request.POST if request.method == "POST" else request.GET
    show_id = source.get("show")
    if show_id and not str(show_id).isdigit():
        show_id = ""
    show = Show.objects.filter(pk=show_id, station=station).first() if show_id else None
    episode_id = source.get("episode")
    if episode_id and not str(episode_id).isdigit():
        episode_id = ""
    episode = (Episode.objects.filter(pk=episode_id, show=show).first() if show and episode_id else
               Episode.objects.filter(pk=episode_id, show__station=station).select_related("show").first() if episode_id else None)
    if episode and show is None:
        show = episode.show
    return show, episode

def _catalog_payload(request, station):
    purpose = (request.POST.get("purpose") or "").strip()
    if purpose not in PURPOSES:
        raise ValueError("Choose new episode, existing episode, or reusable media.")
    show_id = request.POST.get("show") or ""
    show = Show.objects.filter(pk=show_id, station=station).first() if show_id.isdigit() else None
    if purpose != "reusable" and show is None:
        raise ValueError("Choose a show from the active station.")
    episode = None
    if purpose == "existing_episode":
        episode_id = request.POST.get("episode") or ""
        episode = Episode.objects.filter(pk=episode_id, show=show).first() if episode_id.isdigit() else None
        if episode is None:
            raise ValueError("Choose an existing episode from the selected show.")
    title = (request.POST.get("title") or "").strip()
    if purpose == "new_episode" and not title:
        raise ValueError("Enter a title for the new episode.")
    if len(title) > 160:
        raise ValueError("Episode titles must be 160 characters or fewer.")
    file_name = Path((request.POST.get("file_name") or "").strip()).name
    if not file_name:
        raise ValueError("Enter a file name or reusable asset name.")
    if len(file_name) > 255:
        raise ValueError("File names must be 255 characters or fewer.")
    form_data = request.POST.copy()
    form_data["episode"] = str(episode.pk) if episode else ""
    form_data.setdefault("kind", "source")
    form_data.setdefault("version", "v1")
    form_data["file_name"] = file_name
    asset_form = AssetForm(form_data, station=station)
    if not asset_form.is_valid():
        raise ValueError(" ".join(str(error) for error in asset_form.errors.values()))
    episode_number = _int_field(request.POST.get("episode_number"), "Episode number", positive=True)
    runtime_seconds = asset_form.cleaned_data.get("runtime_seconds")
    source_reference = (request.POST.get("source_reference") or "").strip()
    storage_reference = (request.POST.get("storage_reference") or "").strip()
    if len(source_reference) > 300 or len(storage_reference) > 300:
        raise ValueError("Source and storage references must be 300 characters or fewer.")
    source_method = (request.POST.get("source_method") or "other").strip()
    valid_methods = {value for value, _label in Delivery.METHOD_CHOICES}
    if source_method not in valid_methods:
        raise ValueError("Choose a valid submission method.")
    return {"purpose": purpose, "show": show, "episode": episode, "title": title,
            "file_name": file_name, "episode_number": episode_number,
            "runtime_seconds": runtime_seconds, "source_reference": source_reference,
            "storage_reference": storage_reference,
            "source_method": source_method,
            "kind": asset_form.cleaned_data["kind"], "version": asset_form.cleaned_data["version"],
            "local_path": asset_form.cleaned_data.get("local_path") or ""}

@transaction.atomic
def save_catalog_intake(*, station, payload, actor="owner"):
    """Save one catalog record atomically and return (asset, created_episode)."""
    show, episode = payload["show"], payload["episode"]
    if show is not None and show.station_id != station.pk:
        raise ValueError("The selected show belongs to another station.")
    if episode is not None and (episode.show_id != show.pk or episode.show.station_id != station.pk):
        raise ValueError("The selected episode belongs to another station.")
    created_episode = False
    if payload["purpose"] == "new_episode":
        episode = Episode.objects.create(
            show=show, title=payload["title"], episode_number=payload["episode_number"],
            runtime_seconds=None,
            runtime_provenance="",
        )
        created_episode = True
        AuditEvent.objects.create(actor=actor, action="create", entity="Episode", entity_id=episode.pk,
                                   summary="Episode created from catalog-only media intake")
    asset = MediaAsset.objects.create(
        station=station, episode=episode, file_name=payload["file_name"], kind=payload.get("kind", "source"), version=payload.get("version", "v1"),
        local_path=payload.get("local_path", ""),
        runtime_seconds=payload["runtime_seconds"],
        runtime_provenance="manual" if payload["runtime_seconds"] is not None else "",
        # Storage destination is deliberately separate from submission source.
        smb_reference=payload["storage_reference"],
    )
    source = payload["source_reference"] or "not supplied"
    destination = payload["storage_reference"] or "not supplied"
    if episode is not None and payload["source_reference"]:
        method = payload.get("source_method") or "other"
        valid_methods = {value for value, _label in Delivery.METHOD_CHOICES}
        if method not in valid_methods:
            method = "other"
        delivery = Delivery.objects.create(
            method=method, other_method="Manual reference" if method == "other" else "",
            reference=payload["source_reference"],
        )
        delivery.episodes.add(episode)
        AuditEvent.objects.create(actor=actor, action="create", entity="Delivery", entity_id=delivery.pk,
                                   summary="Submission source recorded during catalog-only media intake")
    AuditEvent.objects.create(
        actor=actor, action="create", entity="MediaAsset", entity_id=asset.pk,
        summary=(f"Catalog-only media intake saved {asset.file_name}; "
                 f"submission source: {source}; storage destination: {destination}"),
    )
    return asset, created_episode


def _catalog_payload_fingerprint(payload):
    stable = {
        "purpose": payload["purpose"],
        "show_id": payload["show"].pk if payload.get("show") else None,
        "episode_id": payload["episode"].pk if payload.get("episode") else None,
        "title": payload.get("title", ""),
        "file_name": payload["file_name"],
        "episode_number": payload.get("episode_number"),
        "runtime_seconds": payload.get("runtime_seconds"),
        "source_reference": payload.get("source_reference", ""),
        "storage_reference": payload.get("storage_reference", ""),
        "source_method": payload.get("source_method", "other"),
        "kind": payload.get("kind", "source"),
        "version": payload.get("version", "v1"),
        "local_path": payload.get("local_path", ""),
    }
    encoded = json.dumps(stable, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
    return hashlib.sha256(encoded).hexdigest()


def save_catalog_submission(*, station, payload, submission_token, actor="owner"):
    """Reserve a station/token and save its exact catalog payload once."""
    token = str(submission_token or "").strip()
    if not token or len(token) > 120:
        raise ValueError("This intake submission token is invalid. Reload the form and try again.")
    fingerprint = _catalog_payload_fingerprint(payload)

    existing = CatalogIntakeSubmission.objects.select_related("asset").filter(station=station, token=token).first()
    if existing:
        if existing.payload_fingerprint != fingerprint:
            raise ValueError("This submission token was already used for different media. Reload the form for a fresh token.")
        if existing.asset_id is None:
            raise ValueError("This submission is still being saved. Wait briefly and retry.")
        return existing.asset, False

    try:
        with transaction.atomic():
            submission = CatalogIntakeSubmission.objects.create(
                station=station, token=token, payload_fingerprint=fingerprint,
            )
            asset, created_episode = save_catalog_intake(station=station, payload=payload, actor=actor)
            submission.asset = asset
            submission.save(update_fields=["asset"])
            return asset, created_episode
    except IntegrityError:
        # The unique insert is the reservation. Fetch only after the failed
        # transaction has rolled back so a concurrent winner can be observed.
        existing = CatalogIntakeSubmission.objects.select_related("asset").filter(station=station, token=token).first()
        if existing is None:
            raise
        if existing.payload_fingerprint != fingerprint:
            raise ValueError("This submission token was already used for different media. Reload the form for a fresh token.")
        if existing.asset_id is None:
            raise ValueError("This submission is still being saved. Wait briefly and retry.")
        return existing.asset, False
    except OperationalError as exc:
        # SQLite can report a writer-lock conflict instead of surfacing the
        # unique collision. Keep the request safe and retryable; the winning
        # transaction remains the only catalog write.
        if "locked" not in str(exc).lower():
            raise
        raise ValueError("Another intake submission is being saved. Wait briefly and retry.") from exc

def _form_context(request, station, *, data=None, error=""):
    show, episode = _context(station, request)
    values = (data or request.GET).copy()
    if not values.get("purpose") and episode is not None:
        values["purpose"] = "existing_episode"
    return {
        "station": station, "shows": Show.objects.filter(station=station).order_by("title"),
        "episodes": Episode.objects.filter(show__station=station).select_related("show").order_by("show__title", "title"),
        "selected_show": show, "selected_episode": episode,
        "return_url": safe_return_url(values.get("next")),
        "values": values, "submission_token": values.get("submission_token") or uuid.uuid4(), "error": error,
    }

def catalog_intake(request):
    """Normal intake endpoint; parent routing may expose this as assets/new/."""
    station = resolve_active_station(request)
    if station is None:
        return HttpResponseBadRequest("Create a station before cataloging media.")
    if request.method == "GET":
        return render(request, "media_intake.html", _form_context(request, station))
    if request.method != "POST":
        return HttpResponseNotAllowed(["GET", "POST"])
    # Preserve callers that still submit the legacy AssetForm to /assets/new/.
    # The new form always includes an explicit purpose discriminator.
    if not request.POST.get("purpose"):
        from . import views
        return views.asset_create(request)
    next_url = safe_return_url(request.POST.get("next"))
    submission_token = (request.POST.get("submission_token") or "").strip()
    try:
        payload = _catalog_payload(request, station)
        asset, created_episode = save_catalog_submission(
            station=station, payload=payload, submission_token=submission_token,
        )
    except (ValueError, ValidationError) as exc:
        message = "; ".join(str(item) for item in getattr(exc, "messages", [str(exc)]))
        context = _form_context(request, station, data=request.POST, error=message)
        return render(request, "media_intake.html", context, status=400)
    messages.success(request, f"{asset.file_name} saved to the media library.")
    saved_url = reverse("media-intake-saved", kwargs={"asset_id": asset.pk})
    return redirect(saved_url + "?" + urlencode({"next": next_url}))

def intake_saved(request, asset_id):
    station = resolve_active_station(request)
    asset = get_object_or_404(MediaAsset.objects.select_related("episode__show"), pk=asset_id, station=station)
    plan_url = (f"/shows/{asset.episode.show_id}/premieres/?episode_ids={asset.episode_id}" if asset.episode_id
                else f"/occurrences/new/?asset={asset.pk}")
    prepare_url = f"/media/preparation/?asset={asset.pk}"
    if asset.episode_id:
        prepare_url += f"&show={asset.episode.show_id}&episode={asset.episode_id}"
    return render(request, "media_intake_saved.html", {
        "asset": asset, "next_url": safe_return_url(request.GET.get("next")),
        "prepare_url": prepare_url, "plan_url": plan_url,
    })

def preparation_monitor(request):
    """Use the reviewed queue for monitoring, retry, cancel, and setup."""
    from . import media_queue
    station = resolve_active_station(request)
    if request.method == "POST":
        return media_queue.media_queue(request)
    asset_id = request.GET.get("asset")
    if asset_id:
        asset = (MediaAsset.objects.filter(pk=asset_id, station=station).select_related("episode__show").first()
                 if str(asset_id).isdigit() else None)
        if asset is None:
            return media_queue.media_queue(request)
        # Existing queue context is validated again by intake_context; only
        # inject the station-owned episode/show values derived from the asset.
        query = request.GET.copy()
        if asset.episode_id:
            query["show"] = str(asset.episode.show_id)
            query["episode"] = str(asset.episode_id)
        request.GET = query
    return media_queue.media_queue(request)

media_intake_view = catalog_intake
media_intake_saved = intake_saved
preparation_activity = preparation_monitor
