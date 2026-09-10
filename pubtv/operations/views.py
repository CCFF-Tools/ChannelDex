from datetime import datetime, timedelta, date
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
from django.contrib import messages
from django.db import transaction
from django.db.models import Count, F, Max, Q
from django.http import HttpResponseBadRequest
from django.http import HttpResponse
from django.views.decorators.http import require_POST
import os
import signal
import threading
from django.core.exceptions import ValidationError
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from .forms import OccurrenceForm, StationForm, DeviceForm, ShowForm, ShowSlotDurationForm, EpisodeForm, ProducerForm, EpisodeMilestoneForm, AssetForm, DeliveryForm, SlotForm, AssignmentForm, PreparationForm, ProgrammingForm, UploadForm, AiringForm
from .models import Episode, EpisodeWorkflowMilestone, Occurrence, Show, Station, Device, Producer, MediaAsset, Delivery, RecurrenceSlot, WeeklyEpisodeAssignment, Preparation, OccurrenceProgramming, UploadedOccurrenceCoverage, UploadedScheduleRevision, AiringEvidence, AuditEvent
from .services import schedule_alerts, audit, materialize_assignment, next_premiere_date, suggested_pending_episode, confirm_preparation_fact, create_upload_snapshot, revise_airing, sign_upload_preview, load_upload_preview, commit_upload_preview, preparation_readiness, calendar_capacity


@require_POST
def quit_app(request):
    if os.environ.get("PUBTV_ENABLE_QUIT") != "1":
        return HttpResponse("Quit is unavailable.", status=404)
    try:
        master_pid = int(os.environ.get("PUBTV_MASTER_PID", ""))
    except ValueError:
        master_pid = 0
    if master_pid <= 1 or master_pid == os.getpid():
        return HttpResponse("Quit is unavailable.", status=404)
    response = HttpResponse("PUB-TV is shutting down.")
    # Delay until the response has been handed to the browser.
    def terminate_master():
        os.kill(master_pid, signal.SIGTERM)
    threading.Timer(0.1, terminate_master).start()
    return response

def _alerts(occurrences):
    alerts = []
    for kind, first, second in schedule_alerts(occurrences):
        if kind == "overlap": alerts.append(f"Overlap: {first.label} / {second.label}")
        elif kind == "short_gap": alerts.append(f"Short-gap advisory (coverage unknown unless explicitly scheduled) before {second.label}")
        else: alerts.append(f"Over slot: {first.label} runs beyond its fixed slot")
    return alerts

def _human_duration(total_seconds):
    hours, remainder = divmod(max(int(total_seconds), 0), 3600)
    minutes, seconds = divmod(remainder, 60)
    parts = []
    if hours: parts.append(f"{hours} hour{'s' if hours != 1 else ''}")
    if minutes: parts.append(f"{minutes} minute{'s' if minutes != 1 else ''}")
    if seconds: parts.append(f"{seconds} second{'s' if seconds != 1 else ''}")
    return ", ".join(parts) or "0 minutes"

def dashboard(request):
    station = Station.objects.first()
    occurrences = list(Occurrence.objects.filter(station=station).select_related("episode", "show", "asset", "weekly_assignment", "recurrence_slot")) if station else []
    if station:
        try:
            station_tz = ZoneInfo(station.timezone)
        except ZoneInfoNotFoundError:
            station_tz = ZoneInfo("America/Detroit")
        occurrences.sort(key=lambda item: (item.starts_at.astimezone(station_tz), item.pk))
        programmed = [item for item in occurrences if item.status == "planned"]
        for index, item in enumerate(programmed):
            if index == len(programmed) - 1:
                item.gap_to_next_label = "Last item"
                continue
            gap_seconds = int((programmed[index + 1].starts_at - item.ends_at).total_seconds())
            if gap_seconds > 0:
                item.gap_to_next_label = f"{_human_duration(gap_seconds)} gap"
            elif gap_seconds == 0:
                item.gap_to_next_label = "Continuous"
            else:
                item.gap_to_next_label = f"Overlap by {_human_duration(-gap_seconds)}"
        for item in occurrences:
            if item.status != "planned":
                item.gap_to_next_label = "Not applicable"
    return render(request, "dashboard.html", {"station": station, "occurrences": occurrences, "alerts": _alerts(occurrences)})

