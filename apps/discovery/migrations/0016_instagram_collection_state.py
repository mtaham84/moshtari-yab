from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("discovery", "0015_instagrampage"),
    ]

    operations = [
        migrations.AddField(
            model_name="instagrampage",
            name="queued_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddIndex(
            model_name="instagrampage",
            index=models.Index(fields=["business", "is_active", "last_run_at"], name="igpage_active_fair_idx"),
        ),
        migrations.AddConstraint(
            model_name="opportunity",
            constraint=models.UniqueConstraint(
                condition=models.Q(("source_platform", "instagram")) & ~models.Q(("source_message_id", "")),
                fields=("business", "source_platform", "source_message_id"),
                name="uniq_ig_opportunity_business_message",
            ),
        ),
    ]
