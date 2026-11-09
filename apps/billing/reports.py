"""Read-only cost and revenue numbers for the admin panel (from wallet transactions and need_engine costs)."""
from __future__ import annotations

import logging
from datetime import timedelta
from decimal import Decimal

from django.conf import settings
from django.db.models import Sum
from django.utils import timezone

from apps.businesses.models import Business

from .models import BillingSettings, WalletTransaction

from .engine_tables import (engine_has_column, engine_rows, engine_table_exists,
                            since_ts)

log = logging.getLogger(__name__)


def cost_totals(days: float) -> dict:
    if not engine_table_exists("costs"):
        return {"calls": 0, "usd": 0.0, "toman": 0.0, "tokens": 0}
    r = engine_rows("""SELECT COUNT(*) FILTER (WHERE part = 0) AS calls, COALESCE(SUM(usd), 0) AS usd,
                 COALESCE(SUM(toman), 0) AS toman, COALESCE(SUM(prompt_tokens + completion_tokens), 0) AS tokens
                 FROM {s}.costs WHERE ts >= %s""", (since_ts(days),))[0]
    return {k: float(v or 0) for k, v in r.items()}


def cost_breakdown(days: float, by: str) -> list[dict]:
    col = {"model": "model", "stage": "stage", "business": "business_id"}[by]
    if not engine_table_exists("costs"):
        return []
    rows = engine_rows(f"""SELECT {col} AS key, COUNT(*) FILTER (WHERE part = 0 OR %s) AS calls,
                     SUM(cached::int) AS cached, SUM(prompt_tokens) AS pt, SUM(completion_tokens) AS ct,
                     COALESCE(SUM(usd), 0) AS usd, COALESCE(SUM(toman), 0) AS toman
                     FROM {{s}}.costs WHERE ts >= %s GROUP BY {col} ORDER BY SUM(usd) DESC NULLS LAST""",
                 (by == "business", since_ts(days)))
    if by == "business":
        names = dict(Business.objects.values_list("pk", "name"))
        for r in rows:
            r["label"] = names.get(int(r["key"]), f"#{r['key']}") if str(r["key"] or "").isdigit() else "پلتفرم (منابع عمومی)"
    return rows


def model_usage(days: float) -> list[dict]:
    """Usage per model over ``days`` from BOTH ledgers: the engine's ``costs`` table (Telegram/X analysis, reply, verify)
    and the Instagram analyses (``AIAnalysis`` rows, e.g. Luna). Rows: key, calls, pt, ct, tokens, usd, toman, last_at."""
    from datetime import datetime, timezone as dt_tz

    merged: dict[str, dict] = {}

    def row(key: str) -> dict:
        return merged.setdefault(key, {"key": key, "calls": 0, "pt": 0, "ct": 0, "tokens": 0, "usd": 0.0, "toman": 0.0, "last_at": None})

    def bump_last(r: dict, when) -> None:
        if when and (r["last_at"] is None or when > r["last_at"]):
            r["last_at"] = when

    if engine_table_exists("costs"):
        rows = engine_rows("""SELECT model AS key, COUNT(*) FILTER (WHERE part = 0) AS calls, COALESCE(SUM(prompt_tokens), 0) AS pt,
                        COALESCE(SUM(completion_tokens), 0) AS ct, COALESCE(SUM(usd), 0) AS usd, COALESCE(SUM(toman), 0) AS toman,
                        MAX(ts) AS last_ts FROM {s}.costs WHERE ts >= %s GROUP BY model""", (since_ts(days),))
        for r0 in rows:
            r = row(r0["key"] or "—")
            r["calls"] += int(r0["calls"] or 0)
            r["pt"] += int(r0["pt"] or 0)
            r["ct"] += int(r0["ct"] or 0)
            r["usd"] += float(r0["usd"] or 0)
            r["toman"] += float(r0["toman"] or 0)
            if r0["last_ts"]:
                bump_last(r, datetime.fromtimestamp(float(r0["last_ts"]), tz=dt_tz.utc))
    try:
        from django.db.models import Count, Max, Sum
        from apps.discovery.models import AIAnalysis

        since = timezone.now() - timedelta(days=days)
        for a in (AIAnalysis.objects.filter(created_at__gte=since).exclude(model_name="")
                  .values("model_name").annotate(n=Count("id"), tok=Sum("tokens_used"), usd=Sum("cost_usd"),
                                                 toman=Sum("cost_toman"), last=Max("created_at"))):
            if a["model_name"] == "instagram_direct_cta":      # rule-based, no model call
                continue
            r = row(a["model_name"])
            r["calls"] += int(a["n"] or 0)
            r["tokens"] += int(a["tok"] or 0)
            r["usd"] += float(a["usd"] or 0)
            r["toman"] += float(a["toman"] or 0)
            bump_last(r, a["last"])
    except Exception:
        pass
    try:
        from django.db.models import Count, Max, Sum
        from apps.core.models import LLMUsageLog

        since = timezone.now() - timedelta(days=days)
        cfg = BillingSettings.get()
        toman_per_usd = float(cfg.usd_to_toman * cfg.multiplier)
        for usage in (LLMUsageLog.objects.filter(created_at__gte=since)
                      .values("model").annotate(n=Count("id"), pt=Sum("tokens_in"), ct=Sum("tokens_out"),
                                                 usd=Sum("cost_usd"), last=Max("created_at"))):
            r = row(usage["model"] or "—")
            r["calls"] += int(usage["n"] or 0)
            r["pt"] += int(usage["pt"] or 0)
            r["ct"] += int(usage["ct"] or 0)
            r["usd"] += float(usage["usd"] or 0)
            r["toman"] += float(usage["usd"] or 0) * toman_per_usd
            bump_last(r, usage["last"])
    except Exception:
        log.debug("LLM usage ledger unavailable", exc_info=True)
    for r in merged.values():
        r["tokens"] = r["tokens"] + r["pt"] + r["ct"]
    return sorted(merged.values(), key=lambda r: (-r["usd"], -r["calls"]))


def cost_daily(days: int = 14) -> list[dict]:
    if not engine_table_exists("costs"):
        return []
    rows = engine_rows("""SELECT to_char(to_timestamp(ts) AT TIME ZONE %s, 'YYYY-MM-DD') AS day,
                    COALESCE(SUM(toman), 0) AS toman, COUNT(*) FILTER (WHERE part = 0) AS calls
                    FROM {s}.costs WHERE ts >= %s GROUP BY 1 ORDER BY 1""", (settings.TIME_ZONE, since_ts(days)))
    top = max([float(r["toman"]) for r in rows] or [0]) or 1
    for r in rows:
        r["pct"] = round(float(r["toman"]) / top * 100)
    return rows


def cost_ledger(limit: int = 100, offset: int = 0, business: str = "", model: str = "", stage: str = "") -> list[dict]:
    if not engine_table_exists("costs"):
        return []
    where, params = ["TRUE"], []
    for col, val in (("business_id", business), ("model", model), ("stage", stage)):
        if val:
            where.append(f"{col} = %s")
            params.append(val)
    rows = engine_rows(f"""SELECT id, to_timestamp(ts) AS at, stage, model, prompt_tokens, completion_tokens, cached, usd, toman,
                     business_id, ref, {"charge_factor" if engine_has_column("costs", "charge_factor") else "1"} AS charge_factor FROM {{s}}.costs WHERE {' AND '.join(where)} ORDER BY id DESC LIMIT %s OFFSET %s""",
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
