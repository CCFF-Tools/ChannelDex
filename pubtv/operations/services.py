from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo
from django.db import transaction, connection
from django.db.models import F
from django.core.exceptions import ValidationError
from django.utils import timezone
from django.core.signing import TimestampSigner, BadSignature
from .models import Occurrence, Episode, RecurrenceSlot, UploadedScheduleRevision, UploadedOccurrenceCoverage, WeeklyEpisodeAssignment, Preparation, AssetPreparation, AssetTargetTransfer, AiringEvidence, AuditEvent

def audit(action, entity, obj=None, summary="", actor="owner"):
    return AuditEvent.objects.create(actor=actor, action=action, entity=entity, entity_id=getattr(obj, "pk", None), revision=getattr(obj, "revision", None), summary=summary)

def confirm_preparation_fact(preparation: Preparation, fact, value, actor="owner", provenance="manual", expected_revision=None):
    """Record one of the six checklist facts with independent provenance."""
    fields = {"source_available": "source_available", "ame": "ame_preset", "ftp": "legacy_ftp_output_device", "library": "library_registration", "slot": "slot_assignment", "upload": "legacy_uploaded_schedule_revision"}
    if fact not in fields: raise ValueError("unknown preparation fact")
    now = timezone.now()
    expected_revision = preparation.revision if expected_revision is None else expected_revision
    updates = {fields[fact]: value, f"{fact}_actor": actor, f"{fact}_at": now,
               f"{fact}_provenance": provenance, "revision": expected_revision + 1}
    if Preparation.objects.filter(pk=preparation.pk, revision=expected_revision).update(**updates) != 1:
        raise ValidationError("This preparation changed in another tab; reload before saving.")
    preparation.refresh_from_db()
    return preparation

def sign_upload_preview(*, device_id, external_reference, occurrences):
    payload = {"device": int(device_id), "reference": external_reference,
               "occurrences": [[int(o.pk), int(o.revision)] for o in occurrences]}
    return TimestampSigner(salt="pubtv-upload-preview").sign_object(payload)

def load_upload_preview(token, max_age=3600):
    try:
        return TimestampSigner(salt="pubtv-upload-preview").unsign_object(token, max_age=max_age)
    except (BadSignature, ValueError, TypeError):
        raise ValidationError("Upload preview is invalid or expired.")

@transaction.atomic
def commit_upload_preview(*, device, external_reference, token_payload, submitted_ids, station_id, actor="owner"):
    """Validate a signed preview and create its immutable snapshot as one transaction."""
    pairs = token_payload.get("occurrences")
    if not isinstance(pairs, list) or not pairs:
        raise ValidationError("Upload preview contains no occurrences.")
    signed_ids = [int(pair[0]) for pair in pairs]
    submitted_ids = [int(value) for value in submitted_ids]
    if len(signed_ids) != len(set(signed_ids)) or submitted_ids != signed_ids:
        raise ValidationError("Submitted selection does not match the preview.")
    if int(token_payload.get("device")) != device.pk or token_payload.get("reference") != external_reference:
        raise ValidationError("Upload preview does not match this device or reference.")
    # An insert takes SQLite's write reservation before the validation reads.
    upload = UploadedScheduleRevision.objects.create(
        device=device, external_reference=external_reference, state="active"
    )
    current = list(Occurrence.objects.filter(
        pk__in=signed_ids, station_id=station_id, status="planned"
    ))
    by_id = {item.pk: item for item in current}
    if len(current) != len(pairs) or any(
        pk not in by_id or by_id[pk].revision != int(revision)
        for pk, revision in pairs
    ):
        raise ValidationError("The plan changed after preview; preview again.")
    for occurrence in (by_id[pk] for pk in signed_ids):
        coverage = UploadedOccurrenceCoverage(
            upload=upload, occurrence=occurrence, occurrence_revision=occurrence.revision
        )
        coverage.full_clean()
        coverage.save()
        audit("cover", "UploadedOccurrenceCoverage", coverage,
              f"{occurrence.label} revision {occurrence.revision}", actor)
    audit("create", "UploadedScheduleRevision", upload,
          f"{len(signed_ids)} occurrence snapshots", actor)
    return upload, [by_id[pk] for pk in signed_ids]

