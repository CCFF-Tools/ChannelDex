from datetime import datetime, timedelta, date
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
from django.contrib import messages
from django.db import IntegrityError, transaction
from django.db.models import Count, F, Max, Q, Prefetch
from django.http import HttpResponse, HttpResponseBadRequest, JsonResponse
from django.views.decorators.clickjacking import xframe_options_sameorigin
from django.views.decorators.http import require_POST
import os
import signal
import threading
import hashlib
import plistlib
from ftplib import FTP
from django.core.exceptions import ValidationError
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from .forms import OccurrenceForm, StationForm, CarryForwardSettingsForm, DeviceForm, ShowForm, ShowSlotDurationForm, EpisodeForm, ProducerForm, EpisodeMilestoneForm, AssetForm, DeliveryForm, SlotForm, AssignmentForm, AssetPreparationForm, AssetTargetTransferForm, ProgrammingForm, UploadForm, AiringForm, AutomationPreparationForm, AutomationPublicationForm, UltraNexusSettingsForm
from .models import Episode, EpisodeWorkflowMilestone, Occurrence, Show, Station, Device, Producer, MediaAsset, Delivery, RecurrenceSlot, WeeklyEpisodeAssignment, Preparation, AssetPreparation, AssetTargetTransfer, OccurrenceProgramming, UploadedOccurrenceCoverage, UploadedScheduleRevision, AiringEvidence, AuditEvent, UltraNexusTargetSettings, PreparationBatch, PreparationBatchItem, PreparationJob, SchedulePublicationBatch, ScheduleDeliveryOperation, OccurrenceRevisionSelection, ResearchGate, TransferAttempt, PublicationJob
from .services import schedule_alerts, audit, materialize_assignment, ensure_carry_forward_week, next_premiere_date, suggested_pending_episode, confirm_preparation_fact, create_upload_snapshot, revise_airing, sign_upload_preview, load_upload_preview, commit_upload_preview, preparation_readiness, calendar_capacity, pending_episode_reason
from .automation import approve_snapshot, canonical_hash, invalidate_snapshot, preparation_batch_snapshot, publication_snapshot, preview_schedule, approve_preparation_batch, schedule_ready_binding, generate_nmg_artifact, generate_bin_artifact, publication_artifact_blockers
from .publication_delivery import stage_publication, activate_publication, observe_activation, rollback_publication
from .publication_review import capture_controller_snapshot, publication_review, validate_review_token, cancel_publication
from .schedule_preparation import schedule_prepare
from pubtv.ultranexus.exceptions import UltraNexusError, CapabilityError
from pubtv.ultranexus.secrets import device_secret_reference, new_device_secret_reference, KeychainSecretStore
from pubtv.ultranexus.encoding import discover_executables
from pathlib import Path
import re
import subprocess
import uuid
from django.conf import settings as django_settings


