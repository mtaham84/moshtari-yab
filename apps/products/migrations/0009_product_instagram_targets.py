from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("products", "0008_alter_product_url")]

    operations = [
        migrations.AddField(model_name="product", name="instagram_terms", field=models.JSONField(blank=True, default=list, verbose_name="واژه‌های مرتبط اینستاگرام")),
        migrations.AddField(model_name="product", name="instagram_hashtags", field=models.JSONField(blank=True, default=list, verbose_name="هشتگ‌های هدف اینستاگرام")),
        migrations.AddField(model_name="product", name="instagram_discovery_enabled", field=models.BooleanField(default=False, verbose_name="پایش هشتگی اینستاگرام")),
        migrations.AddField(model_name="product", name="instagram_max_posts", field=models.PositiveSmallIntegerField(default=5, verbose_name="سقف پست هشتگی اینستاگرام")),
        migrations.AddField(model_name="product", name="instagram_window_days", field=models.PositiveSmallIntegerField(default=7, verbose_name="بازهٔ پست هشتگی اینستاگرام (روز)")),
    ]