def revise_airing(evidence, *, status, source, aired_at, actor="owner", notes=""):
    """Append a correction linked to prior airing evidence; never erase the assertion."""
    return AiringEvidence.objects.create(occurrence=evidence.occurrence, status=status, source=source, aired_at=aired_at, actor=actor, notes=notes, supersedes=evidence)

def occurrences_for_slot(slot: RecurrenceSlot, start_date, end_date):
    """Materialize intended weekly occurrences in station-local wall-clock time."""
    tz = ZoneInfo(slot.station.timezone)
    day = start_date
    result = []
    while day <= end_date:
        if day.weekday() == slot.weekday and (not slot.active_from or day >= slot.active_from) and (not slot.active_until or day <= slot.active_until):
            start_time = slot.start_time
            if isinstance(start_time, str): start_time = datetime.strptime(start_time, "%H:%M").time()
            starts_at = datetime.combine(day, start_time, tzinfo=tz)
            assignment = WeeklyEpisodeAssignment.objects.filter(
                show=slot.show, week_start=day - timedelta(days=day.weekday())
            ).first() if slot.show_id else None
            if slot.show_id:
                if assignment and assignment.selection_type != "none" and assignment.episode:
                    role = "rerun" if assignment.selection_type == "rerun" else ("premiere" if slot.is_premiere else "replay")
                    duration = slot.show.slot_duration_seconds or slot.duration_seconds
                    result.append(Occurrence(station=slot.station, show=slot.show, episode=assignment.episode, weekly_assignment=assignment, recurrence_slot=slot, schedule_role=role, item_type="episode", label=slot.show.title, starts_at=starts_at, planned_duration_seconds=duration))
            else:
                result.append(Occurrence(station=slot.station, show=None, episode=None, weekly_assignment=None, recurrence_slot=slot, item_type="media", label="Recurring slot", starts_at=starts_at, planned_duration_seconds=slot.duration_seconds))
        day += timedelta(days=1)
    return result


def active_premiere_slot(show, premiere_date):
    matches = [
        slot for slot in show.slots.filter(is_premiere=True)
        if slot.weekday == premiere_date.weekday()
        and (not slot.active_from or slot.active_from <= premiere_date)
        and (not slot.active_until or slot.active_until >= premiere_date)
    ]
    return matches[0] if len(matches) == 1 else None


def next_premiere_date(show, after=None):
    """Return the next active premiere date after the last planned cycle or a given date."""
    latest = show.weekly_assignments.exclude(premiere_date=None).order_by("-premiere_date").first()
    cursor = after or (latest.premiere_date + timedelta(days=1) if latest else timezone.localdate())
    for offset in range(0, 370):
        candidate = cursor + timedelta(days=offset)
        if active_premiere_slot(show, candidate):
            return candidate
    return None


def suggested_pending_episode(show):
    return show.episodes.filter(status="pending").exclude(
        weeklyepisodeassignment__show=show,
        weeklyepisodeassignment__selection_type="premiere",
    ).order_by(
        F("intended_air_order").asc(nulls_last=True),
        F("intended_premiere_date").asc(nulls_last=True),
        # received_at is a derived Python property; order by its preserved
        # legacy source column for a stable FIFO-compatible database order.
        F("legacy_received_at").asc(nulls_last=True),
        "pk",
    ).first()


