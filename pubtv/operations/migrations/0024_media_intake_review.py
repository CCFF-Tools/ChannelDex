from django.db import migrations, models
import django.db.models.deletion
import uuid
from django.utils import timezone


class Migration(migrations.Migration):
    dependencies = [("operations", "0023_cancelled_publication_delivery")]

    operations = [migrations.CreateModel(
        name="MediaIntakeReview",
        fields=[
            ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
            ("token", models.UUIDField(default=uuid.uuid4, editable=False, unique=True)),
            ("order", models.PositiveIntegerField(default=0)),
            ("action", models.CharField(default="prepare_only", max_length=32)),
            ("payload", models.JSONField(blank=True, default=dict)),
            ("status", models.CharField(choices=[("pending", "Pending"), ("confirmed", "Confirmed"), ("cancelled", "Cancelled")], default="pending", max_length=12)),
            ("created_at", models.DateTimeField(default=timezone.now)),
            ("created_by", models.CharField(default="owner", max_length=120)),
            ("asset", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name="media_intake_reviews", to="operations.mediaasset")),
            ("episode", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name="media_intake_reviews", to="operations.episode")),
            ("show", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="media_intake_reviews", to="operations.show")),
            ("target", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="media_intake_reviews", to="operations.device")),
        ],
        options={"ordering": ["order", "pk"]},
    )]
