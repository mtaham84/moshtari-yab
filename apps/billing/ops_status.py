"""Read-only health of the services and workers for the admin panel: heartbeats, communities, retention,
and testing/importing LLM providers."""
from __future__ import annotations

import json
import logging
import os
import re
import time
from datetime import datetime
import urllib.error
import urllib.request
from decimal import Decimal

from django.conf import settings
from django.db import connection
from django.utils import timezone


from .models import AIModel, BillingSettings, Provider

from .engine_tables import (billed_usd_expr, engine_rows, engine_table_exists)

log = logging.getLogger(__name__)


def heartbeat(name: str) -> None:
    """Liveness marker for the «وضعیت سرویس‌ها» card (stored in EngineSyncCursor, position = unix time)."""
    from apps.discovery.models import EngineSyncCursor

    EngineSyncCursor.objects.update_or_create(name=f"heartbeat:{name}", defaults={"position": int(time.time())})


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
    from apps.discovery.models import EngineSyncCursor

    now = time.time()
    out = []
    try:
        with connection.cursor() as c:
            c.execute("SELECT 1")
        out.append({"name": "پایگاه داده (PostgreSQL)", "state": "ok", "detail": "در دسترس"})
    except Exception as e:  # pragma: no cover
        out.append({"name": "پایگاه داده (PostgreSQL)", "state": "down", "detail": str(e)[:120]})
        return out

    from shared.collector_kit.heartbeat import read_heartbeats

    intervals = {
        "telegram": (float(os.getenv("TG_HEARTBEAT_INTERVAL_SECONDS", "60")), "TG_HEARTBEAT_INTERVAL_SECONDS"),
        "x": (float(os.getenv("X_COLLECT_INTERVAL_SECONDS", "300")), "X_COLLECT_INTERVAL_SECONDS"),
    }
    labels = {"telegram": "کراولر تلگرام", "x": "جمع‌آور X"}
    heartbeats = {row["source"]: row for row in read_heartbeats()}
    for source, label in labels.items():
        heartbeat = heartbeats.get(source)
        if not heartbeat:
            out.append({"name": label, "state": "down", "detail": "هنوز heartbeat ثبت نشده است"})
            continue
        interval, interval_name = intervals[source]
        last_ok = heartbeat.get("last_ok_at")
        age = (timezone.now() - last_ok).total_seconds() if last_ok else None
        state = "down" if age is None or age >= interval * 3 else "ok"
        if heartbeat.get("consecutive_errors") and state == "ok":
            state = "warn"
        detail = (f"آخرین اجرا {_ago(age)} · {heartbeat.get('items_last_run', 0)} مورد · "
                  f"بازه {interval_name}={interval:g} ثانیه")
        if heartbeat.get("last_error"):
            detail += f" · خطا: {heartbeat['last_error'][:100]}"
        out.append({"name": label, "state": state, "detail": detail})

    # need_engine: heartbeat written at the end of every run (need_engine/engine.py)
    hb = engine_rows("SELECT v FROM {s}.kv WHERE k = 'heartbeat'") if engine_table_exists("kv") else []
    pending = engine_rows("SELECT COUNT(*) AS n FROM {s}.pending")[0]["n"] if engine_table_exists("pending") else 0
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
        if engine_table_exists("costs") and engine_table_exists("needs"):
            cfg = BillingSettings.get()
            cached = "TRUE" if cfg.charge_cached else "NOT cached"
            billed = billed_usd_expr()
            for r in engine_rows(f"""
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
            for r in engine_rows("""SELECT chat_id, COUNT(*) AS n, COUNT(*) FILTER (WHERE status = 'open') AS o
                              FROM {s}.needs GROUP BY chat_id"""):
                row(r["chat_id"]).update(needs=int(r["n"]), open_needs=int(r["o"]))
        if engine_table_exists("chat_analysed"):
            for r in engine_rows("SELECT chat_id, n FROM {s}.chat_analysed"):
                row(r["chat_id"])["analysed"] = int(r["n"])
        if engine_table_exists("opportunities"):
            from apps.discovery.models import Opportunity

            for r in engine_rows("""SELECT payload->'source'->>'chat_id' AS chat, COUNT(*) AS n FROM {s}.opportunities
                              GROUP BY 1"""):
                row(r["chat"])["opportunities"] = int(r["n"])
            for r in engine_rows(f"""SELECT o.payload->'source'->>'chat_id' AS chat, COUNT(*) AS n FROM {{s}}.opportunities o
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


def retention_report() -> dict | None:
    path = settings.BASE_DIR / os.getenv("RETENTION_REPORT_FILE", "output/retention_report.json")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        timestamp = datetime.fromisoformat(data["timestamp"].replace("Z", "+00:00"))
    except (OSError, KeyError, ValueError, json.JSONDecodeError):
        return None
    if timezone.is_naive(timestamp):
        timestamp = timezone.make_aware(timestamp)
    age = max(0.0, (timezone.now() - timestamp).total_seconds())
    return {"timestamp": timestamp, "age": age, "updated": _ago(age), "counts": data.get("counts", {})}
