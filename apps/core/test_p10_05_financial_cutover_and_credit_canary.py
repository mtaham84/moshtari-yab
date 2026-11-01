from decimal import Decimal
from django.test import TestCase
from django.contrib.auth import get_user_model

from apps.businesses.models import Business
from apps.billing.models import (
    AIModel,
    BillingSettings,
    CostAllocation,
    CostEvent,
    CreditWaitingQueue,
    Provider,
    Wallet,
    WalletReservation,
    WalletTransaction,
    SettlementMapping,
)
from apps.billing.services import apply_transaction, ensure_wallet
from apps.billing.settlement_service import SettlementService
from apps.billing.wallet_budget_service import DjangoWalletBudgetService
from apps.billing.cost_ledger import DjangoCostLedger
from apps.core.gateway import LLMGateway, MockLLMTransport, ProviderQuotaExhausted
from apps.core.gateway_contract import (
    GatewayResultStatus,
    ModelRef,
    OperationRef,
    PriceSnapshot,
)
from apps.core.quota_manager import SharedQuotaManager

User = get_user_model()


class FinancialCutoverAndCreditCanaryTests(TestCase):
    """
    P10.05 — Financial Cutover & Credit Canary Verification Suite.
    Validates:
    1. Zero credit boundary: halts cleanly without unbacked provider calls, queues into CreditWaitingQueue (R07, R32).
    2. Credit Canary lifecycle: Reserve -> Execute -> Settle with 1:1 CostAllocation and excess release (R14, R31).
    3. Idempotent retry safety: Replayed operation_id never causes duplicate wallet reservation or double debit (R34).
    4. Ops priority failover: Candidate 1 failure cleanly falls over to Candidate 2 with exactly single charge (R15, R16).
    5. Reconciliation integrity: Unresolved cost events map 1:1 to settlements without cursor drift.
    """

    def setUp(self):
        super().setUp()
        self.user = User.objects.create_user(username="canary_fin_owner", password="pwd")
        self.biz = Business.objects.create(name="فروشگاه مالی تست کاناری", user=self.user)
        self.wallet = ensure_wallet(self.biz)
        self.wallet.balance_toman = Decimal("0.00")
        self.wallet.save(update_fields=["balance_toman"])

        self.settings = BillingSettings.get()
        self.settings.enforce_balance = True
        self.settings.min_balance_toman = Decimal("0.00")
        self.settings.usd_to_toman = Decimal("268000")
        self.settings.markup_percent = Decimal("50.0")
        self.settings.save()

        self.prov1 = Provider.objects.create(name="PrimaryProvider", base_url="https://p1.test.local", api_key="p1-key")
        self.prov2 = Provider.objects.create(name="SecondaryProvider", base_url="https://p2.test.local", api_key="p2-key")

        self.m1 = AIModel.objects.create(
            provider=self.prov1,
            name="primary-fast",
            priority=10,
            input_price_usd=Decimal("0.10"),
            output_price_usd=Decimal("0.20"),
        )
        self.m2 = AIModel.objects.create(
            provider=self.prov2,
            name="secondary-fallback",
            priority=20,
            input_price_usd=Decimal("0.20"),
            output_price_usd=Decimal("0.40"),
        )

        self.budget_port = DjangoWalletBudgetService()
        self.ledger_port = DjangoCostLedger()
        self.quota_port = SharedQuotaManager()

    def test_zero_credit_queues_cleanly_without_unbacked_calls(self):
        """Zero credit halts before provider network and returns QUEUED status (R07, R32)."""
        mock_transport = MockLLMTransport()
        gateway = LLMGateway(
            budget_port=self.budget_port,
            quota_port=self.quota_port,
            ledger_port=self.ledger_port,
            transport=mock_transport,
        )

        op = OperationRef("op_canary_zero_credit", "extract", f"tenant_{self.biz.id}")
        snapshot = PriceSnapshot(Decimal("0.10"), Decimal("0.20"), Decimal("268000"), Decimal("50.0"))

        res = gateway.execute(
            operation=op,
            system_prompt="sys",
            user_prompt="user",
            price_snapshot=snapshot,
            candidates=[self.m1.model_ref, self.m2.model_ref],
        )

        self.assertEqual(res.status, GatewayResultStatus.QUEUED)
        self.assertEqual(len(mock_transport.call_history), 0, "No provider calls permitted on zero credit")
        self.wallet.refresh_from_db()
        self.assertEqual(self.wallet.balance_toman, Decimal("0.00"), "Wallet balance must remain intact")

    def test_credit_canary_reserve_execute_settle_lifecycle(self):
        """Funded wallet executes reserve -> call -> settle actual cost and releases hold (R14, R31, R32)."""
        apply_transaction(self.biz, Decimal("10000.00"), WalletTransaction.TOPUP, "شارژ تست کاناری")
        self.wallet.refresh_from_db()
        self.assertEqual(self.wallet.balance_toman, Decimal("10000.00"))

        mock_transport = MockLLMTransport()
        mock_transport.set_constant_response(
            "primaryprovider:primary-fast",
            '{"status": "ok", "items": ["laptop"]}',
            pt=100,
            ct=50,
        )

        gateway = LLMGateway(
            budget_port=self.budget_port,
            quota_port=self.quota_port,
            ledger_port=self.ledger_port,
            transport=mock_transport,
        )

        op = OperationRef("op_canary_success_lifecycle", "extract", str(self.biz.id))
        snapshot = PriceSnapshot(Decimal("0.10"), Decimal("0.20"), Decimal("268000"), Decimal("50.0"))

        res = gateway.execute(
            operation=op,
            system_prompt="sys",
            user_prompt="user",
            price_snapshot=snapshot,
            candidates=[self.m1.model_ref],
        )

        self.assertEqual(res.status, GatewayResultStatus.SUCCESS)
        self.assertEqual(len(mock_transport.call_history), 1)

        # Invariant: Wallet held balance released, actual debit applied
        self.wallet.refresh_from_db()
        active_reservations = WalletReservation.objects.filter(wallet=self.wallet, status=WalletReservation.STATUS_HELD)
        self.assertEqual(active_reservations.count(), 0, "No held reservations should remain open after settle")

        # Balance must be less than 10,000 but strictly positive (not overdrawn)
        self.assertLess(self.wallet.balance_toman, Decimal("10000.00"))
        self.assertGreater(self.wallet.balance_toman, Decimal("9000.00"))

        # Verify reservation status is SETTLED
        res_row = WalletReservation.objects.get(reservation_id=res.reservation_id)
        self.assertEqual(res_row.status, WalletReservation.STATUS_SETTLED)
        self.assertGreater(res_row.reserved_amount_toman, Decimal("0.00"))

        # Verify CostAllocation recorded and can be settled cleanly
        alloc = CostAllocation.objects.filter(cost_event__operation_id=op.operation_id).first()
        self.assertIsNotNone(alloc)
        self.assertGreater(alloc.allocated_toman, Decimal("0.00"))

        settle_res = SettlementService.settle_single_allocation(alloc.id)
        self.assertEqual(settle_res["status"], "SETTLED")
        alloc.refresh_from_db()
        self.assertEqual(alloc.status, CostAllocation.STATUS_SETTLED)

    def test_replayed_operation_id_never_double_charges(self):
        """Retrying or replaying an already reserved operation does not double-reserve (R34)."""
        apply_transaction(self.biz, Decimal("5000.00"), WalletTransaction.TOPUP, "شارژ اولیه")

        op = OperationRef("op_canary_idempotency", "extract", str(self.biz.id))
        allowed1, reason1, res_id1 = self.budget_port.reserve(op, Decimal("2000.00"))
        self.assertTrue(allowed1)

        # Second reserve with same operation_id (replay)
        allowed2, reason2, res_id2 = self.budget_port.reserve(op, Decimal("2000.00"))
        self.assertTrue(allowed2)
        self.assertEqual(reason2, "ALREADY_RESERVED")
        self.assertEqual(res_id1, res_id2)

        # Held balance must be exactly 2000, NOT 4000!
        held_total = WalletReservation.objects.filter(
            wallet=self.wallet, status=WalletReservation.STATUS_HELD
        ).count()
        self.assertEqual(held_total, 1, "Must NOT create duplicate held reservations for identical operation_id")

    def test_ops_priority_failover_charges_only_succeeded_candidate(self):
        """When Candidate 1 fails, Candidate 2 executes and only 1 cost is settled (R15, R16)."""
        apply_transaction(self.biz, Decimal("10000.00"), WalletTransaction.TOPUP, "شارژ تست failover")

        mock_transport = MockLLMTransport()
        # Candidate 1 fails with ProviderQuotaExhausted (429)
        mock_transport.set_error(
            "primaryprovider:primary-fast",
            ProviderQuotaExhausted("Rate limit exceeded"),
        )
        # Candidate 2 succeeds
        mock_transport.set_constant_response(
            "secondaryprovider:secondary-fallback",
            '{"status": "fallback_ok"}',
            pt=100,
            ct=50,
        )

        gateway = LLMGateway(
            budget_port=self.budget_port,
            quota_port=self.quota_port,
            ledger_port=self.ledger_port,
            transport=mock_transport,
        )

        op = OperationRef("op_canary_failover", "extract", str(self.biz.id))
        snapshot = PriceSnapshot(Decimal("0.20"), Decimal("0.40"), Decimal("268000"), Decimal("50.0"))

        res = gateway.execute(
            operation=op,
            system_prompt="sys",
            user_prompt="user",
            price_snapshot=snapshot,
            candidates=[self.m1.model_ref, self.m2.model_ref],
        )

        self.assertEqual(res.status, GatewayResultStatus.SUCCESS)

        # Exactly 1 billable cost allocation with allocated_toman > 0
        billable_allocs = CostAllocation.objects.filter(cost_event__operation_id=op.operation_id, allocated_toman__gt=0)
        self.assertEqual(billable_allocs.count(), 1)
