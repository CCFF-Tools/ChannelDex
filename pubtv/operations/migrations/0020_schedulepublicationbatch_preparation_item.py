from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [("operations", "0019_ultranexus_target_qualification")]

    operations = [
        migrations.AddField(
            model_name="schedulepublicationbatch",
            name="preparation_item",
            field=models.OneToOneField(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name="schedule_publication", to="operations.preparationbatchitem"),
        ),
    ]
