import django.core.validators
from django.db import migrations, models


class Migration(migrations.Migration):

    initial = True
    dependencies = []

    operations = [
        migrations.CreateModel(
            name="SiteAppearance",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("background_image", models.ImageField(blank=True, null=True, upload_to="site/", verbose_name="تصویر پس\u200cزمینه پنل و داشبوردها", help_text="ترجیحاً افقی و باکیفیت (حداقل ۱۹۲۰×۱۰۸۰). اگر خالی باشد تصویر پیش\u200cفرض استفاده می\u200cشود.")),
                ("overlay_percent", models.PositiveSmallIntegerField(default=20, validators=[django.core.validators.MaxValueValidator(80)], verbose_name="تیرگی روی تصویر (۰ تا ۸۰ درصد)", help_text="برای خوانایی بهتر متن روی تصویرهای روشن مقدار را بیشتر کنید.")),
                ("updated_at", models.DateTimeField(auto_now=True, verbose_name="آخرین تغییر")),
            ],
            options={"verbose_name": "ظاهر سایت", "verbose_name_plural": "ظاهر سایت"},
        ),
    ]
