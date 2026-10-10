"""Seller-facing view of «جستجوی مشتری در X» (per-product mode): what was searched, what came back, what the AI
decided for each post and what it cost the seller. Read-only over crawler.x_search_runs / x_post_products / x_posts
(collector + x_ingest) and need_engine.x_hit_results / costs (engine). Every reader tolerates missing tables."""
from __future__ import annotations

import logging
import os
import re
from collections import defaultdict
from datetime import datetime, timedelta

from django.db import connection
from django.utils import timezone

log = logging.getLogger(__name__)

PREFIX = "x:p:"
OUTCOMES = {
    "customer": ("مشتری احتمالی", "فرصت فروش ساخته شد و در «فرصت‌ها» منتظر شماست.", "success"),
    "not_fit": ("دنبال چیز دیگری بود", "قصد خرید داشت، ولی به نظر هوش مصنوعی محصول شما چیزی نیست که می‌خواهد.", "warn"),
    "not_customer": ("خریدار نبود", "آگهی فروش، تبلیغ یا گفتگوی معمولی بود؛ هزینه‌ی پاسخ برایش نشد.", "muted"),
    "skipped": ("بررسی نشد", "پست قدیمی یا تکراری بود و بدون هزینه کنار گذاشته شد.", "muted"),
    "queued": ("در صف بررسی", "در دور بعدی موتور (معمولاً چند دقیقه) بررسی می‌شود.", "info"),
    "off": ("بررسی نشد", "جستجوی این محصول هنگام بررسی خاموش بود؛ هزینه‌ای کسر نشد.", "muted"),
}
FILTERS = [("all", "همه"), ("customer", "مشتری احتمالی"), ("rejected", "رد شده"), ("queued", "در صف"),
           ("searches", "جستجوها")]
SKIP_REASONS = {"stale": "قدیمی‌تر از بازه‌ی مجاز بود", "author_throttle": "از یک نویسنده پست زیادی آمده بود"}


def _engine_schema() -> str:
    from apps.billing.services import _schema

    return _schema()


def _crawler_schema() -> str:
    from apps.billing.services import _crawler_schema

    return _crawler_schema()


def _exists(schema: str, table: str) -> bool:
    with connection.cursor() as c:
        c.execute("SELECT to_regclass(%s)", [f"{schema}.{table}"])
        return c.fetchone()[0] is not None


def _all(sql: str, params=()) -> list[dict]:
    with connection.cursor() as c:
        c.execute(sql, list(params))
        cols = [d[0] for d in c.description]
        return [dict(zip(cols, r)) for r in c.fetchall()]


def humanize_query(query: str) -> dict:
    """`(نوشن OR notion) (موجود OR دارین) -فروش lang:fa` → {"groups": [["نوشن","notion"],["موجود","دارین"]],
    "excluded": ["فروش"]} — what a seller can read without knowing X search syntax."""
    q = re.sub(r"\b(lang|since|until|since_time|until_time|min_faves|min_retweets|filter):\S+", " ", query or "")
    excluded = [t.lstrip("-").strip('"') for t in re.findall(r'(?:^|\s)-("[^"]+"|\S+)', q)]
    q = re.sub(r'(?:^|\s)-("[^"]+"|\S+)', " ", q)
    groups = []
    for inner in re.findall(r"\(([^()]*)\)", q):
        words = [w.strip().strip('"') for w in re.split(r"\s+OR\s+", inner) if w.strip()]
        if words:
            groups.append(words)
    rest = re.sub(r"\([^()]*\)", " ", q)
    for part in re.findall(r'"[^"]+"|\S+', rest):
        if part.upper() != "OR":
            groups.append([part.strip('"')])
    return {"groups": groups, "excluded": [e for e in excluded if e]}


def billing_rate() -> tuple[float, str]:
    from apps.billing.models import BillingSettings

    cfg = BillingSettings.get()
    return float(cfg.usd_to_toman * cfg.multiplier), ("TRUE" if cfg.charge_cached else "NOT cached")


