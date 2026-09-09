from datetime import datetime, timedelta, date
from zoneinfo import ZoneInfo
from django.contrib import messages
from django.db import transaction
from django.db.models import Q
from django.http import HttpResponseBadRequest
from django.core.exceptions import ValidationError
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from .forms import OccurrenceForm, StationForm, DeviceForm, ShowForm, EpisodeForm, AssetForm, DeliveryForm, SlotForm, AssignmentForm, PreparationForm, ProgrammingForm, UploadForm, AiringForm
from .models import Episode, Occurrence, Show, Station, Device, MediaAsset, Delivery, RecurrenceSlot, WeeklyEpisodeAssignment, Preparation, OccurrenceProgramming, UploadedScheduleRevision, AiringEvidence, AuditEvent
from .services import schedule_alerts, audit, materialize_assignment, confirm_preparation_fact, create_upload_snapshot, revise_airing, sign_upload_preview, load_upload_preview, commit_upload_preview

def _alerts(occurrences):
    alerts = []
    for kind, first, second in schedule_alerts(occurrences):
        if kind == "overlap": alerts.append(f"Overlap: {first.label} / {second.label}")
        elif kind == "short_gap": alerts.append(f"Short-gap advisory (coverage unknown unless explicitly scheduled) before {second.label}")
        else: alerts.append(f"Over slot: {first.label} runs beyond its fixed slot")
    return alerts

def dashboard(request):
    station = Station.objects.first()
    occurrences = list(Occurrence.objects.filter(station=station).select_related("episode", "show")) if station else []
    return render(request, "dashboard.html", {"station": station, "occurrences": occurrences, "alerts": _alerts(occurrences)})

def show_list(request):
    station = Station.objects.first()
    return render(request, "show_list.html", {"shows": station.shows.all() if station else []})

def show_detail(request, show_id):
    show = get_object_or_404(Show.objects.prefetch_related("episodes", "occurrences"), pk=show_id)
    pending = show.episodes.filter(status="pending").order_by("intended_air_order", "received_at", "pk")
    return render(request, "show_detail.html", {"show": show, "pending": pending})

def occurrence_create(request):
    station = Station.objects.first()
    if not station: return HttpResponseBadRequest("Create a station first")
    form = OccurrenceForm(request.POST or None, station=station)
    if request.method == "POST" and form.is_valid():
        occurrence = form.save(commit=False); occurrence.station = station
        occurrence.full_clean(); occurrence.save(skip_revision=True)
        audit("create", "Occurrence", occurrence, "Schedule plan created")
        messages.success(request, "Occurrence created."); return redirect("dashboard")
    return render(request, "occurrence_form.html", {"form": form, "title": "Add schedule item"})

def occurrence_edit(request, pk):
    occurrence = get_object_or_404(Occurrence, pk=pk)
    if request.method == "POST":
        expected = request.POST.get("expected_revision")
        form = OccurrenceForm(request.POST, instance=occurrence, station=occurrence.station)
        try: expected_value = int(expected)
        except (TypeError, ValueError): expected_value = None
        if expected_value is None or expected_value < 1 or expected_value != occurrence.revision:
            return HttpResponseBadRequest("This item changed in another tab; reload before saving.")
        if form.is_valid():
            with transaction.atomic():
                updated = Occurrence.objects.filter(pk=pk, revision=expected_value).update(
                    item_type=form.cleaned_data["item_type"], label=form.cleaned_data["label"],
                    show=form.cleaned_data["show"], episode=form.cleaned_data["episode"],
                    starts_at=form.cleaned_data["starts_at"], planned_duration_seconds=form.cleaned_data["planned_duration_seconds"],
                    status=form.cleaned_data["status"], reason=form.cleaned_data["reason"], revision=expected_value + 1)
                if not updated: return HttpResponseBadRequest("This item changed in another tab; reload before saving.")
            audit("update", "Occurrence", occurrence, "Schedule plan revised")
            messages.success(request, "Occurrence updated."); return redirect("dashboard")
    else:
        form = OccurrenceForm(instance=occurrence, station=occurrence.station, initial={"expected_revision": occurrence.revision})
    return render(request, "occurrence_form.html", {"form": form, "title": "Edit schedule item", "occurrence": occurrence})

