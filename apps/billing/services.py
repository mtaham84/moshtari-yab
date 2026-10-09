"""Wallet operations, usage charging (need_engine.costs → wallets) and read-only numbers for the admin panel."""
from __future__ import annotations

import json
import logging
import re
import time
import urllib.error
import urllib.request
from datetime import timedelta
from decimal import ROUND_HALF_UP, Decimal

from django.conf import settings
from django.db import connection, transaction
from django.db.models import Sum
from django.utils import timezone

from apps.businesses.models import Business

from .models import AIModel, BillingSettings, Provider, Wallet, WalletTransaction

log = logging.getLogger(__name__)
CENT = Decimal("0.01")
BILLING_CURSOR = "billing_costs"


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
                      cost_usd=Decimal("0"), calls: int = 0) -> WalletTransaction:
    """Change a balance and log it. ``amount`` > 0 adds credit, < 0 takes it (the balance may go below zero)."""
    ensure_wallet(business)
    wallet = Wallet.objects.select_for_update().get(business=business)
    amount = Decimal(str(amount)).quantize(CENT, ROUND_HALF_UP)
    wallet.balance_toman = (wallet.balance_toman + amount).quantize(CENT)
    wallet.save(update_fields=["balance_toman", "updated_at"])
    return WalletTransaction.objects.create(
        wallet=wallet, kind=kind, amount_toman=amount, balance_after=wallet.balance_toman,
        cost_usd=Decimal(str(cost_usd)).quantize(Decimal("0.000001")), calls=calls,
        description=description[:255], created_by=user if getattr(user, "is_authenticated", False) else None)


def is_blocked(business: Business) -> bool:
    s = BillingSettings.get()
    if not s.enforce_balance:
        return False
    return ensure_wallet(business).balance_toman <= s.min_balance_toman


# ── engine tables (read-only from here) ───────────────────────────────────────
def _schema() -> str:
    s = settings.NEED_ENGINE_SCHEMA
    if not re.fullmatch(r"[a-z_][a-z0-9_]*", s):
        raise ValueError(f"invalid schema: {s!r}")
    return s


def _table_exists(name: str) -> bool:
    with connection.cursor() as c:
        c.execute("SELECT to_regclass(%s)", [f"{_schema()}.{name}"])
        return c.fetchone()[0] is not None


def _rows(sql: str, params=()) -> list[dict]:
    with connection.cursor() as c:
        c.execute(sql.replace("{s}", _schema()), list(params))
        cols = [d[0] for d in c.description]
        return [dict(zip(cols, r)) for r in c.fetchall()]