def scheduling_today(request):
    station = Station.objects.first()
    try:
        selected = date.fromisoformat(request.GET.get("date", "")) if request.GET.get("date") else timezone.localdate()
    except ValueError:
        return HttpResponseBadRequest("Invalid date")
    tz = ZoneInfo(station.timezone) if station else ZoneInfo("America/Detroit")
    start = timezone.make_aware(datetime.combine(selected, datetime.min.time()), tz)
    end = timezone.make_aware(datetime.combine(selected + timedelta(days=1), datetime.min.time()), tz)
    occurrences = list(Occurrence.objects.filter(
        station=station, status="planned", starts_at__gte=start, starts_at__lt=end,
    ).select_related("show", "episode", "weekly_assignment", "recurrence_slot", "preparation").order_by("starts_at")) if station else []
    covered_ids = set(UploadedOccurrenceCoverage.objects.filter(
        upload__state="active", occurrence__in=occurrences,
        occurrence_revision=F("occurrence__revision"),
    ).values_list("occurrence_id", flat=True))
    remaining = [item for item in occurrences if item.pk not in covered_ids]
    for item in remaining:
        item.readiness = preparation_readiness(item)
    return render(request, "scheduling_today.html", {
        "selected_date": selected,
        "previous_date": selected - timedelta(days=1),
        "next_date": selected + timedelta(days=1),
        "remaining": remaining,
        "completed_count": len(covered_ids),
        "total_count": len(occurrences),
    })

def reserved_schedule(request):
    """Show upcoming recurring show reservations at their full slot length."""
    station = Station.objects.first()
    try:
        selected = date.fromisoformat(request.GET.get("date", "")) if request.GET.get("date") else timezone.localdate()
    except ValueError:
        return HttpResponseBadRequest("Invalid date")
    try:
        days = int(request.GET.get("days", "7"))
    except (TypeError, ValueError):
        return HttpResponseBadRequest("Days must be a whole number from 1 to 31.")
    if not 1 <= days <= 31:
        return HttpResponseBadRequest("Days must be a whole number from 1 to 31.")
    items = []
    if station:
        try:
            station_tz = ZoneInfo(station.timezone)
        except ZoneInfoNotFoundError:
            station_tz = ZoneInfo("America/Detroit")
        slots = RecurrenceSlot.objects.filter(
            station=station, show__isnull=False,
        ).select_related("show").order_by("weekday", "start_time", "pk")
        for offset in range(days):
            item_date = selected + timedelta(days=offset)
            for slot in slots:
                if slot.weekday != item_date.weekday():
                    continue
                if slot.active_from and item_date < slot.active_from:
                    continue
                if slot.active_until and item_date > slot.active_until:
                    continue
                starts_at = timezone.make_aware(datetime.combine(item_date, slot.start_time), station_tz)
                duration_seconds = slot.show.slot_duration_seconds or slot.duration_seconds
                items.append({
                    "date": item_date,
                    "starts_at": starts_at,
                    "ends_at": starts_at + timedelta(seconds=duration_seconds),
                    "show": slot.show,
                    "duration_seconds": duration_seconds,
                })
        items.sort(key=lambda item: (item["starts_at"], item["show"].title))
    return render(request, "reserved_schedule.html", {
        "selected_date": selected,
        "end_date": selected + timedelta(days=days - 1),
        "days": days,
        "items": items,
    })

def show_list(request):
    station = Station.objects.first()
    show_type = request.GET.get("show_type", "")
    shows = station.shows.annotate(episode_count=Count("episodes")) if station else Show.objects.none()
    if show_type in dict(Show.SHOW_TYPES):
        shows = shows.filter(show_type=show_type)
    return render(request, "show_list.html", {"shows": shows, "show_types": Show.SHOW_TYPES, "show_type_filter": show_type})

def show_detail(request, show_id):
    show = get_object_or_404(
        Show.objects.prefetch_related(
            "episodes__workflow_milestones", "occurrences__episode", "slots"
        ),
        pk=show_id,
    )
    pending = show.episodes.filter(status="pending").order_by(F("intended_air_order").asc(nulls_last=True), F("received_at").asc(nulls_last=True), "pk")
    return render(request, "show_detail.html", {"show": show, "pending": pending, "episodes": show.episodes.order_by(F("intended_air_order").asc(nulls_last=True), "pk")})