def _crud(request, model, form_class, title, pk=None):
    obj = get_object_or_404(model, pk=pk) if pk else None
    scoped = {ShowForm, EpisodeForm, AssetForm, DeliveryForm}
    kwargs = {"instance": obj}
    if form_class in scoped:
        kwargs["station"] = Station.objects.first()
    form = form_class(request.POST or None, **kwargs)
    if request.method == "POST" and form.is_valid():
        obj = form.save(); audit("update" if pk else "create", model.__name__, obj, title); return redirect("dashboard")
    return render(request, "simple_form.html", {"form": form, "title": title})
def setup_station(request): return _crud(request, Station, StationForm, "Station setup")
def setup_device(request): return _crud(request, Device, DeviceForm, "Device setup")
def show_create(request): return _crud(request, Show, ShowForm, "Create show")
def episode_create(request): return _crud(request, Episode, EpisodeForm, "Create episode")
def asset_create(request): return _crud(request, MediaAsset, AssetForm, "Record media asset")
def delivery_create(request): return _crud(request, Delivery, DeliveryForm, "Record delivery")
def slot_create(request):
    station = Station.objects.first()
    if not station:
        return HttpResponseBadRequest("Create a station first")
    form = SlotForm(request.POST or None, station=station)
    if request.method == "POST" and form.is_valid():
        obj = form.save(commit=False); obj.station = station; obj.full_clean(); obj.save()
        audit("create", "RecurrenceSlot", obj, "Create recurrence slot")
        return redirect("dashboard")
    return render(request, "simple_form.html", {"form": form, "title": "Create recurrence slot"})
def assignment_create(request):
    station = Station.objects.first()
    form = AssignmentForm(request.POST or None, station=station)
    if request.method == "POST" and form.is_valid():
        assignment = form.save()
        materialize_assignment(assignment)
        audit("create", "WeeklyEpisodeAssignment", assignment, "Weekly selection")
        return redirect("dashboard")
    return render(request, "simple_form.html", {"form": form, "title": "Weekly episode assignment"})
def day_view(request):
    station = Station.objects.first()
    try:
        selected = date.fromisoformat(request.GET.get("date", "")) if request.GET.get("date") else timezone.localdate()
    except ValueError:
        return HttpResponseBadRequest("Invalid date")
    tz = ZoneInfo(station.timezone) if station else ZoneInfo("America/Detroit")
    start = timezone.make_aware(datetime.combine(selected, datetime.min.time()), tz)
    end = start + timedelta(days=1)
    qs = Occurrence.objects.filter(station=station, starts_at__gte=start, starts_at__lt=end).select_related("show", "episode") if station else Occurrence.objects.none()
    local_intervals = sorted((max(item.starts_at, start), min(item.ends_at, end)) for item in qs)
    covered = 0
    cursor = start
    for interval_start, interval_end in local_intervals:
        if interval_end <= cursor:
            continue
        covered += (interval_end - max(interval_start, cursor)).total_seconds()
        cursor = max(cursor, interval_end)
    return render(request, "history.html", {"title": "Agenda / day plan", "occurrences": qs, "selected_date": selected, "previous_date": selected - timedelta(days=1), "next_date": selected + timedelta(days=1), "coverage_seconds": int(covered), "unknown_seconds": 86400 - int(covered), "alerts": _alerts(list(qs))})

