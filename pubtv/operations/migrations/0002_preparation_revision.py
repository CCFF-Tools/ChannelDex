from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("operations", "0001_initial")]

    operations = [
        migrations.AddField(
            model_name="preparation",
            name="revision",
            field=models.PositiveIntegerField(default=1),
        ),
    ]