def episode_queue_reorder(request, show_id):
    station = Station.objects.first()
    show = get_object_or_404(Show, pk=show_id, station=station)
    if request.method != "POST":
        return HttpResponseBadRequest("Queue changes must be submitted with POST.")
    try:
        ordered_ids = [int(value) for value in request.POST.get("episode_ids", "").split(",") if value.strip()]
    except ValueError:
        return HttpResponseBadRequest("Queue order contains an invalid episode.")
    if len(ordered_ids) != len(set(ordered_ids)):
        return HttpResponseBadRequest("Submit every upcoming episode exactly once.")
    with transaction.atomic():
        episodes = list(Episode.objects.select_for_update().filter(show=show, status="pending"))
        if not episodes and not ordered_ids:
            messages.success(request, "Upcoming premiere queue is already empty.")
            return redirect("show-detail", show_id=show.pk)
        if not ordered_ids:
            return HttpResponseBadRequest("Submit every upcoming episode exactly once.")
        if {episode.pk for episode in episodes} != set(ordered_ids) or len(episodes) != len(ordered_ids):
            return HttpResponseBadRequest("Queue order must include every upcoming episode exactly once.")
        for position, episode_id in enumerate(ordered_ids, start=1):
            Episode.objects.filter(pk=episode_id).update(intended_air_order=position)
        audit("reorder", "EpisodeQueue", show, f"Upcoming premiere queue reordered: {', '.join(str(i) for i in ordered_ids)}")
    messages.success(request, "Upcoming premiere queue updated.")
    return redirect("show-detail", show_id=show.pk)

def episode_detail(request, pk):
    episode = get_object_or_404(Episode.objects.select_related("show", "producer").prefetch_related("assets", "deliveries", "occurrences", "workflow_milestones"), pk=pk)
    return render(request, "episode_detail.html", {"episode": episode})

def episode_list(request):
    station = Station.objects.first()
    episodes = Episode.objects.filter(show__station=station).select_related("show").prefetch_related("workflow_milestones").order_by("show__title", F("intended_air_order").asc(nulls_last=True), "pk") if station else Episode.objects.none()
    return render(request, "episode_list.html", {"episodes": episodes})

def occurrence_create(request):
    station = Station.objects.first()
    if not station: return HttpResponseBadRequest("Create a station first")
    initial = {key: value for key in ("show", "episode") if request.GET.get(key) and (Show.objects.filter(pk=request.GET[key], station=station).exists() if key == "show" else Episode.objects.filter(pk=request.GET[key], show__station=station).exists())}
    form = OccurrenceForm(request.POST or None, station=station, initial=initial)
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

def _crud(request, model, form_class, title, pk=None, initial=None):
    obj = get_object_or_404(model, pk=pk) if pk else None
    scoped = {ShowForm, AssetForm, DeliveryForm}
    kwargs = {"instance": obj}
    if form_class in scoped:
        kwargs["station"] = Station.objects.first()
    form = form_class(request.POST or None, initial=initial, **kwargs)
    if request.method == "POST" and form.is_valid():
        obj = form.save(); audit("update" if pk else "create", model.__name__, obj, title)
        messages.success(request, f"{title} saved.")
        if isinstance(obj, Show): return redirect("show-detail", show_id=obj.pk)
        if isinstance(obj, MediaAsset) and obj.episode_id: return redirect("episode-detail", pk=obj.episode_id)
        if isinstance(obj, Delivery) and obj.episodes.count() == 1: return redirect("episode-detail", pk=obj.episodes.first().pk)
        return redirect("dashboard")
    intros = {
        "Create show": "Add the show once, then manage its episodes and weekly air times from the show page.",
        "Edit show": "Delivery method is a preference only; each actual delivery keeps its own provenance.",
        "Record media asset": "Record a source or encoded rendition as private metadata. PUB-TV does not move or validate the file.",
        "Record delivery": "Record how a submission was made available and link one or more episodes. PUB-TV does not fetch it.",
    }
    return render(request, "simple_form.html", {"form": form, "title": title, "intro": intros.get(title)})
def setup_station(request):
    existing = Station.objects.first()
    return _crud(request, Station, StationForm, "Station setup", pk=existing.pk if existing else None)
