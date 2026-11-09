"""X (Twitter) numbers for the admin panel: collector status, settings, overview, per-product rows."""
from __future__ import annotations

import json
import logging
import time
from datetime import timedelta

from django.conf import settings
from django.db import connection
from django.utils import timezone


from .models import BillingSettings

from .ops_status import _ago, _state, _crawler_schema, _crawler_table_exists
from .engine_tables import (billed_usd_expr, engine_rows, engine_table_exists,
                            since_ts)

log = logging.getLogger(__name__)


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
    since = since_ts(days)
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
        if engine_table_exists("pending"):
            out["pending"] = engine_rows("SELECT COUNT(*) AS n FROM {s}.pending WHERE chat_id LIKE %s", ("x:%",))[0]["n"]
        if engine_table_exists("chat_analysed"):
            r = engine_rows("SELECT COALESCE(SUM(n), 0) AS n FROM {s}.chat_analysed WHERE chat_id LIKE %s", ("x:%",))
            out["analysed_total"] = int(r[0]["n"]) if r else 0
        if engine_table_exists("x_filter_decisions"):
            rows = engine_rows("""SELECT keep, reason, COUNT(*) AS n FROM {s}.x_filter_decisions
                            WHERE decided_at >= to_timestamp(%s) GROUP BY 1, 2 ORDER BY 3 DESC""", (since,))
            out["prefilter"] = rows[:12]
            out["kept"] = sum(r["n"] for r in rows if r["keep"])
            out["dropped"] = sum(r["n"] for r in rows if not r["keep"])
        if engine_table_exists("needs"):
            r = engine_rows("""SELECT COUNT(*) AS n, COUNT(*) FILTER (WHERE status = 'open') AS o FROM {s}.needs
                         WHERE chat_id LIKE 'x:%%' AND updated_at >= %s""", (since,))[0]
            out["needs"], out["open_needs"] = int(r["n"]), int(r["o"])
        if engine_table_exists("opportunities"):
            from apps.discovery.models import Opportunity

            r = engine_rows("""SELECT COUNT(*) AS n FROM {s}.opportunities WHERE payload->'source'->>'chat_id' LIKE 'x:%%'
                         AND updated_at >= to_timestamp(%s)""", (since,))
            out["opportunities"] = int(r[0]["n"])
            out["delivered"] = Opportunity.objects.filter(
                source_platform="x", created_at__gte=timezone.now() - timedelta(days=days)).count()
        if engine_table_exists("costs"):
            cfg = BillingSettings.get()
            cached = "TRUE" if cfg.charge_cached else "NOT cached"
            billed = billed_usd_expr()
            r = engine_rows("""SELECT COUNT(*) FILTER (WHERE part = 0) AS calls, COALESCE(SUM(toman), 0) AS toman,
                                COALESCE(SUM(usd), 0) AS usd FROM {s}.costs
                         WHERE ts >= %s AND stage LIKE 'need_extraction%%' AND ref LIKE 'x:%%'""", (since,))[0]
            out["cost"]["extract_calls"], out["cost"]["extract_toman"] = int(r["calls"]), float(r["toman"])
            out["cost"]["usd"] = float(r["usd"])
            if engine_table_exists("needs"):
                r = engine_rows(f"""SELECT COALESCE(SUM(c.toman), 0) AS toman, COALESCE(SUM(c.usd), 0) AS usd,
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

    since = since_ts(days)
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
        if engine_table_exists("costs"):
            for r in engine_rows("""SELECT split_part(ref, ':', 3) AS pid, COALESCE(SUM(toman), 0) AS toman FROM {s}.costs
                              WHERE ts >= %s AND stage LIKE 'need_extraction%%' AND ref LIKE 'x:p:%%' GROUP BY 1""", (since,)):
                if (x := row(r["pid"])) is not None:
                    x["analysis_toman"] = float(r["toman"])
            for r in engine_rows("""SELECT substr(ref, 4) AS pids, COALESCE(SUM(toman), 0) AS toman FROM {s}.costs
                              WHERE ts >= %s AND stage = 'x_search' GROUP BY 1""", (since,)):
                pids = [p for p in str(r["pids"]).split(",") if p]
                for pid in pids:
                    if (x := row(pid)) is not None:
                        x["search_toman"] += float(r["toman"]) / max(1, len(pids))
            if engine_table_exists("needs"):
                for r in engine_rows("""SELECT substr(n.chat_id, 5) AS pid, COALESCE(SUM(c.toman), 0) AS toman FROM {s}.costs c
                                  JOIN {s}.needs n ON n.need_id = split_part(c.ref, ':', 1)
                                  WHERE c.ts >= %s AND c.stage NOT LIKE 'need_extraction%%' AND n.chat_id LIKE 'x:p:%%'
                                  GROUP BY 1""", (since,)):
                    if (x := row(r["pid"])) is not None:
                        x["reply_toman"] = float(r["toman"])
        if engine_table_exists("needs"):
            for r in engine_rows("""SELECT substr(chat_id, 5) AS pid, COUNT(*) AS n FROM {s}.needs
                              WHERE chat_id LIKE 'x:p:%%' AND updated_at >= %s GROUP BY 1""", (since,)):
                if (x := row(r["pid"])) is not None:
                    x["needs"] = int(r["n"])
        if engine_table_exists("opportunities"):
            for r in engine_rows("""SELECT substr(payload->'source'->>'chat_id', 5) AS pid, COUNT(*) AS n FROM {s}.opportunities
                              WHERE payload->'source'->>'chat_id' LIKE 'x:p:%%' AND updated_at >= to_timestamp(%s)
                              GROUP BY 1""", (since,)):
                if (x := row(r["pid"])) is not None:
                    x["opportunities"] = int(r["n"])
    except Exception:  # pragma: no cover
        log.exception("x product rows")
    for r in rows.values():
        r["total_toman"] = r["analysis_toman"] + r["search_toman"] + r["reply_toman"]
    return sorted(rows.values(), key=lambda r: (-r["total_toman"], r["name"]))
