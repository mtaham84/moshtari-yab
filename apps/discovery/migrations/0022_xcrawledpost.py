from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('discovery', '0021_opportunity_intent_type'),
    ]

    operations = [
        migrations.CreateModel(
            name='XCrawledPost',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('post_hash', models.CharField(db_index=True, max_length=64, unique=True, verbose_name='هش شناسه توییت')),
                ('tweet_id', models.CharField(db_index=True, max_length=64, unique=True, verbose_name='شناسه توییت X')),
                ('author_handle', models.CharField(blank=True, max_length=100, verbose_name='اکانت توییت')),
                ('posted_at', models.DateTimeField(blank=True, db_index=True, null=True, verbose_name='زمان انتشار توییت')),
                ('crawled_at', models.DateTimeField(auto_now_add=True, db_index=True, verbose_name='زمان ثبت کرول')),
                ('tweet_url', models.CharField(blank=True, max_length=255, verbose_name='آدرس توییت')),
            ],
            options={
                'verbose_name': 'توییت کرول‌شده X',
                'verbose_name_plural': 'توییت‌های کرول‌شده X',
                'indexes': [
                    models.Index(fields=['crawled_at'], name='ix_x_crawled_at'),
                    models.Index(fields=['posted_at'], name='ix_x_posted_at'),
                    models.Index(fields=['post_hash'], name='ix_x_post_hash'),
                    models.Index(fields=['tweet_id'], name='ix_x_tweet_id'),
                ],
            },
        ),
    ]
