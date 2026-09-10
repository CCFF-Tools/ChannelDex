from django.db import migrations, models
import django.db.models.deletion
import django.utils.timezone


def mark_existing_premiere_slots(apps, schema_editor):
    RecurrenceSlot = apps.get_model("operations", "RecurrenceSlot")
    show_ids = (
        RecurrenceSlot.objects.exclude(show_id=None)
        .values_list("show_id", flat=True)
        .distinct()
    )
    for show_id in show_ids:
        first = RecurrenceSlot.objects.filter(show_id=show_id).order_by("pk").first()
        if first:
            first.is_premiere = True
            first.save(update_fields=["is_premiere"])


class Migration(migrations.Migration):
    dependencies = [("operations", "0004_show_metadata_delivery_methods")]

    operations = [
        migrations.CreateModel(
            name="Producer",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("first_name", models.CharField(max_length=80)),
                ("last_name", models.CharField(max_length=80)),
                ("contact", models.CharField(blank=True, max_length=240)),
                ("external_membership_number", models.CharField(blank=True, max_length=120)),
                ("notes", models.TextField(blank=True)),
                ("created_at", models.DateTimeField(default=django.utils.timezone.now)),
                ("station", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="producers", to="operations.station")),
            ],
            options={"ordering": ["last_name", "first_name", "pk"]},
        ),
        migrations.RenameField(
            model_name="episode",
            old_name="producer_id",
            new_name="legacy_producer_reference",
        ),
        migrations.AddField(
            model_name="episode",
            name="producer",
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name="episodes", to="operations.producer"),
        ),
        migrations.AddField(
            model_name="recurrenceslot",
            name="is_premiere",
            field=models.BooleanField(default=False),
        ),
        migrations.RunPython(mark_existing_premiere_slots, migrations.RunPython.noop),
        migrations.AddField(
            model_name="occurrence",
            name="recurrence_slot",
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="occurrences", to="operations.recurrenceslot"),
        ),
        migrations.AddField(
            model_name="occurrence",
            name="schedule_role",
            field=models.CharField(choices=[("manual", "Manual schedule item"), ("premiere", "New premiere"), ("replay", "Replay"), ("rerun", "Selected older episode")], default="manual", max_length=16),
        ),
        migrations.CreateModel(
            name="EpisodeWorkflowMilestone",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("stage", models.CharField(choices=[("received", "Received"), ("downloaded", "Downloaded"), ("encoded", "Encoded"), ("transferred", "Transferred"), ("scheduled", "Scheduled")], max_length=16)),
                ("completed_at", models.DateTimeField(default=django.utils.timezone.now)),
                ("actor", models.CharField(default="owner", max_length=120)),
                ("provenance", models.CharField(default="manual", max_length=200)),
                ("notes", models.TextField(blank=True)),
                ("episode", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="workflow_milestones", to="operations.episode")),
            ],
            options={"ordering": ["completed_at", "pk"]},
        ),
        migrations.AlterField(
            model_name="recurrenceslot",
            name="weekday",
            field=models.PositiveSmallIntegerField(choices=[(0, "Monday"), (1, "Tuesday"), (2, "Wednesday"), (3, "Thursday"), (4, "Friday"), (5, "Saturday"), (6, "Sunday")]),
        ),
    ]
