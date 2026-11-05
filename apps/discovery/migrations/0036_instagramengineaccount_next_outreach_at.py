from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('discovery', '0035_configchannelsource'),
    ]

    operations = [
        migrations.AddField(
            model_name='instagramengineaccount',
            name='next_outreach_at',
            field=models.DateTimeField(blank=True, null=True, verbose_name='زودترین زمان دایرکت سرد بعدی (فاصله انسانی)'),
        ),
    ]
