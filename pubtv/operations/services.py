from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from django.db import transaction, connection
from django.core.exceptions import ValidationError
from django.utils import timezone
from django.core.signing import TimestampSigner, BadSignature
from .models import Occurrence, Episode, RecurrenceSlot, UploadedScheduleRevision, UploadedOccurrenceCoverage, WeeklyEpisodeAssignment, Preparation, AiringEvidence, AuditEvent

def audit(action, entity, obj=None, summary="", actor="owner"):
    return AuditEvent.objects.create(actor=actor, action=action, entity=entity, entity_id=getattr(obj, "pk", None), revision=getattr(obj, "revision", None), summary=summary)

def confirm_preparation_fact(preparation: Preparation, fact, value, actor="owner", provenance="manual", expected_revision=None):
    """Record one of the six checklist facts with independent provenance."""
    fields = {"source_available": "source_available", "ame": "ame_preset", "ftp": "ftp_output_device", "library": "library_registration", "slot": "slot_assignment", "upload": "uploaded_schedule_revision"}
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
                    result.append(Occurrence(station=slot.station, show=slot.show, episode=assignment.episode, weekly_assignment=assignment, item_type="episode", label=slot.show.title, starts_at=starts_at, planned_duration_seconds=slot.duration_seconds))
            else:
                result.append(Occurrence(station=slot.station, show=None, episode=None, weekly_assignment=None, item_type="media", label="Recurring slot", starts_at=starts_at, planned_duration_seconds=slot.duration_seconds))
        day += timedelta(days=1)
    return result


@transaction.atomic
def materialize_assignment(assignment):
    """Create all occurrences for an assignment, validating each before saving."""
    assignment.full_clean()
    slots = RecurrenceSlot.objects.filter(show=assignment.show, station=assignment.show.station)
    start = assignment.week_start
    for slot in slots:
        for occurrence in occurrences_for_slot(slot, start, start + timedelta(days=6)):
            if Occurrence.objects.filter(weekly_assignment=assignment, starts_at=occurrence.starts_at).exists():
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
