from django.db import migrations, models
import django.db.models.deletion


def backfill_device_station(apps, schema_editor):
    Station = apps.get_model("operations", "Station")
    Device = apps.get_model("operations", "Device")
    station = Station.objects.filter(name="PUB-TV").order_by("pk").first()
    if station is None:
        if not Device.objects.filter(station__isnull=True).exists():
            return
        station = Station.objects.create(name="PUB-TV", timezone="America/Detroit")
    Device.objects.filter(station__isnull=True).update(station=station)


class Migration(migrations.Migration):
    dependencies = [("operations", "0030_media_relink_review")]
    operations = [
        migrations.AddField(model_name="device", name="station", field=models.ForeignKey(null=True, on_delete=django.db.models.deletion.CASCADE, related_name="devices", to="operations.station")),
        migrations.RunPython(backfill_device_station, migrations.RunPython.noop),
        migrations.AlterField(model_name="device", name="station", field=models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="devices", to="operations.station")),
        migrations.AlterField(model_name="device", name="name", field=models.CharField(max_length=120)),
        migrations.AddConstraint(model_name="device", constraint=models.UniqueConstraint(fields=("station", "name"), name="unique_station_device_name")),
    ]
