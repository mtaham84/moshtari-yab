from decimal import Decimal

from django.test import TestCase
from django.contrib.auth import get_user_model

from apps.businesses.models import Business
from apps.billing.models import (
    BillingSettings,
    CostEvent,
    CostAllocation,
    SettlementMapping,
    Wallet,
    WalletReservation,
    WalletTransaction,
)
from apps.billing.settlement_service import SettlementService
from apps.billing.wallet_budget_service import DjangoWalletBudgetService
from apps.core.gateway_contract import OperationRef

User = get_user_model()


class SettlementMappingTestCase(TestCase):
    """
    P06.05 Verification: Discrete mapping settlement, out-of-order commit resilience,
    crash/retry idempotency, and unresolved cost holds (R31, R32).
    """

    def setUp(self):
        super().setUp()
        self.user = User.objects.create(username="settle_owner", email="sowner@test.local")
        self.biz = Business.objects.create(name="Settlement Biz", user=self.user)
        self.wallet, _ = Wallet.objects.get_or_create(business=self.biz)
        self.wallet.balance_toman = Decimal("20000.00")
        self.wallet.save(update_fields=["balance_toman"])

        self.settings = BillingSettings.get()
        self.settings.enforce_balance = True
        self.settings.min_balance_toman = Decimal("0.00")
        self.settings.save()

    def _create_event_and_allocation(self, op_id: str, att_id: str, amount_toman: Decimal, is_unknown=False, is_cached=False):
        evt = CostEvent.objects.create(
            operation_id=op_id,
            attempt_id=att_id,
            provider_name="gemini",
            model_name="gemini-1.5-flash",
            stage="test_stage",
            prompt_tokens=100,
            completion_tokens=50,
            total_tokens=150,
            is_cached=is_cached,
            is_unknown=is_unknown,
            usd_to_toman_rate=Decimal("268000"),
            markup_multiplier=Decimal("1.50"),
            total_cost_usd=Decimal("0.001"),
            total_cost_toman=amount_toman,
        )
        alloc = CostAllocation.objects.create(
            cost_event=evt,
            business=self.biz,
            part_index=0,
            payer_count=1,
            charge_factor=Decimal("1.0"),
            allocated_usd=Decimal("0.001"),
            allocated_toman=amount_toman,
            status=CostAllocation.STATUS_PENDING,
        )
        return evt, alloc

    def test_out_of_order_commit_settles_both_allocations_correctly(self):
        """
        Smaller ID committed later is NOT skipped (remedying R31 MAX(id) cursor flaw).
        Allocation A (id smaller) and Allocation B (id larger) are both settled exactly once.
        """
        _, alloc_a = self._create_event_and_allocation("op-a", "att-a", Decimal("1000.00"))
        _, alloc_b = self._create_event_and_allocation("op-b", "att-b", Decimal("2000.00"))

        self.assertLess(alloc_a.pk, alloc_b.pk)

        # Simulate B being settled FIRST (e.g. earlier worker or commit)
        res_b = SettlementService.settle_single_allocation(alloc_b.pk)
        self.assertEqual(res_b["status"], "SETTLED")

        # Now settle A (the smaller ID, as would happen with late commit)
        res_a = SettlementService.settle_single_allocation(alloc_a.pk)
        self.assertEqual(res_a["status"], "SETTLED")

        # Both have 1:1 SettlementMapping rows
        self.assertTrue(SettlementMapping.objects.filter(allocation=alloc_a).exists())
        self.assertTrue(SettlementMapping.objects.filter(allocation=alloc_b).exists())

        # Total deducted = 1000 + 2000 = 3000
        self.wallet.refresh_from_db()
        self.assertEqual(self.wallet.balance_toman, Decimal("17000.00"))

    def test_retry_and_crash_idempotency_creates_zero_duplicate_charge(self):
        """Replaying settlement for an already mapped allocation does not charge again."""
        _, alloc = self._create_event_and_allocation("op-repeat", "att-repeat", Decimal("1500.00"))

        res1 = SettlementService.settle_single_allocation(alloc.pk)
        self.assertEqual(res1["status"], "SETTLED")
        self.assertEqual(res1["already_settled"], False)

        self.wallet.refresh_from_db()
        self.assertEqual(self.wallet.balance_toman, Decimal("18500.00"))

        # Replay settlement on same allocation (e.g. worker retry / crash recovery)
        res2 = SettlementService.settle_single_allocation(alloc.pk)
        self.assertEqual(res2["status"], "SETTLED")
        self.assertEqual(res2["already_settled"], True)

        # Wallet balance remains 18500! Zero duplicate charge!
        self.wallet.refresh_from_db()
        self.assertEqual(self.wallet.balance_toman, Decimal("18500.00"))
        self.assertEqual(SettlementMapping.objects.filter(allocation=alloc).count(), 1)

    def test_unresolved_cost_hold_does_not_debit_wallet(self):
        """Allocation with is_unknown=True is held as UNRESOLVED and does not charge wallet."""
        _, alloc = self._create_event_and_allocation("op-unknown", "att-unknown", Decimal("2000.00"), is_unknown=True)

        res = SettlementService.settle_single_allocation(alloc.pk)
        self.assertEqual(res["status"], "UNRESOLVED")

        alloc.refresh_from_db()
        self.assertEqual(alloc.status, CostAllocation.STATUS_UNRESOLVED)

        # Wallet balance untouched
        self.wallet.refresh_from_db()
        self.assertEqual(self.wallet.balance_toman, Decimal("20000.00"))
        self.assertFalse(SettlementMapping.objects.filter(allocation=alloc).exists())

    def test_reservation_linking_and_settlement(self):
        """When an active reservation exists, settlement settles the reservation and links it."""
        budget_svc = DjangoWalletBudgetService()
        op = OperationRef("op-with-res", "extract", str(self.biz.pk))
        allowed, _, res_id = budget_svc.reserve(op, Decimal("3000.00"))
        self.assertTrue(allowed)

        # Cost event for this operation with actual 2200 Toman
        _, alloc = self._create_event_and_allocation("op-with-res", "att-res-1", Decimal("2200.00"))

        res = SettlementService.settle_single_allocation(alloc.pk)
        self.assertEqual(res["status"], "SETTLED")

        mapping = SettlementMapping.objects.get(allocation=alloc)
        self.assertIsNotNone(mapping.reservation)
        self.assertEqual(mapping.reservation.reservation_id, res_id)
        self.assertEqual(mapping.reservation.status, WalletReservation.STATUS_SETTLED)
        self.assertEqual(mapping.settled_amount_toman, Decimal("2200.00"))

        # Wallet balance debited only actual 2200
        self.wallet.refresh_from_db()
        self.assertEqual(self.wallet.balance_toman, Decimal("17800.00"))

    def test_batch_settlement_summary(self):
        """Batch settlement processes multiple allocations and returns complete summary."""
        self._create_event_and_allocation("op-b1", "att-b1", Decimal("500.00"))
        self._create_event_and_allocation("op-b2", "att-b2", Decimal("600.00"))
        self._create_event_and_allocation("op-b3", "att-b3", Decimal("0.00"), is_cached=True)
        self._create_event_and_allocation("op-b4", "att-b4", Decimal("700.00"), is_unknown=True)

        summary = SettlementService.settle_pending_allocations(batch_size=10)
        self.assertEqual(summary["processed"], 4)
        self.assertEqual(summary["settled"], 2)  # b1 and b2
        self.assertEqual(summary["waived"], 1)   # b3
        self.assertEqual(summary["unresolved"], 1) # b4
        self.assertEqual(summary["total_toman"], Decimal("1100.00"))
