import django.utils.timezone
from django.db import migrations, models
import django.db.models.deletion
import uuid


def backfill_asset_identity(apps, schema_editor):
    Asset = apps.get_model("operations", "MediaAsset")
    Item = apps.get_model("operations", "PreparationBatchItem")
    # Only retained exact input evidence may seed source identity. Prepared
    # output inspections belong to renditions and cannot identify source bytes.
    for asset in Asset.objects.all().iterator():
        candidates = set(Item.objects.filter(asset_id=asset.pk).exclude(
            selected_input_path="").exclude(selected_input_hash="").values_list(
                "selected_input_path", "selected_input_hash"))
        if len(candidates) == 1:
            path, digest = candidates.pop()
            if path.startswith("/") and len(path) <= 500 and len(digest) == 64:
                Asset.objects.filter(pk=asset.pk, local_path="", content_identity="").update(
                    local_path=path, content_identity=digest)


class Migration(migrations.Migration):
    dependencies = [("operations", "0029_library_identity_metadata")]
    operations = [migrations.CreateModel(
        name="MediaRelinkReview",
        fields=[
            ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
            ("token", models.UUIDField(default=uuid.uuid4, editable=False, unique=True)),
            ("proposed_path", models.CharField(max_length=500)),
            ("file_size", models.PositiveBigIntegerField(default=0)),
            ("mtime_ns", models.BigIntegerField(default=0)),
            ("observed_hash", models.CharField(blank=True, max_length=128)),
            ("original_path", models.CharField(blank=True, max_length=500)),
            ("original_identity", models.CharField(blank=True, max_length=128)),
            ("error", models.TextField(blank=True)),
            ("status", models.CharField(choices=[(x, x.replace("_", " ").title()) for x in ("queued", "checking", "matched", "new_version", "confirm_queued", "confirming", "failed", "confirmed", "cancelled")], default="queued", max_length=16)),
            ("created_at", models.DateTimeField(default=django.utils.timezone.now)),
            ("actor", models.CharField(default="owner", max_length=120)),
            ("asset", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="relink_reviews", to="operations.mediaasset")),
        ],
    ),
        migrations.AddField(model_name="preparationbatchitem", name="reviewed_file_size", field=models.PositiveBigIntegerField(blank=True, null=True)),
        migrations.AddField(model_name="preparationbatchitem", name="reviewed_mtime_ns", field=models.BigIntegerField(blank=True, null=True)),
        migrations.RunPython(backfill_asset_identity, migrations.RunPython.noop)]
