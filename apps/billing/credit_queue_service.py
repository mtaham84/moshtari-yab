import logging
import uuid
from decimal import Decimal, ROUND_HALF_UP
from typing import List, Optional

from django.db import transaction
from django.utils import timezone

from apps.businesses.models import Business
from apps.core.gateway_contract import BudgetPort, OperationRef
from apps.billing.models import CreditWaitingQueue
from apps.billing.wallet_budget_service import DjangoWalletBudgetService

log = logging.getLogger(__name__)

CENT = Decimal("0.01")


class CreditQueueService:
    """
    Authoritative service managing jobs waiting for wallet credit (P06.04 / R32, R07).
    Single source of truth for waiting_for_credit.
    """

    @classmethod
    @transaction.atomic
    def enqueue_waiting_work(
        cls,
        business: Business,
        operation_id: str,
        stage: str,
        payload: dict,
        estimated_toman: Decimal,
    ) -> CreditWaitingQueue:
        """
        Enqueues or updates a work item waiting for credit.
        Idempotent: Re-enqueueing the same operation_id updates the existing waiting record
        rather than creating duplicate queue items.
        Zero paid calls, zero debits.
        """
        estimated_toman = Decimal(str(estimated_toman)).quantize(CENT, ROUND_HALF_UP)

        existing = CreditWaitingQueue.objects.select_for_update().filter(
            business=business,
            operation_id=operation_id,
            status=CreditWaitingQueue.STATUS_WAITING,
        ).first()

        now = timezone.now()
        if existing:
            existing.retry_count += 1
            existing.payload = payload
            existing.estimated_toman = estimated_toman
            existing.last_checked_at = now
            existing.save(update_fields=["retry_count", "payload", "estimated_toman", "last_checked_at", "updated_at"])
            return existing

        work_item_id = f"work_{operation_id[:16]}_{uuid.uuid4().hex[:8]}"
        item = CreditWaitingQueue.objects.create(
            work_item_id=work_item_id,
            operation_id=operation_id,
            business=business,
            stage=stage,
            payload=payload,
            estimated_toman=estimated_toman,
            status=CreditWaitingQueue.STATUS_WAITING,
            last_checked_at=now,
        )
        log.info(
            "Enqueued work item %s for business %s (operation %s, stage %s, estimated %s toman)",
            work_item_id, business.pk, operation_id, stage, estimated_toman,
        )
        return item

    @classmethod
    @transaction.atomic
    def check_and_resume_eligible(
        cls,
        business: Business,
        budget_service: Optional[BudgetPort] = None,
    ) -> List[CreditWaitingQueue]:
        """
        Inspects waiting items for the business in FIFO order and checks budget reservation eligibility.
        Resumes items only if budget can be reserved; stops at the first insufficient fund item.
        Never revives CANCELLED or EXPIRED jobs.
        """
        budget_svc = budget_service or DjangoWalletBudgetService()

        waiting_items = list(
            CreditWaitingQueue.objects.select_for_update()
            .filter(business=business, status=CreditWaitingQueue.STATUS_WAITING)
            .order_by("created_at", "id")
        )

        resumed_items: List[CreditWaitingQueue] = []
        now = timezone.now()

        for item in waiting_items:
            # OperationRef for the waiting work item
            op = OperationRef(
                operation_id=item.operation_id,
                kind=item.stage,
                payer_tenant_id=str(business.pk),
            )

            # Check eligibility and reserve funds atomically
            allowed, reason, res_id = budget_svc.reserve(op, item.estimated_toman)
            item.last_checked_at = now

            if allowed:
                item.status = CreditWaitingQueue.STATUS_RESUMED
                item.save(update_fields=["status", "last_checked_at", "updated_at"])
                resumed_items.append(item)
                log.info(
                    "Resumed waiting work item %s for business %s (res_id: %s)",
                    item.work_item_id, business.pk, res_id,
                )
            else:
                item.retry_count += 1
                item.save(update_fields=["retry_count", "last_checked_at", "updated_at"])
                log.info(
                    "Work item %s still ineligible for business %s: %s",
                    item.work_item_id, business.pk, reason,
                )
                # Funds exhausted for remaining items in FIFO queue
                break

        return resumed_items

    @classmethod
    @transaction.atomic
    def cancel_waiting_work(cls, work_item_id: str) -> bool:
        """
        Explicitly cancels a waiting work item so it will not auto-resume on top-up.
        """
        item = CreditWaitingQueue.objects.select_for_update().filter(work_item_id=work_item_id).first()
        if not item:
            return False

        if item.status == CreditWaitingQueue.STATUS_WAITING:
            item.status = CreditWaitingQueue.STATUS_CANCELLED
            item.save(update_fields=["status", "updated_at"])
            return True
        return False

    @classmethod
    def get_waiting_summary(cls, business: Business) -> dict:
        """
        Summary of work items waiting for credit for display in seller panel.
        """
        qs = CreditWaitingQueue.objects.filter(business=business, status=CreditWaitingQueue.STATUS_WAITING)
        count = qs.count()
        total_toman = sum((q.estimated_toman for q in qs), Decimal("0"))
        by_stage = {}
        for q in qs:
            by_stage[q.stage] = by_stage.get(q.stage, 0) + 1
        return {
            "count": count,
            "total_estimated_toman": total_toman,
            "by_stage": by_stage,
        }