def occurrences_for_cycle(assignment):
    """Build one premiere-to-premiere cycle, independent of Monday calendar boundaries."""
    premiere_date = assignment.premiere_date
    if not premiere_date:
        premiere_slot = assignment.show.slots.filter(is_premiere=True).order_by("pk").first()
        premiere_date = assignment.week_start + timedelta(days=premiere_slot.weekday if premiere_slot else 0)
    premiere_slot = active_premiere_slot(assignment.show, premiere_date)
    if not premiere_slot or assignment.selection_type == "none" or not assignment.episode:
        return []
    tz = ZoneInfo(assignment.show.station.timezone)
    cycle_start = datetime.combine(premiere_date, premiere_slot.start_time, tzinfo=tz)
    cycle_end = cycle_start + timedelta(days=7)
    result = []
    for slot in assignment.show.slots.all():
        for offset in range(0, 8):
            day = premiere_date + timedelta(days=offset)
            if day.weekday() != slot.weekday:
                continue
            if slot.active_from and day < slot.active_from:
                continue
            if slot.active_until and day > slot.active_until:
                continue
            starts_at = datetime.combine(day, slot.start_time, tzinfo=tz)
            if not (cycle_start <= starts_at < cycle_end):
                continue
            role = "rerun" if assignment.selection_type == "rerun" else (
                "premiere" if slot.pk == premiere_slot.pk else "replay"
            )
            result.append(Occurrence(
                station=assignment.show.station, show=assignment.show,
                episode=assignment.episode, weekly_assignment=assignment,
                recurrence_slot=slot, schedule_role=role, item_type="episode",
                label=assignment.show.title, starts_at=starts_at,
                planned_duration_seconds=assignment.show.slot_duration_seconds or slot.duration_seconds,
            ))
    return sorted(result, key=lambda item: item.starts_at)


@transaction.atomic
def materialize_assignment(assignment):
    """Create all occurrences for an assignment, validating each before saving."""
    assignment.full_clean()
    for occurrence in occurrences_for_cycle(assignment):
        if Occurrence.objects.filter(
            weekly_assignment=assignment,
            starts_at=occurrence.starts_at,
            status="planned",
        ).exists():
            continue
        occurrence.full_clean()
        occurrence.save(skip_revision=True)


@transaction.atomic
def create_upload_snapshot(*, device, occurrences, external_reference="", actor="owner"):
    """Create one immutable upload revision and exact occurrence snapshots."""
    selected = list(occurrences)
    if any(o.status != "planned" for o in selected):
        raise ValidationError("Only current planned occurrences may be uploaded.")
    upload = UploadedScheduleRevision.objects.create(
        device=device, external_reference=external_reference, state="active"
    )
    for occurrence in selected:
        coverage = UploadedOccurrenceCoverage(
            upload=upload, occurrence=occurrence, occurrence_revision=occurrence.revision
        )
        coverage.full_clean()
        coverage.save()
        audit("cover", "UploadedOccurrenceCoverage", coverage, f"{occurrence.label} revision {occurrence.revision}", actor)
    audit("create", "UploadedScheduleRevision", upload, f"{len(selected)} occurrence snapshots", actor)
    return upload

def schedule_alerts(occurrences):
    ordered = sorted(occurrences, key=lambda item: item.starts_at)
    alerts = []
    for previous, current in zip(ordered, ordered[1:]):
        delta = (current.starts_at - previous.ends_at).total_seconds()
        if delta < 0: alerts.append(("overlap", previous, current))
        elif delta < 60: alerts.append(("short_gap", previous, current))
    alerts.extend(("over_slot", item, None) for item in ordered if item.over_slot)
    return alerts


