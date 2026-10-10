from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("discovery", "0010_monitoredcommunity_crawler_sync")]

    operations = [migrations.AddField(
        model_name="opportunityproductmatch",
        name="reply_variants",
        field=models.JSONField(blank=True, null=True, verbose_name="پیشنویس‌های پاسخ X"),
    )]