def _file_evidence(path, approved_hash=""):
    """Return safe local evidence for a diagnostic; never approves it."""
    result = {"path": str(path or ""), "readable": False, "observed_hash": "", "approved_hash": approved_hash or "", "stale": False}
    if not path:
        return result
    try:
        digest = hashlib.sha256()
        with open(path, "rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(chunk)
        result["readable"] = True
        result["observed_hash"] = digest.hexdigest()
        result["stale"] = bool(approved_hash and result["observed_hash"].lower() != approved_hash.lower())
    except (OSError, ValueError):
        result["error"] = "File is unavailable or unreadable."
    return result


def _run_device_diagnostics(target, posted, current, only=None):
    """Probe only the displayed settings; no upload, encoding, or approval."""
    def value(name, fallback=""):
        return posted.get(name) if name in posted else getattr(current, name, fallback) if current else fallback

    config = dict(current.settings) if current else {}
    config.update({key: posted.get(key, config.get(key, "")) for key in ("ame_executable", "ame_preset", "ffmpeg_executable", "ffmpeg_qualification_manifest")})
    username = posted.get("username") or config.get("ftp_username") or (current.command_username if current else "")
    findings = []
    if only in (None, "ftp"):
        ftp = {"name": "FTP login and directory access", "status": "unknown"}
        password = posted.get("password", "")
        connection = None
        try:
            if not password and current and (current.secret_reference or current.command_secret_reference):
                reference = current.secret_reference or current.command_secret_reference
                service, account = reference.split(":", 1)
                password = KeychainSecretStore().get(type(device_secret_reference(target.pk))(service, account))
            if not value("host") or not username or not password:
                ftp.update(status="blocked", detail="Host, username, or Keychain password is unavailable.")
            else:
                connection = FTP()
                connection.connect(value("host"), int(value("port", 21) or 21), timeout=3)
                connection.login(username, password)
                connection.nlst(value("media_directory", ""))
                connection.quit()
                ftp.update(status="pass", detail="Login and directory listing succeeded; no files were changed.", details=["Connection opened with a 3-second timeout.", "Directory listing completed.", "No STOR, DELE, rename, or other write command was issued."])
        except Exception:
            try:
                connection.close()
            except Exception:
                pass
            ftp.update(status="fail", detail="Login or directory listing failed.", details=["The failure was redacted to avoid exposing credentials or server details."])
        findings.append(ftp)

    if only in (None, "ame"):
        ame_path = config.get("ame_executable")
        preset_path = config.get("ame_preset")
        ame = _file_evidence(ame_path)
        preset = _file_evidence(preset_path, current.ame_preset_sha256 if current else "")
        ame_ok = ame["readable"] and preset["readable"] and not preset["stale"]
        findings.append({"name": "Adobe Media Encoder executable and preset", "status": "observed" if ame_ok else "fail", "detail": "Executable and preset are readable; this does not qualify encoding.", "details": [f"Executable: {'found' if ame['readable'] else 'missing'}.", f"Preset: {'found' if preset['readable'] else 'missing'}.", f"Approved fingerprint: {'matches' if preset['readable'] and not preset['stale'] and preset['approved_hash'] else 'not confirmed'}.", "No encoding or qualification action was performed."]})

    if only in (None, "ffmpeg"):
        ffmpeg_path = config.get("ffmpeg_executable")
        ffprobe_path = str(Path(ffmpeg_path).with_name("ffprobe")) if ffmpeg_path else ""
        versions = {}
        for name, path in (("FFmpeg", ffmpeg_path), ("ffprobe", ffprobe_path)):
            try:
                completed = subprocess.run((path, "-version"), check=False, capture_output=True, text=True, timeout=3)
                versions[name] = (completed.stdout or completed.stderr).splitlines()[0][:160] if completed.returncode == 0 else "unavailable"
            except (OSError, subprocess.SubprocessError, ValueError):
                versions[name] = "unavailable"
        findings.append({"name": "FFmpeg and ffprobe", "status": "pass" if all(v != "unavailable" for v in versions.values()) else "fail", "detail": "Version probes completed without encoding.", "details": [f"{name}: {version}" for name, version in versions.items()]})

    if only in (None, "evidence"):
        evidence = []
        for label, path, approved in (("base NMG", value("base_nmg_path"), current.base_nmg_hash if current else ""), ("base BIN", value("base_bin_path"), current.base_bin_hash if current else ""), ("qualification manifest", config.get("ffmpeg_qualification_manifest"), config.get("ffmpeg_qualification_sha256", ""))):
            evidence.append({"name": label, **_file_evidence(path, approved)})
        evidence_ok = bool(evidence) and all(item["path"] and item["readable"] and item["approved_hash"] and not item["stale"] for item in evidence)
        unconfirmed = any(item["path"] and item["readable"] and not item["approved_hash"] for item in evidence)
        findings.append({"name": "Evidence-file integrity", "status": "pass" if evidence_ok else "blocked" if unconfirmed else "fail", "detail": "Observed files are compared with approved fingerprints; missing approvals and mismatches remain unconfirmed.", "details": [f"{item['name']}: {'missing' if not item['path'] else 'unreadable' if not item['readable'] else 'approved fingerprint missing' if not item['approved_hash'] else 'stale mismatch' if item['stale'] else 'readable and matching'}" for item in evidence]})
    return findings


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
    response = HttpResponse("ChannelDex is shutting down.")
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


def _coarse_capacity_blocks(day_capacity):
    """Collapse detailed capacity into consecutive Available/Reserved blocks."""
    blocks = []
    for interval in day_capacity.get("intervals", []):
        status = "available" if interval["status"] == "available" else "reserved"
        show_label = interval.get("show_label")
        same_reservation = not blocks or status == "available" or blocks[-1].get("show_label") == show_label
        if blocks and blocks[-1]["status"] == status and blocks[-1]["end"] == interval["start"] and same_reservation:
            blocks[-1]["end"] = interval["end"]
            continue
        blocks.append({
            "kind": "capacity",
            "start": interval["start"],
            "end": interval["end"],
            "status": status,
            "label": "Available" if status == "available" else "Reserved",
            "show_label": show_label,
        })
    return blocks


def _apply_carry_forward(request, station, week_start):
    created = ensure_carry_forward_week(station, week_start)
    if created:
        show_names = ", ".join(item.show.title for item in created)
        week_label = week_start.strftime("%b %d").replace(" 0", " ")
        messages.info(
            request,
            f"Carried the most recent episode into the week of {week_label}: {show_names}. "
            "Each item is labeled as an automatic carry-forward replay and can be replaced by an explicit plan.",
        )
    return created


def _pending_queue(show):
    """Return the premiere queue using only stored fields; receipt is derived."""
    episodes = list(show.episodes.filter(status="pending").prefetch_related("deliveries"))
    missing_receipt = datetime.max.replace(tzinfo=ZoneInfo("UTC"))
    return sorted(episodes, key=lambda item: (item.intended_air_order is None, item.intended_air_order or 0, item.received_at or missing_receipt, item.pk))

def dashboard(request):
    station = Station.objects.first()
    try:
        selected = date.fromisoformat(request.GET.get("date", "")) if request.GET.get("date") else timezone.localdate()
    except ValueError:
        return HttpResponseBadRequest("Invalid date")
    if station:
        _apply_carry_forward(request, station, selected - timedelta(days=selected.weekday()))
    occurrences = []
    if station:
        try:
            station_tz = ZoneInfo(station.timezone)
        except ZoneInfoNotFoundError:
            station_tz = ZoneInfo("America/Detroit")
        start = timezone.make_aware(datetime.combine(selected, datetime.min.time()), station_tz)
        end = timezone.make_aware(datetime.combine(selected + timedelta(days=1), datetime.min.time()), station_tz)
        occurrences = [item for item in Occurrence.objects.filter(station=station, starts_at__lt=end).select_related("episode", "show", "asset", "weekly_assignment", "recurrence_slot", "preparation").order_by("starts_at") if item.ends_at > start]
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
    covered_ids = set(UploadedOccurrenceCoverage.objects.filter(upload__state="active", occurrence__in=occurrences, occurrence_revision=F("occurrence__revision")).values_list("occurrence_id", flat=True)) if occurrences else set()
    for item in occurrences:
        item.readiness = preparation_readiness(item)
        item.covered = item.pk in covered_ids
    return render(request, "dashboard.html", {"station": station, "occurrences": occurrences, "selected_date": selected, "previous_date": selected - timedelta(days=1), "next_date": selected + timedelta(days=1), "covered_count": len(covered_ids), "remaining_count": len(occurrences) - len(covered_ids), "alerts": _alerts(occurrences)})


def occurrence_workbench(request, pk):
    occurrence = get_object_or_404(Occurrence.objects.select_related("station", "show", "episode", "asset", "weekly_assignment", "recurrence_slot", "preparation"), pk=pk)
    preparation = getattr(occurrence, "preparation", None)
    asset_preparation = getattr(occurrence.asset, "preparation_record", None) if occurrence.asset_id else None
    transfers = list(occurrence.asset.target_transfers.select_related("device").order_by("device__name", "pk")) if occurrence.asset_id else []
    legacy_preparation = preparation if preparation and not asset_preparation else None
    programming = OccurrenceProgramming.objects.filter(occurrence=occurrence).order_by("-pk")
    coverage = UploadedOccurrenceCoverage.objects.filter(occurrence=occurrence).select_related("upload").order_by("-upload__uploaded_at")
    uploads = [item for item in coverage if item.upload.state == "active" and item.occurrence_revision == occurrence.revision]
    stale_uploads = [item for item in coverage if item not in uploads]
    evidence = AiringEvidence.objects.filter(occurrence=occurrence).order_by("-aired_at", "-pk")
    activity = AuditEvent.objects.filter(entity="Occurrence", entity_id=occurrence.pk).order_by("-occurred_at")
    return render(request, "occurrence_workbench.html", {"occurrence": occurrence, "preparation": preparation, "asset_preparation": asset_preparation, "transfers": transfers, "legacy_preparation": legacy_preparation, "programming": programming, "uploads": uploads, "stale_uploads": stale_uploads, "evidence": evidence, "activity": activity, "readiness": preparation_readiness(occurrence), "selected_date": occurrence.starts_at.date()})


def settings_view(request):
    station = Station.objects.first()
    form_data = request.POST if request.method == "POST" else None
    carry_forward_form = CarryForwardSettingsForm(form_data, instance=station) if station else None
    if request.method == "POST" and carry_forward_form and carry_forward_form.is_valid():
        station = carry_forward_form.save()
        audit(
            "update", "Station", station,
            "Automatic episode carry-forward enabled" if station.carry_forward_unassigned_episodes else "Automatic episode carry-forward disabled",
        )
        audit(
            "update", "Station", station,
            "Automatic controller schedule pull enabled" if station.auto_pull_controller_schedule else "Automatic controller schedule pull disabled; manual controller pull required",
        )
        messages.success(request, "Schedule carry-forward setting updated.")
        return redirect("settings")
    return render(request, "settings.html", {"station": station, "devices": Device.objects.all().order_by("name"), "carry_forward_form": carry_forward_form})


def help_page(request):
    return render(request, "help.html")


def device_settings(request, pk):
    target = get_object_or_404(Device, pk=pk)
    current = UltraNexusTargetSettings.objects.filter(target=target, is_current=True).first()
    initial = {}
    if current:
        initial = {field: getattr(current, field, "") for field in (
            "host", "port", "media_directory", "schedule_path", "reconciliation_mode",
            "base_nmg_path", "base_nmg_hash", "base_bin_path", "base_bin_hash", "command_port",
            "command_username", "controller_family", "firmware_version", "output_number",
            "media_profile", "profile_identity_hash", "ame_preset_sha256", "ffmpeg_profile_sha256",
            "nmg_template_sha256", "bin_template_sha256", "qualification_status", "qualification_evidence_hash")}
        initial.update({key: current.settings.get(key, "") for key in (
            "ftp_username", "ame_executable", "ame_preset", "ffmpeg_executable", "ffmpeg_build_sha256",
            "ffmpeg_qualification_manifest", "ffmpeg_qualification_sha256", "nmg_resource_template_reference",
            "nmg_schedule_template_base", "bin_resource_template_reference", "bin_schedule_template_slot",
            "capability_flags")})
        initial["settings_version"] = current.version
        initial["capability_flags"] = ",".join(current.capability_flags if isinstance(current.capability_flags, list) else [])
        initial["username"] = current.settings.get("ftp_username") or current.command_username
    else:
        initial["settings_version"] = 0
    detected = discover_executables()
    if not initial.get("ame_executable"):
        initial["ame_executable"] = detected.get("ame") or ""
    if not initial.get("ffmpeg_executable"):
        initial["ffmpeg_executable"] = detected.get("ffmpeg") or ""
    diagnostic_actions = {
        "diagnostics": None, "test_ftp": "ftp", "test_ame": "ame",
        "test_ffmpeg": "ffmpeg", "test_evidence": "evidence",
    }
    if request.method == "POST" and request.POST.get("action") in diagnostic_actions:
        diagnostics = _run_device_diagnostics(target, request.POST, current, diagnostic_actions[request.POST.get("action")])
        form = UltraNexusSettingsForm(request.POST or initial)
        return render(request, "device_settings.html", {"target": target, "form": form, "current": current, "diagnostics": diagnostics})
    if request.method == "POST":
        form = UltraNexusSettingsForm(request.POST)
        if form.is_valid():
            values = form.cleaned_data
            expected_version = values.get("settings_version")
            displayed_version = current.version if current else 0
            if expected_version is None:
                form.add_error(None, "Reload these device settings before saving.")
                return render(request, "device_settings.html", {"target": target, "form": form, "current": current})
            if expected_version != displayed_version:
                form.add_error(None, "These device settings changed in another window. Reload and review them before saving.")
                return render(request, "device_settings.html", {"target": target, "form": form, "current": current})
            values["schedule_path"] = current.schedule_path if current and current.schedule_path else "/internal/schedule/schedule.bin"
            values["reconciliation_mode"] = current.reconciliation_mode if current else "preserve"
            values["command_port"] = values.get("command_port") or (
                current.command_port if current else 23
            )
            username = values.get("username", "")
            previous_reference = None
            if current:
                reference_text = current.secret_reference or current.command_secret_reference
                previous_reference = reference_text
                reference = device_secret_reference(target.pk)
                if reference_text and ":" in reference_text:
                    service, account = reference_text.split(":", 1)
                    reference = type(reference)(service, account)
            else:
                reference = device_secret_reference(target.pk)
            if not current and not values.get("password"):
                form.add_error("password", "Enter a password for the first device setup.")
                return render(request, "device_settings.html", {"target": target, "form": form, "current": current})
            if values.get("password"):
                reference = new_device_secret_reference(target.pk)
            settings_data = dict(current.settings) if current else {}
            for key in ("ftp_username", "ame_executable", "ame_preset", "ffmpeg_executable", "ffmpeg_qualification_manifest"):
                if key in request.POST:
                    settings_data[key] = values.get(key, "")
                elif not current:
                    settings_data.setdefault(key, "")
            settings_data["ftp_username"] = username or settings_data.get("ftp_username", "")
            settings_data["command_username"] = username or settings_data.get("command_username", "")
            model_values = {field.name: getattr(current, field.name, "") for field in UltraNexusTargetSettings._meta.fields if field.name not in {"id", "target", "version", "settings", "is_current", "created_at", "created_by", "settings_hash"}} if current else {}
            for name in model_values:
                if name in request.POST and name in values:
                    model_values[name] = values[name]
            model_values.update({"secret_reference": reference.service + ":" + reference.account, "command_secret_reference": reference.service + ":" + reference.account, "command_username": username or model_values.get("command_username", "")})
            capability_value = current.capability_flags if current else []
            model_values["capability_flags"] = list(capability_value or [])
            settings_data["capability_flags"] = model_values.get("capability_flags", [])
            hash_payload = {**settings_data, **{key: str(model_values.get(key, "")) for key in ("host", "port", "media_directory", "schedule_path", "secret_reference", "reconciliation_mode", "base_nmg_path", "base_nmg_hash", "base_bin_path", "base_bin_hash", "command_port", "command_username", "command_secret_reference", "controller_family", "firmware_version", "output_number", "media_profile", "profile_identity_hash", "ame_preset_sha256", "ffmpeg_profile_sha256", "nmg_template_sha256", "bin_template_sha256", "qualification_status", "qualification_evidence_hash")}, "capability_flags": model_values.get("capability_flags", [])}
            store = KeychainSecretStore()
            if values.get("password"):
                try:
                    store.set(reference, values["password"])
                except (CapabilityError, OSError, subprocess.CalledProcessError):
                    messages.error(request, "The Keychain password could not be saved.")
                    return render(request, "device_settings.html", {"target": target, "form": form, "current": current})
            try:
                with transaction.atomic():
                    locked = UltraNexusTargetSettings.objects.select_for_update().filter(target=target, is_current=True).first()
                    locked_version = locked.version if locked else 0
                    if locked_version != expected_version:
                        raise ValidationError("These device settings changed in another window. Reload and review them before saving.")
                    record = UltraNexusTargetSettings(
                    target=target, version=(locked.version + 1 if locked else 1), is_current=True,
                    host=values.get("host") or model_values.get("host", ""), port=values.get("port") or model_values.get("port"), media_directory=values.get("media_directory") or model_values.get("media_directory", ""),
                    schedule_path=values.get("schedule_path") or model_values.get("schedule_path", ""), secret_reference=reference.service + ":" + reference.account,
                    reconciliation_mode=values.get("reconciliation_mode") or model_values.get("reconciliation_mode", "preserve"), command_port=values.get("command_port") or model_values.get("command_port") or 23,
                    command_username=username or model_values.get("command_username", ""), command_secret_reference=reference.service + ":" + reference.account,
                    controller_family=values.get("controller_family") or model_values.get("controller_family", ""), firmware_version=values.get("firmware_version") or model_values.get("firmware_version", ""),
                    output_number=values.get("output_number") or model_values.get("output_number"), media_profile=values.get("media_profile") or model_values.get("media_profile", ""),
                    profile_identity_hash=values.get("profile_identity_hash") or model_values.get("profile_identity_hash", ""), ame_preset_sha256=values.get("ame_preset_sha256") or model_values.get("ame_preset_sha256", ""), ffmpeg_profile_sha256=values.get("ffmpeg_profile_sha256") or model_values.get("ffmpeg_profile_sha256", ""), nmg_template_sha256=values.get("nmg_template_sha256") or model_values.get("nmg_template_sha256", ""), bin_template_sha256=values.get("bin_template_sha256") or model_values.get("bin_template_sha256", ""),
                    base_nmg_path=values.get("base_nmg_path") or model_values.get("base_nmg_path", ""), base_nmg_hash=values.get("base_nmg_hash") or model_values.get("base_nmg_hash", ""), base_bin_path=values.get("base_bin_path") or model_values.get("base_bin_path", ""), base_bin_hash=values.get("base_bin_hash") or model_values.get("base_bin_hash", ""),
                    qualification_status=values.get("qualification_status") or model_values.get("qualification_status", "open"), qualification_evidence_hash=values.get("qualification_evidence_hash") or model_values.get("qualification_evidence_hash", ""),
                    settings=settings_data, settings_hash=canonical_hash(hash_payload),
                )
                    for field_name, field_value in model_values.items():
                        if field_name not in {"secret_reference", "command_secret_reference", "command_username"} and hasattr(record, field_name):
                            setattr(record, field_name, field_value)
                    record.secret_reference = reference.service + ":" + reference.account
                    record.command_secret_reference = record.secret_reference
                    record.command_username = username or model_values.get("command_username", "")
                    record.settings_hash = canonical_hash({**settings_data, **{field.name: str(getattr(record, field.name, "")) for field in UltraNexusTargetSettings._meta.fields if field.name in hash_payload}})
                    record.full_clean(validate_constraints=False)
                    UltraNexusTargetSettings.objects.filter(target=target, is_current=True).update(is_current=False)
                    record.save()
            except ValidationError as exc:
                if values.get("password"):
                    try:
                        store.delete(reference)
                    except (CapabilityError, OSError, subprocess.CalledProcessError):
                        pass
                form.add_error(None, "; ".join(exc.messages))
                return render(request, "device_settings.html", {"target": target, "form": form, "current": current})
            except Exception:
                if values.get("password"):
                    try:
                        store.delete(reference)
                    except (CapabilityError, OSError, subprocess.CalledProcessError):
                        pass
                raise
            if values.get("password") and previous_reference and previous_reference != record.secret_reference:
                try:
                    previous_service, previous_account = previous_reference.split(":", 1)
                    store.delete(type(reference)(previous_service, previous_account))
                except (ValueError, CapabilityError, OSError, subprocess.CalledProcessError):
                    pass
            messages.success(request, "Station device settings saved. Qualification remains explicit and versioned.")
            return redirect("device-settings", pk=target.pk)
    else:
        form = UltraNexusSettingsForm(initial=initial)
    return render(request, "device_settings.html", {"target": target, "form": form, "current": current})


def _resolve_application_executable(selected):
    """Resolve a macOS application bundle to its executable without guessing broadly."""
    selected_path = Path(selected.rstrip("/"))
    if selected_path.suffix.lower() != ".app":
        return str(selected_path) if selected_path.exists() else ""

    macos_dir = selected_path / "Contents" / "MacOS"
    candidates = []
    try:
        with (selected_path / "Contents" / "Info.plist").open("rb") as stream:
            bundle_executable = plistlib.load(stream).get("CFBundleExecutable")
        if isinstance(bundle_executable, str) and bundle_executable and Path(bundle_executable).name == bundle_executable:
            candidates.append(bundle_executable)
    except (OSError, ValueError, KeyError, plistlib.InvalidFileException, TypeError):
        pass
    # Older or malformed bundles commonly use the bundle stem. Keep this as a
    # narrow fallback and never search outside Contents/MacOS.
    candidates.append(selected_path.stem)
    for name in candidates:
        executable = macos_dir / name
        if executable.exists():
            return str(executable)
    return ""


def browse_path(request):
    """Open a local Finder chooser; safe JSON response and editable fallback."""
    if request.method != "POST":
        return JsonResponse({"error": "POST required"}, status=405)
    kind = request.POST.get("kind", "file")
    if kind not in {"file", "application"}:
        return JsonResponse({"error": "Unsupported chooser kind"}, status=400)
    script = ('POSIX path of (choose file with prompt "Choose an application" '
              'of type {"com.apple.application-bundle"})' if kind == "application"
              else 'POSIX path of (choose file)')
    try:
        result = subprocess.run(("osascript", "-e", script), check=False, capture_output=True, text=True, timeout=120)
    except (OSError, subprocess.TimeoutExpired):
        return JsonResponse({"path": "", "available": False, "cancelled": False, "resolved": False, "error": "The local picker is unavailable."})
    if result.returncode != 0:
        cancelled = re.search(r"(?<!\d)-128(?!\d)", result.stderr or "") is not None
        return JsonResponse({"path": "", "available": True, "cancelled": cancelled, "resolved": False,
                             "error": "" if cancelled else "The local picker failed."})
    selected = result.stdout.strip()
    resolved = _resolve_application_executable(selected) if kind == "application" and selected else (selected if selected and Path(selected).exists() else "")
    if resolved:
        return JsonResponse({"path": resolved, "available": True, "cancelled": False, "resolved": True, "error": ""})
    return JsonResponse({"path": "", "available": True, "cancelled": False, "resolved": False, "error": "The selected application executable could not be resolved."})


def _automation_context(request, *, form_p=None, form_s=None, message=""):
    target_id = request.GET.get("target") or request.POST.get("target")
    target = Device.objects.filter(pk=target_id).first() if target_id else Device.objects.order_by("name").first()
    gates = list(ResearchGate.objects.filter(target=target).order_by("key")) if target else []
    preview = None
    if target:
        selected = list(Occurrence.objects.filter(status="planned").select_related("show", "episode", "asset").order_by("starts_at"))
        preview = preview_schedule(selected, target=target)
    current_settings = UltraNexusTargetSettings.objects.filter(target=target, is_current=True).first() if target else None
    settings_initial = None
    if current_settings:
        settings_initial = {
            "host": current_settings.host,
            "port": current_settings.port,
            "media_directory": current_settings.media_directory,
            "schedule_path": current_settings.schedule_path,
            "secret_reference": current_settings.secret_reference,
            "reconciliation_mode": current_settings.reconciliation_mode,
            "base_nmg_path": current_settings.base_nmg_path,
            "base_nmg_hash": current_settings.base_nmg_hash,
            "base_bin_path": current_settings.base_bin_path,
            "base_bin_hash": current_settings.base_bin_hash,
            "command_port": current_settings.command_port,
            "command_username": current_settings.command_username,
            "command_secret_reference": current_settings.command_secret_reference,
            "controller_family": current_settings.controller_family,
            "firmware_version": current_settings.firmware_version,
            "output_number": current_settings.output_number,
            "media_profile": current_settings.media_profile,
            "profile_identity_hash": current_settings.profile_identity_hash,
            "ame_preset_sha256": current_settings.ame_preset_sha256,
            "ffmpeg_profile_sha256": current_settings.ffmpeg_profile_sha256,
            "nmg_template_sha256": current_settings.nmg_template_sha256,
            "bin_template_sha256": current_settings.bin_template_sha256,
            "qualification_status": current_settings.qualification_status,
            "qualification_evidence_hash": current_settings.qualification_evidence_hash,
            "bin_resource_template_reference": current_settings.settings.get("bin_resource_template_reference"),
            "bin_schedule_template_slot": current_settings.settings.get("bin_schedule_template_slot"),
            "ftp_username": current_settings.settings.get("ftp_username", ""),
            "ame_executable": current_settings.settings.get("ame_executable", ""),
            "ame_preset": current_settings.settings.get("ame_preset", ""),
            "ffmpeg_executable": current_settings.settings.get("ffmpeg_executable", ""),
            "ffmpeg_build_sha256": current_settings.settings.get("ffmpeg_build_sha256", ""),
            "ffmpeg_qualification_manifest": current_settings.settings.get("ffmpeg_qualification_manifest", ""),
            "ffmpeg_qualification_sha256": current_settings.settings.get("ffmpeg_qualification_sha256", ""),
            "nmg_resource_template_reference": current_settings.settings.get("nmg_resource_template_reference"),
            "nmg_schedule_template_base": current_settings.settings.get("nmg_schedule_template_base"),
            "capability_flags": ",".join(current_settings.capability_flags or []),
        }
    publication_batches = list(SchedulePublicationBatch.objects.select_related("target").prefetch_related(
        "occurrence_selections__occurrence", "artifact_revisions", "delivery_operations"
    ).order_by("-created_at")[:12])
    from .schedule_preparation import publication_readiness
    publication_cards = []
    for batch in publication_batches:
        bins = [item for item in batch.artifact_revisions.all() if item.artifact_type == "bin"]
        operations = list(batch.delivery_operations.all())
        operation = max(operations, key=lambda item: item.pk) if operations else None
        review = publication_review(batch)
        publication_cards.append({"batch": batch, "readiness": publication_readiness(batch), "publication_review": review,
                                  "bin_artifact": max(bins, key=lambda item: item.revision) if bins else None,
                                  "operation": operation,
                                  "can_rollback": bool(operation and operation.remote_backup_path and operation.state in {
                                      "schedule_transferred", "activation_requested", "activation_acknowledged",
                                      "activation_observed", "ambiguous",
                                  })})
    return {"station": Station.objects.first(), "targets": Device.objects.order_by("name"), "target": target,
            "research_gates": gates, "preparation_batches": PreparationBatch.objects.select_related("target").prefetch_related("items__asset").order_by("-created_at")[:12],
            "publication_batches": publication_batches, "publication_cards": publication_cards,
            "preparation_form": form_p or AutomationPreparationForm(), "publication_form": form_s or AutomationPublicationForm(),
            "settings_form": UltraNexusSettingsForm(initial=settings_initial),
            "schedule_preview": preview, "automation_message": message}


def automation_dashboard(request):
    """Owner-facing two approval workflow; all external work remains gated."""
    if request.method == "POST":
        action = request.POST.get("action", "")
        if action == "create_delivery_publication":
            item = get_object_or_404(PreparationBatchItem.objects.select_related("batch", "occurrence"), pk=request.POST.get("item"))
            if not item.occurrence_id:
                messages.info(request, "Select confirmed premiere cycles in Review schedule.")
                return redirect("schedule-prepare")
            if not (item.resulting_binding_id or item.execution_status == "ready"):
                messages.error(request, "Media preparation must be ready before creating a schedule publication.")
                return redirect("schedule-delivery")
            batch = SchedulePublicationBatch.objects.filter(preparation_item=item).first()
            if not batch:
                try:
                    with transaction.atomic():
                        batch = SchedulePublicationBatch.objects.create(preparation_item=item, target=item.batch.target, workflow_mode="selected_changes", reconciliation_mode="preserve")
                except IntegrityError:
                    batch = SchedulePublicationBatch.objects.get(preparation_item=item)
            OccurrenceRevisionSelection.objects.get_or_create(
                publication_batch=batch,
                occurrence=item.occurrence,
                defaults={"occurrence_revision": item.occurrence.revision, "operation": "add"},
            )
            messages.success(request, "Selected schedule publication is ready for review.")
            return redirect("schedule-delivery")
        if action == "save_target_settings":
            return redirect("device-settings", pk=request.POST.get("target"))
        if action == "create_preparation":
            form = AutomationPreparationForm(request.POST)
            if form.is_valid():
                batch = PreparationBatch.objects.create(target=form.cleaned_data["target"], label=form.cleaned_data["label"] or "Preparation batch")
                for position, asset in enumerate(form.cleaned_data["assets"]):
                    PreparationBatchItem.objects.create(batch=batch, asset=asset, encode_before_transfer=form.cleaned_data["encode_before_transfer"], position=position)
                messages.success(request, f"Preparation batch {batch.pk} created. Review and approve each item before execution.")
                return redirect("schedule-delivery")
            return render(request, "ultranexus_automation.html", _automation_context(request, form_p=form))
        if action == "toggle_preparation_item":
            item = get_object_or_404(PreparationBatchItem.objects.select_related("batch"), pk=request.POST.get("item"))
            if item.batch.approval_1_status == "approved":
                messages.error(request, "The encoding choice is frozen by Approval 1. Create a new batch to change it.")
            else:
                item.encode_before_transfer = request.POST.get("encode_before_transfer") == "on"
                item.save(update_fields=["encode_before_transfer"])
                messages.success(request, "Media preparation choice updated.")
            return redirect("schedule-delivery")
        if action == "approve_preparation":
            batch = get_object_or_404(PreparationBatch, pk=request.POST.get("batch"))
            try:
                approve_preparation_batch(batch)
                batch.status = "approved"; batch.save(update_fields=["status"])
                PreparationJob.objects.get_or_create(
                    idempotency_key=f"preparation:{batch.pk}:{batch.approval_1_hash}",
                    defaults={"batch": batch},
                )
                messages.success(request, "Approval 1 recorded. Media preparation and verified transfer are queued for the local worker.")
            except ValueError as exc:
                messages.error(request, str(exc))
            return redirect("schedule-delivery")
        if action == "execute_preparation":
            messages.info(request, "Review and retry individual items in Media Preparation.")
            return redirect("media-queue")
        if action == "create_publication":
            form = AutomationPublicationForm(request.POST)
            if form.is_valid():
                batch = SchedulePublicationBatch(
                    target=form.cleaned_data["target"],
                    reconciliation_mode=form.cleaned_data["reconciliation_mode"],
                    workflow_mode=form.cleaned_data["workflow_mode"],
                    requested_activation_at=form.cleaned_data["requested_activation_at"],
                )
                batch.full_clean()
                batch.save()
                for occurrence in form.cleaned_data["occurrences"]:
                    OccurrenceRevisionSelection.objects.create(publication_batch=batch, occurrence=occurrence, occurrence_revision=occurrence.revision)
                messages.success(request, f"Publication batch {batch.pk} created. Preview it before Approval 2.")
                return redirect("schedule-delivery")
            return render(request, "ultranexus_automation.html", _automation_context(request, form_s=form))
        if action == "set_publication_change":
            try:
                with transaction.atomic():
                    selection_ref = get_object_or_404(OccurrenceRevisionSelection.objects.select_related("publication_batch"),
                                                      pk=request.POST.get("selection"))
                    Device.objects.select_for_update().get(pk=selection_ref.publication_batch.target_id)
                    batch = SchedulePublicationBatch.objects.select_for_update().get(pk=selection_ref.publication_batch_id)
                    if batch.status == "cancelled":
                        raise ValueError("Cancelled publications cannot be edited")
                    if batch.activated_at or batch.delivery_operations.filter(state__in=ScheduleDeliveryOperation.ACTIVE).exists():
                        raise ValueError("Publication changes are locked after delivery staging")
                    selection = OccurrenceRevisionSelection.objects.select_for_update().get(pk=selection_ref.pk)
                    operation = request.POST.get("operation", "")
                    slot_text = request.POST.get("source_bin_slot", "").strip()
                    selection.operation = operation
                    try:
                        selection.source_bin_slot = int(slot_text) if slot_text else None
                    except ValueError:
                        raise ValueError("BIN slot must be an integer")
                    selection.source_bin_record_hash = request.POST.get("source_bin_record_hash", "").strip().lower()
                    selection.full_clean()
                    selection.save(update_fields=["operation", "source_bin_slot", "source_bin_record_hash"])
                    if batch.approval_2_status == "approved":
                        invalidate_snapshot(batch, approval=2, reason="selected controller change revised")
                messages.success(request, "Selected controller change saved. Generate new artifacts before approval.")
            except (ValidationError, ValueError) as exc:
                messages.error(request, "; ".join(exc.messages) if isinstance(exc, ValidationError) else str(exc))
            return redirect("schedule-delivery")
        if action == "approve_publication":
            batch_id = request.POST.get("batch")
            with transaction.atomic():
                initial = get_object_or_404(SchedulePublicationBatch, pk=batch_id)
                Device.objects.select_for_update().get(pk=initial.target_id)
                batch = SchedulePublicationBatch.objects.select_for_update().select_related("target").get(pk=batch_id)
                selection_ids = list(batch.occurrence_selections.values_list("occurrence_id", flat=True))
                list(Occurrence.objects.select_for_update().filter(pk__in=selection_ids))
                selections = list(batch.occurrence_selections.select_for_update().select_related(
                    "occurrence", "occurrence__station", "occurrence__show",
                    "occurrence__episode", "occurrence__asset",
                ).order_by("occurrence__starts_at", "pk"))
                try:
                    review = validate_review_token(batch, request.POST.get("review_token", ""))
                except ValueError:
                    snapshot = None
                else:
                    snapshot = publication_snapshot(batch)
                    snapshot["review_hash"] = review["review_hash"]
                preview_result = preview_schedule([selection.occurrence for selection in selections if selection.operation != "delete"],
                                          target=batch.target, workflow_mode=batch.workflow_mode)
                if snapshot is None:
                    preview_result["blockers"].append("controller review snapshot is missing or stale")
                result = preview_result
                for selection in selections:
                    if selection.occurrence_revision != selection.occurrence.revision:
                        result["blockers"].append(f"{selection.occurrence.label}: occurrence revision changed")
                    asset = selection.occurrence.asset
                    if selection.operation != "delete" and asset and not schedule_ready_binding(asset, batch.target):
                        result["blockers"].append(f"{selection.occurrence.label}: Approval 1 media preparation is not verified")
                result["blockers"].extend(publication_artifact_blockers(batch))
                if snapshot is not None and not snapshot["controller_snapshot_hash"]:
                    result["blockers"].append("controller snapshot hash is required")
                if result["blockers"]:
                    if batch.approval_2_status == "approved":
                        batch.approval_2_status = "stale"
                        batch.save(update_fields=["approval_2_status"])
                else:
                    # Re-read the signed review immediately before persisting Approval 2;
                    # selection edits are serialized on the same batch/device boundary.
                    try:
                        final_review = validate_review_token(batch, request.POST.get("review_token", ""))
                    except ValueError as exc:
                        result["blockers"].append(str(exc))
                        batch.approval_2_status = "stale"
                        batch.save(update_fields=["approval_2_status"])
                    else:
                        batch.review_diff = final_review.get("diff", {})
                        batch.save(update_fields=["review_diff"])
                        final_snapshot = publication_snapshot(batch)
                        final_snapshot["review_hash"] = final_review["review_hash"]
                        approve_snapshot(batch, final_snapshot, approval=2)
            if result["blockers"]:
                messages.error(request, "Approval 2 blocked: " + "; ".join(result["blockers"]))
            else:
                messages.success(request, "Approval 2 recorded. No schedule coverage is created by approval.")
            return redirect("schedule-delivery")
        if action == "review_publication":
            batch = get_object_or_404(SchedulePublicationBatch, pk=request.POST.get("batch"))
            try:
                snapshot = capture_controller_snapshot(batch)
                review = publication_review(batch, snapshot)
                if review.get("blockers"):
                    messages.error(request, "Controller review blocked: " + "; ".join(review["blockers"]))
                else:
                    messages.success(request, "Controller BIN captured. Review the exact diff and media hashes before Approval 2.")
            except (ValueError, ValidationError, OSError, UltraNexusError) as exc:
                messages.error(request, "Controller review failed: " + str(exc))
            return redirect("schedule-delivery")
        if action == "cancel_publication":
            batch = get_object_or_404(SchedulePublicationBatch, pk=request.POST.get("batch"))
            try:
                cancel_publication(batch.pk)
            except ValueError as exc:
                messages.error(request, str(exc))
            else:
                messages.success(request, "Publication cancelled. Prepared media and audit records were retained.")
            return redirect("schedule-delivery")
        if action == "generate_nmg":
            batch = get_object_or_404(SchedulePublicationBatch, pk=request.POST.get("batch"))
            try:
                artifact = generate_nmg_artifact(batch)
                messages.success(request, f"Generated and validated NMG revision {artifact.revision}: {artifact.content_hash}")
            except (ValueError, ValidationError) as exc:
                messages.error(request, str(exc))
            return redirect("schedule-delivery")
        if action == "generate_bin":
            batch = get_object_or_404(SchedulePublicationBatch, pk=request.POST.get("batch"))
            key = f"generate-bin-{batch.pk}-{batch.controller_snapshot_hash or 'unpulled'}"
            job, created = PublicationJob.objects.get_or_create(
                idempotency_key=key, defaults={"publication_batch": batch, "kind": "generate_bin"})
            if not created and job.status in {"failed", "cancelled"}:
                job.status = "queued"; job.error = ""; job.finished_at = None
                job.save(update_fields=["status", "error", "finished_at"])
            messages.success(request, f"BIN generation queued (job {job.pk}); worker status is visible in the job record.")
            return redirect("schedule-delivery")
        if action == "stage_publication":
            batch = get_object_or_404(SchedulePublicationBatch, pk=request.POST.get("batch"))
            if request.POST.get("confirm_stage") != "on":
                messages.error(request, "Confirm the reviewed BIN hash before staging.")
            else:
                try:
                    operation = stage_publication(batch.pk, expected_hash=request.POST.get("expected_hash", ""))
                    messages.success(request, f"Candidate staged; current remote BIN retained as operation {operation.pk} rollback artifact.")
                except (ValueError, ValidationError, UltraNexusError, OSError):
                    messages.error(request, "Staging failed; review the operation and remote schedule before retrying.")
            return redirect("schedule-delivery")
        if action == "activate_publication":
            batch = get_object_or_404(SchedulePublicationBatch, pk=request.POST.get("batch"))
            operation = batch.delivery_operations.order_by("-pk").first()
            if request.POST.get("confirm_activate") != "on" or not operation:
                messages.error(request, "A staged operation and final confirmation are required.")
            else:
                try:
                    activate_publication(operation.pk, expected_hash=request.POST.get("expected_hash", ""))
                    messages.success(request, "LOADSCH acknowledged. Attach independent controller evidence before recording coverage.")
                except (ValueError, ValidationError, UltraNexusError, OSError):
                    messages.error(request, "Activation is uncertain or blocked; review the operation and controller evidence.")
            return redirect("schedule-delivery")
        if action == "observe_publication":
            batch = get_object_or_404(SchedulePublicationBatch, pk=request.POST.get("batch"))
            operation = batch.delivery_operations.order_by("-pk").first()
            if not operation or request.POST.get("confirm_observation") != "on":
                messages.error(request, "Confirm independent activation evidence for a specific operation.")
            elif request.POST.get("expected_hash", "").lower() != operation.artifact.content_hash:
                messages.error(request, "Observed candidate hash does not match the activation.")
            else:
                try:
                    observe_activation(operation.pk, evidence_file=request.POST.get("evidence_file", ""))
                    messages.success(request, "Independent activation observation and uploaded coverage recorded.")
                except (ValueError, ValidationError, UltraNexusError, OSError):
                    messages.error(request, "Activation evidence was not accepted; review the operation.")
            return redirect("schedule-delivery")
        if action == "rollback_publication":
            batch = get_object_or_404(SchedulePublicationBatch, pk=request.POST.get("batch"))
            operation = batch.delivery_operations.order_by("-pk").first()
            if not operation or request.POST.get("confirm_rollback") != "on":
                messages.error(request, "Confirm a specific known-good rollback before restoring it.")
            else:
                try:
                    rollback_publication(operation.pk, expected_rollback_hash=request.POST.get("rollback_hash", ""))
                    messages.success(request, "Known-good BIN restored and LOADSCH acknowledged; observe controller state separately.")
                except (ValueError, ValidationError, UltraNexusError, OSError):
                    messages.error(request, "Rollback is uncertain or blocked; inspect remote and controller state.")
            return redirect("schedule-delivery")
    return render(request, "ultranexus_automation.html", _automation_context(request))


def device_list(request):
    return render(request, "device_list.html", {"devices": Device.objects.all().order_by("name")})


def schedule_view(request):
    mode = request.GET.get("mode", "day")
    if mode not in {"day", "week", "agenda", "recurring"}:
        mode = "day"
    if mode == "week":
        return week_view(request)
    if mode == "agenda":
        return agenda_view(request)
    if mode == "recurring":
        return reserved_schedule(request)
    return day_view(request)

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
    sort = request.GET.get("sort", "title")
    valid_sorts = {"title", "code", "show_type", "episodes", "weekly_time"}
    if sort not in valid_sorts:
        sort = "title"
    today = timezone.localdate()
    active_slots = RecurrenceSlot.objects.filter(
        Q(active_from__isnull=True) | Q(active_from__lte=today),
        Q(active_until__isnull=True) | Q(active_until__gte=today),
    ).order_by("weekday", "start_time", "pk")
    shows = list(
        station.shows.annotate(episode_count=Count("episodes")).prefetch_related(
            Prefetch("slots", queryset=active_slots, to_attr="weekly_slots")
        ) if station else Show.objects.none()
    )
    if show_type in dict(Show.SHOW_TYPES):
        shows = [show for show in shows if show.show_type == show_type]

    def sort_key(show):
        slots = getattr(show, "weekly_slots", [])
        first_slot = (slots[0].weekday, slots[0].start_time, slots[0].pk) if slots else (99, datetime.max.time(), 0)
        if sort == "code":
            return (show.code.casefold(), show.title.casefold())
        if sort == "show_type":
            return ((show.get_show_type_display() or "Unclassified").casefold(), show.title.casefold())
        if sort == "episodes":
            return (show.episode_count, show.title.casefold())
        if sort == "weekly_time":
            return (*first_slot, show.title.casefold())
        return (show.title.casefold(), show.pk)

    shows.sort(key=sort_key)
    return render(request, "show_list.html", {
        "shows": shows,
        "show_types": Show.SHOW_TYPES,
        "show_type_filter": show_type,
        "show_sort": sort,
    })

def show_detail(request, show_id):
    show = get_object_or_404(
        Show.objects.prefetch_related(
            "occurrences__episode", "occurrences__programming",
            "occurrences__airing_evidence", "slots"
        ),
        pk=show_id,
    )
    pending = _pending_queue(show)
    episodes = sorted(
        list(show.episodes.prefetch_related(
            "deliveries", "workflow_milestones", "assets__preparation_record",
            "assets__target_transfers", "occurrences__programming",
            "occurrences__airing_evidence",
        )),
        key=lambda item: (item.intended_air_order is None, item.intended_air_order or 0, item.pk),
    )
    for episode in episodes:
        episode.queue_reason = pending_episode_reason(episode) if episode.status == "pending" else ""
    pending = [episode for episode in episodes if episode.status == "pending"]
    return render(request, "show_detail.html", {"show": show, "pending": pending, "episodes": episodes})

def episode_queue_reorder(request, show_id):
    station = Station.objects.first()
    show = get_object_or_404(Show, pk=show_id, station=station)
    if request.method != "POST":
        return HttpResponseBadRequest("Queue changes must be submitted with POST.")
    try:
        raw_ids = request.POST.getlist("episode_ids")
        if len(raw_ids) == 1 and "," in raw_ids[0]:
            raw_ids = raw_ids[0].split(",")
        ordered_ids = [int(value) for value in raw_ids if str(value).strip()]
        positions = request.POST.getlist("queue_positions")
        if positions and len(positions) == len(ordered_ids):
            try:
                ordered_ids = [episode_id for _, episode_id in sorted(zip((int(pos) for pos in positions), ordered_ids), key=lambda pair: (pair[0], pair[1]))]
            except ValueError:
                return HttpResponseBadRequest("Queue positions must be whole numbers.")
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
    episode = get_object_or_404(
        Episode.objects.select_related("show", "producer").prefetch_related(
            "assets__preparation_record", "assets__target_transfers", "deliveries",
            "occurrences__programming", "occurrences__airing_evidence",
            "workflow_milestones",
        ),
        pk=pk,
    )
    return render(request, "episode_detail.html", {"episode": episode})

def episode_list(request):
    station = Station.objects.first()
    episodes = Episode.objects.filter(show__station=station).select_related("show").prefetch_related(
        "deliveries", "workflow_milestones", "assets__preparation_record",
        "assets__target_transfers", "occurrences__programming",
        "occurrences__airing_evidence",
    ).order_by("show__title", F("intended_air_order").asc(nulls_last=True), "pk") if station else Episode.objects.none()
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
        messages.success(request, "Occurrence created."); return redirect("occurrence-workbench", pk=occurrence.pk)
    return render(request, "occurrence_form.html", {"form": form, "title": "Add to schedule"})

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
                    asset=form.cleaned_data["asset"],
                    starts_at=form.cleaned_data["starts_at"], planned_duration_seconds=form.cleaned_data["planned_duration_seconds"],
                    status=form.cleaned_data["status"], reason=form.cleaned_data["reason"], revision=expected_value + 1)
                if not updated: return HttpResponseBadRequest("This item changed in another tab; reload before saving.")
            audit("update", "Occurrence", occurrence, "Schedule plan revised")
            messages.success(request, "Occurrence updated."); return redirect("occurrence-workbench", pk=occurrence.pk)
    else:
        form = OccurrenceForm(instance=occurrence, station=occurrence.station, initial={"expected_revision": occurrence.revision})
    return render(request, "occurrence_form.html", {"form": form, "title": "Edit schedule item", "occurrence": occurrence})