def calendar_capacity(station, selected_date, days=1):
    """Return reserved/available intervals in station-local time.

    Recurring slots reserve their full configured duration even when no episode
    runtime is recorded.  A shorter known runtime creates a labeled virtual
    channel filler interval inside that reservation; it never becomes available.
    """
    tz = ZoneInfo(station.timezone)
    utc = ZoneInfo("UTC")
    result = []
    for offset in range(days):
        day = selected_date + timedelta(days=offset)
        local_start = datetime.combine(day, datetime.min.time(), tzinfo=tz)
        local_end = datetime.combine(day + timedelta(days=1), datetime.min.time(), tzinfo=tz)
        start = local_start.astimezone(utc)
        end = local_end.astimezone(utc)
        reservations = []
        planned = list(Occurrence.objects.filter(
            station=station, status="planned", starts_at__lt=end,
        ).select_related("show", "episode", "asset"))
        for item in planned:
            item_start = item.starts_at.astimezone(utc)
            item_end = item.ends_at.astimezone(utc)
            if item_end > start:
                reservations.append((max(item_start, start), min(item_end, end), item, "planned"))
        slots = RecurrenceSlot.objects.filter(station=station).select_related("show")
        for slot in slots:
            candidate_days = [day]
            if slot.duration_seconds > 0:
                candidate_days.append(day - timedelta(days=1))
            for slot_day in candidate_days:
                if slot.weekday != slot_day.weekday() or (slot.active_from and slot_day < slot.active_from) or (slot.active_until and slot_day > slot.active_until):
                    continue
                local_slot_start = datetime.combine(slot_day, slot.start_time, tzinfo=tz)
                slot_start = local_slot_start.astimezone(utc)
                slot_end = slot_start + timedelta(seconds=slot.duration_seconds)
                if slot_start >= end or slot_end <= start:
                    continue
                matching = next((
                    item for item in planned
                    if item.recurrence_slot_id == slot.pk
                    and item.starts_at.astimezone(utc) == slot_start
                ), None)
                reservations.append((max(slot_start, start), min(slot_end, end), matching, "recurring"))
        reservations.sort(key=lambda value: value[0])
        merged = []
        for interval_start, interval_end, item, source in reservations:
            # Keep adjacent reservations separate so one show's filler cannot
            # consume the next reserved slot. Overlaps remain a union below.
            if merged and interval_start < merged[-1]["end"]:
                merged[-1]["end"] = max(merged[-1]["end"], interval_end)
                # The union no longer has one unambiguous content runtime.
                existing = merged[-1]["item"]
                if not (
                    existing is not None
                    and item is not None
                    and getattr(existing, "pk", None) == getattr(item, "pk", None)
                ):
                    merged[-1]["item"] = None
                    merged[-1]["source"] = "overlap"
                elif source == "recurring":
                    # A materialized recurring occurrence is also present as a
                    # planned item. Preserve its show-reservation semantics.
                    merged[-1]["source"] = "recurring"
                continue
            merged.append({"start": interval_start, "end": interval_end, "item": item, "source": source})
        intervals = []
        cursor = start
        for index, reservation in enumerate(merged):
            if cursor < reservation["start"]:
                intervals.append({"start": cursor.astimezone(tz), "end": reservation["start"].astimezone(tz), "status": "available", "label": "Available"})
            item = reservation["item"]
            runtime = None
            if item and item.episode_id:
                runtime = item.episode.runtime_seconds
            elif item and item.asset_id:
                runtime = item.asset.runtime_seconds
            # Render only the portion not already occupied by an earlier
            # reservation; availability still uses the merged union.
            filler_start = max(reservation["start"], cursor)
            if filler_start >= reservation["end"]:
                cursor = max(cursor, reservation["end"])
                continue
            if runtime is not None:
                content_end = min(reservation["end"], filler_start + timedelta(seconds=runtime))
                if filler_start < content_end:
                    intervals.append({"start": filler_start.astimezone(tz), "end": content_end.astimezone(tz), "status": "reserved", "label": "Reserved", "item": item})
                if content_end < reservation["end"]:
                    intervals.append({"start": content_end.astimezone(tz), "end": reservation["end"].astimezone(tz), "status": "virtual-channel-filler", "label": "Virtual-channel filler (reserved)", "item": item})
            elif reservation["source"] == "recurring":
                intervals.append({"start": filler_start.astimezone(tz), "end": reservation["end"].astimezone(tz), "status": "reserved", "label": "Reserved (runtime not determined)", "item": item})
            else:
                intervals.append({"start": filler_start.astimezone(tz), "end": reservation["end"].astimezone(tz), "status": "reserved", "label": "Reserved", "item": item})
            cursor = max(cursor, reservation["end"])
        if cursor < end:
            intervals.append({"start": cursor.astimezone(tz), "end": end.astimezone(tz), "status": "available", "label": "Available"})
        result.append({"date": day, "intervals": intervals})
    return result

