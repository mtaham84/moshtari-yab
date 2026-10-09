"""Bridge between need_engine (the analysis core) and the Django panel.

need_engine never writes to the panel's tables; it publishes Opportunity JSON to its own schema
(``need_engine.opportunities``, ``seq`` grows on every change). ``import_opportunity`` turns one payload into
panel records, split by the seller (business) that owns each matched product. It is idempotent: a later version of
the same ``opportunity_id`` (e.g. status → resolved) updates the existing records instead of duplicating them.
"""
from __future__ import annotations

import json
import logging
from collections import defaultdict
from datetime import datetime
from typing import Any

from django.conf import settings
from django.db import connection, transaction
from django.db.models import F, Q
from django.utils.dateparse import parse_datetime

from apps.products.models import Product

from .models import (ENGINE_STATUS_MAP, AIAnalysis, Customer, Evidence, MonitoredCommunity, Opportunity,
                     OpportunityProductMatch)
from .sources import GLOBAL

log = logging.getLogger(__name__)

STRENGTH_INTENT = {"strong": 0.9, "medium": 0.7, "weak": 0.5}
ENGINE_OWNED_STATUSES = {"NEW", "RESOLVED", "EXPIRED"}  # seller-set statuses (REVIEWED, CONTACTED, …) are kept


def _dt(value: Any) -> datetime | None:
    return parse_datetime(value) if isinstance(value, str) else value


def _customer(business, platform: str, cand: dict) -> Customer:
    ext = str(cand.get("external_user_id") or "")
    customer, _ = Customer.objects.get_or_create(business=business, source_platform=platform, external_user_id=ext)
    customer.name = cand.get("customer_name") or customer.name
    customer.source_username = (cand.get("username") or customer.source_username or "").lstrip("@")
    customer.source_profile_url = cand.get("profile_url") or customer.source_profile_url
    customer.phone_number = cand.get("phone_number") or customer.phone_number
    customer.save()
    return customer


def sellers_allowed_for_chat(chat_id: Any) -> set[int] | None:
    """Same rule as need_engine/access.py: None = every seller (active GLOBAL source), else the owners of the
    active PRIVATE sources. Chats that are not Telegram ids (offline demo files) are not restricted."""
    if not str(chat_id or "").lstrip("-").isdigit():
        return None
    rows = MonitoredCommunity.objects.filter(telegram_chat_id=int(chat_id), is_active=True).values_list("scope", "business_id")
    owners: set[int] = set()
    for scope, business_id in rows:
        if scope == GLOBAL:
            return None
        owners.add(business_id)
    return owners


@transaction.atomic
def import_opportunity(payload: dict) -> list[Opportunity]:
    """Upsert one engine opportunity. Returns the panel opportunities (one per seller)."""
    matches = payload.get("matched_products") or []
    ids = [int(m["product_id"]) for m in matches if str(m.get("product_id", "")).isdigit()]
    products = {p.id: p for p in Product.objects.filter(id__in=ids).select_related("business", "category")}
    allowed = sellers_allowed_for_chat((payload.get("source") or {}).get("chat_id"))
    by_business: dict[int, list[tuple[dict, Product]]] = defaultdict(list)
    for m in matches:
        p = products.get(int(m["product_id"])) if str(m.get("product_id", "")).isdigit() else None
        if p is None:
            log.warning("opportunity %s: unknown product %s skipped", payload.get("opportunity_id"), m.get("product_id"))
            continue
        if allowed is not None and p.business_id not in allowed:   # double check of the engine's access rule
            log.warning("opportunity %s: seller %s does not watch this chat; product %s skipped",
                        payload.get("opportunity_id"), p.business_id, p.id)
            continue
        by_business[p.business_id].append((m, p))

    engine_id = str(payload["opportunity_id"])
    source, need, cand = payload.get("source") or {}, payload.get("need") or {}, payload.get("candidate") or {}
    platform = source.get("platform") or "telegram"
    evidence = source.get("evidence") or []
    engine_status = ENGINE_STATUS_MAP.get(payload.get("status", "open"), "NEW")
    out = []

    # a status-only update may arrive for sellers that are no longer in the match list → close those too
    for opp in Opportunity.objects.filter(engine_opportunity_id=engine_id).exclude(business_id__in=by_business.keys()):
        if engine_status != "NEW" and opp.status in ENGINE_OWNED_STATUSES | {"REVIEWED"}:
            opp.status = engine_status
            opp.save(update_fields=["status", "updated_at"])

    for business_id, items in by_business.items():
        items.sort(key=lambda x: -float(x[0].get("match_score") or 0))
        top_match, top_product = items[0]
        business = top_product.business
        customer = _customer(business, platform, cand)
        category = top_product.category
        opp = Opportunity.objects.filter(business=business, engine_opportunity_id=engine_id).first()
        is_new = opp is None
        if is_new:
            opp = Opportunity(business=business, engine_opportunity_id=engine_id, status=engine_status)
        elif engine_status != "NEW" and opp.status in ENGINE_OWNED_STATUSES | {"REVIEWED"}:
            opp.status = engine_status
        opp.customer = customer
        opp.category = category
        opp.category_name_snapshot = category.name if category else ""
        opp.category_path_snapshot = category.get_full_path() if category else ""
        opp.category_confidence = float(top_match.get("match_score") or 0)
        opp.source_platform = platform
        opp.source_message_id = str(evidence[-1]["message_id"]) if evidence else ""
        opp.source_message_timestamp = _dt(evidence[0]["timestamp"]) if evidence else None
        opp.source_raw_message = "\n".join(e.get("text", "") for e in evidence)
        opp.expires_at = _dt(payload.get("expires_at"))
        opp.trace_metadata = {"engine": {k: v for k, v in payload.items() if k != "matched_products"},
                              "matched_products": [m for m, _ in items]}
        opp.save()
        if is_new and str(source.get("chat_id") or "").lstrip("-").isdigit():
            MonitoredCommunity.objects.filter(business=business, telegram_chat_id=int(source["chat_id"])).update(
                leads_discovered_count=F("leads_discovered_count") + 1)

        verdict = top_match.get("verdict") or {}
        AIAnalysis.objects.update_or_create(opportunity=opp, defaults={
            "need": need.get("summary") or need.get("situation") or "",
            "intent_score": STRENGTH_INTENT.get(need.get("strength"), 0.5),
            "product_fit_score": float(top_match.get("match_score") or 0),
            "confidence": float(need.get("priority") or 0),
            "why_selected": " — ".join(x for x in (need.get("situation"), verdict.get("reason")) if x),
            "suggested_reply": top_match.get("reply_draft") or "",
            "model_name": "need_engine",
            "cost_toman": round(float((payload.get("cost") or {}).get("toman") or 0)),
        })

        opp.product_matches.all().delete()
        OpportunityProductMatch.objects.bulk_create([
            OpportunityProductMatch(opportunity=opp, product=p, rank=i, match_score=float(m.get("match_score") or 0),
                                    recommendation_reason=(m.get("verdict") or {}).get("reason") or "",
                                    matching_attributes=m.get("verdict") or {})
            for i, (m, p) in enumerate(items, 1)])

        opp.evidence_items.all().delete()
        ev = [Evidence(opportunity=opp, evidence_type="customer_message", content=e.get("text", ""),
                       source_reference=e.get("url") or f"{platform}:{source.get('chat_id')}:{e.get('message_id')}")
              for e in evidence]
        if need.get("situation"):
            ev.append(Evidence(opportunity=opp, evidence_type="need_signal", content=need["situation"],
                               source_reference=need.get("label", "")))
        for c in verdict.get("conflicts") or []:
            ev.append(Evidence(opportunity=opp, evidence_type="product_match", content=c, source_reference=f"product:{top_product.id}"))
        Evidence.objects.bulk_create(ev)
        out.append(opp)
    return out


