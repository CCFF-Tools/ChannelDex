from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("operations", "0027_alter_publicationjob_kind")]

    operations = [
        migrations.AddField(
            model_name="preparationbatch",
            name="removed_from_list",
            field=models.BooleanField(default=False),
        ),
    ]
