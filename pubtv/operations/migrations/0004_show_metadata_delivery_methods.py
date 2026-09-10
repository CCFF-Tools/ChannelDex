from django.db import migrations, models

def migrate_legacy_email(apps, schema_editor):
    Delivery = apps.get_model("operations", "Delivery")
    Delivery.objects.filter(method="email").update(method="email_link")
    Delivery.objects.filter(method="smb").update(method="smb_transfer")

def reverse_legacy_methods(apps, schema_editor):
    Delivery = apps.get_model("operations", "Delivery")
    Delivery.objects.filter(method="email_link").update(method="email")
    Delivery.objects.filter(method="smb_transfer").update(method="smb")

class Migration(migrations.Migration):
    dependencies = [("operations", "0003_upload_revision")]
    operations = [
        migrations.AddField(model_name="show", name="website", field=models.URLField(blank=True)),
        migrations.AddField(model_name="show", name="youtube", field=models.URLField(blank=True)),
        migrations.AddField(model_name="show", name="facebook", field=models.URLField(blank=True)),
        migrations.AddField(model_name="show", name="instagram", field=models.URLField(blank=True)),
        migrations.AddField(model_name="show", name="primary_delivery_method", field=models.CharField(blank=True, choices=[("google_drive", "Google Drive"), ("dropbox", "Dropbox"), ("email_link", "Email link"), ("smb_transfer", "SMB Transfer"), ("other", "Other")], max_length=20)),
        migrations.AddField(model_name="show", name="primary_delivery_other", field=models.CharField(blank=True, max_length=160)),
        migrations.AddField(model_name="delivery", name="other_method", field=models.CharField(blank=True, max_length=160)),
        migrations.RunPython(migrate_legacy_email, reverse_legacy_methods),
        migrations.AlterField(model_name="delivery", name="method", field=models.CharField(choices=[("google_drive", "Google Drive"), ("dropbox", "Dropbox"), ("email_link", "Email link"), ("smb_transfer", "SMB Transfer"), ("other", "Other")], max_length=20)),
    ]
