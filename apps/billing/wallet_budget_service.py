import logging
import uuid
from datetime import timedelta
from decimal import Decimal, ROUND_HALF_UP
from typing import List, Optional, Tuple

from django.db import models, transaction
from django.utils import timezone

from apps.businesses.models import Business
from apps.core.gateway_contract import BudgetPort, OperationRef
from apps.billing.models import AIModel, BillingSettings, Wallet, WalletReservation, WalletTransaction

log = logging.getLogger(__name__)

CENT = Decimal("0.01")
RESERVATION_EXPIRY_MINUTES = 15


class InsufficientFundsError(Exception):
    """Raised when wallet does not have sufficient spendable balance."""
    pass


class UnboundedCostError(ValueError):
    """Raised when no reliable pricing bound exists for an operation."""
    pass


def compute_conservative_bound(
    candidate_models: List[AIModel],
    max_output_tokens: int = 1500,
    estimated_input_tokens: int = 2000,
) -> Decimal:
    """
    Computes conservative maximum cost in Toman across all candidate models (P06.03 / R32).
    Takes the maximum possible cost considering token ceiling and currency snapshot.
    If no candidate models or invalid bounds, raises UnboundedCostError.
    """
    if not candidate_models:
        raise UnboundedCostError("No candidate models provided to compute conservative cost bound.")

    cfg = BillingSettings.get()
    rate = cfg.usd_to_toman
    multiplier = cfg.multiplier

    max_toman = Decimal("0")
    for m in candidate_models:
        in_price = m.input_price_usd or Decimal("0")
        out_price = m.output_price_usd or Decimal("0")
        if in_price <= 0 and out_price <= 0:
            # Free model
            continue
        in_cost_usd = (Decimal(str(estimated_input_tokens)) / Decimal("1000000")) * in_price
        out_cost_usd = (Decimal(str(max_output_tokens)) / Decimal("1000000")) * out_price
        total_usd = in_cost_usd + out_cost_usd
        toman = (total_usd * rate * multiplier).quantize(CENT, ROUND_HALF_UP)
        if toman > max_toman:
            max_toman = toman

    # Fallback minimal bound if all candidate models are free or zero
    if max_toman <= Decimal("0"):
        return Decimal("1.00")

    return max_toman


class DjangoWalletBudgetService(BudgetPort):
    """
    Authoritative Django DB implementation of BudgetPort (P06.03 / R32).
    Enforces atomic credit reservations with row-level locks (SELECT FOR UPDATE).
    Zero platform debt, zero un-backed provider calls.
    """

    @transaction.atomic
    def reserve(self, operation: OperationRef, estimated_toman: Decimal) -> Tuple[bool, str, Optional[str]]:
        """
        Attempts to reserve estimated funds for an operation.
        Returns: (is_allowed, reason, reservation_id)
        """
        estimated_toman = Decimal(str(estimated_toman)).quantize(CENT, ROUND_HALF_UP)
        payer_id = operation.payer_tenant_id

        # Platform call / no payer tenant: bypass wallet reservation
        if not payer_id or payer_id in ("platform", "platform_system"):
            return (True, "PLATFORM_CALL", None)

        if payer_id.startswith("tenant_"):
            payer_id = payer_id.replace("tenant_", "")

        try:
            biz = Business.objects.get(pk=int(payer_id)) if str(payer_id).isdigit() else None
        except Business.DoesNotExist:
            return (False, "BUSINESS_NOT_FOUND", None)

        if not biz:
            return (False, "INVALID_PAYER", None)

        # 1. Acquire row lock on Wallet
        wallet, _ = Wallet.objects.select_for_update().get_or_create(business=biz)

        # 2. Check for existing active reservation on this operation (Idempotency)
        existing_res = WalletReservation.objects.filter(
            operation_id=operation.operation_id,
            wallet=wallet,
            status=WalletReservation.STATUS_HELD,
            expires_at__gt=timezone.now(),
        ).first()

        if existing_res:
            # Replaying same operation does NOT double-reserve!
            return (True, "ALREADY_RESERVED", existing_res.reservation_id)

        # 3. Compute currently active held reservations
        now = timezone.now()
        held_agg = WalletReservation.objects.filter(
            wallet=wallet,
            status=WalletReservation.STATUS_HELD,
            expires_at__gt=now,
        ).aggregate(models.Sum("reserved_amount_toman"))
        held_amount = held_agg["reserved_amount_toman__sum"] or Decimal("0")

        # 4. Compute spendable balance
        cfg = BillingSettings.get()
        floor = max(Decimal("0"), cfg.min_balance_toman) if cfg.enforce_balance else Decimal("0")
        available = max(Decimal("0"), wallet.balance_toman - held_amount - floor)

        if available < estimated_toman:
            log.warning(
                "Budget reservation rejected for business %s (operation %s): available %s < required %s",
                payer_id, operation.operation_id, available, estimated_toman,
            )
            return (False, "INSUFFICIENT_FUNDS", None)

        # 5. Create reservation
        reservation_id = f"res_{operation.operation_id[:16]}_{uuid.uuid4().hex[:8]}"
        res = WalletReservation.objects.create(
            reservation_id=reservation_id,
            operation_id=operation.operation_id,
            wallet=wallet,
            reserved_amount_toman=estimated_toman,
            status=WalletReservation.STATUS_HELD,
            expires_at=now + timedelta(minutes=RESERVATION_EXPIRY_MINUTES),
        )

        return (True, "RESERVED", res.reservation_id)

    @transaction.atomic
    def settle(self, reservation_id: str, actual_toman: Decimal) -> bool:
        """
        Debits the actual consumed cost and releases the reserved funds.
        """
        actual_toman = Decimal(str(actual_toman)).quantize(CENT, ROUND_HALF_UP)
        try:
            res = WalletReservation.objects.select_for_update().get(reservation_id=reservation_id)
        except WalletReservation.DoesNotExist:
            log.error("Cannot settle unknown reservation: %s", reservation_id)
            return False

        if res.status == WalletReservation.STATUS_SETTLED:
            # Already settled idempotently
            return True

        wallet = Wallet.objects.select_for_update().get(pk=res.wallet_id)

        # Deduct actual cost from wallet
        if actual_toman > Decimal("0"):
            wallet.balance_toman = (wallet.balance_toman - actual_toman).quantize(CENT)
            wallet.save(update_fields=["balance_toman", "updated_at"])

            WalletTransaction.objects.create(
                wallet=wallet,
                kind=WalletTransaction.USAGE,
                amount_toman=-actual_toman,
                balance_after=wallet.balance_toman,
                description=f"تسویه رزرو {reservation_id}",
            )

        res.status = WalletReservation.STATUS_SETTLED
        res.save(update_fields=["status", "updated_at"])
        return True

    @transaction.atomic
    def release(self, reservation_id: str) -> bool:
        """
        Releases reserved funds in case of operation cancellation or error.
        """
        try:
            res = WalletReservation.objects.select_for_update().get(reservation_id=reservation_id)
        except WalletReservation.DoesNotExist:
            return False

        if res.status == WalletReservation.STATUS_HELD:
            res.status = WalletReservation.STATUS_RELEASED
            res.save(update_fields=["status", "updated_at"])
        return True

    def reconcile(self, reservation_id: str) -> str:
        """
        Returns status of reservation for reconciliation.
        """
        res = WalletReservation.objects.filter(reservation_id=reservation_id).first()
        if not res:
            return "NOT_FOUND"
        if res.status == WalletReservation.STATUS_HELD and res.expires_at <= timezone.now():
            return "EXPIRED"
        return res.status
