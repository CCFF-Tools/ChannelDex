import uuid
from datetime import timedelta
from zoneinfo import ZoneInfo

import django.db.models.deletion
import django.utils.timezone
from django.db import migrations, models


def backfill_audit_stations(apps, schema_editor):
    AuditEvent = apps.get_model("operations", "AuditEvent")
    def owner(obj, seen):
        if obj is None or (obj._meta.label_lower, obj.pk) in seen:
            return None
        seen.add((obj._meta.label_lower, obj.pk))
        if obj._meta.model_name == 'station':
            return obj.pk
        if obj._meta.model_name == 'delivery':
            stations = {owner(episode, seen.copy()) for episode in obj.episodes.all()}
            stations.discard(None)
            return stations.pop() if len(stations) == 1 else None
        if getattr(obj, 'station_id', None):
            return obj.station_id
        for name in ('show', 'episode', 'occurrence', 'asset', 'device', 'target',
                     'upload', 'batch', 'publication_batch', 'preparation_batch',
                     'preparation_item', 'item', 'assignment', 'delivery'):
            if getattr(obj, name + '_id', None):
                result = owner(getattr(obj, name), seen)
                if result:
                    return result
        return None
    for event in AuditEvent.objects.filter(station__isnull=True).iterator():
        try:
            model = apps.get_model('operations', {'EpisodeQueue': 'Show'}.get(event.entity, event.entity))
        except LookupError:
            continue
        station_id = owner(model.objects.filter(pk=event.entity_id).first(), set()) if event.entity_id else None
        if station_id:
            event.station_id = station_id
            event.save(update_fields=["station"])


def backfill_occurrence_cycle_identity(apps, schema_editor):
    """Identify legacy recurring rows without moving or rewriting evidence."""
    Occurrence = apps.get_model("operations", "Occurrence")
    for occurrence in Occurrence.objects.filter(recurrence_slot__isnull=False).select_related(
        "recurrence_slot", "weekly_assignment", "station"
    ).iterator():
        slot = occurrence.recurrence_slot
        assignment = occurrence.weekly_assignment
        changed = []
        if assignment:
            premiere = assignment.premiere_date
            if premiere is None:
                premiere = assignment.week_start + timedelta(days=slot.weekday)
            expected_date = premiere + timedelta(days=(slot.weekday - premiere.weekday()) % 7)
            occurrence.cycle_key = (
                f"assignment:{assignment.pk}:slot:{slot.pk}:date:{expected_date.isoformat()}"
            )
            try:
                local_start = occurrence.starts_at.astimezone(ZoneInfo(occurrence.station.timezone))
            except (ValueError, KeyError):
                local_start = occurrence.starts_at
            if local_start.date() != expected_date or local_start.time().replace(tzinfo=None) != slot.start_time:
                changed.append("time")
            if occurrence.episode_id != assignment.episode_id:
                changed.append("episode")
            if occurrence.status != "planned":
                changed.append("status")
        else:
            occurrence.cycle_key = f"legacy:slot:{slot.pk}:occurrence:{occurrence.pk}"
            changed.append("legacy one-off")
        if changed:
            occurrence.is_exception = True
            occurrence.exception_reason = "Legacy exception: " + ", ".join(changed)
        occurrence.save(update_fields=["cycle_key", "is_exception", "exception_reason"])


class Migration(migrations.Migration):
    dependencies = [("operations", "0033_catalog_intake_submission")]  # noqa: RUF012

    operations = [  # noqa: RUF012
        migrations.AddField("show", "archived_at", models.DateTimeField(blank=True, null=True)),
        migrations.AddField("episode", "archived_at", models.DateTimeField(blank=True, null=True)),
        migrations.AddField("occurrence", "cycle_key", models.CharField(blank=True, default="", max_length=160)),
        migrations.AddField("occurrence", "is_exception", models.BooleanField(default=False)),
        migrations.AddField("occurrence", "exception_reason", models.CharField(blank=True, default="", max_length=80)),
        migrations.AddField("occurrence", "exception_of", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name="schedule_exceptions", to="operations.occurrence")),
        migrations.AddField("auditevent", "station", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name="audit_events", to="operations.station")),
        migrations.CreateModel(
            name="MediaIntakeDraft",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("token", models.UUIDField(default=uuid.uuid4, editable=False, unique=True)),
                ("payload", models.JSONField(default=dict)),
                ("state", models.CharField(default="draft", max_length=32)),
                ("revision", models.PositiveIntegerField(default=1)),
                ("created_at", models.DateTimeField(default=django.utils.timezone.now)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("station", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="media_intake_drafts", to="operations.station")),
            ],
        ),
        migrations.RunPython(backfill_audit_stations, migrations.RunPython.noop),
        migrations.RunPython(backfill_occurrence_cycle_identity, migrations.RunPython.noop),
    ]