def preparation_readiness(occurrence):
    """Return canonical preparation status with a compatibility fallback.

    This describes readiness and upload coverage only; it is never evidence that
    an item aired.
    """
    required = ["source_available", "ame_preset", "ftp_output_device", "library_registration", "slot_assignment", "uploaded_schedule_revision"]
    prep = getattr(occurrence, "preparation", None)
    asset = occurrence.asset
    asset_prep = getattr(asset, "preparation_record", None) if asset else None
    transfers = list(asset.target_transfers.all()) if asset else []
    coverage_devices = set(UploadedOccurrenceCoverage.objects.filter(
        occurrence=occurrence,
        occurrence_revision=occurrence.revision,
        upload__state="active",
    ).values_list("upload__device_id", flat=True))
    programmed_by_device = {
        row.device_id: row.slot_assignment
        for row in occurrence.programming.all()
        if row.slot_assignment
    }
    if occurrence.item_type == "live":
        values = {field: "N/A (live)" for field in required}
        values["slot_assignment"] = ""
        values["uploaded_schedule_revision"] = ""
        if prep:
            values.update({field: getattr(prep, "slot_assignment", "") for field in ("slot_assignment",)})
        values["slot_assignment"] = bool(programmed_by_device) or values["slot_assignment"]
        values["uploaded_schedule_revision"] = bool(coverage_devices)
    else:
        values = {field: "" for field in required}
        values["source_available"] = getattr(asset_prep, "source_available", "") if asset_prep else ""
        values["ame_preset"] = getattr(asset_prep, "ame_preset", "") if asset_prep else ""
        complete_transfer_devices = {
            transfer.device_id for transfer in transfers
            if transfer.ftp_details and transfer.library_registration
        }
        # Legacy transfer text can still participate only when it names the
        # same concrete device used by programming and upload coverage.
        if not complete_transfer_devices and prep and prep.legacy_ftp_output_device and prep.library_registration:
            legacy_name = prep.legacy_ftp_output_device.strip()
            complete_transfer_devices = set(
                occurrence.programming.filter(device__name=legacy_name, slot_assignment__gt="")
                .values_list("device_id", flat=True)
            )
        chain_devices = complete_transfer_devices & set(programmed_by_device) & coverage_devices
        chain_ready = bool(chain_devices)
        transfer = next((item for item in transfers if item.device_id in chain_devices), None)
        values["ftp_output_device"] = transfer.ftp_details if transfer else (getattr(prep, "legacy_ftp_output_device", "") if prep else "")
        values["library_registration"] = transfer.library_registration if transfer else (getattr(prep, "library_registration", "") if prep else "")
        values["slot_assignment"] = bool(chain_ready)
        values["uploaded_schedule_revision"] = bool(chain_ready)
        if prep:
            # Legacy records remain usable until their canonical facts are entered.
            values["source_available"] = values["source_available"] or prep.source_available
            values["ame_preset"] = values["ame_preset"] or prep.ame_preset
            if not chain_ready:
                values["slot_assignment"] = ""
                values["uploaded_schedule_revision"] = ""
    complete = sum(1 for value in values.values() if value not in ("", None, "no", False))
    return {"complete": complete, "total": len(required), "label": f"{complete}/{len(required)} preparation facts", "ready": complete == len(required)}

@transaction.atomic
def advance_passed_premiere(occurrence, now=None):
    """Classify a passed premiere only with exact active upload coverage."""
    now = now or timezone.now()
    if occurrence.item_type != "episode" or occurrence.status != "planned" or occurrence.starts_at >= now:
        return False
    if not occurrence.episode or occurrence.episode.status != "pending": return False
    local_date = timezone.localtime(occurrence.starts_at, ZoneInfo(occurrence.station.timezone)).date()
    week_start = local_date - timedelta(days=local_date.weekday())
    assignment = occurrence.weekly_assignment
    if not assignment or assignment.show_id != occurrence.episode.show_id or assignment.episode_id != occurrence.episode_id or assignment.week_start != week_start or assignment.selection_type != "premiere": return False
    covered = any(
        coverage.occurrence.programming.filter(device_id=coverage.upload.device_id).exists()
        for coverage in UploadedOccurrenceCoverage.objects.filter(
            upload__state="active", occurrence=occurrence, occurrence_revision=occurrence.revision
        ).select_related("upload", "occurrence")
    )
    if not covered: return False
    return Episode.objects.filter(pk=occurrence.episode_id, status="pending").update(status="previously_scheduled") == 1