def week_view(request):
    station = Station.objects.first()
    try:
        selected = date.fromisoformat(request.GET.get("date", "")) if request.GET.get("date") else timezone.localdate()
    except ValueError:
        return HttpResponseBadRequest("Invalid date")
    selected -= timedelta(days=selected.weekday())
    days = []
    for offset in range(7):
        day = selected + timedelta(days=offset)
        days.append(day)
    tz = ZoneInfo(station.timezone) if station else ZoneInfo("America/Detroit")
    start = timezone.make_aware(datetime.combine(selected, datetime.min.time()), tz)
    end = timezone.make_aware(datetime.combine(selected + timedelta(days=7), datetime.min.time()), tz)
    occurrences = Occurrence.objects.filter(station=station, starts_at__gte=start, starts_at__lt=end).order_by("starts_at") if station else []
    return render(request, "history.html", {"title": "Week plan", "occurrences": occurrences, "week_days": days, "selected_date": selected})

def agenda_view(request):
    try:
        days = min(max(int(request.GET.get("days", "7")), 1), 31)
    except ValueError:
        return HttpResponseBadRequest("Invalid range")
    station = Station.objects.first()
    start = timezone.localdate()
    end = start + timedelta(days=days)
    tz = ZoneInfo(station.timezone) if station else ZoneInfo("America/Detroit")
    start_at = timezone.make_aware(datetime.combine(start, datetime.min.time()), tz)
    end_at = timezone.make_aware(datetime.combine(end, datetime.min.time()), tz)
    occurrences = Occurrence.objects.filter(station=station, starts_at__gte=start_at, starts_at__lt=end_at).order_by("starts_at") if station else []
    return render(request, "history.html", {"title": "Upcoming agenda", "occurrences": occurrences, "selected_date": start})
def history_view(request):
    station = Station.objects.first(); occurrences = Occurrence.objects.filter(station=station).order_by("starts_at") if station else []
    return render(request, "history.html", {"title": "History: plans and evidence", "occurrences": occurrences, "uploads": UploadedScheduleRevision.objects.all().order_by("-uploaded_at"), "previously_scheduled": Episode.objects.filter(status="previously_scheduled").select_related("show"), "evidence": AiringEvidence.objects.all().order_by("aired_at"), "audit_events": AuditEvent.objects.all().order_by("occurred_at")})
def preparation_edit(request, pk):
    occurrence = get_object_or_404(Occurrence, pk=pk)
    obj, _ = Preparation.objects.get_or_create(occurrence=occurrence)
    if request.method == "POST":
        form = PreparationForm(request.POST, instance=obj)
        expected = request.POST.get("expected_revision")
        try: expected = int(expected)
        except (TypeError, ValueError): expected = None
        if expected is None or expected != obj.revision:
            return HttpResponseBadRequest("This preparation changed in another tab; reload before saving.")
        if form.is_valid():
            actor = request.POST.get("actor", "owner") or "owner"
            provenance = form.cleaned_data.get("provenance", "manual") or "manual"
            changed = [name for name in form.changed_data if name not in {"provenance", "expected_revision"}]
            mapping = {"source_available": "source_available", "ame_preset": "ame", "ftp_output_device": "ftp", "library_registration": "library", "slot_assignment": "slot", "uploaded_schedule_revision": "upload"}
            if not changed:
                audit("update", "Preparation", obj, "Preparation facts changed: none", actor)
                return redirect("dashboard")
            now = timezone.now()
            updates = {"revision": expected + 1}
            for field in changed:
                fact = mapping[field]
                updates[field] = form.cleaned_data[field]
                updates[f"{fact}_actor"] = actor
                updates[f"{fact}_at"] = now
                updates[f"{fact}_provenance"] = provenance
            if Preparation.objects.filter(pk=obj.pk, revision=expected).update(**updates) != 1:
                return HttpResponseBadRequest("This preparation changed in another tab; reload before saving.")
            obj.refresh_from_db()
            audit("update", "Preparation", obj, f"Preparation facts changed: {', '.join(changed) or 'none'}", actor)
            return redirect("dashboard")
    else:
        form = PreparationForm(instance=obj, initial={"expected_revision": obj.revision})
    return render(request, "simple_form.html", {"form": form, "title": "Preparation checklist", "occurrence": occurrence})
def programming_create(request, pk):
    occurrence = get_object_or_404(Occurrence, pk=pk); form = ProgrammingForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        obj = form.save(commit=False); obj.occurrence=occurrence; obj.save(); audit("create", "OccurrenceProgramming", obj, "Programming confirmation"); return redirect("dashboard")
    return render(request, "simple_form.html", {"form": form, "title": "Programming confirmation"})