def _crud(request, model, form_class, title, pk=None, initial=None, extra_context=None):
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
        next_url = request.GET.get("next", "")
        if next_url.startswith("/") and not next_url.startswith("//"):
            return redirect(next_url)
        return redirect("dashboard")
    intros = {
        "Create show": "Add the show once, then manage its episodes and weekly air times from the show page.",
        "Edit show": "Delivery method is a preference only; each actual delivery keeps its own provenance.",
        "Record media asset": "Record a source or encoded rendition as private metadata. ChannelDex does not move or validate the file.",
        "Record delivery": "Record how a submission was made available and link one or more episodes. ChannelDex does not fetch it.",
    }
    context = {"form": form, "title": title, "intro": intros.get(title)}
    context.update(extra_context or {})
    return render(request, "simple_form.html", context)
def setup_station(request):
    existing = Station.objects.first()
    return _crud(request, Station, StationForm, "Station setup", pk=existing.pk if existing else None)
def setup_device(request): return _crud(request, Device, DeviceForm, "Device setup")
def show_create(request):
    return _crud(
        request, Show, ShowForm, "Create show",
        extra_context={"producer_url": "/producers/new/?popup=1"},
    )
def show_edit(request, show_id):
    station = Station.objects.first()
    show = get_object_or_404(Show, pk=show_id, station=station)
    return _crud(request, Show, ShowForm, "Edit show", pk=show.pk)

