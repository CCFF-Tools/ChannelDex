from django.db import migrations, models
import django.db.models.deletion


def stale_unverified_approvals(apps, schema_editor):
    Batch = apps.get_model("operations", "SchedulePublicationBatch")
    (Batch.objects.filter(approval_2_status="approved")
          .exclude(status__in=("verified", "cancelled"))
          .update(approval_2_status="stale", status="blocked"))


class Migration(migrations.Migration):
    dependencies = [("operations", "0024_media_intake_review")]

    operations = [
        migrations.AlterField("publicationjob", "kind", models.CharField(choices=[("preview", "Preview"), ("prepare_publication", "Prepare Publication"), ("generate_bin", "Generate Bin"), ("deliver", "Deliver"), ("verify", "Verify"), ("reconcile", "Reconcile")], max_length=24)),
        migrations.AddField("station", "auto_pull_controller_schedule", models.BooleanField(default=True)),
        migrations.AddField("controllersnapshot", "trigger", models.CharField(choices=[("automatic", "Automatic"), ("manual", "Manual")], default="manual", max_length=16)),
        migrations.AddField("controllersnapshot", "actor", models.CharField(default="owner", max_length=120)),
        migrations.AddField("controllersnapshot", "settings_revision", models.PositiveIntegerField(blank=True, null=True)),
        migrations.AddField("controllersnapshot", "settings_hash", models.CharField(blank=True, max_length=128)),
        migrations.AddField("controllersnapshot", "target_path", models.CharField(default="/internal/schedule/schedule.bin", max_length=500)),
        migrations.AddField("controllersnapshot", "source_bytes", models.PositiveBigIntegerField(default=0)),
        migrations.AddField("controllersnapshot", "qualification", models.JSONField(blank=True, default=dict)),
        migrations.AddField("schedulepublicationbatch", "controller_snapshot", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name="publication_batches", to="operations.controllersnapshot")),
        migrations.AddField("schedulepublicationbatch", "controller_snapshot_hash", models.CharField(blank=True, max_length=128)),
        migrations.AddField("schedulepublicationbatch", "mutation_plan", models.JSONField(blank=True, default=dict)),
        migrations.AddField("schedulepublicationbatch", "mutation_plan_hash", models.CharField(blank=True, max_length=128)),
        migrations.AddField("schedulepublicationbatch", "review_diff", models.JSONField(blank=True, default=dict)),
        migrations.AddField("schedulepublicationbatch", "preparation_kind", models.CharField(default="publication", max_length=24)),
        migrations.AddField("artifactrevision", "scope", models.CharField(default="change_set", max_length=24)),
        migrations.AddField("artifactrevision", "base_controller_bin_hash", models.CharField(blank=True, max_length=128)),
        migrations.RunPython(stale_unverified_approvals, migrations.RunPython.noop),
    ]
