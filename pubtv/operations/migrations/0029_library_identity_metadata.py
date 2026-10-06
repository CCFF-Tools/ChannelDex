from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("operations", "0028_preparationbatch_removed_from_list")]

    operations = [
        migrations.AddField(
            model_name="episode", name="episode_number",
            field=models.PositiveIntegerField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="episode", name="runtime_provenance",
            field=models.CharField(blank=True, max_length=32),
        ),
        migrations.AddField(
            model_name="mediaasset", name="runtime_provenance",
            field=models.CharField(blank=True, max_length=32),
        ),
        migrations.AddField(
            model_name="mediaasset", name="local_path",
            field=models.CharField(blank=True, max_length=500),
        ),
        migrations.AddField(
            model_name="mediaasset", name="content_identity",
            field=models.CharField(blank=True, max_length=128),
        ),
    ]