def _engine_table(name: str) -> str | None:
    """Qualified name of a need_engine table, or None if the engine has not created it yet."""
    schema = getattr(settings, "NEED_ENGINE_SCHEMA", "need_engine")
    if not schema.replace("_", "").isalnum():
        raise ValueError(f"invalid NEED_ENGINE_SCHEMA: {schema!r}")
    with connection.cursor() as cur:
        cur.execute("SELECT to_regclass(%s)", [f"{schema}.{name}"])
        return f"{schema}.{name}" if cur.fetchone()[0] else None


def published_after(seq: int, limit: int = 500) -> list[tuple[int, dict]]:
    """(seq, payload) of opportunities the engine published after ``seq`` (read-only)."""
    table = _engine_table("opportunities")
    if not table:
        return []
    with connection.cursor() as cur:
        cur.execute(f"SELECT seq, payload FROM {table} WHERE seq > %s ORDER BY seq LIMIT %s", [seq, limit])
        return [(int(s), p if isinstance(p, dict) else json.loads(p)) for s, p in cur.fetchall()]


def _has_column(table: str, column: str) -> bool:
    schema, name = table.split(".")
    with connection.cursor() as cur:
        cur.execute("SELECT 1 FROM information_schema.columns WHERE table_schema = %s AND table_name = %s AND column_name = %s",
                    [schema, name, column])
        return cur.fetchone() is not None


def engine_totals(business=None) -> dict:
    """The engine's lifetime counters (read-only). Zeros if the engine has not run yet.

    With ``business``: the seller's share of the LLM cost (matching/replies of its products, analysis of its private
    groups split among their owners) over the messages reviewed for it (its private groups + global groups).
    """
    empty = {"messages_analysed": 0, "llm_calls": 0, "cost_toman": 0.0, "cost_per_message_toman": 0.0}
    costs, kv = _engine_table("costs"), _engine_table("kv")
    if not costs or not kv:
        return empty
    if business is not None:
        per_chat = _engine_table("chat_analysed")
        if not per_chat or not _has_column(costs, "business_id"):
            return empty
        chats = [str(c) for c in MonitoredCommunity.objects.filter(telegram_chat_id__isnull=False).filter(
            Q(scope=GLOBAL) | Q(business=business)).values_list("telegram_chat_id", flat=True).distinct()]
        with connection.cursor() as cur:
            cur.execute(f"SELECT COUNT(*), COALESCE(SUM(toman), 0) FROM {costs} WHERE business_id = %s", [str(business.pk)])
            calls, toman = cur.fetchone()
            cur.execute(f"SELECT COALESCE(SUM(n), 0) FROM {per_chat} WHERE chat_id = ANY(%s)", [chats])
            n = int(cur.fetchone()[0] or 0)
        return {"messages_analysed": n, "llm_calls": int(calls), "cost_toman": float(toman),
                "cost_per_message_toman": float(toman) / n if n else 0.0}
    once = "WHERE part = 0" if _has_column(costs, "part") else ""
    with connection.cursor() as cur:
        cur.execute(f"SELECT COALESCE(SUM(toman), 0) FROM {costs}")
        toman = cur.fetchone()[0]
        cur.execute(f"SELECT COUNT(*) FROM {costs} {once}")
        calls = cur.fetchone()[0]
        cur.execute(f"SELECT (v #>> '{{}}')::bigint FROM {kv} WHERE k = 'messages_analysed'")
        row = cur.fetchone()
    n = int(row[0]) if row and row[0] is not None else 0
    return {"messages_analysed": n, "llm_calls": int(calls), "cost_toman": float(toman),
            "cost_per_message_toman": float(toman) / n if n else 0.0}
