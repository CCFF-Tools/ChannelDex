from django.db import migrations, models

class Migration(migrations.Migration):
    dependencies = [("operations", "0006_premiere_cycles_and_show_duration")]
    operations = [
        migrations.AddField(model_name="show", name="show_type", field=models.CharField(blank=True, choices=[("arts_and_culture", "Arts and Culture"), ("religious", "Religious"), ("opinion", "Opinion"), ("public_affairs", "Public Affairs"), ("government", "Government"), ("education", "Education"), ("community", "Community"), ("entertainment", "Entertainment"), ("sports", "Sports"), ("other", "Other")], max_length=24)),
        migrations.AddField(model_name="show", name="show_type_other", field=models.CharField(blank=True, max_length=160)),
    ]