def product_stats(business, products, days: float = 30) -> dict[str, dict]:
    """Per product (id as str): found / reviewed / customers / rejected / billed toman / last search & find, last `days`."""
    pids = [str(p.pk) for p in products]
    out = {pid: {"found": 0, "reviewed": 0, "customers": 0, "rejected": 0, "queued": 0, "cost_toman": 0.0,
                 "searches": 0, "seen": 0, "last_search": None, "last_found": None} for pid in pids}
    if not pids:
        return out
    since = timezone.now() - timedelta(days=days)
    cs, es = _crawler_schema(), _engine_schema()
    try:
        if _exists(cs, "x_post_products"):
            for r in _all(f"""SELECT product_id, COUNT(*) AS n, MAX(found_at) AS last FROM {cs}.x_post_products
                              WHERE product_id = ANY(%s) AND found_at >= %s GROUP BY 1""", (pids, since)):
                out[r["product_id"]].update(found=int(r["n"]), last_found=r["last"])
        if _exists(cs, "x_search_runs"):
            for r in _all(f"""SELECT product_id, COUNT(*) AS n, COALESCE(SUM(fetched), 0) AS seen, MAX(ran_at) AS last
                              FROM {cs}.x_search_runs WHERE product_id = ANY(%s) AND ran_at >= %s GROUP BY 1""", (pids, since)):
                out[r["product_id"]].update(searches=int(r["n"]), seen=int(r["seen"]), last_search=r["last"])
        if _exists(es, "x_hit_results"):
            for r in _all(f"""SELECT substr(chat_id, 5) AS pid, outcome, COUNT(*) AS n FROM {es}.x_hit_results
                              WHERE chat_id = ANY(%s) AND decided_at >= %s GROUP BY 1, 2""",
                          ([PREFIX + p for p in pids], since)):
                row = out.get(r["pid"])
                if row is None:
                    continue
                n = int(r["n"])
                row["reviewed"] += n if r["outcome"] != "skipped" else 0
                row["customers"] += n if r["outcome"] == "customer" else 0
                row["rejected"] += n if r["outcome"] in {"not_fit", "not_customer"} else 0
        if _exists(es, "costs"):
            for pid, toman in billed_toman(business, pids, since.timestamp()).items():
                out[pid]["cost_toman"] = toman
    except Exception:  # pragma: no cover - crawler/engine schema not reachable
        log.exception("x product stats")
    for row in out.values():
        row["queued"] = max(0, row["found"] - row["reviewed"])
    return out


def billed_toman(business, pids: list[str], since_ts: float) -> dict[str, float]:
    """What the seller was billed per product for X search: analysis + search fee + reply drafting."""
    from apps.billing.services import _billed

    es = _engine_schema()
    rate, cached = billing_rate()
    billed = _billed()
    bid = str(business.pk)
    usd: dict[str, float] = defaultdict(float)
    for r in _all(f"""SELECT split_part(ref, ':', 3) AS pid, COALESCE(SUM({billed}), 0) AS usd FROM {es}.costs
                      WHERE business_id = %s AND ts >= %s AND stage LIKE 'need_extraction%%' AND ref LIKE 'x:p:%%'
                      AND {cached} GROUP BY 1""", (bid, since_ts)):
        usd[r["pid"]] += float(r["usd"])
    for r in _all(f"""SELECT substr(ref, 4) AS pids, COALESCE(SUM({billed}), 0) AS usd FROM {es}.costs
                      WHERE business_id = %s AND ts >= %s AND stage = 'x_search' GROUP BY 1""", (bid, since_ts)):
        mine = [p for p in str(r["pids"]).split(",") if p in pids]
        for p in mine:
            usd[p] += float(r["usd"]) / len(mine)
    if _exists(es, "needs"):
        billed_c = billed.replace("usd", "c.usd").replace("charge_factor", "c.charge_factor")
        for r in _all(f"""SELECT substr(n.chat_id, 5) AS pid, COALESCE(SUM({billed_c}), 0) AS usd FROM {es}.costs c
                          JOIN {es}.needs n ON n.need_id = split_part(c.ref, ':', 1)
                          WHERE c.business_id = %s AND c.ts >= %s AND c.stage NOT LIKE 'need_extraction%%'
                          AND c.stage <> 'x_search' AND n.chat_id LIKE 'x:p:%%' AND {cached.replace('cached', 'c.cached')}
                          GROUP BY 1""", (bid, since_ts)):
            usd[r["pid"]] += float(r["usd"])
    return {p: round(usd.get(p, 0.0) * rate) for p in pids}


def search_phrases(product, days: float = 30, limit: int = 12) -> list[dict]:
    cs = _crawler_schema()
    rows: list[dict] = []
    try:
        if _exists(cs, "x_search_runs"):
            rows = _all(f"""SELECT query, COUNT(*) AS runs, COALESCE(SUM(fetched), 0) AS seen, COALESCE(SUM(new_posts), 0) AS new,
                                   MAX(ran_at) AS last FROM {cs}.x_search_runs WHERE product_id = %s AND ran_at >= %s
                            GROUP BY 1 ORDER BY MAX(ran_at) DESC LIMIT %s""",
                        (str(product.pk), timezone.now() - timedelta(days=days), limit))
        if not rows and _exists(cs, "x_post_products"):
            rows = _all(f"""SELECT query, 0 AS runs, 0 AS seen, COUNT(*) AS new, MAX(found_at) AS last FROM {cs}.x_post_products
                            WHERE product_id = %s AND query IS NOT NULL GROUP BY 1 ORDER BY 5 DESC LIMIT %s""",
                        (str(product.pk), limit))
    except Exception:  # pragma: no cover
        log.exception("x search phrases")
    return [{**r, **humanize_query(r["query"])} for r in rows]


