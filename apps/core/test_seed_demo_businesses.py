from decimal import Decimal
from io import StringIO

from django.core.management import call_command
from django.test import TestCase

from apps.billing.models import WalletTransaction
from apps.businesses.models import Business


class SeedDemoBusinessesTests(TestCase):
    def test_stone_wallet_gift_is_applied_once_across_seed_runs(self):
        call_command("seed_demo_businesses", stdout=StringIO())
        business = Business.objects.get(user__username="sang@peyda.ir")
        first_balance = business.wallet.balance_toman
        first_gifts = WalletTransaction.objects.filter(
            wallet=business.wallet, kind=WalletTransaction.GIFT
        ).count()
        business.wallet.balance_toman = Decimal("400000.00")
        business.wallet.save(update_fields=["balance_toman"])

        call_command("seed_demo_businesses", stdout=StringIO())

        business.wallet.refresh_from_db()
        self.assertEqual(first_balance, Decimal("3000000.00"))
        self.assertEqual(business.wallet.balance_toman, Decimal("400000.00"))
        self.assertEqual(first_gifts, 1)
        self.assertEqual(
            WalletTransaction.objects.filter(
                wallet=business.wallet, kind=WalletTransaction.GIFT
            ).count(),
            1,
        )
