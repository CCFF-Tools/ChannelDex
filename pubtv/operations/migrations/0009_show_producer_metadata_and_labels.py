from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("operations", "0008_media_asset_identity")]

    operations = [
        migrations.RenameField(
            model_name="show",
            old_name="producer_contact",
            new_name="legacy_producer_contact",
        ),
        migrations.AddField(
            model_name="show",
            name="description",
            field=models.TextField(blank=True),
        ),
        migrations.AddField(
            model_name="show",
            name="producer_email",
            field=models.EmailField(blank=True, max_length=254),
        ),
        migrations.AddField(
            model_name="show",
            name="producer_phone",
            field=models.CharField(blank=True, max_length=80),
        ),
        migrations.RenameField(
            model_name="producer",
            old_name="contact",
            new_name="legacy_contact",
        ),
        migrations.AddField(
            model_name="producer",
            name="email",
            field=models.EmailField(blank=True, max_length=254),
        ),
        migrations.AddField(
            model_name="producer",
            name="phone",
            field=models.CharField(blank=True, max_length=80),
        ),
    ]
