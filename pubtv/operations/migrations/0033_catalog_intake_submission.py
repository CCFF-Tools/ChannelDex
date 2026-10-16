from django.db import migrations, models
import django.db.models.deletion
import django.utils.timezone


class Migration(migrations.Migration):
    dependencies = [("operations", "0032_assignment_supersession_and_asset_station")]

    operations = [
        migrations.CreateModel(
            name="CatalogIntakeSubmission",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("token", models.CharField(max_length=120)),
                ("payload_fingerprint", models.CharField(max_length=64)),
                ("created_at", models.DateTimeField(default=django.utils.timezone.now)),
                ("asset", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name="catalog_intake_submissions", to="operations.mediaasset")),
                ("station", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="catalog_intake_submissions", to="operations.station")),
            ],
            options={
                "constraints": [models.UniqueConstraint(fields=("station", "token"), name="unique_catalog_intake_station_token")],
            },
        ),
    ]
