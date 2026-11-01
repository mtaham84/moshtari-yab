import threading
from decimal import Decimal
from unittest.mock import patch

from django.test import TransactionTestCase, TestCase
from django.contrib.auth import get_user_model
from django.utils import timezone

from apps.businesses.models import Business
from apps.core.gateway_contract import OperationRef
from apps.billing.models import (
    AIModel,
    BillingSettings,
    Provider,
    Wallet,
    WalletReservation,
    WalletTransaction,
)
from apps.billing.wallet_budget_service import (
    DjangoWalletBudgetService,
    compute_conservative_bound,
    UnboundedCostError,
)

User = get_user_model()


class WalletReservationTestCase(TestCase):
    """
    P06.03 Verification: Atomic reservation of credit, zero platform debt,
    idempotent replay, and conservative token bounds (R32).
    """

    def setUp(self):
        super().setUp()
        self.user = User.objects.create(username="wallet_user", email="wallet@test.local")
        self.biz = Business.objects.create(name="Wallet Business", user=self.user)
        self.wallet, _ = Wallet.objects.get_or_create(business=self.biz)
        self.wallet.balance_toman = Decimal("10000.00")
        self.wallet.save(update_fields=["balance_toman"])

        self.service = DjangoWalletBudgetService()

        # Settings: min_balance = 1000, enforce_balance = True
        self.settings = BillingSettings.get()
        self.settings.enforce_balance = True
        self.settings.min_balance_toman = Decimal("1000.00")
        self.settings.usd_to_toman = Decimal("268000")
        self.settings.markup_percent = Decimal("50.0")
        self.settings.save()

    def test_reservation_success_within_available_balance(self):
        """Funds are reserved when available balance (balance - floor - held) exceeds estimate."""
        op = OperationRef("op-res-1", "extract", str(self.biz.pk))
        # balance = 10000, floor = 1000, available = 9000
        allowed, reason, res_id = self.service.reserve(op, Decimal("5000.00"))

        self.assertTrue(allowed)
        self.assertEqual(reason, "RESERVED")
        self.assertIsNotNone(res_id)

        res = WalletReservation.objects.get(reservation_id=res_id)
        self.assertEqual(res.status, WalletReservation.STATUS_HELD)
        self.assertEqual(res.reserved_amount_toman, Decimal("5000.00"))

    def test_insufficient_funds_fails_without_creating_debt(self):
        """When available balance is less than required, reservation fails with zero debt."""
        op = OperationRef("op-res-fail", "extract", str(self.biz.pk))
        # available = 9000, requested = 9500 -> INSUFFICIENT_FUNDS
        allowed, reason, res_id = self.service.reserve(op, Decimal("9500.00"))

        self.assertFalse(allowed)
        self.assertEqual(reason, "INSUFFICIENT_FUNDS")
        self.assertIsNone(res_id)

        # Confirm wallet balance unchanged
        self.wallet.refresh_from_db()
        self.assertEqual(self.wallet.balance_toman, Decimal("10000.00"))
        self.assertFalse(WalletReservation.objects.filter(operation_id="op-res-fail").exists())

    def test_reservation_replay_is_idempotent_no_double_deduction(self):
        """Calling reserve twice with identical operation_id returns ALREADY_RESERVED."""
        op = OperationRef("op-idempotent", "extract", str(self.biz.pk))
        allowed1, reason1, res_id1 = self.service.reserve(op, Decimal("4000.00"))
        self.assertTrue(allowed1)

        # Second reserve with same operation_id
        allowed2, reason2, res_id2 = self.service.reserve(op, Decimal("4000.00"))
        self.assertTrue(allowed2)
        self.assertEqual(reason2, "ALREADY_RESERVED")
        self.assertEqual(res_id1, res_id2)

        # Only 1 reservation row exists
        self.assertEqual(WalletReservation.objects.filter(operation_id="op-idempotent").count(), 1)

    def test_settle_debits_actual_cost_and_sets_status_settled(self):
        """Settle debits actual consumed amount and creates WalletTransaction."""
        op = OperationRef("op-settle", "extract", str(self.biz.pk))
        allowed, reason, res_id = self.service.reserve(op, Decimal("4000.00"))
        self.assertTrue(allowed)

        # Actual cost is 2500 Toman (less than 4000 estimate)
        settled = self.service.settle(res_id, Decimal("2500.00"))
        self.assertTrue(settled)

        res = WalletReservation.objects.get(reservation_id=res_id)
        self.assertEqual(res.status, WalletReservation.STATUS_SETTLED)

        self.wallet.refresh_from_db()
        # Original 10000 - 2500 actual = 7500
        self.assertEqual(self.wallet.balance_toman, Decimal("7500.00"))

        tx = WalletTransaction.objects.filter(wallet=self.wallet, kind=WalletTransaction.USAGE).first()
        self.assertIsNotNone(tx)
        self.assertEqual(tx.amount_toman, Decimal("-2500.00"))

    def test_release_frees_reserved_balance(self):
        """Release frees the held amount so subsequent requests can use the funds."""
        op1 = OperationRef("op-rel-1", "extract", str(self.biz.pk))
        # Reserve full available (9000)
        allowed1, _, res_id1 = self.service.reserve(op1, Decimal("9000.00"))
        self.assertTrue(allowed1)

        # Op2 fails because balance is fully held
        op2 = OperationRef("op-rel-2", "extract", str(self.biz.pk))
        allowed2, reason2, _ = self.service.reserve(op2, Decimal("1000.00"))
        self.assertFalse(allowed2)
        self.assertEqual(reason2, "INSUFFICIENT_FUNDS")

        # Release Op1
        released = self.service.release(res_id1)
        self.assertTrue(released)

        # Now Op2 succeeds!
        allowed3, reason3, _ = self.service.reserve(op2, Decimal("1000.00"))
        self.assertTrue(allowed3)
        self.assertEqual(reason3, "RESERVED")

    def test_compute_conservative_bound(self):
        """compute_conservative_bound calculates conservative ceiling across models."""
        provider = Provider.objects.create(name="ProvTest", base_url="https://api.test.local")
        m1 = AIModel.objects.create(
            provider=provider,
            name="cheap-model",
            input_price_usd=Decimal("0.10"),
            output_price_usd=Decimal("0.20"),
        )
        m2 = AIModel.objects.create(
            provider=provider,
            name="expensive-model",
            input_price_usd=Decimal("1.00"),
            output_price_usd=Decimal("2.00"),
        )

        bound = compute_conservative_bound([m1, m2], max_output_tokens=1000, estimated_input_tokens=1000)
        self.assertGreater(bound, Decimal("0"))

        with self.assertRaises(UnboundedCostError):
            compute_conservative_bound([])


