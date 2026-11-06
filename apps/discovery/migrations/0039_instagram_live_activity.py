from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('discovery', '0038_customer_unique_per_platform'),
    ]

    operations = [
        migrations.CreateModel(
            name='InstagramLiveWorkerState',
            fields=[
                ('worker_key', models.CharField(choices=[('scraper', 'ربات اسکرپر و استخراج لید'), ('outreach', 'ربات پیام‌رسان دایرکت'), ('cockpit', 'کابین و شبیه‌ساز')], max_length=32, primary_key=True, serialize=False)),
                ('is_active', models.BooleanField(default=False, verbose_name='فعال در این لحظه')),
                ('current_account', models.CharField(blank=True, default='', max_length=150, verbose_name='اکانت جاری')),
                ('action_type', models.CharField(blank=True, default='IDLE', max_length=50, verbose_name='نوع اکشن لحظه‌ای')),
                ('details', models.CharField(blank=True, default='', max_length=500, verbose_name='شرح فعالیت لحظه‌ای')),
                ('media_type', models.CharField(blank=True, default='NONE', max_length=32, verbose_name='نوع مدیا')),
                ('media_code', models.CharField(blank=True, default='', max_length=64, verbose_name='کد پست')),
                ('media_url', models.CharField(blank=True, default='', max_length=500, verbose_name='لینک پست')),
                ('duration_seconds', models.FloatField(default=0.0, verbose_name='مدت تماشا')),
                ('updated_at', models.DateTimeField(auto_now=True, verbose_name='آخرین پالس')),
                ('metadata', models.JSONField(blank=True, default=dict, verbose_name='متادیتا')),
            ],
            options={
                'verbose_name': 'وضعیت لحظه‌ای ورکر اینستاگرام',
                'verbose_name_plural': 'وضعیت‌های لحظه‌ای ورکرهای اینستاگرام',
                'db_table': 'ig_live_worker_states',
            },
        ),
        migrations.CreateModel(
            name='InstagramLiveActivity',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('account_username', models.CharField(db_index=True, max_length=150, verbose_name='اکانت مجری')),
                ('action_type', models.CharField(choices=[('SCRAPE', 'اسکرپ هشتگ/کلیدواژه'), ('WATCH_VIDEO', 'تماشای ویدیو / ریلز'), ('WATCH_STORY', 'تماشای استوری'), ('COMMENTS', 'بررسی کامنت‌ها'), ('LEAD_FOUND', 'کشف خریدار'), ('SEND_DM', 'ارسال دایرکت'), ('WARMUP', 'گشت‌وگذار ارگانیک'), ('COOLDOWN', 'استراحت و وقفه ضدبن'), ('SYSTEM', 'سیستمی'), ('ERROR', 'خطای عملیاتی')], db_index=True, max_length=32, verbose_name='نوع عملیات')),
                ('title', models.CharField(max_length=255, verbose_name='عنوان خلاصه')),
                ('description', models.TextField(blank=True, default='', verbose_name='توضیحات و جزییات لاگ')),
                ('media_type', models.CharField(blank=True, default='NONE', max_length=32, verbose_name='نوع مدیا')),
                ('media_code', models.CharField(blank=True, default='', max_length=64, verbose_name='کد کوتاه اینستاگرام')),
                ('media_url', models.CharField(blank=True, default='', max_length=500, verbose_name='لینک اینستاگرام')),
                ('duration_seconds', models.FloatField(default=0.0, verbose_name='مدت زمان تماشا یا عملیات (ثانیه)')),
                ('level', models.CharField(default='INFO', max_length=20, verbose_name='سطح لاگ')),
                ('metadata', models.JSONField(blank=True, default=dict, verbose_name='داده‌های تکمیلی')),
                ('created_at', models.DateTimeField(auto_now_add=True, db_index=True, verbose_name='زمان ثبت')),
            ],
            options={
                'verbose_name': 'فعالیت زنده اینستاگرام',
                'verbose_name_plural': 'فعالیت‌های زنده اینستاگرام',
                'db_table': 'ig_live_activities',
                'ordering': ['-created_at', '-id'],
                'indexes': [
                    models.Index(fields=['-created_at'], name='ix_ig_live_created_at'),
                    models.Index(fields=['action_type', '-created_at'], name='ix_ig_live_type_created'),
                    models.Index(fields=['account_username', '-created_at'], name='ix_ig_live_acc_created'),
                ],
            },
        ),
    ]