def setup_device(request): return _crud(request, Device, DeviceForm, "Device setup")
def show_create(request): return _crud(request, Show, ShowForm, "Create show")
def show_edit(request, show_id):
    station = Station.objects.first()
    show = get_object_or_404(Show, pk=show_id, station=station)
    return _crud(request, Show, ShowForm, "Edit show", pk=show.pk)

def producer_create(request):
    station = Station.objects.first()
    if not station:
        return HttpResponseBadRequest("Create a station first")
    show = Show.objects.filter(pk=request.GET.get("show"), station=station).first()
    episode = Episode.objects.filter(pk=request.GET.get("episode"), show__station=station).first()
    form = ProducerForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        producer = form.save(commit=False)
        producer.station = station
        producer.full_clean()
        producer.save()
        audit("create", "Producer", producer, "Producer added")
        messages.success(request, f"Producer {producer} added and selected.")
        if episode:
            return redirect(f"/episodes/{episode.pk}/edit/?producer={producer.pk}")
        suffix = f"?producer={producer.pk}"
        if show:
            suffix += f"&show={show.pk}"
        return redirect(f"/episodes/new/{suffix}")
    return render(request, "simple_form.html", {
        "form": form,
        "title": "Add producer",
        "intro": "Add the producer by name. Contact and membership details remain private local metadata.",
    })

def episode_create(request):
    station = Station.objects.first()
    show = get_object_or_404(Show, pk=request.GET.get("show"), station=station) if request.GET.get("show") and station else None
    producer = Producer.objects.filter(pk=request.GET.get("producer"), station=station).first() if station else None
    initial = {}
    if show: initial["show"] = show.pk
    if producer: initial["producer"] = producer.pk
    if show:
        current_max = show.episodes.aggregate(value=Max("intended_air_order"))["value"] or 0
        initial["intended_air_order"] = current_max + 1
    form = EpisodeForm(request.POST or None, station=station, initial=initial)
    if request.method == "POST" and form.is_valid():
        episode = form.save()
        audit("create", "Episode", episode, "Episode created")
        messages.success(request, "Episode saved. Record its current workflow stage when ready.")
        return redirect("episode-detail", pk=episode.pk)
    producer_url = f"/producers/new/?show={show.pk}" if show else "/producers/new/"
    return render(request, "simple_form.html", {
        "form": form, "title": "Add episode", "producer_url": producer_url,
        "queue_preview": show.episodes.filter(status="pending").order_by(F("intended_air_order").asc(nulls_last=True), F("received_at").asc(nulls_last=True), "pk") if show else None,
        "queue_show": show,
        "intro": "Create the episode record first; delivery, media, workflow, and scheduling actions remain separate and auditable.",
    })

def episode_edit(request, pk):
    station = Station.objects.first()
    episode = get_object_or_404(Episode, pk=pk, show__station=station)
    selected_producer = Producer.objects.filter(pk=request.GET.get("producer"), station=station).first()
    form = EpisodeForm(
        request.POST or None,
        instance=episode,
        station=station,
        initial={"producer": selected_producer.pk} if selected_producer else None,
    )
    if request.method == "POST" and form.is_valid():
        episode = form.save()
        audit("update", "Episode", episode, "Episode updated")
        messages.success(request, "Episode updated.")
        return redirect("episode-detail", pk=episode.pk)
    return render(request, "simple_form.html", {
        "form": form, "title": "Edit episode",
        "producer_url": f"/producers/new/?episode={episode.pk}",
        "intro": "Queue classification and airing evidence are managed separately from the episode's workflow milestones.",
    })

def episode_milestone_create(request, pk):
    station = Station.objects.first()
    episode = get_object_or_404(Episode, pk=pk, show__station=station)
    form = EpisodeMilestoneForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        milestone = form.save(commit=False)
        milestone.episode = episode
        try:
            milestone.full_clean()
        except ValidationError as error:
            form.add_error(None, error)
        else:
            milestone.save()
            audit("create", "EpisodeWorkflowMilestone", milestone, f"{milestone.get_stage_display()} recorded", milestone.actor)
            messages.success(request, f"{milestone.get_stage_display()} milestone recorded.")
            return redirect("episode-detail", pk=episode.pk)
    return render(request, "simple_form.html", {
        "form": form, "title": "Record episode workflow milestone", "episode": episode,
        "intro": "Milestones are progressive manual facts. They do not change queue classification and do not prove airing.",
        "stage_help": EpisodeMilestoneForm.STAGE_HELP,
    })