@xframe_options_sameorigin
def producer_create(request):
    station = Station.objects.first()
    if not station:
        return HttpResponseBadRequest("Create a station first")
    show = Show.objects.filter(pk=request.GET.get("show"), station=station).first()
    episode = Episode.objects.filter(pk=request.GET.get("episode"), show__station=station).first()
    popup = request.GET.get("popup") == "1"
    form = ProducerForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        producer = form.save(commit=False)
        producer.station = station
        producer.full_clean()
        producer.save()
        audit("create", "Producer", producer, "Producer added")
        if popup:
            return render(request, "producer_popup_complete.html", {"producer": producer})
        messages.success(request, f"Producer {producer} added and selected.")
        next_url = request.GET.get("next", "")
        if next_url.startswith("/") and not next_url.startswith("//"):
            return redirect(next_url + ("&" if "?" in next_url else "?") + f"producer={producer.pk}")
        if episode:
            return redirect(f"/episodes/{episode.pk}/edit/?producer={producer.pk}")
        suffix = f"?producer={producer.pk}"
        if show:
            suffix += f"&show={show.pk}"
        return redirect(f"/episodes/new/{suffix}")
    return render(request, "producer_popup_form.html" if popup else "simple_form.html", {
        "form": form,
        "title": "Add producer",
        "intro": "Add the producer by name. Contact and membership details remain private local metadata.",
    })

