from django.db import migrations, models
import django.db.models.deletion
import django.utils.timezone
import uuid


class Migration(migrations.Migration):
    dependencies = [("operations", "0025_fresh_controller_bin")]

    operations = [
        migrations.CreateModel(
            name="GuidedEpisodeDelivery",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("token", models.UUIDField(default=uuid.uuid4, editable=False, unique=True)),
                ("state", models.CharField(choices=[("intake_review", "Intake Review"), ("intake_confirmed", "Intake Confirmed"), ("media_queued", "Media Queued"), ("media_ready", "Media Ready"), ("cycle_planned", "Cycle Planned"), ("controller_pulled", "Controller Pulled"), ("change_review", "Change Review"), ("approval_2", "Approval 2"), ("staged", "Staged"), ("delivered", "Delivered"), ("verification_pending", "Verification Pending"), ("verified", "Verified"), ("blocked", "Blocked"), ("rolled_back", "Rolled Back"), ("cancelled", "Cancelled")], default="intake_review", max_length=24)),
                ("intake_snapshot", models.JSONField(blank=True, default=dict)),
                ("intake_hash", models.CharField(blank=True, max_length=64)),
                ("change_review", models.JSONField(blank=True, default=dict)),
                ("change_review_hash", models.CharField(blank=True, max_length=64)),
                ("candidate_hash", models.CharField(blank=True, max_length=128)),
                ("current_hash", models.CharField(blank=True, max_length=128)),
                ("rollback_hash", models.CharField(blank=True, max_length=128)),
                ("activation_count", models.PositiveSmallIntegerField(default=0)),
                ("verification_prompted", models.BooleanField(default=False)),
                ("last_error", models.TextField(blank=True)),
                ("created_at", models.DateTimeField(default=django.utils.timezone.now)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("created_by", models.CharField(default="owner", max_length=120)),
                ("asset", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="guided_deliveries", to="operations.mediaasset")),
                ("episode", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="guided_deliveries", to="operations.episode")),
                ("preparation_batch", models.OneToOneField(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name="guided_delivery", to="operations.preparationbatch")),
                ("publication_batch", models.OneToOneField(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name="guided_delivery", to="operations.schedulepublicationbatch")),
                ("cycle_assignment", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name="guided_deliveries", to="operations.weeklyepisodeassignment")),
                ("show", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="guided_episode_deliveries", to="operations.show")),
                ("target", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="guided_episode_deliveries", to="operations.device")),
            ],
        ),
        migrations.AddConstraint(
            model_name="guidedepisodedelivery",
            constraint=models.UniqueConstraint(condition=models.Q(state__in=("intake_review", "intake_confirmed", "media_queued", "media_ready", "cycle_planned", "controller_pulled", "change_review", "approval_2", "staged", "delivered", "verification_pending")), fields=("target", "episode"), name="unique_active_guided_episode_delivery"),
        ),
    ]