def upload_create(request):
    station = Station.objects.first()
    form = UploadForm(request.POST or None, station=station)
    if request.method == "POST" and form.is_valid():
        submitted = list(form.cleaned_data["occurrences"])
        if any(item.status != "planned" for item in submitted):
            if request.POST.get("upload_preview_token"):
                return HttpResponseBadRequest("Upload preview is invalid, expired, or stale.")
            form.add_error("occurrences", "Only current planned occurrences may be previewed.")
            return render(request, "simple_form.html", {"form": form, "title": "Preview and commit uploaded schedule"})
        selected = submitted
        if not selected:
            form.add_error("occurrences", "Select at least one current planned occurrence.")
            return render(request, "simple_form.html", {"form": form, "title": "Preview and commit uploaded schedule"})
        preview = {
            "device": form.cleaned_data["device"],
            "external_reference": form.cleaned_data["external_reference"],
            "occurrences": selected,
            "conflicts": _alerts(selected),
            "token": sign_upload_preview(device_id=form.cleaned_data["device"].pk, external_reference=form.cleaned_data["external_reference"], occurrences=selected),
        }
        if request.POST.get("preview"):
            return render(request, "simple_form.html", {"form": form, "title": "Preview and commit uploaded schedule", "upload_preview": preview})
        try:
            signed = load_upload_preview(request.POST.get("upload_preview_token", ""))
            upload, committed_occurrences = commit_upload_preview(
                device=form.cleaned_data["device"],
                external_reference=form.cleaned_data["external_reference"],
                token_payload=signed,
                submitted_ids=[item.pk for item in submitted],
                station_id=station.pk,
            )
        except (ValidationError, KeyError, TypeError, ValueError):
            return HttpResponseBadRequest("Upload preview is invalid, expired, or stale.")
        for occurrence in committed_occurrences:
            from .services import advance_passed_premiere
            advance_passed_premiere(occurrence)
        messages.success(request, "Schedule upload snapshot committed.")
        return redirect("history")
    return render(request, "simple_form.html", {"form": form, "title": "Preview and commit uploaded schedule"})

def upload_transition(request, pk, state):
    if request.method != "POST" or state not in {"superseded", "invalidated"}:
        return HttpResponseBadRequest("Use POST with a valid upload transition.")
    upload = get_object_or_404(UploadedScheduleRevision, pk=pk)
    reason = request.POST.get("reason", "").strip()
    if not reason:
        return HttpResponseBadRequest("A transition reason is required.")
    try:
        expected_revision = int(request.POST.get("expected_revision", ""))
    except (TypeError, ValueError):
        return HttpResponseBadRequest("Expected upload revision is required.")
    with transaction.atomic():
        if UploadedScheduleRevision.objects.filter(pk=pk, state="active", revision=expected_revision).update(
            state=state, supersession_reason=reason, revision=expected_revision + 1) != 1:
            return HttpResponseBadRequest("Upload is no longer active or changed; reload before transitioning.")
        upload.refresh_from_db()
        audit("transition", "UploadedScheduleRevision", upload, f"{state}: {reason}", request.POST.get("actor", "owner"))
    return redirect("history")
def airing_create(request, pk):
    occurrence = get_object_or_404(Occurrence, pk=pk); form = AiringForm(request.POST or None, occurrence=occurrence)
    if request.method == "POST" and form.is_valid():
        obj=form.save(commit=False); obj.occurrence=occurrence; obj.actor="owner"
        if obj.supersedes_id:
            obj = revise_airing(obj.supersedes, status=obj.status, source=obj.source, aired_at=obj.aired_at, actor=obj.actor, notes=obj.notes)
        else:
            obj.save()
        audit("create", "AiringEvidence", obj, "Airing evidence"); return redirect("history")
    return render(request, "simple_form.html", {"form": form, "title": "Airing evidence"})