def _episode_create(request, reference=None):
    station = Station.objects.first()
    show = reference.show if reference else (
        get_object_or_404(Show, pk=request.GET.get("show"), station=station)
        if request.GET.get("show") and station else None
    )
    producer = Producer.objects.filter(pk=request.GET.get("producer"), station=station).first() if station else None
    initial = {
        "show": reference.show_id,
        "title": reference.title,
        "producer": reference.producer_id,
        "intended_air_order": reference.intended_air_order,
        "intended_premiere_date": reference.intended_premiere_date,
        "runtime_seconds": reference.runtime_seconds,
    } if reference else {}
    if show and not reference:
        initial["show"] = show.pk
    if producer:
        initial["producer"] = producer.pk
    if show and not reference:
        current_max = show.episodes.aggregate(value=Max("intended_air_order"))["value"] or 0
        initial["intended_air_order"] = current_max + 1
    form = EpisodeForm(request.POST or None, station=station, initial=initial)
    if request.method == "POST" and form.is_valid():
        episode = form.save()
        summary = f"Episode duplicated from {reference.pk}" if reference else "Episode created"
        audit("create", "Episode", episode, summary)
        messages.success(request, "Duplicate episode saved as a separate record." if reference else "Episode saved. Record its current workflow stage when ready.")
        return redirect("episode-detail", pk=episode.pk)
    producer_url = f"/producers/new/?popup=1&show={show.pk}" if show else "/producers/new/?popup=1"
    return render(request, "simple_form.html", {
        "form": form, "title": "Duplicate episode" if reference else "Add episode", "producer_url": producer_url,
        "queue_preview": _pending_queue(show) if show else None,
        "queue_show": show,
        "intro": (
            f"Review the copied fields from {reference.title}, then save a separate episode record. Deliveries, assets, workflow facts, schedules, and airing evidence are not copied."
            if reference else
            "Create the episode record first; delivery, media, workflow, and scheduling actions remain separate and auditable."
        ),
    })


