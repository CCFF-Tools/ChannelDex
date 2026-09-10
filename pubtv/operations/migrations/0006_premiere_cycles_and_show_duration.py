from datetime import timedelta

from django.db import migrations, models


def seed_cycle_and_duration(apps, schema_editor):
    Show = apps.get_model("operations", "Show")
    WeeklyEpisodeAssignment = apps.get_model("operations", "WeeklyEpisodeAssignment")

    for show in Show.objects.all():
        first_slot = show.slots.order_by("pk").first()
        if first_slot:
            show.slot_duration_seconds = first_slot.duration_seconds
            show.save(update_fields=["slot_duration_seconds"])

    for assignment in WeeklyEpisodeAssignment.objects.select_related("show"):
        premiere_slot = assignment.show.slots.filter(is_premiere=True).order_by("pk").first()
        offset = premiere_slot.weekday if premiere_slot else 0
        assignment.premiere_date = assignment.week_start + timedelta(days=offset)
        assignment.save(update_fields=["premiere_date"])


class Migration(migrations.Migration):
    dependencies = [("operations", "0005_ux_workflows")]

    operations = [
        migrations.AddField(
            model_name="show",
            name="slot_duration_seconds",
            field=models.PositiveIntegerField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="weeklyepisodeassignment",
            name="premiere_date",
            field=models.DateField(blank=True, null=True),
        ),
        migrations.RunPython(seed_cycle_and_duration, migrations.RunPython.noop),
    ]