def asset_create(request):
    station = Station.objects.first()
    episode = get_object_or_404(Episode, pk=request.GET.get("episode"), show__station=station) if request.GET.get("episode") and station else None
    return _crud(request, MediaAsset, AssetForm, "Record media asset", initial={"episode": episode.pk} if episode else None)
def delivery_create(request):
    station = Station.objects.first()
    episode = get_object_or_404(Episode, pk=request.GET.get("episode"), show__station=station) if request.GET.get("episode") and station else None
    return _crud(request, Delivery, DeliveryForm, "Record delivery", initial={"episode": episode.pk} if episode else None)
def slot_create(request):
    station = Station.objects.first()
    if not station:
        return HttpResponseBadRequest("Create a station first")
    show = Show.objects.filter(pk=request.GET.get("show"), station=station).first()
    initial = {"show": show.pk} if show else None
    form = SlotForm(request.POST or None, station=station, show=show, initial=initial)
    if request.method == "POST" and form.is_valid():
        obj = form.save(commit=False); obj.station = station
        if obj.show.slot_duration_seconds:
            obj.duration_seconds = obj.show.slot_duration_seconds
        else:
            obj.show.slot_duration_seconds = obj.duration_seconds
            obj.show.save(update_fields=["slot_duration_seconds"])
        obj.full_clean(); obj.save()
        audit("create", "RecurrenceSlot", obj, "Create recurrence slot")
        messages.success(request, "Weekly time slot added.")
        return redirect("show-slot-manager", show_id=obj.show_id)
    return render(request, "simple_form.html", {"form": form, "title": "Add weekly time slot", "advanced_fields": True, "hide_duration": bool(show and show.slot_duration_seconds), "intro": "Add one weekly air time. Mark exactly one active time as the new-episode premiere slot; the other times are replays."})

def slot_edit(request, pk):
    station = Station.objects.first()
    slot = get_object_or_404(RecurrenceSlot, pk=pk, station=station)
    form = SlotForm(request.POST or None, instance=slot, station=station, show=slot.show)
    if request.method == "POST" and form.is_valid():
        if slot.occurrences.exists() and form.changed_data:
            boundary = form.cleaned_data.get("active_from") or timezone.localdate()
            if slot.active_from and boundary <= slot.active_from:
                form.add_error("active_from", "The replacement must start after the preserved slot version begins.")
                return render(request, "simple_form.html", {"form": form, "title": "Edit weekly time slot", "advanced_fields": True, "hide_duration": bool(slot.show.slot_duration_seconds), "intro": "Effective dates preserve historical definitions. Leave them blank for an ongoing time slot."})
            if form.cleaned_data.get("active_until") and form.cleaned_data["active_until"] < boundary:
                form.add_error("active_until", "The replacement end date cannot be before its effective start.")
                return render(request, "simple_form.html", {"form": form, "title": "Edit weekly time slot", "advanced_fields": True, "hide_duration": bool(slot.show.slot_duration_seconds), "intro": "Effective dates preserve historical definitions. Leave them blank for an ongoing time slot."})
            desired = {
                "station": slot.station, "show": slot.show,
                "weekday": form.cleaned_data["weekday"],
                "start_time": form.cleaned_data["start_time"],
                "duration_seconds": form.cleaned_data["duration_seconds"],
                "is_premiere": form.cleaned_data["is_premiere"],
                "active_from": boundary,
                "active_until": form.cleaned_data.get("active_until"),
            }
            with transaction.atomic():
                RecurrenceSlot.objects.filter(pk=slot.pk).update(active_until=boundary - timedelta(days=1))
                replacement = RecurrenceSlot(**desired)
                replacement.full_clean()
                replacement.save()
                audit("supersede", "RecurrenceSlot", slot, f"Weekly slot retained through {boundary - timedelta(days=1)}")
                audit("create", "RecurrenceSlot", replacement, f"Replacement weekly slot effective {boundary}")
            messages.success(request, "A new effective-dated slot version was saved; the prior definition and occurrences were preserved.")
            return redirect("show-slot-manager", show_id=replacement.show_id)
        slot = form.save()
        audit("update", "RecurrenceSlot", slot, "Unused weekly time slot updated")
        messages.success(request, "Weekly time slot updated.")
        return redirect("show-slot-manager", show_id=slot.show_id)
    return render(request, "simple_form.html", {"form": form, "title": "Edit weekly time slot", "advanced_fields": True, "hide_duration": bool(slot.show.slot_duration_seconds), "intro": "Effective dates preserve historical definitions. Leave them blank for an ongoing time slot."})