def episode_create(request):
    return _episode_create(request)


def episode_duplicate(request, pk):
    station = Station.objects.first()
    reference = get_object_or_404(
        Episode.objects.select_related("show", "producer"),
        pk=pk,
        show__station=station,
    )
    return _episode_create(request, reference=reference)

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
        "producer_url": f"/producers/new/?popup=1&episode={episode.pk}",
        "intro": "Queue classification and airing evidence are managed separately from the episode's workflow milestones.",
    })

def episode_milestone_create(request, pk):
    station = Station.objects.first()
    episode = get_object_or_404(Episode, pk=pk, show__station=station)
    if request.method != "GET":
        messages.info(request, "Workflow milestones are now derived from delivery, asset, transfer, programming, and upload facts.")
    return redirect("episode-detail", pk=episode.pk)
    # Kept below as a compatibility reference for migrated installations.
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
def asset_edit(request, pk):
    station = Station.objects.first()
    asset = get_object_or_404(MediaAsset, pk=pk, episode__show__station=station)
    return _crud(request, MediaAsset, AssetForm, "Edit media asset", pk=asset.pk)
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
    if request.GET.get("legacy") != "1":
        return redirect(f"/shows/{show.pk}/#weekly-times")
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
            posted_week = posted_premiere - timedelta(days=posted_premiere.weekday())
            # The database identity is show + Monday week. Resolve that exact
            # row even when a legacy or corrected record has a different or
            # missing premiere_date, avoiding a duplicate-key server error.
            existing = WeeklyEpisodeAssignment.objects.filter(
                show_id=posted_show_id,
                show__station=station,
                week_start=posted_week,
            ).first()
    existing_episode_id = existing.episode_id if existing else None
    form = AssignmentForm(
        form_data,
        instance=existing,
        station=station,
        initial=initial,
    )
    if request.method == "POST" and form.is_valid():
        form.instance.is_automatic_carry_forward = False
        if existing:
            blocked = Occurrence.objects.filter(weekly_assignment=existing).filter(Q(uploaded_revisions__isnull=False) | Q(airing_evidence__isnull=False)).exists()
            if blocked:
                form.add_error(None, "This premiere cycle cannot be changed because an upload or airing record already relies on it.")
                return render(request, "assignment_form.html", {"form": form, "title": "Plan the next premiere cycle", "selected_show": selected_show, "queue": _pending_queue(selected_show), "prior_cycle": existing}, status=400)
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
                | ~Q(legacy_ftp_output_device="")
                | ~Q(library_registration="")
                | ~Q(slot_assignment="")
                | ~Q(legacy_uploaded_schedule_revision="")
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
                    {"form": form, "title": "Plan the next premiere cycle", "selected_show": selected_show, "queue": _pending_queue(selected_show), "prior_cycle": existing},
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
                        if episode_changed:
                            # An asset belongs to the prior episode; never silently
                            # carry it across a premiere-cycle correction.
                            occurrence.asset = None
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
    queue = _pending_queue(selected_show) if selected_show else []
    prior_cycle = selected_show.weekly_assignments.exclude(premiere_date=None).order_by("-premiere_date").first() if selected_show else None
    return render(request, "assignment_form.html", {
        "form": form,
        "title": "Plan the next premiere cycle",
        "selected_show": selected_show,
        "queue": queue,
        "prior_cycle": prior_cycle,
        "premiere_setup_missing": bool(selected_show and not next_premiere_date(selected_show)),
    })
