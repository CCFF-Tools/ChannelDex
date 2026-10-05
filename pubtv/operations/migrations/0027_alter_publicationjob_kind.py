from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("operations", "0026_guided_episode_delivery")]

    operations = [
        migrations.AlterField(
            model_name="publicationjob",
            name="kind",
            field=models.CharField(choices=[("preview", "Preview"), ("prepare_publication", "Prepare Publication"), ("generate_nmg", "Generate Nmg"), ("generate_bin", "Generate Bin"), ("guided_delivery", "Guided Delivery"), ("deliver", "Deliver"), ("verify", "Verify"), ("reconcile", "Reconcile")], max_length=24),
        ),
    ]
