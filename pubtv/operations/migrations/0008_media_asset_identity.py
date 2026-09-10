import uuid
from django.db import migrations, models


def copy_existing_labels(apps, schema_editor):
    MediaAsset = apps.get_model("operations", "MediaAsset")
    for asset in MediaAsset.objects.filter(file_name=""):
        asset.file_name = asset.label
        asset.save(update_fields=["file_name"])


def assign_asset_ids(apps, schema_editor):
    MediaAsset = apps.get_model("operations", "MediaAsset")
    for asset in MediaAsset.objects.filter(asset_id__isnull=True):
        asset.asset_id = uuid.uuid4()
        asset.save(update_fields=["asset_id"])


class Migration(migrations.Migration):
    dependencies = [("operations", "0007_show_type")]
    operations = [
        migrations.AddField(
            model_name="mediaasset", name="asset_id",
            field=models.UUIDField(null=True, editable=False),
        ),
        migrations.AddField(
            model_name="mediaasset", name="file_name",
            field=models.CharField(blank=True, max_length=255),
        ),
        migrations.RunPython(copy_existing_labels, migrations.RunPython.noop),
        migrations.RunPython(assign_asset_ids, migrations.RunPython.noop),
        migrations.AlterField(
            model_name="mediaasset", name="asset_id",
            field=models.UUIDField(default=uuid.uuid4, editable=False, unique=True),
        ),
    ]
