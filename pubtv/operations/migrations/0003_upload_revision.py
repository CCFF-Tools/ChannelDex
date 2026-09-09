from django.db import migrations, models

class Migration(migrations.Migration):
    dependencies = [("operations", "0002_preparation_revision")]
    operations = [migrations.AddField(
        model_name="uploadedschedulerevision",
        name="revision",
        field=models.PositiveIntegerField(default=1),
    )]
