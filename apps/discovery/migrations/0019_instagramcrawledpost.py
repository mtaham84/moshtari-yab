from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('discovery', '0018_instagramengineaccount_instagramenginelead'),
    ]

    operations = [
        migrations.CreateModel(
            name='InstagramCrawledPost',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('post_hash', models.CharField(db_index=True, max_length=64, unique=True, verbose_name='هش شناسه پست')),
                ('media_id', models.CharField(db_index=True, max_length=64, verbose_name='شناسه پست اینستاگرام')),
                ('media_code', models.CharField(blank=True, max_length=32, verbose_name='کد کوتاه پست')),
                ('account_username', models.CharField(max_length=100, verbose_name='اکانت اسکرپر')),
                ('posted_at', models.DateTimeField(db_index=True, verbose_name='زمان انتشار پست')),
                ('crawled_at', models.DateTimeField(auto_now_add=True, db_index=True, verbose_name='زمان ثبت کرول')),
            ],
            options={
                'verbose_name': 'پست کرول‌شده اینستاگرام',
                'verbose_name_plural': 'پست‌های کرول‌شده اینستاگرام',
                'indexes': [
                    models.Index(fields=['crawled_at'], name='ix_ig_crawled_at'),
                    models.Index(fields=['posted_at'], name='ix_ig_posted_at'),
                    models.Index(fields=['post_hash'], name='ix_ig_post_hash'),
                ],
            },
        ),
    ]
