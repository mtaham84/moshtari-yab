from django.db import migrations, models
from django.db.models import Count


def merge_duplicate_customers(apps, schema_editor):
    """Keep the oldest Customer of each (business, platform, external id); move opportunities and fill empty fields."""
    Customer = apps.get_model("discovery", "Customer")
    Opportunity = apps.get_model("discovery", "Opportunity")
    groups = (Customer.objects.exclude(external_user_id="")
              .values("business_id", "source_platform", "external_user_id")
              .annotate(n=Count("id")).filter(n__gt=1))
    for group in groups:
        rows = list(Customer.objects.filter(
            business_id=group["business_id"], source_platform=group["source_platform"],
            external_user_id=group["external_user_id"]).order_by("id"))
        keep, duplicates = rows[0], rows[1:]
        changed = []
        for field in ("name", "phone_number", "source_profile_url", "source_username"):
            if not getattr(keep, field):
                value = next((getattr(d, field) for d in duplicates if getattr(d, field)), None)
                if value:
                    setattr(keep, field, value)
                    changed.append(field)
        if changed:
            keep.save(update_fields=changed)
        Opportunity.objects.filter(customer__in=duplicates).update(customer=keep)
        Customer.objects.filter(pk__in=[d.pk for d in duplicates]).delete()


class Migration(migrations.Migration):

    dependencies = [
        ('businesses', '0009_seed_business_types'),
        ('discovery', '0037_instagram_account_management'),
    ]

    operations = [
        migrations.RunPython(merge_duplicate_customers, migrations.RunPython.noop),
        migrations.AddConstraint(
            model_name='customer',
            constraint=models.UniqueConstraint(condition=models.Q(('external_user_id', ''), _negated=True), fields=('business', 'source_platform', 'external_user_id'), name='uniq_customer_per_platform'),
        ),
    ]