def charge_pending_usage(grace_seconds: float = 5.0) -> dict:
    """Turn new rows of ``need_engine.costs`` into one USAGE transaction per seller.

    charge = real cost ($) × USD→Toman rate × (1 + markup). Platform rows (no business) only move the cursor.
    Idempotent: the last processed cost id is kept in ``EngineSyncCursor(name="billing_costs")``.
    """
    from apps.discovery.models import EngineSyncCursor

    if not _table_exists("costs"):
        return {"businesses": 0, "rows": 0, "toman": Decimal("0")}
    cfg = BillingSettings.get()
    with transaction.atomic():
        cursor, _ = EngineSyncCursor.objects.select_for_update().get_or_create(name=BILLING_CURSOR)
        top = _rows("SELECT MAX(id) AS m FROM {s}.costs WHERE id > %s AND ts < %s",
                    (cursor.position, time.time() - grace_seconds))[0]["m"]
        if top is None:
            return {"businesses": 0, "rows": 0, "toman": Decimal("0")}
        cached = "" if cfg.charge_cached else "AND NOT cached"
        groups = _rows(f"""SELECT business_id, COUNT(*) AS calls, COALESCE(SUM(usd), 0) AS usd FROM {{s}}.costs
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
            apply_transaction(b, -amount, WalletTransaction.USAGE, f"مصرف هوش مصنوعی ({g['calls']} فراخوانی)",
                              cost_usd=usd, calls=int(g["calls"]))
            total += amount
            n += 1
        cursor.position = top
        cursor.save(update_fields=["position", "updated_at"])
    if n:
        log.info("billing: charged %d sellers %s toman (%d cost rows)", n, total, rows)
    return {"businesses": n, "rows": rows, "toman": total}


def heartbeat(name: str) -> None:
    """Liveness marker for the «وضعیت سرویس‌ها» card (stored in EngineSyncCursor, position = unix time)."""
    from apps.discovery.models import EngineSyncCursor

    EngineSyncCursor.objects.update_or_create(name=f"heartbeat:{name}", defaults={"position": int(time.time())})


def waiting_payment() -> dict[str, dict]:
    """Work held by need_engine for sellers without balance (kv ``waiting_payment``, refreshed every engine run):
    {business_id: {"messages", "needs", "products"}}. Processed automatically after a top-up."""
    try:
        if not _table_exists("kv"):
            return {}
        rows = _rows("SELECT v FROM {s}.kv WHERE k = 'waiting_payment'")
    except Exception:  # pragma: no cover - engine schema not reachable
        return {}
    if not rows:
        return {}
    v = rows[0]["v"] if isinstance(rows[0]["v"], dict) else json.loads(rows[0]["v"])
    return {str(k): {x: int(d.get(x, 0) or 0) for x in ("messages", "needs", "products")}
            for k, d in (v.get("sellers") or {}).items()}


# ── numbers for the admin panel ───────────────────────────────────────────────
def _since(days: float) -> float:
    return time.time() - days * 86400


def cost_totals(days: float) -> dict:
    if not _table_exists("costs"):
        return {"calls": 0, "usd": 0.0, "toman": 0.0, "tokens": 0}
    r = _rows("""SELECT COUNT(*) FILTER (WHERE part = 0) AS calls, COALESCE(SUM(usd), 0) AS usd,
                 COALESCE(SUM(toman), 0) AS toman, COALESCE(SUM(prompt_tokens + completion_tokens), 0) AS tokens
                 FROM {s}.costs WHERE ts >= %s""", (_since(days),))[0]
    return {k: float(v or 0) for k, v in r.items()}


def cost_breakdown(days: float, by: str) -> list[dict]:
    col = {"model": "model", "stage": "stage", "business": "business_id"}[by]
    if not _table_exists("costs"):
        return []
    rows = _rows(f"""SELECT {col} AS key, COUNT(*) FILTER (WHERE part = 0 OR %s) AS calls,
                     SUM(cached::int) AS cached, SUM(prompt_tokens) AS pt, SUM(completion_tokens) AS ct,
                     COALESCE(SUM(usd), 0) AS usd, COALESCE(SUM(toman), 0) AS toman
                     FROM {{s}}.costs WHERE ts >= %s GROUP BY {col} ORDER BY SUM(usd) DESC NULLS LAST""",
                 (by == "business", _since(days)))
    if by == "business":
        names = dict(Business.objects.values_list("pk", "name"))
        for r in rows:
            r["label"] = names.get(int(r["key"]), f"#{r['key']}") if str(r["key"] or "").isdigit() else "پلتفرم (منابع عمومی)"
    return rows


def cost_daily(days: int = 14) -> list[dict]:
    if not _table_exists("costs"):
        return []
    rows = _rows("""SELECT to_char(to_timestamp(ts) AT TIME ZONE %s, 'YYYY-MM-DD') AS day,
                    COALESCE(SUM(toman), 0) AS toman, COUNT(*) FILTER (WHERE part = 0) AS calls
                    FROM {s}.costs WHERE ts >= %s GROUP BY 1 ORDER BY 1""", (settings.TIME_ZONE, _since(days)))
    top = max([float(r["toman"]) for r in rows] or [0]) or 1
    for r in rows:
        r["pct"] = round(float(r["toman"]) / top * 100)
    return rows


def cost_ledger(limit: int = 100, offset: int = 0, business: str = "", model: str = "", stage: str = "") -> list[dict]:
    if not _table_exists("costs"):
        return []
    where, params = ["TRUE"], []
    for col, val in (("business_id", business), ("model", model), ("stage", stage)):
        if val:
            where.append(f"{col} = %s")
            params.append(val)
    rows = _rows(f"""SELECT id, to_timestamp(ts) AS at, stage, model, prompt_tokens, completion_tokens, cached, usd, toman,
                     business_id, ref FROM {{s}}.costs WHERE {' AND '.join(where)} ORDER BY id DESC LIMIT %s OFFSET %s""",
                 params + [limit, offset])
    names = dict(Business.objects.values_list("pk", "name"))
    for r in rows:
        b = r["business_id"]
        r["business"] = names.get(int(b), f"#{b}") if str(b or "").isdigit() else "پلتفرم"
    return rows


def revenue(days: float) -> Decimal:
    since = timezone.now() - timedelta(days=days)
    v = WalletTransaction.objects.filter(kind=WalletTransaction.USAGE, created_at__gte=since).aggregate(s=Sum("amount_toman"))["s"]
    return -(v or Decimal("0"))


# ── service status ────────────────────────────────────────────────────────────
def _ago(seconds: float | None) -> str:
    if seconds is None:
        return "هرگز"
    if seconds < 90:
        return f"{int(seconds)} ثانیه پیش"
    if seconds < 5400:
        return f"{int(seconds // 60)} دقیقه پیش"
    if seconds < 172800:
        return f"{int(seconds // 3600)} ساعت پیش"
    return f"{int(seconds // 86400)} روز پیش"


def _state(age: float | None, ok: float, warn: float) -> str:
    if age is None:
        return "down"
    return "ok" if age <= ok else ("warn" if age <= warn else "down")


def service_status() -> list[dict]:
    from apps.discovery.models import EngineSyncCursor, MonitoredCommunity

    now = time.time()
    out = []
    try:
        with connection.cursor() as c:
            c.execute("SELECT 1")
        out.append({"name": "پایگاه داده (PostgreSQL)", "state": "ok", "detail": "در دسترس"})
    except Exception as e:  # pragma: no cover
        out.append({"name": "پایگاه داده (PostgreSQL)", "state": "down", "detail": str(e)[:120]})
        return out

    # need_engine: heartbeat written at the end of every run (need_engine/engine.py)
    hb = _rows("SELECT v FROM {s}.kv WHERE k = 'heartbeat'") if _table_exists("kv") else []
    pending = _rows("SELECT COUNT(*) AS n FROM {s}.pending")[0]["n"] if _table_exists("pending") else 0
    if hb:
        v = hb[0]["v"] if isinstance(hb[0]["v"], dict) else json.loads(hb[0]["v"])
        age = now - float(v.get("ts", 0))
        detail = f"آخرین اجرا {_ago(age)} · {pending} پیام در صف"
        state = _state(age, 300, 1800)
        if v.get("errors"):
            state = "warn" if state == "ok" else state
            detail += f" · خطا: {str(v['errors'][-1])[:100]}"
        out.append({"name": "موتور تحلیل (need_engine)", "state": state, "detail": detail})
    else:
        out.append({"name": "موتور تحلیل (need_engine)", "state": "down", "detail": "هنوز اجرا نشده است"})

    # Telegram crawler: newest archived message + community states
    crawler_schema = re.sub(r"[^a-z0-9_]", "", (getattr(settings, "TG_DB_SCHEMA", "") or "crawler"))
    age = None
    with connection.cursor() as c:
        c.execute("SELECT to_regclass(%s)", [f"{crawler_schema}.tg_messages"])
        if c.fetchone()[0]:
            c.execute(f"SELECT EXTRACT(EPOCH FROM now() - MAX(date)) FROM {crawler_schema}.tg_messages")
            r = c.fetchone()[0]
            age = float(r) if r is not None else None
    counts = {s: MonitoredCommunity.objects.filter(sync_status=s).count() for s in ("ACTIVE", "PENDING", "ERROR", "PAUSED")}
    detail = (f"آخرین پیام {_ago(age)} · در حال پایش {counts['ACTIVE']} · در صف {counts['PENDING']} · "
              f"خطا {counts['ERROR']} · متوقف {counts['PAUSED']}")
    state = _state(age, 3600, 6 * 3600) if counts["ACTIVE"] else ("warn" if counts["PENDING"] else "unknown")
    if counts["ERROR"] and state == "ok":
        state = "warn"
    out.append({"name": "کراولر تلگرام", "state": state, "detail": detail})

    # sync_opportunities + billing (same process)
    beat = EngineSyncCursor.objects.filter(name="heartbeat:sync").first()
    age = now - beat.position if beat else None
    out.append({"name": "همگام‌سازی فرصت‌ها و کسر هزینه (sync)", "state": _state(age, 120, 900),
                "detail": f"آخرین اجرا {_ago(age)}"})

    # providers configured?
    active = AIModel.objects.filter(is_active=True, provider__is_active=True)
    roles = [r for r, f in (("استخراج", "use_extract"), ("تطبیق", "use_verify"), ("پاسخ", "use_reply"))
             if not active.filter(**{f: True}).exists()]
    if not active.exists():
        out.append({"name": "مدل‌های هوش مصنوعی", "state": "warn",
                    "detail": "مدلی در پنل تعریف نشده؛ موتور از تنظیمات NE_* فایل ‎.env استفاده می‌کند"})
    else:
        out.append({"name": "مدل‌های هوش مصنوعی", "state": "warn" if roles else "ok",
                    "detail": f"{active.count()} مدل فعال" + (f" · بدون مدل برای: {'، '.join(roles)} (از ‎.env)" if roles else "")})
    return out


def test_provider(provider: Provider) -> tuple[bool, str]:
    """GET {base_url}/models with the key — a cheap way to see if the endpoint and key work."""
    req = urllib.request.Request(provider.base_url.rstrip("/") + "/models",
                                 headers={"Authorization": f"Bearer {provider.api_key}"})
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            data = json.loads(r.read().decode("utf-8", "replace") or "{}")
            n = len(data.get("data") or data.get("models") or [])
            return True, f"اتصال برقرار است (HTTP {r.status}، {n} مدل)"
    except urllib.error.HTTPError as e:
        return False, f"HTTP {e.code}: {e.read().decode('utf-8', 'replace')[:200]}"
    except Exception as e:
        return False, f"خطای اتصال: {e}"


def import_from_env() -> tuple[Provider, int]:
    """Create a provider + models from the current NE_* settings (first-time setup)."""
    from need_engine.config import EngineConfig

    cfg = EngineConfig()
    # the key is not copied: an empty key means «use NE_LLM_API_KEY», so .env stays the single place for it
    provider, _ = Provider.objects.get_or_create(base_url=cfg.llm_base_url.rstrip("/"),
                                                 defaults={"name": "پیش‌فرض (‎.env)", "notes": "کلید از NE_LLM_API_KEY"})
    names = dict.fromkeys([cfg.extract_model, cfg.verify_model, cfg.reply_model, *cfg.prices.keys()])
    created = 0
    for name in names:
        pin, pout = (cfg.prices.get(name) or (0, 0))
        lim = cfg.rate_limits.get(name) or {}
        _, made = AIModel.objects.get_or_create(provider=provider, name=name, defaults={
            "input_price_usd": Decimal(str(pin)), "output_price_usd": Decimal(str(pout)),
            "use_extract": name == cfg.extract_model, "use_verify": name == cfg.verify_model,
            "use_reply": name == cfg.reply_model, "rpm": lim.get("rpm"), "tpm": lim.get("tpm"), "rpd": lim.get("rpd")})
        created += int(made)
    if cfg.embed_backend == "gemini":
        _, made = AIModel.objects.get_or_create(provider=provider, name=cfg.embed_model, defaults={
            "label": "Embedding", "input_price_usd": Decimal(str(cfg.embed_price_per_m))})
        created += int(made)
    return provider, created
