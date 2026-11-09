"""Wallet operations, usage charging (need_engine.costs → wallets) and read-only numbers for the admin panel."""
from __future__ import annotations

import json
import logging
import time
from decimal import ROUND_HALF_UP, Decimal

from django.db import transaction
from django.db.models import Q

from apps.businesses.models import Business

from .models import BillingSettings, Wallet, WalletTransaction

from .engine_tables import (billed_usd_expr, engine_rows, engine_table_exists)

log = logging.getLogger(__name__)
CENT = Decimal("0.01")
BILLING_CURSOR = "billing_costs"


# ── pricing ───────────────────────────────────────────────────────────────────
def toman_per_usd(cfg: BillingSettings | None = None) -> Decimal:
    """What one USD of provider cost costs the seller in toman (exchange rate × markup multiplier)."""
    cfg = cfg or BillingSettings.get()
    return cfg.usd_to_toman * cfg.multiplier


def billed_toman(cost_usd, cfg: BillingSettings | None = None) -> Decimal:
    """The single place that converts a provider cost (USD) to what the seller pays (toman, incl. markup)."""
    return (Decimal(str(cost_usd or 0)) * toman_per_usd(cfg)).quantize(CENT, ROUND_HALF_UP)


# ── wallets ───────────────────────────────────────────────────────────────────
def ensure_wallet(business: Business, with_gift: bool = False) -> Wallet:
    wallet, created = Wallet.objects.get_or_create(business=business)
    if created and with_gift:
        gift = BillingSettings.get().signup_credit_toman
        if gift > 0:
            apply_transaction(business, gift, WalletTransaction.GIFT, "اعتبار هدیه‌ی ثبت‌نام")
            wallet.refresh_from_db()
    return wallet


@transaction.atomic
def apply_transaction(business: Business, amount, kind: str, description: str = "", user=None,
                      cost_usd=Decimal("0"), calls: int = 0) -> WalletTransaction | None:
    """Change a balance and log it; background usage debits are clipped at the spendable balance floor."""
    ensure_wallet(business)
    wallet = Wallet.objects.select_for_update().get(business=business)
    amount = Decimal(str(amount)).quantize(CENT, ROUND_HALF_UP)
    cost_usd = Decimal(str(cost_usd))
    if kind == WalletTransaction.USAGE and amount < 0:
        cfg = BillingSettings.get()
        floor = max(Decimal("0"), cfg.min_balance_toman) if cfg.enforce_balance else Decimal("0")
        requested = -amount
        available = max(Decimal("0"), wallet.balance_toman - floor)
        charged = min(requested, available).quantize(CENT, ROUND_HALF_UP)
        if charged <= 0:
            return None
        if charged < requested:
            cost_usd *= charged / requested
            description = (description + " (محدود به اعتبار باقی‌مانده)")[:255]
        amount = -charged
    wallet.balance_toman = (wallet.balance_toman + amount).quantize(CENT)
    wallet.save(update_fields=["balance_toman", "updated_at"])
    return WalletTransaction.objects.create(
        wallet=wallet, kind=kind, amount_toman=amount, balance_after=wallet.balance_toman,
        cost_usd=cost_usd.quantize(Decimal("0.000001"), ROUND_HALF_UP), calls=calls,
        description=description[:255], created_by=user if getattr(user, "is_authenticated", False) else None)


@transaction.atomic
def charge_usage(business: Business, amount, description: str = "", *, cost_usd=Decimal("0"), calls: int = 0):
    """Charge background usage without overdrawing a seller; partial charges stop exactly at the spendable floor."""
    return apply_transaction(business, -abs(Decimal(str(amount))), WalletTransaction.USAGE, description,
                             cost_usd=cost_usd, calls=calls)


def balance_is_blocked(balance, cfg: BillingSettings) -> bool:
    """Single source of truth for "this wallet is out of credit".

    need_engine.registry.balance_blocked mirrors this rule for the engine process (no Django there);
    apps.billing.tests.BalanceBlockedRuleTests keeps the two in sync."""
    balance = balance or 0
    return balance <= 0 or bool(cfg.enforce_balance and balance <= cfg.min_balance_toman)


