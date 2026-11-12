from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("discovery", "0043_opportunity_unique_engine_id"),
    ]

    operations = [
        migrations.AddConstraint(
            model_name="opportunity",
            constraint=models.UniqueConstraint(
                condition=models.Q(("engine_opportunity_id", ""), _negated=True),
                fields=("business", "engine_opportunity_id"),
                name="uniq_opportunity_business_engine_id",
            ),
        ),
    ]
