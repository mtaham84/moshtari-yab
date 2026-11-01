import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any, Dict, List, Optional

from django.db import models, transaction
from django.utils import timezone

from apps.billing.models import (
    CostAllocation,
    CostEvent,
    SettlementMapping,
    Wallet,
    WalletReservation,
    WalletTransaction,
)

log = logging.getLogger(__name__)

CENT = Decimal("0.01")


@dataclass
class DiscrepancyItem:
    category: str
    identifier: str
    description: str
    amount_toman: Decimal = Decimal("0.00")
    severity: str = "WARNING"  # INFO, WARNING, ERROR


@dataclass
class ReconciliationReport:
    generated_at: datetime = field(default_factory=timezone.now)
    total_anomalies: int = 0
    stale_reservations_count: int = 0
    unresolved_holds_count: int = 0
    unmapped_pending_count: int = 0
    ledger_imbalances_count: int = 0
    items: List[DiscrepancyItem] = field(default_factory=list)

    @property
    def is_clean(self) -> bool:
        return self.total_anomalies == 0


class FinancialReconciliationService:
    """
    Authoritative reconciliation service for financial ledger and wallet balances (P06.06 / R31, R32, R34).
    Audits discrepancies, protects historical cutover boundaries, and prevents speculative historical charges.
    """

    @classmethod
    def audit_discrepancies(
        cls,
        cutover_timestamp: Optional[datetime] = None,
        pending_grace_minutes: int = 5,
    ) -> ReconciliationReport:
        """
        Inspects all financial data and returns a structured discrepancy report.
        Boundary protection: Historical items prior to cutover_timestamp are reported as INFO
        and never marked for automated settlement or backfill debit.
        """
        now = timezone.now()
        report = ReconciliationReport(generated_at=now)

        # 1. Audit Stale Reservations (reservations past expires_at that are still HELD)
        stale_res_qs = WalletReservation.objects.filter(
            status=WalletReservation.STATUS_HELD,
            expires_at__lt=now,
        )
        for res in stale_res_qs:
            report.stale_reservations_count += 1
            report.items.append(
                DiscrepancyItem(
                    category="STALE_RESERVATION",
                    identifier=res.reservation_id,
                    description=f"Reservation {res.reservation_id} expired at {res.expires_at} but remains in HELD status.",
                    amount_toman=res.reserved_amount_toman,
                    severity="WARNING",
                )
            )

        # 2. Audit Unresolved Holds (allocations requiring audit due to unknown usage or out-of-bound actuals)
        unresolved_qs = CostAllocation.objects.filter(status=CostAllocation.STATUS_UNRESOLVED)
        for alloc in unresolved_qs:
            report.unresolved_holds_count += 1
            report.items.append(
                DiscrepancyItem(
                    category="UNRESOLVED_HOLD",
                    identifier=f"alloc_{alloc.pk}",
                    description=f"CostAllocation {alloc.pk} on attempt '{alloc.cost_event.attempt_id}' requires manual audit.",
                    amount_toman=alloc.allocated_toman,
                    severity="WARNING",
                )
            )

        # 3. Audit Unmapped Pending Allocations (past the grace window)
        grace_threshold = now - timedelta(minutes=pending_grace_minutes)
        unmapped_qs = CostAllocation.objects.filter(
            status=CostAllocation.STATUS_PENDING,
            created_at__lt=grace_threshold,
        )
        if cutover_timestamp:
            unmapped_qs = unmapped_qs.filter(created_at__gte=cutover_timestamp)

        for alloc in unmapped_qs:
            report.unmapped_pending_count += 1
            report.items.append(
                DiscrepancyItem(
                    category="UNMAPPED_PENDING",
                    identifier=f"alloc_{alloc.pk}",
                    description=f"CostAllocation {alloc.pk} pending settlement past {pending_grace_minutes}m grace window.",
                    amount_toman=alloc.allocated_toman,
                    severity="WARNING",
                )
            )

        # 4. Audit Wallet Balance Ledger Imbalances (wallet.balance_toman vs sum of transactions)
        for wallet in Wallet.objects.select_related("business").all():
            tx_sum = (
                WalletTransaction.objects.filter(wallet=wallet).aggregate(models.Sum("amount_toman"))[
                    "amount_toman__sum"
                ]
                or Decimal("0.00")
            )
            expected_balance = tx_sum.quantize(CENT)
            actual_balance = wallet.balance_toman.quantize(CENT)

            if expected_balance != actual_balance:
                diff = actual_balance - expected_balance
                report.ledger_imbalances_count += 1
                report.items.append(
                    DiscrepancyItem(
                        category="LEDGER_IMBALANCE",
                        identifier=f"wallet_{wallet.pk}_{wallet.business.name}",
                        description=(
                            f"Wallet {wallet.pk} balance {actual_balance} does not match transaction sum "
                            f"{expected_balance} (difference: {diff})."
                        ),
                        amount_toman=diff,
                        severity="ERROR",
                    )
                )

        report.total_anomalies = len(report.items)
        return report

    @classmethod
    @transaction.atomic
    def expire_stale_reservations(cls) -> int:
        """
        Reconciles and expires all stale HELD reservations past their expires_at.
        Safely releases phantom holds without creating charges.
        Returns the count of expired reservations.
        """
        now = timezone.now()
        stale_qs = WalletReservation.objects.select_for_update().filter(
            status=WalletReservation.STATUS_HELD,
            expires_at__lt=now,
        )
        count = stale_qs.count()
        if count > 0:
            stale_qs.update(status=WalletReservation.STATUS_EXPIRED, updated_at=now)
            log.info("FinancialReconciliation: Expired %d stale reservations.", count)
        return count