def blocked_wallets_q(cfg: BillingSettings) -> Q:
    """`balance_is_blocked` as a queryset filter on Wallet (for counting/listing blocked sellers)."""
    q = Q(balance_toman__lte=0)
    if cfg.enforce_balance:
        q |= Q(balance_toman__lte=cfg.min_balance_toman)
    return q


def is_blocked(business: Business) -> bool:
    return balance_is_blocked(ensure_wallet(business).balance_toman, BillingSettings.get())


def charge_pending_usage(grace_seconds: float = 5.0) -> dict:
    """Turn new rows of ``need_engine.costs`` into one USAGE transaction per seller.

    charge = real cost ($) × USD→Toman rate × (1 + markup). Platform rows (no business) only move the cursor.
    Idempotent: the last processed cost id is kept in ``EngineSyncCursor(name="billing_costs")``.
    """
    from apps.discovery.models import EngineSyncCursor

    if not engine_table_exists("costs"):
        return {"businesses": 0, "rows": 0, "toman": Decimal("0")}
    cfg = BillingSettings.get()
    with transaction.atomic():
        cursor, _ = EngineSyncCursor.objects.select_for_update().get_or_create(name=BILLING_CURSOR)
        top = engine_rows("SELECT MAX(id) AS m FROM {s}.costs WHERE id > %s AND ts < %s",
                    (cursor.position, time.time() - grace_seconds))[0]["m"]
        if top is None:
            return {"businesses": 0, "rows": 0, "toman": Decimal("0")}
        cached = "" if cfg.charge_cached else "AND NOT cached"
        groups = engine_rows(f"""SELECT business_id, COUNT(*) AS calls, COALESCE(SUM({billed_usd_expr()}), 0) AS usd FROM {{s}}.costs
                           WHERE id > %s AND id <= %s AND business_id IS NOT NULL {cached}
                           GROUP BY business_id""", (cursor.position, top))
        rows = top - cursor.position
        businesses = {str(b.pk): b for b in Business.objects.filter(
            pk__in=[int(g["business_id"]) for g in groups if str(g["business_id"]).isdigit()])}
        total = Decimal("0")
        rate = cfg.usd_to_toman * cfg.multiplier
        n = 0
        for g in groups:
            b = businesses.get(str(g["business_id"]))
            usd = Decimal(str(g["usd"] or 0))
            amount = (usd * rate).quantize(CENT, ROUND_HALF_UP)
            if b is None or amount <= 0:
                continue
            tx = charge_usage(b, amount, f"مصرف هوش مصنوعی ({g['calls']} فراخوانی)",
                              cost_usd=usd, calls=int(g["calls"]))
            if tx:
                total += -tx.amount_toman
                n += 1
        cursor.position = top
        cursor.save(update_fields=["position", "updated_at"])
    if n:
        log.info("billing: charged %d sellers %s toman (%d cost rows)", n, total, rows)
    return {"businesses": n, "rows": rows, "toman": total}


def waiting_payment() -> dict[str, dict]:
    """Work held by need_engine for sellers without balance (kv ``waiting_payment``, refreshed every engine run):
    {business_id: {"messages", "needs", "products"}}. Processed automatically after a top-up."""
    try:
        if not engine_table_exists("kv"):
            return {}
        rows = engine_rows("SELECT v FROM {s}.kv WHERE k = 'waiting_payment'")
    except Exception:  # pragma: no cover - engine schema not reachable
        return {}
    if not rows:
        return {}
    v = rows[0]["v"] if isinstance(rows[0]["v"], dict) else json.loads(rows[0]["v"])
    return {str(k): {x: int(d.get(x, 0) or 0) for x in ("messages", "needs", "products")}
            for k, d in (v.get("sellers") or {}).items()}
