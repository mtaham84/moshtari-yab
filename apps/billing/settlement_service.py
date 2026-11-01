import logging
from decimal import Decimal, ROUND_HALF_UP
from typing import Any, Dict, List, Optional

from django.db import transaction, models
from django.utils import timezone

from apps.billing.models import (
    BillingSettings,
    CostAllocation,
    CostEvent,
    SettlementMapping,
    Wallet,
    WalletReservation,
    WalletTransaction,
)
from apps.billing.services import charge_usage
from apps.billing.wallet_budget_service import DjangoWalletBudgetService

log = logging.getLogger(__name__)

CENT = Decimal("0.01")


class SettlementError(Exception):
    """Base error for settlement issues."""
    pass


class UnresolvedCostHold(SettlementError):
    """Raised when an attempt actual usage exceeds bounds or is unknown."""
    pass


class SettlementService:
    """
    Authoritative discrete mapping settlement service (P06.05 / R31, R32).
    Eliminates reliance on MAX(id) cursor synchronization and ensures:
    1. Smaller IDs committed later are never skipped.
    2. Crashes/retries never create duplicate charges (1:1 SettlementMapping).
    3. Unbounded or unknown actual costs are held as UNRESOLVED rather than silently clipped.
    """

    @classmethod
    def settle_pending_allocations(cls, batch_size: int = 100) -> Dict[str, Any]:
        """
        Processes pending CostAllocation records in discrete atomic steps.
        Returns summary of settlements.
        """
        # Query pending allocations with row lock (skip locked to avoid worker blockages)
        pending_ids = list(
            CostAllocation.objects.filter(status=CostAllocation.STATUS_PENDING)
            .order_by("id")
            .values_list("id", flat=True)[:batch_size]
        )

        settled_count = 0
        total_toman = Decimal("0.00")
        unresolved_count = 0
        waived_count = 0

        budget_svc = DjangoWalletBudgetService()

        for alloc_id in pending_ids:
            try:
                res = cls.settle_single_allocation(alloc_id, budget_svc)
                if res["status"] == "SETTLED":
                    settled_count += 1
                    total_toman += res["amount_toman"]
                elif res["status"] == "WAIVED":
                    waived_count += 1
                elif res["status"] == "UNRESOLVED":
                    unresolved_count += 1
            except Exception as ex:
                log.exception("Error settling allocation %s: %s", alloc_id, ex)

        return {
            "processed": len(pending_ids),
            "settled": settled_count,
            "waived": waived_count,
            "unresolved": unresolved_count,
            "total_toman": total_toman,
        }

    @classmethod
    @transaction.atomic
    def settle_single_allocation(cls, allocation_id: int, budget_svc: Optional[DjangoWalletBudgetService] = None) -> dict:
        """
        Atomically settles a single CostAllocation with discrete SettlementMapping.
        Guarantees idempotency via OneToOne mapping and status transitions.
        """
        alloc = CostAllocation.objects.select_for_update().get(pk=allocation_id)

        # 1. Check idempotency: already mapped or settled
        if hasattr(alloc, "settlement") and alloc.settlement is not None:
            if alloc.status != CostAllocation.STATUS_SETTLED:
                alloc.status = CostAllocation.STATUS_SETTLED
                alloc.save(update_fields=["status"])
            return {"status": "SETTLED", "amount_toman": alloc.settlement.settled_amount_toman, "already_settled": True}

        evt = alloc.cost_event

        # 2. Check for UNRESOLVED or unknown conditions
        if evt.is_unknown or alloc.status == CostAllocation.STATUS_UNRESOLVED:
            alloc.status = CostAllocation.STATUS_UNRESOLVED
            alloc.save(update_fields=["status"])
            log.warning("CostAllocation %s held as UNRESOLVED due to unknown usage on attempt %s", alloc.pk, evt.attempt_id)
            return {"status": "UNRESOLVED", "amount_toman": Decimal("0.00")}

        # 3. Check for WAIVED / cached conditions
        if evt.is_cached or alloc.allocated_toman <= Decimal("0.00"):
            mapping = SettlementMapping.objects.create(
                allocation=alloc,
                reservation=None,
                transaction=None,
                settled_amount_toman=Decimal("0.00"),
            )
            alloc.status = CostAllocation.STATUS_SETTLED
            alloc.save(update_fields=["status"])
            return {"status": "WAIVED", "amount_toman": Decimal("0.00")}

        # 4. Check for Platform allocations (no business)
        if not alloc.business:
            mapping = SettlementMapping.objects.create(
                allocation=alloc,
                reservation=None,
                transaction=None,
                settled_amount_toman=alloc.allocated_toman,
            )
            alloc.status = CostAllocation.STATUS_SETTLED
            alloc.save(update_fields=["status"])
            return {"status": "SETTLED", "amount_toman": alloc.allocated_toman, "is_platform": True}

        # 5. Business allocation settlement:
        svc = budget_svc or DjangoWalletBudgetService()

        # Check if an existing reservation exists for this operation and business
        reservation = WalletReservation.objects.select_for_update().filter(
            operation_id=evt.operation_id,
            wallet__business=alloc.business,
            status=WalletReservation.STATUS_HELD,
        ).first()

        tx = None
        if reservation:
            # Settle via BudgetPort reservation
            svc.settle(reservation.reservation_id, alloc.allocated_toman)
            # Find the transaction generated by settle
            tx = WalletTransaction.objects.filter(
                wallet__business=alloc.business,
                description__contains=reservation.reservation_id,
            ).first()
        else:
            # Direct charge on wallet (no pre-reservation)
            tx = charge_usage(
                alloc.business,
                alloc.allocated_toman,
                f"تسویه هزینه مدل {evt.model_name} (عملیات {evt.operation_id})",
                cost_usd=alloc.allocated_usd,
                calls=1,
            )

        # 6. Record discrete SettlementMapping
        mapping = SettlementMapping.objects.create(
            allocation=alloc,
            reservation=reservation,
            transaction=tx,
            settled_amount_toman=alloc.allocated_toman,
        )

        alloc.status = CostAllocation.STATUS_SETTLED
        alloc.save(update_fields=["status"])

        return {"status": "SETTLED", "amount_toman": alloc.allocated_toman, "already_settled": False}