def show_slot_duration_edit(request, show_id):
    station = Station.objects.first()
    show = get_object_or_404(Show, pk=show_id, station=station)
    form = ShowSlotDurationForm(request.POST or None, instance=show)
    if request.method == "POST" and form.is_valid():
        show = form.save()
        audit("update", "Show", show, f"Show time slot length changed to {show.slot_duration_label}")
        messages.success(request, f"All future {show.title} premiere and replay occurrences will use {show.slot_duration_label}.")
        return redirect("show-slot-manager", show_id=show.pk)
    return render(request, "simple_form.html", {
        "form": form, "title": "Set show time slot length",
        "intro": "Set this once for the show. It applies to every future premiere and replay; existing planned occurrences keep their recorded length.",
    })

def show_slot_manager(request, show_id):
    station = Station.objects.first()
    show = get_object_or_404(Show, pk=show_id, station=station)
    today = timezone.localdate()
    slots = list(show.slots.order_by("weekday", "start_time", "pk"))
    active = [slot for slot in slots if (not slot.active_from or slot.active_from <= today) and (not slot.active_until or slot.active_until >= today)]
    premiere_count = sum(slot.is_premiere for slot in active)
    warning = None
    if active and premiere_count != 1:
        warning = f"This show currently has {premiere_count} active premiere slots; it needs exactly one."
    return render(request, "slot_manager.html", {"show": show, "slots": slots, "today": today, "warning": warning})
