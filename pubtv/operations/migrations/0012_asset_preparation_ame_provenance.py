from django.db import migrations, models


def copy_existing_ame_provenance(apps, schema_editor):
    AssetPreparation = apps.get_model("operations", "AssetPreparation")
    for record in AssetPreparation.objects.all().iterator():
        record.ame_at = record.encoded_at
        record.ame_actor = record.encoded_actor
        record.ame_provenance = record.encoded_provenance
        record.save(update_fields=["ame_at", "ame_actor", "ame_provenance"])


class Migration(migrations.Migration):
    dependencies = [("operations", "0011_alter_mediaasset_legacy_label")]

    operations = [
        migrations.AddField(model_name="assetpreparation", name="ame_at", field=models.DateTimeField(blank=True, null=True)),
        migrations.AddField(model_name="assetpreparation", name="ame_actor", field=models.CharField(blank=True, max_length=120)),
        migrations.AddField(model_name="assetpreparation", name="ame_provenance", field=models.TextField(blank=True)),
        migrations.RunPython(copy_existing_ame_provenance, migrations.RunPython.noop),
    ]
