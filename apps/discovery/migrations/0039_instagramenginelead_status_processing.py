from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('discovery', '0038_instagram_live_activity'),
    ]

    operations = [
        migrations.AlterField(
            model_name='instagramenginelead',
            name='status',
            field=models.CharField(
                choices=[
                    ('NEW', 'جدید (در صف ارسال اولویت‌دار)'),
                    ('PROCESSING', 'در حال پردازش / ارسال توسط ورکر'),
                    ('QUEUED', 'در صف ارسال زمان‌بندی‌شده'),
                    ('DM_SENT', 'دایرکت ارسال شد (در انتظار پاسخ)'),
                    ('REPLIED', 'پاسخ مشتری دریافت شد'),
                    ('FOLLOWUP_SENT', 'پیام دوم ارسال شد'),
                    ('FAILED', 'ارسال ناموفق'),
                    ('SKIPPED', 'رد شده (نامعتبر یا گاردریل اسپم)'),
                ],
                db_index=True,
                default='NEW',
                max_length=20,
                verbose_name='وضعیت لید',
            ),
        ),
    ]
