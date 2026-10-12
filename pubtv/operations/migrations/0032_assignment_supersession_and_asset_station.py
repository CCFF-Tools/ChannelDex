from django.db import migrations, models
import django.db.models.deletion


def populate_asset_stations(apps, schema_editor):
    MediaAsset = apps.get_model("operations", "MediaAsset")
    for asset in MediaAsset.objects.filter(episode__isnull=False).select_related("episode__show"):
        asset.station_id = asset.episode.show.station_id
        asset.save(update_fields=["station"])


class Migration(migrations.Migration):
    dependencies = [("operations", "0031_device_station")]

    operations = [
        migrations.AddField(
            model_name="mediaasset",
            name="station",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="media_assets",
                to="operations.station",
            ),
        ),
        migrations.RunPython(
            populate_asset_stations,
            migrations.RunPython.noop,
        ),
        migrations.RemoveConstraint(
            model_name="weeklyepisodeassignment",
            name="unique_show_week",
        ),
        migrations.AddField(
            model_name="weeklyepisodeassignment",
            name="state",
            field=models.CharField(
                choices=[("active", "Active"), ("superseded", "Superseded")],
                default="active",
                max_length=16,
            ),
        ),
        migrations.AddField(
            model_name="weeklyepisodeassignment",
            name="superseded_by",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="superseded_assignments",
                to="operations.weeklyepisodeassignment",
            ),
        ),
        migrations.AddConstraint(
            model_name="weeklyepisodeassignment",
            constraint=models.UniqueConstraint(
                condition=models.Q(("state", "active")),
                fields=("show", "week_start"),
                name="unique_active_show_week",
            ),
        ),
    ]
