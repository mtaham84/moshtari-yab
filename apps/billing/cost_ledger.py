import logging
from decimal import Decimal, ROUND_HALF_UP
from typing import List, Optional

from django.db import transaction, IntegrityError

from apps.businesses.models import Business
from apps.core.gateway_contract import (
    LedgerPort,
    AttemptRef,
    PriceSnapshot,
    UsageResult,
    CostAllocation as DomainCostAllocation,
)
from apps.billing.models import CostEvent, CostAllocation as CostAllocationModel

log = logging.getLogger(__name__)

CENT = Decimal("0.01")
MICRO_USD = Decimal("0.000001")


class DjangoCostLedger(LedgerPort):
    """
    Authoritative Django DB implementation of LedgerPort (P06.02 / R14, R31).
    Atomically records CostEvent and its multi-payer CostAllocation rows in a single transaction.
    """

    def record_attempt(
        self,
        operation,
        attempt,
        price_snapshot,
        ref: str,
    ) -> str:
        """Fallback adapter for gateway record_attempt calls."""
        usage = attempt.usage or UsageResult(0, 0, 0, False, Decimal("0"), Decimal("0"))
        allocations = [
            DomainCostAllocation(
                payer_id=str(operation.payer_tenant_id) if operation.payer_tenant_id else None,
                share_ratio=Decimal("1.0"),
                allocated_cost_usd=usage.cost_usd,
                allocated_cost_toman=usage.cost_toman,
            )
        ]
        evt = self.record_cost_event(attempt, price_snapshot, usage, allocations)
        return str(evt.pk)

    @transaction.atomic
    def record_cost_event(
        self,
        attempt: AttemptRef,
        price_snapshot: PriceSnapshot,
        usage: UsageResult,
        allocations: List[DomainCostAllocation],
    ) -> CostEvent:
        """
        Records an attempt cost event and all its payer allocations atomically.
        If attempt_id already exists:
        - If identical, idempotently returns existing event.
        - If conflict, raises ValueError / IntegrityError.
        If any allocation fails or crashes, the entire transaction rolls back.
        """
        op_id = attempt.operation_ref.operation_id if attempt.operation_ref else attempt.operation_id
        stage = getattr(attempt.operation_ref, "kind", getattr(attempt.operation_ref, "caller_stage", "default")) if attempt.operation_ref else "default"

        # 1. Check idempotency for this attempt
        existing_event = CostEvent.objects.filter(attempt_id=attempt.attempt_id).first()
        if existing_event:
            # Idempotent replay check
            if (
                existing_event.operation_id == op_id
                and existing_event.provider_name == attempt.model_ref.provider_name
                and existing_event.model_name == attempt.model_ref.model_name
            ):
                return existing_event
            raise IntegrityError(
                f"Attempt ID conflict: attempt '{attempt.attempt_id}' already recorded with different parameters."
            )

        # 2. Decimal conversions with fixed units
        cost_usd = Decimal(str(usage.cost_usd)).quantize(MICRO_USD, ROUND_HALF_UP)
        cost_toman = Decimal(str(usage.cost_toman)).quantize(CENT, ROUND_HALF_UP)
        raw_rate = getattr(price_snapshot, "usd_to_toman", getattr(price_snapshot, "usd_to_toman_rate", Decimal("268000")))
        rate = Decimal(str(raw_rate)).quantize(Decimal("1"), ROUND_HALF_UP)
        raw_multiplier = price_snapshot.multiplier if hasattr(price_snapshot, "multiplier") else getattr(price_snapshot, "markup_multiplier", Decimal("1.5000"))
        markup = Decimal(str(raw_multiplier)).quantize(Decimal("0.0001"), ROUND_HALF_UP)
        price_ver = getattr(price_snapshot, "version_hash", getattr(price_snapshot, "version", "v1"))

        # 3. Create authoritative CostEvent
        cost_event = CostEvent.objects.create(
            operation_id=op_id,
            attempt_id=attempt.attempt_id,
            provider_name=attempt.model_ref.provider_name,
            model_name=attempt.model_ref.model_name,
            stage=stage,
            prompt_tokens=usage.prompt_tokens,
            completion_tokens=usage.completion_tokens,
            total_tokens=usage.total_tokens,
            is_cached=usage.is_cached,
            is_estimated=usage.is_estimated,
            is_unknown=usage.is_unknown,
            price_version=price_ver,
            usd_to_toman_rate=rate,
            markup_multiplier=markup,
            total_cost_usd=cost_usd,
            total_cost_toman=cost_toman,
        )

        # 4. Create CostAllocations atomically for each payer
        payer_count = len(allocations)
        created_allocations = []

        for idx, alloc in enumerate(allocations):
            biz = None
            if alloc.payer_id:
                try:
                    biz = Business.objects.get(pk=int(alloc.payer_id)) if str(alloc.payer_id).isdigit() else None
                except Business.DoesNotExist:
                    biz = None

            alloc_usd = Decimal(str(alloc.allocated_cost_usd)).quantize(MICRO_USD, ROUND_HALF_UP)
            alloc_toman = Decimal(str(alloc.allocated_cost_toman)).quantize(CENT, ROUND_HALF_UP)
            factor = Decimal(str(alloc.charge_factor)).quantize(Decimal("0.01"), ROUND_HALF_UP)

            status = CostAllocationModel.STATUS_PENDING
            if usage.is_unknown:
                status = CostAllocationModel.STATUS_UNRESOLVED
            elif usage.is_cached or alloc_toman == Decimal("0"):
                status = CostAllocationModel.STATUS_WAIVED

            allocation_row = CostAllocationModel.objects.create(
                cost_event=cost_event,
                business=biz,
                part_index=idx,
                payer_count=payer_count,
                charge_factor=factor,
                allocated_usd=alloc_usd,
                allocated_toman=alloc_toman,
                status=status,
            )
            created_allocations.append(allocation_row)

        return cost_event
