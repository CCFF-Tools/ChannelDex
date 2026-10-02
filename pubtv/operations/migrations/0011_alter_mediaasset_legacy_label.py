from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("operations", "0010_canonical_data_foundation")]

    operations = [
        migrations.AlterField(
            model_name="mediaasset",
            name="legacy_label",
            field=models.CharField(blank=True, max_length=160),
        ),
    ]
