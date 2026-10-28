from django.db import migrations, models
import django.db.models.deletion
import django.utils.timezone


class Migration(migrations.Migration):

    dependencies = [
        ('businesses', '0001_initial'),
        ('discovery', '0019_instagramcrawledpost'),
    ]

    operations = [
        migrations.CreateModel(
            name='InstagramCrawlExecution',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('tenant_id', models.CharField(db_index=True, default='tenant_default', max_length=64, verbose_name='شناسه مستأجر')),
                ('query_text', models.CharField(db_index=True, max_length=255, verbose_name='کوئری یا هشتگ')),
                ('target_type', models.CharField(choices=[('HASHTAG', 'هشتگ'), ('KEYWORD', 'کلمه کلیدی'), ('PAGE', 'صفحه هدف')], default='HASHTAG', max_length=50, verbose_name='نوع هدف')),
                ('account_username', models.CharField(db_index=True, max_length=150, verbose_name='نام کاربری اکانت اسکرپر')),
                ('status', models.CharField(choices=[('SUCCESS', 'موفق'), ('NO_POSTS', 'بدون پست جدید'), ('THROTTLED', 'محدودیت موقت'), ('FAILED', 'خطا')], default='SUCCESS', max_length=50, verbose_name='وضعیت اجرا')),
                ('posts_inspected', models.PositiveIntegerField(default=0, verbose_name='تعداد پست‌های بررسی‌شده')),
                ('qualifying_posts', models.PositiveIntegerField(default=0, verbose_name='پست‌های حائز شرایط (کامنت >= ۵۰)')),
                ('leads_found', models.PositiveIntegerField(default=0, verbose_name='تعداد سرنخ‌های جدید کشف‌شده')),
                ('message', models.TextField(blank=True, verbose_name='پیام و جزییات نتیجه')),
                ('crawled_at', models.DateTimeField(db_index=True, default=django.utils.timezone.now, verbose_name='زمان کرول (UTC)')),
                ('account', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='crawl_executions', to='discovery.instagramengineaccount', verbose_name='اکانت ثبت‌شده')),
                ('business', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.CASCADE, related_name='instagram_crawl_executions', to='businesses.business', verbose_name='کسب‌وکار مستأجر')),
            ],
            options={
                'verbose_name': 'لاگ کرول اینستاگرام',
                'verbose_name_plural': 'لاگ‌های کرول اینستاگرام',
                'ordering': ['-crawled_at'],
                'indexes': [
                    models.Index(fields=['crawled_at'], name='ix_ig_exec_crawled_at'),
                    models.Index(fields=['tenant_id', 'query_text'], name='ix_ig_exec_tenant_query'),
                    models.Index(fields=['account_username'], name='ix_ig_exec_account'),
                ],
            },
        ),
    ]
