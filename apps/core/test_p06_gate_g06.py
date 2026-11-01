import threading
from decimal import Decimal
from unittest.mock import patch

from django.test import TransactionTestCase
from django.contrib.auth import get_user_model
from django.utils import timezone

from apps.businesses.models import Business
from apps.billing.models import (
    AIModel,
    BillingSettings,
    CostAllocation,
    CostEvent,
    CreditWaitingQueue,
    Provider,
    SettlementMapping,
    Wallet,
    WalletReservation,
    WalletTransaction,
)
from apps.core.gateway import LLMGateway, MockLLMTransport, ProviderQuotaExhausted
from apps.core.gateway_contract import (
    GatewayResultStatus,
    ModelRef,
    OperationRef,
    PriceSnapshot,
    UsageResult,
    CostAllocation as DomainCostAllocation,
)
from apps.core.quota_manager import SharedQuotaManager
from apps.billing.wallet_budget_service import DjangoWalletBudgetService
from apps.billing.cost_ledger import DjangoCostLedger
from apps.billing.credit_queue_service import CreditQueueService
from apps.billing.settlement_service import SettlementService
from apps.billing.reconciliation import FinancialReconciliationService

User = get_user_model()


class GateG06ComprehensiveFinancialGateTestCase(TransactionTestCase):
    """
    Gate G06 Exit Verification Matrix:
    1. Zero paid calls without valid reservation; zero platform debt.
    2. Real two-connection/thread concurrency safety on marginal balance.
    3. Out-of-order commits and lost-ACK / disconnect reconciliation.
    4. Multi-payer crash atomicity (zero orphaned events or partial allocations).
    5. Quota failure compensation after budget reservation.
    6. Unknown usage held as UNRESOLVED without auto-debit or silent clip.
    7. Credit queue idempotency and zombie job prevention.
    """

    def setUp(self):
        super().setUp()
        self.user = User.objects.create(username="g06_admin", email="g06@test.local")
        self.biz = Business.objects.create(name="G06 Business", user=self.user)
        self.wallet, _ = Wallet.objects.get_or_create(business=self.biz)
        self.wallet.balance_toman = Decimal("10000.00")
        self.wallet.save(update_fields=["balance_toman"])

        self.settings = BillingSettings.get()
        self.settings.enforce_balance = True
        self.settings.min_balance_toman = Decimal("0.00")
        self.settings.usd_to_toman = Decimal("268000")
        self.settings.markup_percent = Decimal("50.0")
        self.settings.save()

        # Providers and models
        self.prov = Provider.objects.create(name="G06Prov", base_url="https://api.g06.local", api_key="secret")
        self.m1 = AIModel.objects.create(
            provider=self.prov,
            name="g06-model-1",
            priority=10,
            input_price_usd=Decimal("0.10"),
            output_price_usd=Decimal("0.20"),
        )

        self.budget_port = DjangoWalletBudgetService()
        self.ledger_port = DjangoCostLedger()
        self.quota_port = SharedQuotaManager()

    def test_g06_zero_budget_zero_paid_calls_and_zero_platform_debt(self):
        """Zero credit must return QUEUED, make zero network calls, and create zero platform debt."""
        self.wallet.balance_toman = Decimal("0.00")
        self.wallet.save(update_fields=["balance_toman"])

        mock_transport = MockLLMTransport()
        gateway = LLMGateway(
            budget_port=self.budget_port,
            quota_port=self.quota_port,
            ledger_port=self.ledger_port,
            transport=mock_transport,
        )

        op = OperationRef("op-g06-zero", "extract", str(self.biz.pk))
        snapshot = PriceSnapshot(Decimal("0.10"), Decimal("0.20"), Decimal("268000"), Decimal("50.0"))

        res = gateway.execute(
            operation=op,
            system_prompt="sys",
            user_prompt="user",
            price_snapshot=snapshot,
            candidates=[self.m1.model_ref],
        )

        self.assertEqual(res.status, GatewayResultStatus.QUEUED)
        self.assertEqual(len(mock_transport.call_history), 0)
        self.wallet.refresh_from_db()
        self.assertEqual(self.wallet.balance_toman, Decimal("0.00"))
        self.assertFalse(WalletReservation.objects.filter(operation_id="op-g06-zero").exists())

    def test_g06_two_connections_marginal_balance_concurrency(self):
        """Two concurrent workers on marginal balance: exactly one succeeds, zero overdraft."""
        # Set balance to exactly 5000: enough for ONE 4000 reservation, but NOT two
        self.wallet.balance_toman = Decimal("5000.00")
        self.wallet.save(update_fields=["balance_toman"])

        results = []

        def worker_task(wid: int):
            op = OperationRef(f"op-g06-race-{wid}", "extract", str(self.biz.pk))
            res = self.budget_port.reserve(op, Decimal("4000.00"))
            results.append((wid, res))

        t1 = threading.Thread(target=worker_task, args=(1,))
        t2 = threading.Thread(target=worker_task, args=(2,))
        t1.start()
        t2.start()
        t1.join()
        t2.join()

        successes = [r for r in results if r[1][0] is True]
        failures = [r for r in results if r[1][0] is False]

        self.assertEqual(len(successes), 1)
        self.assertEqual(len(failures), 1)
        self.assertEqual(failures[0][1][1], "INSUFFICIENT_FUNDS")

    def test_g06_out_of_order_commit_and_discrete_mapping_settlement(self):
        """Out of order commits with discrete mapping: smaller ID committed later settles cleanly."""
        # Create cost events for op1 and op2
        evt1 = CostEvent.objects.create(
            operation_id="op-g06-ooo-1",
            attempt_id="att-g06-ooo-1",
            provider_name="G06Prov",
            model_name="g06-model-1",
            stage="extract",
            usd_to_toman_rate=Decimal("268000"),
            total_cost_toman=Decimal("1200.00"),
        )
        alloc1 = CostAllocation.objects.create(
            cost_event=evt1,
            business=self.biz,
            allocated_toman=Decimal("1200.00"),
            status=CostAllocation.STATUS_PENDING,
        )

        evt2 = CostEvent.objects.create(
            operation_id="op-g06-ooo-2",
            attempt_id="att-g06-ooo-2",
            provider_name="G06Prov",
            model_name="g06-model-1",
            stage="extract",
            usd_to_toman_rate=Decimal("268000"),
            total_cost_toman=Decimal("1800.00"),
        )
        alloc2 = CostAllocation.objects.create(
            cost_event=evt2,
            business=self.biz,
            allocated_toman=Decimal("1800.00"),
            status=CostAllocation.STATUS_PENDING,
        )

        self.assertLess(alloc1.pk, alloc2.pk)

        # Settle alloc2 FIRST (simulating late commit of alloc1)
        res2 = SettlementService.settle_single_allocation(alloc2.pk)
        self.assertEqual(res2["status"], "SETTLED")

        # Now settle alloc1
        res1 = SettlementService.settle_single_allocation(alloc1.pk)
        self.assertEqual(res1["status"], "SETTLED")

        # Both settled with discrete mapping
        self.assertTrue(SettlementMapping.objects.filter(allocation=alloc1).exists())
        self.assertTrue(SettlementMapping.objects.filter(allocation=alloc2).exists())
        self.assertEqual(alloc1.settlement.settled_amount_toman, Decimal("1200.00"))
        self.assertEqual(alloc2.settlement.settled_amount_toman, Decimal("1800.00"))

        # Re-running settlement on alloc1 is idempotent (zero duplicate charge)
        res1_replay = SettlementService.settle_single_allocation(alloc1.pk)
        self.assertTrue(res1_replay["already_settled"])

    def test_g06_crash_between_event_and_allocation_rolls_back(self):
        """Simulated crash mid-payer iteration rolls back completely: zero partial allocations."""
        user2 = User.objects.create(username="g06_p2", email="p2@test.local")
        biz2 = Business.objects.create(name="Biz Two", user=user2)

        op = OperationRef("op-g06-crash", "extract", str(self.biz.pk))
        from apps.core.gateway_contract import AttemptRef
        att = AttemptRef("att-g06-crash", op.operation_id, 1, self.m1.model_ref, operation_ref=op)
        usage = UsageResult(100, 50, 150, False, Decimal("0.001"), Decimal("402.00"))
        allocs = [
            DomainCostAllocation(str(self.biz.pk), Decimal("0.5"), Decimal("0.0005"), Decimal("201.00")),
            DomainCostAllocation(str(biz2.pk), Decimal("0.5"), Decimal("0.0005"), Decimal("201.00")),
        ]

        real_create = CostAllocation.objects.create
        step = 0

        def faulty_alloc_create(**kw):
            nonlocal step
            step += 1
            if step == 2:
                raise RuntimeError("Simulated crash mid-payer iteration")
            return real_create(**kw)

        snapshot = PriceSnapshot(Decimal("0.10"), Decimal("0.20"), Decimal("268000"), Decimal("50.0"))

        with patch.object(CostAllocation.objects, "create", side_effect=faulty_alloc_create):
            with self.assertRaises(RuntimeError):
                self.ledger_port.record_cost_event(att, snapshot, usage, allocs)

        # Neither the CostEvent nor any CostAllocation was committed
        self.assertFalse(CostEvent.objects.filter(attempt_id="att-g06-crash").exists())
        self.assertFalse(CostAllocation.objects.filter(cost_event__operation_id="op-g06-crash").exists())

    def test_g06_quota_failure_compensates_budget_reservation(self):
        """When quota rejects a call after budget was reserved, reservation is safely released."""
        mock_transport = MockLLMTransport()
        # Mock quota exhaustion via 429 cooldown
        self.quota_port.record_provider_429(self.m1.model_ref, retry_after_sec=60.0)

        gateway = LLMGateway(
            budget_port=self.budget_port,
            quota_port=self.quota_port,
            ledger_port=self.ledger_port,
            transport=mock_transport,
        )

        op = OperationRef("op-g06-quota-fail", "extract", str(self.biz.pk))
        snapshot = PriceSnapshot(Decimal("0.10"), Decimal("0.20"), Decimal("268000"), Decimal("50.0"))

        res = gateway.execute(
            operation=op,
            system_prompt="sys",
            user_prompt="user",
            price_snapshot=snapshot,
            candidates=[self.m1.model_ref],
        )

        self.assertEqual(res.status, GatewayResultStatus.UNAVAILABLE)

        # Confirm reservation was released (not left holding user funds!)
        reservations = WalletReservation.objects.filter(operation_id="op-g06-quota-fail")
        if reservations.exists():
            for r in reservations:
                self.assertEqual(r.status, WalletReservation.STATUS_RELEASED)

    def test_g06_unknown_usage_held_as_unresolved_not_auto_debited(self):
        """An attempt returning unknown usage is held as UNRESOLVED and never auto-debited."""
        evt = CostEvent.objects.create(
            operation_id="op-g06-unknown",
            attempt_id="att-g06-unknown",
            provider_name="G06Prov",
            model_name="g06-model-1",
            stage="extract",
            usd_to_toman_rate=Decimal("268000"),
            total_cost_toman=Decimal("5000.00"),
            is_unknown=True,
        )
        alloc = CostAllocation.objects.create(
            cost_event=evt,
            business=self.biz,
            allocated_toman=Decimal("5000.00"),
            status=CostAllocation.STATUS_PENDING,
        )

        res = SettlementService.settle_single_allocation(alloc.pk)
        self.assertEqual(res["status"], "UNRESOLVED")

        alloc.refresh_from_db()
        self.assertEqual(alloc.status, CostAllocation.STATUS_UNRESOLVED)

        # Wallet balance remained intact (10000 Toman)
        self.wallet.refresh_from_db()
        self.assertEqual(self.wallet.balance_toman, Decimal("10000.00"))
        self.assertFalse(SettlementMapping.objects.filter(allocation=alloc).exists())

    def test_g06_waiting_queue_no_duplicate_jobs_and_no_zombie_resumption(self):
        """Re-enqueueing updates existing item; cancelled jobs are never revived."""
        item1 = CreditQueueService.enqueue_waiting_work(
            business=self.biz,
            operation_id="op-g06-queue",
            stage="extract",
            payload={"step": 1},
            estimated_toman=Decimal("1000.00"),
        )
        item2 = CreditQueueService.enqueue_waiting_work(
            business=self.biz,
            operation_id="op-g06-queue",
            stage="extract",
            payload={"step": 2},
            estimated_toman=Decimal("1200.00"),
        )

        self.assertEqual(item1.work_item_id, item2.work_item_id)
        self.assertEqual(CreditWaitingQueue.objects.filter(operation_id="op-g06-queue").count(), 1)

        # Cancel the work item
        CreditQueueService.cancel_waiting_work(item1.work_item_id)

        # Top up wallet
        self.wallet.balance_toman = Decimal("50000.00")
        self.wallet.save(update_fields=["balance_toman"])

        # Check resumption
        resumed = CreditQueueService.check_and_resume_eligible(self.biz)
        self.assertEqual(len(resumed), 0)
        item1.refresh_from_db()
        self.assertEqual(item1.status, CreditWaitingQueue.STATUS_CANCELLED)