def assignment_create(request):
    station = Station.objects.first()
    initial = {}
    selected_show = None
    form_data = request.POST or None
    if request.method == "POST" and request.POST.get("week_start") and not request.POST.get("premiere_date"):
        form_data = request.POST.copy()
        try:
            legacy_week = date.fromisoformat(request.POST["week_start"])
            legacy_show = Show.objects.get(pk=request.POST.get("show"), station=station)
            premiere_slot = legacy_show.slots.filter(is_premiere=True).order_by("pk").first()
            form_data["premiere_date"] = (legacy_week + timedelta(days=premiere_slot.weekday if premiere_slot else 0)).isoformat()
        except (ValueError, Show.DoesNotExist):
            pass
    if station and request.GET.get("show"):
        selected_show = Show.objects.filter(pk=request.GET["show"], station=station).first()
        if selected_show:
            initial["show"] = selected_show.pk
            initial["premiere_date"] = next_premiere_date(selected_show)
            suggestion = suggested_pending_episode(selected_show)
            if suggestion:
                initial["episode"] = suggestion.pk
    if station and request.GET.get("episode"):
        episode = Episode.objects.filter(pk=request.GET["episode"], show__station=station).first()
        if episode:
            selected_show = episode.show
            initial.update({
                "episode": episode.pk,
                "show": episode.show_id,
                "premiere_date": next_premiere_date(episode.show),
            })
    existing = None
    if request.method == "POST" and station:
        try:
            posted_show_id = int(request.POST.get("show", ""))
            posted_premiere = date.fromisoformat(form_data.get("premiere_date", ""))
        except (TypeError, ValueError):
            pass
        else:
            selected_show = Show.objects.filter(pk=posted_show_id, station=station).first()
            existing = WeeklyEpisodeAssignment.objects.filter(
                show_id=posted_show_id,
                show__station=station,
                premiere_date=posted_premiere,
            ).first()
            if not existing:
                posted_week = posted_premiere - timedelta(days=posted_premiere.weekday())
                existing = WeeklyEpisodeAssignment.objects.filter(
                    show_id=posted_show_id, show__station=station,
                    week_start=posted_week, premiere_date=None,
                ).first()
    existing_episode_id = existing.episode_id if existing else None
    form = AssignmentForm(
        form_data,
        instance=existing,
        station=station,
        initial=initial,
    )
    if request.method == "POST" and form.is_valid():
        if existing:
            blocked = Occurrence.objects.filter(weekly_assignment=existing).filter(Q(uploaded_revisions__isnull=False) | Q(airing_evidence__isnull=False)).exists()
            if blocked:
                form.add_error(None, "This premiere cycle cannot be changed because an upload or airing record already relies on it.")
                return render(request, "assignment_form.html", {"form": form, "title": "Plan the next premiere cycle", "selected_show": selected_show, "queue": selected_show.episodes.filter(status="pending").order_by(F("intended_air_order").asc(nulls_last=True), F("received_at").asc(nulls_last=True), "pk"), "prior_cycle": existing}, status=400)
            episode_changed = existing_episode_id != getattr(form.cleaned_data["episode"], "pk", None)
            programming_recorded = OccurrenceProgramming.objects.filter(
                occurrence__weekly_assignment=existing
            ).exists()
            preparation_recorded = Preparation.objects.filter(
                occurrence__weekly_assignment=existing
            ).filter(
                Q(source_available__in=("yes", "na"))
                | Q(source_available_at__isnull=False)
                | ~Q(ame_preset="")
                | ~Q(ftp_output_device="")
                | ~Q(library_registration="")
                | ~Q(slot_assignment="")
                | ~Q(uploaded_schedule_revision="")
            ).exists()
            workflow_recorded = programming_recorded or preparation_recorded
            if episode_changed and workflow_recorded:
                form.add_error(
                    None,
                    "The episode cannot be changed after preparation or programming facts are recorded.",
                )
                return render(
                    request,
                    "assignment_form.html",
                    {"form": form, "title": "Plan the next premiere cycle", "selected_show": selected_show, "queue": selected_show.episodes.filter(status="pending").order_by(F("intended_air_order").asc(nulls_last=True), F("received_at").asc(nulls_last=True), "pk"), "prior_cycle": existing},
                    status=400,
                )
            with transaction.atomic():
                assignment = form.save()
                planned = list(
                    Occurrence.objects.select_for_update().filter(
                        weekly_assignment=assignment,
                        status="planned",
                    )
                )
                for occurrence in planned:
                    if assignment.selection_type == "none":
                        occurrence.status = "cancelled"
                        occurrence.reason = "Weekly selection changed to no program."
                    else:
                        occurrence.show = assignment.show
                        occurrence.episode = assignment.episode
                        occurrence.item_type = "episode"
                        occurrence.label = assignment.show.title
                        occurrence.schedule_role = (
                            "rerun" if assignment.selection_type == "rerun"
                            else ("premiere" if occurrence.recurrence_slot and occurrence.recurrence_slot.is_premiere else "replay")
                        )
                    occurrence.full_clean()
                    occurrence.save()
                materialize_assignment(assignment)
                audit("update", "WeeklyEpisodeAssignment", assignment, "Premiere cycle corrected")
        else:
            assignment = form.save()
            materialize_assignment(assignment)
            audit("create", "WeeklyEpisodeAssignment", assignment, "Premiere cycle planned")
        messages.success(request, "Premiere cycle planned. The premiere and following replays now use the selected episode.")
        return redirect(f"/week/?date={assignment.premiere_date.isoformat()}")
    queue = selected_show.episodes.filter(status="pending").order_by(
        F("intended_air_order").asc(nulls_last=True),
        F("intended_premiere_date").asc(nulls_last=True),
        F("received_at").asc(nulls_last=True), "pk"
    ) if selected_show else Episode.objects.none()
    prior_cycle = selected_show.weekly_assignments.exclude(premiere_date=None).order_by("-premiere_date").first() if selected_show else None
    return render(request, "assignment_form.html", {
        "form": form,
        "title": "Plan the next premiere cycle",
        "selected_show": selected_show,
        "queue": queue,
        "prior_cycle": prior_cycle,
    })