class ConcurrentWalletReservationTestCase(TransactionTestCase):
    """
    P06.03 Verification: Race condition / multi-worker safety on marginal balance.
    Must use TransactionTestCase to test real DB transactions and SELECT FOR UPDATE across threads.
    """

    def setUp(self):
        super().setUp()
        self.user = User.objects.create(username="race_user", email="race@test.local")
        self.biz = Business.objects.create(name="Race Business", user=self.user)
        # Balance = 5000, min_balance floor = 0 -> Exactly enough for ONE 4000 reservation, but NOT two!
        self.wallet, _ = Wallet.objects.get_or_create(business=self.biz)
        self.wallet.balance_toman = Decimal("5000.00")
        self.wallet.save(update_fields=["balance_toman"])
        self.service = DjangoWalletBudgetService()

        self.settings = BillingSettings.get()
        self.settings.enforce_balance = True
        self.settings.min_balance_toman = Decimal("0.00")
        self.settings.save()

    def test_concurrent_reservations_cannot_overdraw_wallet(self):
        """Two concurrent workers competing for marginal balance: exactly one succeeds."""
        results = []

        def worker(worker_id: int):
            op = OperationRef(f"op-race-{worker_id}", "extract", str(self.biz.pk))
            res = self.service.reserve(op, Decimal("4000.00"))
            results.append((worker_id, res))

        t1 = threading.Thread(target=worker, args=(1,))
        t2 = threading.Thread(target=worker, args=(2,))

        t1.start()
        t2.start()
        t1.join()
        t2.join()

        successes = [r for r in results if r[1][0] is True]
        failures = [r for r in results if r[1][0] is False]

        self.assertEqual(len(successes), 1, "Exactly one worker must succeed on marginal balance")
        self.assertEqual(len(failures), 1, "The other worker must be rejected with INSUFFICIENT_FUNDS")
        self.assertEqual(failures[0][1][1], "INSUFFICIENT_FUNDS")