def timeline(product, business, show: str = "all", limit: int = 60) -> list[dict]:
    """Newest first: posts the product's search found (with the AI's decision) and grouped search rounds."""
    cs, es = _crawler_schema(), _engine_schema()
    pid = str(product.pk)
    events: list[dict] = []
    try:
        if show != "searches" and _exists(cs, "x_post_products") and _exists(cs, "x_posts"):
            hits = _all(f"""SELECT h.tweet_id, h.query, h.found_at, p.author_handle, p.author_name, p.text, p.url,
                                   p.created_at, p.kind FROM {cs}.x_post_products h JOIN {cs}.x_posts p USING (tweet_id)
                            WHERE h.product_id = %s ORDER BY h.found_at DESC, h.hit_id DESC LIMIT 300""", (pid,))
            results = {}
            if hits and _exists(es, "x_hit_results"):
                results = {r["message_id"]: r for r in _all(
                    f"""SELECT message_id, outcome, reason, need_id, decided_at FROM {es}.x_hit_results
                        WHERE chat_id = %s AND message_id = ANY(%s)""", (PREFIX + pid, [h["tweet_id"] for h in hits]))}
            from .models import Opportunity

            need_ids = [r["need_id"] for r in results.values() if r.get("need_id") and r["outcome"] == "customer"]
            opps = {o.engine_opportunity_id: o.pk for o in Opportunity.objects.filter(
                business=business, engine_opportunity_id__in=need_ids)} if need_ids else {}
            for h in hits:
                res = results.get(h["tweet_id"])
                outcome = res["outcome"] if res else ("queued" if product.x_search_enabled else "off")
                reason = (res or {}).get("reason") or ""
                if outcome == "skipped":
                    reason = SKIP_REASONS.get(reason, "")
                group = "customer" if outcome == "customer" else "rejected" if outcome in {"not_fit", "not_customer"} \
                    else "queued" if outcome == "queued" else "other"
                if show not in {"all", group}:
                    continue
                label, help_text, tone = OUTCOMES[outcome]
                events.append({"type": "post", "at": h["found_at"], "outcome": outcome, "label": label, "help": help_text,
                               "tone": tone, "reason": reason, "text": h["text"], "handle": h["author_handle"],
                               "name": h["author_name"] or h["author_handle"], "url": h["url"], "posted_at": h["created_at"],
                               "is_reply": h["kind"] in {"reply", "quote"}, "decided_at": (res or {}).get("decided_at"),
                               "opportunity_id": opps.get((res or {}).get("need_id"))})
        if show in {"all", "searches"} and _exists(cs, "x_search_runs"):
            runs = _all(f"""SELECT ran_at, query, fetched, new_posts FROM {cs}.x_search_runs WHERE product_id = %s
                            ORDER BY ran_at DESC LIMIT 200""", (pid,))
            for g in _group_runs(runs):
                events.append(g)
    except Exception:  # pragma: no cover
        log.exception("x timeline")
    events.sort(key=lambda e: e["at"] or timezone.now(), reverse=True)
    return events[:limit]


def _group_runs(runs: list[dict], gap_minutes: int = 15) -> list[dict]:
    """Searches of one collector round (a few minutes apart) → one log line."""
    groups: list[dict] = []
    for r in runs:   # newest first
        g = groups[-1] if groups else None
        if g is None or g["start"] - r["ran_at"] > timedelta(minutes=gap_minutes):
            g = {"type": "search", "at": r["ran_at"], "start": r["ran_at"], "phrases": 0, "seen": 0, "new": 0}
            groups.append(g)
        g["start"] = r["ran_at"]
        g["phrases"] += 1
        g["seen"] += int(r["fetched"] or 0)
        g["new"] += int(r["new_posts"] or 0)
    return groups


def collector_line() -> dict:
    """Plain-language state of the shared X searcher for sellers."""
    from apps.billing.services import x_collector_status

    st = x_collector_status()
    interval = float(os.getenv("X_COLLECT_INTERVAL_SECONDS", "300") or 300)
    if not st or st.get("age") is None:
        return {"ok": False, "text": "جستجوگر X هنوز راه‌اندازی نشده است؛ به‌محض فعال شدن، جستجو خودکار شروع می‌شود."}
    if st.get("status") in {"AUTH_FAILED", "FAILED", "CIRCUIT_OPEN", "RATE_LIMITED"} or st["age"] > interval * 6:
        return {"ok": False, "text": f"جستجوگر X موقتاً متوقف است (آخرین دور {st['updated']}). به‌زودی ادامه می‌دهد؛ "
                                     "در این مدت هزینه‌ای کسر نمی‌شود."}
    minutes = max(1, round(interval / 60))
    return {"ok": True, "text": f"جستجوگر X فعال است · آخرین دور {st['updated']} · تقریباً هر {minutes} دقیقه یک دور"}