def day_view(request):
    station = Station.objects.first()
    try:
        selected = date.fromisoformat(request.GET.get("date", "")) if request.GET.get("date") else timezone.localdate()
    except ValueError:
        return HttpResponseBadRequest("Invalid date")
    tz = ZoneInfo(station.timezone) if station else ZoneInfo("America/Detroit")
    start = timezone.make_aware(datetime.combine(selected, datetime.min.time()), tz)
    end = timezone.make_aware(datetime.combine(selected + timedelta(days=1), datetime.min.time()), tz)
    qs = Occurrence.objects.filter(
        station=station, status="planned",
        starts_at__lt=end,
    ).select_related("show", "episode", "weekly_assignment", "recurrence_slot", "preparation") if station else Occurrence.objects.none()
    qs = [item for item in qs if item.ends_at > start]
    # Do elapsed-time arithmetic in UTC: subtracting two zone-aware values
    # with the same tzinfo otherwise measures wall-clock time across DST.
    utc = ZoneInfo("UTC")
    local_intervals = sorted((max(item.starts_at, start).astimezone(utc), min(item.ends_at, end).astimezone(utc)) for item in qs)
    covered = 0
    cursor = start.astimezone(utc)
    for interval_start, interval_end in local_intervals:
        if interval_end <= cursor:
            continue
        covered += (interval_end - max(interval_start, cursor)).total_seconds()
        cursor = max(cursor, interval_end)
    show_type = request.GET.get("show_type", "")
    if show_type:
        qs = [item for item in qs if item.show and item.show.show_type == show_type]
    occurrences = list(qs)
    for item in occurrences: item.readiness = preparation_readiness(item)
    total_seconds = int((end.astimezone(utc) - start.astimezone(utc)).total_seconds())
    return render(request, "calendar_day.html", {"title": "Day plan", "occurrences": occurrences, "selected_date": selected, "previous_date": selected - timedelta(days=1), "next_date": selected + timedelta(days=1), "capacity": calendar_capacity(station, selected)[0] if station else {"intervals": []}, "capacity_filter": request.GET.get("capacity", "all"), "show_type_filter": show_type, "show_types": Show.SHOW_TYPES, "coverage_label": _human_duration(covered), "alerts": _alerts(occurrences)})

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
    occurrences = Occurrence.objects.filter(
        station=station, status="planned",
        starts_at__lt=end,
    ).select_related("show", "episode", "weekly_assignment", "recurrence_slot", "preparation").order_by("starts_at") if station else []
    show_type = request.GET.get("show_type", "")
    if show_type:
        occurrences = [item for item in occurrences if item.show and item.show.show_type == show_type]
    occurrences = [item for item in occurrences if item.ends_at > start]
    for item in occurrences: item.readiness = preparation_readiness(item)
    day_segments = []
    for day in days:
        day_start = timezone.make_aware(datetime.combine(day, datetime.min.time()), tz)
        day_end = timezone.make_aware(datetime.combine(day + timedelta(days=1), datetime.min.time()), tz)
        for item in occurrences:
            if item.starts_at < day_end and item.ends_at > day_start:
                day_segments.append({"date": day, "occurrence": item,
                                     "start": max(item.starts_at, day_start).astimezone(tz),
                                     "end": min(item.ends_at, day_end).astimezone(tz)})
    capacity = calendar_capacity(station, selected, 7) if station else []
    return render(request, "calendar_week.html", {"title": "Week plan", "occurrences": occurrences, "day_segments": day_segments, "week_days": days, "selected_date": selected, "previous_week": selected - timedelta(days=7), "next_week": selected + timedelta(days=7), "capacity": capacity, "capacity_filter": request.GET.get("capacity", "all"), "show_type_filter": show_type, "show_types": Show.SHOW_TYPES, "alerts": _alerts(occurrences)})

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
    occurrences = Occurrence.objects.filter(
        station=station,
        starts_at__gte=start_at,
        starts_at__lt=end_at,
    ).select_related("show", "episode", "weekly_assignment", "recurrence_slot", "preparation").order_by("starts_at") if station else []
    occurrences = list(occurrences)
    for item in occurrences: item.readiness = preparation_readiness(item)
    return render(request, "calendar_agenda.html", {"title": "Upcoming agenda", "occurrences": occurrences, "selected_date": start, "alerts": _alerts(occurrences)})
def history_view(request):
    station = Station.objects.first(); occurrences = list(Occurrence.objects.filter(station=station).select_related("show", "episode", "weekly_assignment", "recurrence_slot", "preparation").order_by("starts_at")) if station else []
    for item in occurrences: item.readiness = preparation_readiness(item)
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
    initial = None
    if request.GET.get("occurrence") and station:
        selected_occurrence = Occurrence.objects.filter(
            pk=request.GET["occurrence"], station=station, status="planned"
        ).first()
        if selected_occurrence:
            initial = {"occurrences": [selected_occurrence.pk]}
    form = UploadForm(request.POST or None, station=station, initial=initial)
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
