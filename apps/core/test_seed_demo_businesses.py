from decimal import Decimal
from io import StringIO
import os
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.test import TestCase

from apps.billing.models import WalletTransaction
from apps.businesses.models import Business


class SeedDemoBusinessesTests(TestCase):
    def test_stone_wallet_gift_is_applied_once_across_seed_runs(self):
        User = get_user_model()
        original_passwords = {}
        for username in ("konkur@peyda.ir", "digital@peyda.ir", "sang@peyda.ir"):
            original_passwords[username] = User.objects.create_user(
                username=username, password=f"existing-{username}"
            ).password

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
        for username, password_hash in original_passwords.items():
            self.assertEqual(User.objects.get(username=username).password, password_hash)
        self.assertEqual(first_gifts, 1)
        self.assertEqual(
            WalletTransaction.objects.filter(
                wallet=business.wallet, kind=WalletTransaction.GIFT
            ).count(),
            1,
        )

    def test_new_demo_accounts_share_one_generated_password(self):
        output = StringIO()
        with patch.dict(os.environ, {"DEMO_ACCOUNTS_PASSWORD": ""}):
            call_command("seed_demo_businesses", stdout=output)

        lines = [
            line for line in output.getvalue().splitlines()
            if line.startswith("Generated demo account password: " )
        ]
        self.assertEqual(len(lines), 1)
        password = lines[0].removeprefix("Generated demo account password: " )
        for username in ("konkur@peyda.ir", "digital@peyda.ir", "sang@peyda.ir"):
            self.assertTrue(get_user_model().objects.get(username=username).check_password(password))

    def test_environment_password_is_used_for_new_demo_accounts(self):
        output = StringIO()
        with patch.dict(os.environ, {"DEMO_ACCOUNTS_PASSWORD": "configured-demo-password"}):
            call_command("seed_demo_businesses", stdout=output)

        self.assertNotIn("configured-demo-password", output.getvalue())
        for username in ("konkur@peyda.ir", "digital@peyda.ir", "sang@peyda.ir"):
            self.assertTrue(
                get_user_model().objects.get(username=username).check_password("configured-demo-password")
            )