def day_view(request):
    station = Station.objects.first()
    try:
        selected = date.fromisoformat(request.GET.get("date", "")) if request.GET.get("date") else timezone.localdate()
    except ValueError:
        return HttpResponseBadRequest("Invalid date")
    if station:
        _apply_carry_forward(request, station, selected - timedelta(days=selected.weekday()))
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
    capacity = calendar_capacity(station, selected)[0] if station else {"intervals": []}
    timeline = [{"kind": "occurrence", "starts_at": item.starts_at, "ends_at": item.ends_at, "occurrence": item} for item in occurrences]
    timeline += [{"kind": "capacity", "starts_at": interval["start"], "ends_at": interval["end"], "interval": interval} for interval in capacity.get("intervals", [])]
    timeline.sort(key=lambda item: (item["starts_at"], 0 if item["kind"] == "occurrence" else 1))
    return render(request, "calendar_day.html", {"title": "Day plan", "occurrences": occurrences, "timeline": timeline, "selected_date": selected, "previous_date": selected - timedelta(days=1), "next_date": selected + timedelta(days=1), "capacity": capacity, "capacity_filter": request.GET.get("capacity", "all"), "show_type_filter": show_type, "show_types": Show.SHOW_TYPES, "coverage_label": _human_duration(covered), "alerts": _alerts(occurrences)})

