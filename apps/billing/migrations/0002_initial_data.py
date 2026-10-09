"""Settings row (rate from NE_USD_TO_TOMAN) and a zero-balance wallet for every existing seller."""
import os
from decimal import Decimal

from django.db import migrations


def forwards(apps, schema_editor):
    BillingSettings = apps.get_model("billing", "BillingSettings")
    Wallet = apps.get_model("billing", "Wallet")
    Business = apps.get_model("businesses", "Business")
    try:
        rate = Decimal(os.environ.get("NE_USD_TO_TOMAN") or "100000")
    except Exception:
        rate = Decimal("100000")
    BillingSettings.objects.get_or_create(pk=1, defaults={"usd_to_toman": rate})
    have = set(Wallet.objects.values_list("business_id", flat=True))
    Wallet.objects.bulk_create([Wallet(business_id=pk) for pk in Business.objects.values_list("pk", flat=True)
                                if pk not in have])


class Migration(migrations.Migration):
    dependencies = [("billing", "0001_initial"), ("businesses", "0007_message_style")]
    operations = [migrations.RunPython(forwards, migrations.RunPython.noop)]
