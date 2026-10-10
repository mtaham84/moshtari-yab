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


def _has_column(table: str, column: str) -> bool:
    with connection.cursor() as c:
        c.execute("SELECT 1 FROM information_schema.columns WHERE table_schema = %s AND table_name = %s AND column_name = %s",
                  [_schema(), table, column])
        return c.fetchone() is not None


def _billed() -> str:
    """Billed share of a cost row: a chat watched privately by several sellers is analysed once, each owner pays it all."""
    return "usd * charge_factor" if _has_column("costs", "charge_factor") else "usd"


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
        groups = _rows(f"""SELECT business_id, COUNT(*) AS calls, COALESCE(SUM({_billed()}), 0) AS usd FROM {{s}}.costs
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
                     business_id, ref, {"charge_factor" if _has_column("costs", "charge_factor") else "1"} AS charge_factor FROM {{s}}.costs WHERE {' AND '.join(where)} ORDER BY id DESC LIMIT %s OFFSET %s""",
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
    x = x_collector_service()
    if x:
        out.append(x)

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


# ── Telegram groups: one row per group (however many sources point to it) ────
def _crawler_schema() -> str:
    return re.sub(r"[^a-z0-9_]", "", (getattr(settings, "TG_DB_SCHEMA", "") or "crawler")) or "crawler"


def _crawler_table_exists(name: str) -> bool:
    with connection.cursor() as c:
        c.execute("SELECT to_regclass(%s)", [f"{_crawler_schema()}.{name}"])
        return c.fetchone()[0] is not None


def group_engine_stats() -> dict[str, dict]:
    """Per Telegram chat id: real AI cost (need extraction + verify/reply of its needs), what sellers are billed for it,
    messages analysed, needs, engine opportunities and the opportunities delivered to sellers."""
    out: dict[str, dict] = {}

    def row(chat) -> dict:
        return out.setdefault(str(chat), {"usd": 0.0, "toman": 0.0, "billed_usd": 0.0, "analysed": 0, "needs": 0,
                                          "open_needs": 0, "opportunities": 0, "seller_opportunities": 0})

    try:
        if _table_exists("costs") and _table_exists("needs"):
            cfg = BillingSettings.get()
            cached = "TRUE" if cfg.charge_cached else "NOT cached"
            billed = _billed()
            for r in _rows(f"""
                    WITH x AS (
                        SELECT split_part(c.ref, ':', 1) AS chat, c.* FROM {{s}}.costs c WHERE c.stage = 'need_extraction'
                        UNION ALL
                        SELECT n.chat_id AS chat, c.* FROM {{s}}.costs c
                        JOIN {{s}}.needs n ON n.need_id = split_part(c.ref, ':', 1)
                        WHERE c.stage <> 'need_extraction' AND c.ref LIKE 'need\\_%%')
                    SELECT chat, COALESCE(SUM(usd), 0) AS usd, COALESCE(SUM(toman), 0) AS toman,
                           COALESCE(SUM({billed}) FILTER (WHERE business_id IS NOT NULL AND {cached}), 0) AS billed_usd
                    FROM x GROUP BY chat"""):
                d = row(r["chat"])
                d["usd"], d["toman"], d["billed_usd"] = float(r["usd"]), float(r["toman"]), float(r["billed_usd"])
            for r in _rows("""SELECT chat_id, COUNT(*) AS n, COUNT(*) FILTER (WHERE status = 'open') AS o
                              FROM {s}.needs GROUP BY chat_id"""):
                row(r["chat_id"]).update(needs=int(r["n"]), open_needs=int(r["o"]))
        if _table_exists("chat_analysed"):
            for r in _rows("SELECT chat_id, n FROM {s}.chat_analysed"):
                row(r["chat_id"])["analysed"] = int(r["n"])
        if _table_exists("opportunities"):
            from apps.discovery.models import Opportunity

            for r in _rows("""SELECT payload->'source'->>'chat_id' AS chat, COUNT(*) AS n FROM {s}.opportunities
                              GROUP BY 1"""):
                row(r["chat"])["opportunities"] = int(r["n"])
            for r in _rows(f"""SELECT o.payload->'source'->>'chat_id' AS chat, COUNT(*) AS n FROM {{s}}.opportunities o
                               JOIN {Opportunity._meta.db_table} d ON d.engine_opportunity_id = o.opportunity_id
                               GROUP BY 1"""):
                row(r["chat"])["seller_opportunities"] = int(r["n"])
    except Exception:  # pragma: no cover - engine schema not reachable / older engine
        log.exception("group stats")
    return out


def community_groups(status: str = "", scope: str = "") -> list[dict]:
    """Sources grouped by Telegram group (chat id once joined, otherwise the link). The engine analyses each group once;
    every seller with a private source pays its whole analysis. Also lists groups the crawler watches without any
    source in the panel (e.g. the groups file): their messages are archived but NOT analysed."""
    from apps.discovery.models import MonitoredCommunity

    cfg = BillingSettings.get()
    rate = float(cfg.usd_to_toman * cfg.multiplier)
    stats = group_engine_stats()
    groups: dict[str, dict] = {}
    for c in MonitoredCommunity.objects.select_related("business").order_by("scope", "-is_active", "created_at"):
        key = str(c.telegram_chat_id) if c.telegram_chat_id else f"link:{c.normalized_link}"
        g = groups.setdefault(key, {"key": key, "chat_id": c.telegram_chat_id, "title": "", "link": c.handle_or_link,
                                    "sources": [], "members": 0, "scanned": 0, "last": None})
        g["sources"].append(c)
        if c.name and c.name != c.handle_or_link and not g["title"]:
            g["title"] = c.name
        g["members"] = max(g["members"], c.members_count or 0)
        g["scanned"] = max(g["scanned"], c.messages_scanned_count or 0)
        if c.last_scanned_at and (g["last"] is None or c.last_scanned_at > g["last"]):
            g["last"] = c.last_scanned_at

    known = {g["chat_id"] for g in groups.values() if g["chat_id"]}
    try:
        if _crawler_table_exists("tg_chats"):
            cs = _crawler_schema()
            with connection.cursor() as cur:
                cur.execute(f"""SELECT t.chat_id, t.title, t.username, t.join_link, t.members_count,
                                       (SELECT COUNT(*) FROM {cs}.tg_messages m WHERE m.chat_id = t.chat_id AND NOT m.is_context),
                                       t.last_scanned_at
                                FROM {cs}.tg_chats t WHERE t.is_monitored""")
                for chat_id, title, username, join_link, members, n, last in cur.fetchall():
                    if chat_id in known:
                        continue
                    link = f"@{username}" if username else (join_link or str(chat_id))
                    groups[str(chat_id)] = {"key": str(chat_id), "chat_id": chat_id, "title": title or "", "link": link,
                                            "sources": [], "members": members or 0, "scanned": n or 0, "last": last,
                                            "orphan": True}
    except Exception:  # pragma: no cover
        log.exception("crawler groups")

    out = []
    for g in groups.values():
        src = g["sources"]
        active = [c for c in src if c.is_active]
        g["global"] = [c for c in src if c.is_global]
        g["private"] = [c for c in src if not c.is_global]
        g["private_active"] = [c for c in g["private"] if c.is_active]
        g["has_global"] = any(c.is_active for c in g["global"])
        g["orphan"] = g.get("orphan", False)
        g["analysed_by_engine"] = bool(active) and bool(g["chat_id"])
        st = sorted({c.sync_status for c in src}) if src else []
        g["status"] = "ACTIVE" if "ACTIVE" in st and active else (st[0] if st else "")
        s = stats.get(str(g["chat_id"]), {}) if g["chat_id"] else {}
        g["stats"] = s
        g["billed_toman"] = s.get("billed_usd", 0.0) * rate
        g["payers"] = "پلتفرم" if g["has_global"] and not g["private_active"] else (
            f"{len(g['private_active'])} فروشنده (هر کدام کامل)" if g["private_active"] else "—")
        if status and status not in {c.sync_status for c in src}:
            continue
        if scope == "GLOBAL" and not g["global"]:
            continue
        if scope == "PRIVATE" and not g["private"]:
            continue
        if scope == "SHARED" and len(g["private"]) < 2 and not (g["global"] and g["private"]):
            continue
        if scope == "ORPHAN" and not g["orphan"]:
            continue
        out.append(g)
    out.sort(key=lambda g: (g["orphan"], -len(g["private_active"]), -(g["stats"].get("toman") or 0), g["link"]))
    return out


# ── X (Twitter) ───────────────────────────────────────────────────────────────
X_CHAT = "x:public"   # chat_id of every X post in the engine (need_engine.access.X_CHAT)


def _env_flag(name: str, default: str) -> str:
    import os

    return (os.getenv(name, default) or "").strip()


def x_collector_status() -> dict | None:
    """The collector's status.json (shared appdata volume); None when it never ran."""
    import os
    from datetime import datetime

    path = settings.BASE_DIR / os.getenv("X_STATUS_FILE", "data/x_collected/status.json")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None
    try:
        updated = datetime.fromisoformat(str(data.get("updated_at", "")).replace("Z", "+00:00"))
        data["age"] = max(0.0, time.time() - updated.timestamp())
    except ValueError:
        data["age"] = None
    data["updated"] = _ago(data["age"])
    return data


def x_collector_service() -> dict | None:
    if not settings.X_PANEL_ENABLED and not x_collector_status():
        return None
    st = x_collector_status()
    interval = float(_env_flag("X_COLLECT_INTERVAL_SECONDS", "300") or 300)
    if not st:
        return {"name": "جمع‌آور X", "state": "unknown",
                "detail": "هنوز اجرا نشده (docker compose --profile x up -d x)"}
    state = _state(st["age"], interval * 3, interval * 12)
    if st.get("status") in {"AUTH_FAILED", "RATE_LIMITED", "CIRCUIT_OPEN", "FAILED"}:
        state = "down" if st.get("status") == "AUTH_FAILED" else "warn"
    detail = (f"آخرین دور {st['updated']} · وضعیت {st.get('status')} · امروز {st.get('collected_today', 0)}"
              f" از سقف {st.get('daily_cap', '?')} پست")
    if st.get("failed_queries"):
        detail += f" · {st['failed_queries']} کوئری ناموفق"
    return {"name": "جمع‌آور X", "state": state, "detail": detail}


def x_settings() -> list[tuple[str, str, str]]:
    """(env name, value, meaning) of the knobs that shape X collection; read-only (edit .env + `up -d`)."""
    rows = [
        ("X_COLLECT_INTERVAL_SECONDS", "300", "فاصله‌ی دورهای جستجو (ثانیه)"),
        ("X_COLLECT_QUERIES_PER_CYCLE", "12", "کوئری در هر دور"),
        ("X_COLLECT_MAX_PER_QUERY", "50", "حداکثر پست هر کوئری"),
        ("X_COLLECT_DAILY_CAP", "500", "سقف پست جدید در روز"),
        ("X_QUERY_MODE", "both", "نوع کوئری‌ها"),
        ("X_QUERY_LLM", "false", "واژه‌های جستجو با هوش مصنوعی"),
        ("X_QUERY_FIRST_LOOKBACK_HOURS", "168", "بازه‌ی اولین جستجوی هر کوئری (ساعت)"),
        ("X_AUTO_INGEST", "true", "ورود خودکار پست‌ها به پایگاه داده"),
        ("TWITTER_SEARCH_METHOD", "auto", "روش درخواست جستجو"),
        ("NE_X_ENABLED", "false", "تحلیل پست‌های X در موتور"),
        ("NE_X_MODE", "product", "product = هر محصول جدا با هزینه‌ی فروشنده؛ public = عمومی"),
        ("X_SEARCH_FEE_TOMAN", "0", "کارمزد هر جستجو برای فروشنده (تومان)"),
        ("NE_X_PREFILTER", "shadow", "پیش‌فیلتر (shadow = فقط ثبت، حذف نمی‌کند)"),
        ("NE_X_MAX_POST_AGE_HOURS", "168", "قدیمی‌ترین پست قابل تحلیل (ساعت)"),
        ("NE_X_NEED_TTL_HOURS", "168", "عمر نیاز X (ساعت)"),
    ]
    return [(k, _env_flag(k, d) or d, label) for k, d, label in rows]


def x_overview(days: float) -> dict:
    """Funnel and cost of the X source over the period: collected → analysed → needs → opportunities → delivered."""
    since = _since(days)
    out = {"posts": 0, "posts_total": 0, "last_post": None, "analysed_total": 0, "prefilter": [], "kept": 0, "dropped": 0,
           "needs": 0, "open_needs": 0, "opportunities": 0, "delivered": 0, "pending": 0,
           "cost": {"extract_toman": 0.0, "extract_calls": 0, "downstream_toman": 0.0, "billed_toman": 0.0, "usd": 0.0},
           "by_query": [], "recent": [], "products": []}
    try:
        cs = _crawler_schema()
        if _crawler_table_exists("x_posts"):
            with connection.cursor() as c:
                c.execute(f"""SELECT COUNT(*) FILTER (WHERE loaded_at >= to_timestamp(%s)), COUNT(*), MAX(loaded_at)
                              FROM {cs}.x_posts""", [since])
                out["posts"], out["posts_total"], out["last_post"] = c.fetchone()
                c.execute(f"""SELECT tweet_id, author_handle, text, created_at, url, query FROM {cs}.x_posts
                              ORDER BY loaded_at DESC, row_id DESC LIMIT 15""")
                cols = [d[0] for d in c.description]
                out["recent"] = [dict(zip(cols, r)) for r in c.fetchall()]
                c.execute(f"""SELECT COALESCE(query, '') AS query, COUNT(*) AS n FROM {cs}.x_posts
                              WHERE loaded_at >= to_timestamp(%s) GROUP BY 1 ORDER BY 2 DESC LIMIT 15""", [since])
                out["by_query"] = [{"query": q, "n": n} for q, n in c.fetchall()]
        if _table_exists("pending"):
            out["pending"] = _rows("SELECT COUNT(*) AS n FROM {s}.pending WHERE chat_id LIKE %s", ("x:%",))[0]["n"]
        if _table_exists("chat_analysed"):
            r = _rows("SELECT COALESCE(SUM(n), 0) AS n FROM {s}.chat_analysed WHERE chat_id LIKE %s", ("x:%",))
            out["analysed_total"] = int(r[0]["n"]) if r else 0
        if _table_exists("x_filter_decisions"):
            rows = _rows("""SELECT keep, reason, COUNT(*) AS n FROM {s}.x_filter_decisions
                            WHERE decided_at >= to_timestamp(%s) GROUP BY 1, 2 ORDER BY 3 DESC""", (since,))
            out["prefilter"] = rows[:12]
            out["kept"] = sum(r["n"] for r in rows if r["keep"])
            out["dropped"] = sum(r["n"] for r in rows if not r["keep"])
        if _table_exists("needs"):
            r = _rows("""SELECT COUNT(*) AS n, COUNT(*) FILTER (WHERE status = 'open') AS o FROM {s}.needs
                         WHERE chat_id LIKE 'x:%%' AND updated_at >= %s""", (since,))[0]
            out["needs"], out["open_needs"] = int(r["n"]), int(r["o"])
        if _table_exists("opportunities"):
            from apps.discovery.models import Opportunity

            r = _rows("""SELECT COUNT(*) AS n FROM {s}.opportunities WHERE payload->'source'->>'chat_id' LIKE 'x:%%'
                         AND updated_at >= to_timestamp(%s)""", (since,))
            out["opportunities"] = int(r[0]["n"])
            out["delivered"] = Opportunity.objects.filter(
                source_platform="x", created_at__gte=timezone.now() - timedelta(days=days)).count()
        if _table_exists("costs"):
            cfg = BillingSettings.get()
            cached = "TRUE" if cfg.charge_cached else "NOT cached"
            billed = _billed()
            r = _rows("""SELECT COUNT(*) FILTER (WHERE part = 0) AS calls, COALESCE(SUM(toman), 0) AS toman,
                                COALESCE(SUM(usd), 0) AS usd FROM {s}.costs
                         WHERE ts >= %s AND stage LIKE 'need_extraction%%' AND ref LIKE 'x:%%'""", (since,))[0]
            out["cost"]["extract_calls"], out["cost"]["extract_toman"] = int(r["calls"]), float(r["toman"])
            out["cost"]["usd"] = float(r["usd"])
            if _table_exists("needs"):
                r = _rows(f"""SELECT COALESCE(SUM(c.toman), 0) AS toman, COALESCE(SUM(c.usd), 0) AS usd,
                                     COALESCE(SUM({billed}) FILTER (WHERE c.business_id IS NOT NULL AND {cached}), 0) AS billed
                              FROM {{s}}.costs c JOIN {{s}}.needs n ON n.need_id = split_part(c.ref, ':', 1)
                              WHERE c.ts >= %s AND c.stage NOT LIKE 'need_extraction%%' AND n.chat_id LIKE 'x:%%'""", (since,))[0]
                out["cost"]["downstream_toman"] = float(r["toman"])
                out["cost"]["usd"] += float(r["usd"])
                out["cost"]["billed_toman"] = float(r["billed"]) * float(cfg.usd_to_toman * cfg.multiplier)
    except Exception:  # pragma: no cover - engine/crawler schema not reachable
        log.exception("x overview")
    out["products"] = x_product_rows(days)
    c = out["cost"]
    c["total_toman"] = c["extract_toman"] + c["downstream_toman"]
    c["per_post"] = c["extract_toman"] / out["posts"] if out["posts"] else 0.0
    c["per_opportunity"] = c["total_toman"] / out["opportunities"] if out["opportunities"] else 0.0
    return out


def x_product_rows(days: float) -> list[dict]:
    """Per-product X search (NE_X_MODE=product): products with X search on and what their search cost/brought."""
    from apps.products.models import Product

    since = _since(days)
    rows: dict[str, dict] = {}
    for p in Product.objects.filter(x_search_enabled=True).select_related("business").order_by("business__name", "name"):
        rows[str(p.pk)] = {"id": p.pk, "name": p.name, "business": p.business.name if p.business_id else "—",
                           "business_id": p.business_id, "on": True, "hits": 0, "needs": 0, "opportunities": 0,
                           "analysis_toman": 0.0, "search_toman": 0.0, "reply_toman": 0.0}

    def row(pid: str) -> dict | None:
        if pid not in rows:
            p = Product.objects.filter(pk=pid).select_related("business").first() if str(pid).isdigit() else None
            if p is None:
                return None
            rows[pid] = {"id": p.pk, "name": p.name, "business": p.business.name if p.business_id else "—",
                         "business_id": p.business_id, "on": p.x_search_enabled, "hits": 0, "needs": 0, "opportunities": 0,
                         "analysis_toman": 0.0, "search_toman": 0.0, "reply_toman": 0.0}
        return rows[pid]

    try:
        if _crawler_table_exists("x_post_products"):
            with connection.cursor() as c:
                c.execute(f"""SELECT product_id, COUNT(*) FROM {_crawler_schema()}.x_post_products
                              WHERE found_at >= to_timestamp(%s) GROUP BY 1""", [since])
                for pid, n in c.fetchall():
                    if (r := row(str(pid))) is not None:
                        r["hits"] = int(n)
        if _table_exists("costs"):
            for r in _rows("""SELECT split_part(ref, ':', 3) AS pid, COALESCE(SUM(toman), 0) AS toman FROM {s}.costs
                              WHERE ts >= %s AND stage LIKE 'need_extraction%%' AND ref LIKE 'x:p:%%' GROUP BY 1""", (since,)):
                if (x := row(r["pid"])) is not None:
                    x["analysis_toman"] = float(r["toman"])
            for r in _rows("""SELECT substr(ref, 4) AS pids, COALESCE(SUM(toman), 0) AS toman FROM {s}.costs
                              WHERE ts >= %s AND stage = 'x_search' GROUP BY 1""", (since,)):
                pids = [p for p in str(r["pids"]).split(",") if p]
                for pid in pids:
                    if (x := row(pid)) is not None:
                        x["search_toman"] += float(r["toman"]) / max(1, len(pids))
            if _table_exists("needs"):
                for r in _rows("""SELECT substr(n.chat_id, 5) AS pid, COALESCE(SUM(c.toman), 0) AS toman FROM {s}.costs c
                                  JOIN {s}.needs n ON n.need_id = split_part(c.ref, ':', 1)
                                  WHERE c.ts >= %s AND c.stage NOT LIKE 'need_extraction%%' AND n.chat_id LIKE 'x:p:%%'
                                  GROUP BY 1""", (since,)):
                    if (x := row(r["pid"])) is not None:
                        x["reply_toman"] = float(r["toman"])
        if _table_exists("needs"):
            for r in _rows("""SELECT substr(chat_id, 5) AS pid, COUNT(*) AS n FROM {s}.needs
                              WHERE chat_id LIKE 'x:p:%%' AND updated_at >= %s GROUP BY 1""", (since,)):
                if (x := row(r["pid"])) is not None:
                    x["needs"] = int(r["n"])
        if _table_exists("opportunities"):
            for r in _rows("""SELECT substr(payload->'source'->>'chat_id', 5) AS pid, COUNT(*) AS n FROM {s}.opportunities
                              WHERE payload->'source'->>'chat_id' LIKE 'x:p:%%' AND updated_at >= to_timestamp(%s)
                              GROUP BY 1""", (since,)):
                if (x := row(r["pid"])) is not None:
                    x["opportunities"] = int(r["n"])
    except Exception:  # pragma: no cover
        log.exception("x product rows")
    for r in rows.values():
        r["total_toman"] = r["analysis_toman"] + r["search_toman"] + r["reply_toman"]
    return sorted(rows.values(), key=lambda r: (-r["total_toman"], r["name"]))
