from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("discovery", "0023_twitterengineaccount"),
    ]

    operations = [
        migrations.AddField(
            model_name="instagramengineaccount",
            name="status_message",
            field=models.CharField(blank=True, default="", max_length=255, verbose_name="پیام آخرین وضعیت / خطا"),
        ),
    ]
