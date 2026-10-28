from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('discovery', '0020_instagramcrawlexecution'),
    ]

    operations = [
        migrations.AddField(
            model_name='opportunity',
            name='intent_type',
            field=models.CharField(
                blank=True,
                choices=[
                    ('buy_signal', 'سیگنال خرید صریح'),
                    ('recommendation_ask', 'درخواست معرفی / پیشنهاد'),
                    ('complaint', 'نارضایتی از محصول فعلی'),
                    ('comparison', 'مقایسه گزینه‌ها'),
                    ('switching', 'تغییر و جایگزینی'),
                    ('general', 'عمومی'),
                ],
                default='',
                max_length=32,
                verbose_name='نوع قصد خرید',
            ),
        ),
    ]
