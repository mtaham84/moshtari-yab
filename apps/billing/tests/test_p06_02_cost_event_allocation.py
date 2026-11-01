from decimal import Decimal
from unittest.mock import patch

from django.test import TestCase
from django.db import IntegrityError, transaction
from django.contrib.auth import get_user_model

from apps.businesses.models import Business

User = get_user_model()
from apps.core.gateway_contract import (
    ModelRef,
    OperationRef,
    AttemptRef,
    PriceSnapshot,
    UsageResult,
    CostAllocation as DomainCostAllocation,
)
from apps.billing.models import CostEvent, CostAllocation as CostAllocationModel
from apps.billing.cost_ledger import DjangoCostLedger


class CostEventAllocationTestCase(TestCase):
    """
    P06.02 Verification: Atomic multi-payer allocation, distinct attempt tracking,
    Decimal currency precision, and status segregation (R14, R31).
    """

    def setUp(self):
        super().setUp()
        self.user1 = User.objects.create(username="biz_owner1", email="owner1@test.local")
        self.user2 = User.objects.create(username="biz_owner2", email="owner2@test.local")
        self.biz1 = Business.objects.create(name="Business One", user=self.user1)
        self.biz2 = Business.objects.create(name="Business Two", user=self.user2)
        self.ledger = DjangoCostLedger()

        self.price_snapshot = PriceSnapshot(
            input_price_usd_per_1m=Decimal("0.50"),
            output_price_usd_per_1m=Decimal("1.50"),
            usd_to_toman=Decimal("268000"),
            markup_percent=Decimal("50.0"),
        )

    def test_atomic_multi_payer_allocation_success(self):
        """CostEvent and all CostAllocation rows are created atomically with exact Decimal amounts."""
        op_ref = OperationRef(operation_id="op-100", kind="need_extraction", payer_tenant_id=str(self.biz1.pk))
        att_ref = AttemptRef(
            attempt_id="att-100-1",
            operation_id=op_ref.operation_id,
            attempt_number=1,
            model_ref=ModelRef("openai", "gpt-4o-mini"),
            is_parse_retry=False,
            operation_ref=op_ref,
        )
        usage = UsageResult(
            prompt_tokens=1000,
            completion_tokens=500,
            total_tokens=1500,
            is_cached=False,
            cost_usd=Decimal("0.001250"),
            cost_toman=Decimal("502.50"),
        )
        # Shared between 2 businesses: 50% each
        allocations = [
            DomainCostAllocation(
                payer_id=str(self.biz1.pk),
                share_ratio=Decimal("0.5"),
                allocated_cost_usd=Decimal("0.000625"),
                allocated_cost_toman=Decimal("251.25"),
            ),
            DomainCostAllocation(
                payer_id=str(self.biz2.pk),
                share_ratio=Decimal("0.5"),
                allocated_cost_usd=Decimal("0.000625"),
                allocated_cost_toman=Decimal("251.25"),
            ),
        ]

        event = self.ledger.record_cost_event(att_ref, self.price_snapshot, usage, allocations)

        self.assertIsNotNone(event.pk)
        self.assertEqual(event.operation_id, "op-100")
        self.assertEqual(event.total_cost_toman, Decimal("502.50"))

        alloc_rows = CostAllocationModel.objects.filter(cost_event=event).order_by("part_index")
        self.assertEqual(alloc_rows.count(), 2)
        self.assertEqual(alloc_rows[0].business, self.biz1)
        self.assertEqual(alloc_rows[0].allocated_toman, Decimal("251.25"))
        self.assertEqual(alloc_rows[0].status, CostAllocationModel.STATUS_PENDING)
        self.assertEqual(alloc_rows[1].business, self.biz2)
        self.assertEqual(alloc_rows[1].allocated_toman, Decimal("251.25"))

    def test_crash_mid_payer_rolls_back_entire_transaction_zero_partial_allocation(self):
        """If a crash/exception occurs between payers, entire transaction rolls back; no partial allocations."""
        op_ref = OperationRef(operation_id="op-101", kind="need_extraction", payer_tenant_id=str(self.biz1.pk))
        att_ref = AttemptRef(
            attempt_id="att-101-1",
            operation_id=op_ref.operation_id,
            attempt_number=1,
            model_ref=ModelRef("openai", "gpt-4o-mini"),
            is_parse_retry=False,
            operation_ref=op_ref,
        )
        usage = UsageResult(
            prompt_tokens=1000,
            completion_tokens=500,
            total_tokens=1500,
            is_cached=False,
            cost_usd=Decimal("0.001250"),
            cost_toman=Decimal("502.50"),
        )
        allocations = [
            DomainCostAllocation(
                payer_id=str(self.biz1.pk),
                share_ratio=Decimal("0.5"),
                allocated_cost_usd=Decimal("0.000625"),
                allocated_cost_toman=Decimal("251.25"),
            ),
            DomainCostAllocation(
                payer_id=str(self.biz2.pk),
                share_ratio=Decimal("0.5"),
                allocated_cost_usd=Decimal("0.000625"),
                allocated_cost_toman=Decimal("251.25"),
            ),
        ]

        # Simulate exception during the creation of the second allocation
        real_create = CostAllocationModel.objects.create
        call_count = 0

        def faulty_create(**kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 2:
                raise RuntimeError("Simulated crash mid-payer iteration!")
            return real_create(**kwargs)

        with patch.object(CostAllocationModel.objects, "create", side_effect=faulty_create):
            with self.assertRaises(RuntimeError):
                self.ledger.record_cost_event(att_ref, self.price_snapshot, usage, allocations)

        # Confirm rollback: NEITHER the CostEvent NOR any CostAllocation was committed!
        self.assertFalse(CostEvent.objects.filter(attempt_id="att-101-1").exists())
        self.assertFalse(CostAllocationModel.objects.filter(cost_event__operation_id="op-101").exists())

    def test_parse_retry_and_fallback_retains_all_paid_attempts_without_duplicate(self):
        """Parse retry and model fallback attempts for the same operation_id are stored distinctly."""
        op_ref = OperationRef(operation_id="op-multi-attempt", kind="chat_json", payer_tenant_id=str(self.biz1.pk))

        # Attempt 1: Gemini failed or malformed JSON
        att1 = AttemptRef(
            attempt_id="att-gemini-1",
            operation_id=op_ref.operation_id,
            attempt_number=1,
            model_ref=ModelRef("gemini", "gemini-1.5-flash"),
            is_parse_retry=False,
            operation_ref=op_ref,
        )
        usage1 = UsageResult(
            prompt_tokens=500,
            completion_tokens=100,
            total_tokens=600,
            is_cached=False,
            cost_usd=Decimal("0.000300"),
            cost_toman=Decimal("120.60"),
        )
        alloc1 = [DomainCostAllocation(payer_id=str(self.biz1.pk), share_ratio=Decimal("1.0"),
                                       allocated_cost_usd=Decimal("0.000300"), allocated_cost_toman=Decimal("120.60"))]

        # Attempt 2: Parse repair on Gemini
        att2 = AttemptRef(
            attempt_id="att-gemini-2",
            operation_id=op_ref.operation_id,
            attempt_number=2,
            model_ref=ModelRef("gemini", "gemini-1.5-flash"),
            is_parse_retry=True,
            operation_ref=op_ref,
        )
        usage2 = UsageResult(
            prompt_tokens=650,
            completion_tokens=150,
            total_tokens=800,
            is_cached=False,
            cost_usd=Decimal("0.000400"),
            cost_toman=Decimal("160.80"),
        )
        alloc2 = [DomainCostAllocation(payer_id=str(self.biz1.pk), share_ratio=Decimal("1.0"),
                                       allocated_cost_usd=Decimal("0.000400"), allocated_cost_toman=Decimal("160.80"))]

        # Attempt 3: Fallback to OpenAI gpt-4o-mini
        att3 = AttemptRef(
            attempt_id="att-openai-3",
            operation_id=op_ref.operation_id,
            attempt_number=3,
            model_ref=ModelRef("openai", "gpt-4o-mini"),
            is_parse_retry=False,
            operation_ref=op_ref,
        )
        usage3 = UsageResult(
            prompt_tokens=500,
            completion_tokens=200,
            total_tokens=700,
            is_cached=False,
            cost_usd=Decimal("0.000550"),
            cost_toman=Decimal("221.10"),
        )
        alloc3 = [DomainCostAllocation(payer_id=str(self.biz1.pk), share_ratio=Decimal("1.0"),
                                       allocated_cost_usd=Decimal("0.000550"), allocated_cost_toman=Decimal("221.10"))]

        e1 = self.ledger.record_cost_event(att1, self.price_snapshot, usage1, alloc1)
        e2 = self.ledger.record_cost_event(att2, self.price_snapshot, usage2, alloc2)
        e3 = self.ledger.record_cost_event(att3, self.price_snapshot, usage3, alloc3)

        # All 3 attempts share the same operation_id
        events = CostEvent.objects.filter(operation_id="op-multi-attempt").order_by("created_at", "id")
        self.assertEqual(events.count(), 3)
        self.assertEqual([e.attempt_id for e in events], ["att-gemini-1", "att-gemini-2", "att-openai-3"])

        # Replaying attempt 2 idempotently returns e2 without creating a duplicate
        e2_replay = self.ledger.record_cost_event(att2, self.price_snapshot, usage2, alloc2)
        self.assertEqual(e2_replay.pk, e2.pk)
        self.assertEqual(CostEvent.objects.filter(operation_id="op-multi-attempt").count(), 3)

    def test_status_segregation_unknown_vs_cached_vs_actual(self):
        """Usage unknown becomes UNRESOLVED; cached or zero-cost becomes WAIVED; normal becomes PENDING."""
        op_ref = OperationRef(operation_id="op-status", kind="test", payer_tenant_id=str(self.biz1.pk))

        # 1. Unknown usage
        att_un = AttemptRef("att-un", op_ref.operation_id, 1, ModelRef("gemini", "gemini-flash"), operation_ref=op_ref)
        usage_un = UsageResult(0, 0, 0, False, Decimal("0"), Decimal("0"), is_unknown=True)
        alloc_un = [DomainCostAllocation(str(self.biz1.pk), Decimal("1.0"), Decimal("0"), Decimal("0"))]
        e_un = self.ledger.record_cost_event(att_un, self.price_snapshot, usage_un, alloc_un)
        self.assertEqual(e_un.allocations.first().status, CostAllocationModel.STATUS_UNRESOLVED)

        # 2. Cached usage
        att_cache = AttemptRef("att-cache", op_ref.operation_id, 2, ModelRef("gemini", "gemini-flash"), operation_ref=op_ref)
        usage_cache = UsageResult(100, 20, 120, True, Decimal("0"), Decimal("0"))
        alloc_cache = [DomainCostAllocation(str(self.biz1.pk), Decimal("1.0"), Decimal("0"), Decimal("0"))]
        e_cache = self.ledger.record_cost_event(att_cache, self.price_snapshot, usage_cache, alloc_cache)
        self.assertEqual(e_cache.allocations.first().status, CostAllocationModel.STATUS_WAIVED)

        # 3. Normal actual usage
        att_act = AttemptRef("att-act", op_ref.operation_id, 3, ModelRef("gemini", "gemini-flash"), operation_ref=op_ref)
        usage_act = UsageResult(100, 20, 120, False, Decimal("0.000100"), Decimal("40.20"))
        alloc_act = [DomainCostAllocation(str(self.biz1.pk), Decimal("1.0"), Decimal("0.000100"), Decimal("40.20"))]
        e_act = self.ledger.record_cost_event(att_act, self.price_snapshot, usage_act, alloc_act)
        self.assertEqual(e_act.allocations.first().status, CostAllocationModel.STATUS_PENDING)