def week_view(request):
    station = Station.objects.first()
    try:
        selected = date.fromisoformat(request.GET.get("date", "")) if request.GET.get("date") else timezone.localdate()
    except ValueError:
        return HttpResponseBadRequest("Invalid date")
    selected -= timedelta(days=selected.weekday())
    if station:
        _apply_carry_forward(request, station, selected)
    days = []
    for offset in range(7):
        day = selected + timedelta(days=offset)
        days.append(day)
    tz = ZoneInfo(station.timezone) if station else ZoneInfo("America/Detroit")
    start = timezone.make_aware(datetime.combine(selected, datetime.min.time()), tz)
    end = timezone.make_aware(datetime.combine(selected + timedelta(days=7), datetime.min.time()), tz)
    requested_mode = request.GET.get("view", "capacity")
    # Preserve old bookmarked URLs while presenting only the two clear modes.
    view_mode = {"scheduled": "schedule", "availability": "capacity"}.get(requested_mode, requested_mode)
    if view_mode not in {"schedule", "capacity"}:
        view_mode = "capacity"
    occurrences = Occurrence.objects.filter(
        station=station, status="planned",
        starts_at__lt=end,
    ).select_related("show", "episode", "weekly_assignment", "recurrence_slot", "preparation").order_by("starts_at") if station else []
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
    segments_by_day = {day: [] for day in days}
    for segment in day_segments:
        segments_by_day[segment["date"]].append({
            "kind": "occurrence", "start": segment["start"], "end": segment["end"],
            "occurrence": segment["occurrence"],
        })
    capacity_by_day = {item["date"]: _coarse_capacity_blocks(item) for item in capacity}
    week_columns = []
    for day in days:
        blocks = capacity_by_day.get(day, [])
        if view_mode == "capacity":
            items = blocks
        else:
            items = segments_by_day[day] + [item for item in blocks if item["status"] == "available"]
            items.sort(key=lambda item: (item["start"], 0 if item["kind"] == "occurrence" else 1))
        week_columns.append({"date": day, "items": items})
    return render(request, "calendar_week.html", {
        "title": "Week plan", "view_mode": view_mode, "occurrences": occurrences,
        "day_segments": day_segments, "week_days": days, "week_columns": week_columns,
        "selected_date": selected, "previous_week": selected - timedelta(days=7),
        "next_week": selected + timedelta(days=7), "capacity": capacity,
        "alerts": _alerts(occurrences) if view_mode == "schedule" else [],
    })

def agenda_view(request):
    try:
        days = min(max(int(request.GET.get("days", "7")), 1), 31)
    except ValueError:
        return HttpResponseBadRequest("Invalid range")
    station = Station.objects.first()
    start = timezone.localdate()
    end = start + timedelta(days=days)
    if station:
        carry_week = start - timedelta(days=start.weekday())
        last_week = (end - timedelta(days=1)) - timedelta(days=(end - timedelta(days=1)).weekday())
        while carry_week <= last_week:
            _apply_carry_forward(request, station, carry_week)
            carry_week += timedelta(days=7)
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
    tab = request.GET.get("tab", "uploads")
    if tab not in {"uploads", "air", "audit"}: tab = "uploads"
    uploads = UploadedScheduleRevision.objects.all().order_by("-uploaded_at")
    evidence = AiringEvidence.objects.all().order_by("-aired_at")
    audit_events = AuditEvent.objects.all().order_by("-occurred_at")
    query = request.GET.get("q", "").strip()
    if query:
        uploads = uploads.filter(external_reference__icontains=query)
        evidence = evidence.filter(source__icontains=query)
        audit_events = audit_events.filter(summary__icontains=query)
    return render(request, "history.html", {"title": "History: evidence and audit", "tab": tab, "query": query, "uploads": uploads, "evidence": evidence, "audit_events": audit_events})
def preparation_edit(request, pk):
    occurrence = get_object_or_404(Occurrence, pk=pk)
    if not occurrence.asset_id:
        messages.info(request, "This occurrence has no asset. Live items are N/A; select an asset for media preparation.")
        return redirect("occurrence-workbench", pk=occurrence.pk)
    asset = occurrence.asset
    prep, _ = AssetPreparation.objects.get_or_create(asset=asset)
    transfer = AssetTargetTransfer.objects.filter(asset=asset).first()
    prep_form = AssetPreparationForm(request.POST or None, instance=prep, prefix="prep")
    if request.method == "POST" and "save_preparation" in request.POST:
        if prep_form.is_valid():
            prep_form.save()
            audit("update", "AssetPreparation", prep, "Canonical asset preparation updated")
            messages.success(request, "Canonical asset preparation saved.")
            return redirect("occurrence-workbench", pk=occurrence.pk)
    transfer_form = AssetTargetTransferForm(station=occurrence.station, prefix="transfer")
    return render(request, "asset_preparation_form.html", {"occurrence": occurrence, "asset": asset, "prep_form": prep_form, "transfer_form": transfer_form})


def transfer_create(request, asset_id):
    asset = get_object_or_404(MediaAsset.objects.select_related("episode__show"), pk=asset_id)
    station = asset.episode.show.station if asset.episode_id else Station.objects.first()
    form = AssetTargetTransferForm(request.POST or None, station=station, prefix="transfer")
    if request.method == "POST" and form.is_valid():
        transfer = form.save(commit=False); transfer.asset = asset
        transfer.full_clean(); transfer.save()
        audit("create", "AssetTargetTransfer", transfer, "Canonical asset transfer created")
        messages.success(request, "Transfer record saved.")
        return redirect("episode-detail", pk=asset.episode_id) if asset.episode_id else redirect("dashboard")
    return render(request, "asset_transfer_form.html", {"asset": asset, "form": form, "title": "Add asset transfer"})


def transfer_edit(request, asset_id, pk):
    asset = get_object_or_404(MediaAsset, pk=asset_id)
    transfer = get_object_or_404(AssetTargetTransfer, pk=pk, asset=asset)
    station = asset.episode.show.station if asset.episode_id else Station.objects.first()
    form = AssetTargetTransferForm(request.POST or None, instance=transfer, station=station, prefix="transfer")
    if request.method == "POST" and form.is_valid():
        transfer = form.save(commit=False); transfer.asset = asset; transfer.full_clean(); transfer.save()
        audit("update", "AssetTargetTransfer", transfer, "Canonical asset transfer updated")
        messages.success(request, "Transfer record updated.")
        return redirect("episode-detail", pk=asset.episode_id) if asset.episode_id else redirect("dashboard")
    return render(request, "asset_transfer_form.html", {"asset": asset, "form": form, "title": "Edit asset transfer", "transfer": transfer})
def programming_create(request, pk):
    occurrence = get_object_or_404(Occurrence, pk=pk); form = ProgrammingForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        obj, created = OccurrenceProgramming.objects.update_or_create(
            occurrence=occurrence,
            device=form.cleaned_data["device"],
            defaults={"slot_assignment": form.cleaned_data.get("slot_assignment", ""), "note": form.cleaned_data.get("note", ""), "actor": request.POST.get("actor", "owner") or "owner", "provenance": request.POST.get("provenance", "manual") or "manual"},
        )
        audit("create" if created else "update", "OccurrenceProgramming", obj, "Programming record saved"); return redirect("occurrence-workbench", pk=occurrence.pk)
    return render(request, "simple_form.html", {"form": form, "title": "Record device programming", "occurrence": occurrence, "device_url": "/setup/device/?next=/occurrences/%s/programming/" % occurrence.pk})
def upload_create(request):
    station = Station.objects.first()
    initial = None
    if station:
        raw_occurrences = request.GET.getlist("occurrence") + request.GET.getlist("occurrences")
        requested_ids = []
        for value in raw_occurrences:
            requested_ids.extend(part.strip() for part in value.split(",") if part.strip())
        try:
            requested_ids = list(dict.fromkeys(int(value) for value in requested_ids))
        except ValueError:
            requested_ids = []
        selected = list(Occurrence.objects.filter(pk__in=requested_ids, station=station, status="planned"))
        if selected:
            initial = {"occurrences": [item.pk for item in selected]}
    form = UploadForm(request.POST or None, station=station, initial=initial)
    if request.method == "POST" and form.is_valid():
        submitted = list(form.cleaned_data["occurrences"])
        if any(item.status != "planned" for item in submitted):
            form.add_error("occurrences", "Only current planned occurrences may be recorded.")
            return render(request, "simple_form.html", {"form": form, "title": "Record schedule upload"})
        selected = submitted
        if not selected:
            form.add_error("occurrences", "Select at least one current planned occurrence.")
            return render(request, "simple_form.html", {"form": form, "title": "Record schedule upload"})
        token = sign_upload_preview(
            device_id=form.cleaned_data["device"].pk,
            external_reference=form.cleaned_data["external_reference"],
            occurrences=selected,
        )
        try:
            signed = load_upload_preview(token)
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
        return redirect("occurrence-workbench", pk=committed_occurrences[0].pk if committed_occurrences else submitted[0].pk)
    return render(request, "simple_form.html", {"form": form, "title": "Record schedule upload"})

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
