"""The panel's own (small) LLM calls, made with need_engine's client so they share its rate limits, response cache
and cost ledger: «نمونه بساز» (sample draft before saving the style), «دوباره بنویس» (new draft for an opportunity)
and «پر کردن خودکار از لینک» (product page → form fields). Every call is charged to the seller who asked for it.
"""
from __future__ import annotations

import logging
from contextlib import contextmanager
from typing import Iterator

from django.conf import settings
from django.db import connection
from psycopg.conninfo import make_conninfo

from need_engine.config import EngineConfig
from need_engine.registry import ModelRegistry
from need_engine.llm import LLMClient, LLMError
from need_engine.reply import MessageStyle as EngineStyle
from need_engine.reply import write_reply
from need_engine.store import Store

log = logging.getLogger(__name__)


class AgentUnavailable(RuntimeError):
    """Shown to the seller as is (Persian)."""


def _dsn() -> str:
    """Django's own database (in tests: the test database)."""
    s = connection.settings_dict
    params = {"host": s.get("HOST"), "port": str(s.get("PORT") or ""), "dbname": s.get("NAME"),
              "user": s.get("USER"), "password": s.get("PASSWORD")}
    return make_conninfo(**{k: v for k, v in params.items() if v})


@contextmanager
def panel_llm() -> Iterator[tuple[EngineConfig, LLMClient]]:
    cfg = EngineConfig()
    cfg.state_schema = settings.NEED_ENGINE_SCHEMA
    cfg.public_base_url = settings.PUBLIC_BASE_URL
    cfg.database_url = _dsn()   # providers / models / prices of the admin panel are read from the same database
    mock = None
    registry = ModelRegistry(cfg)
    if settings.PANEL_MOCK_LLM:
        from need_engine.mock import mock_llm as mock
    elif not registry.has_credentials():
        raise AgentUnavailable("کلید مدل زبانی روی سرور تنظیم نشده است (پنل مدیریت ← مدل‌ها، یا NE_LLM_API_KEY).")
    store = Store(_dsn(), cfg.state_schema)
    try:
        yield cfg, LLMClient(cfg, store, mock=mock, registry=registry)
    except AgentUnavailable:
        raise
    except LLMError as e:
        log.warning("panel LLM call failed: %s", e)
        raise AgentUnavailable("ارتباط با مدل زبانی برقرار نشد یا سهمیه‌ی امروز تمام شده است. کمی بعد دوباره امتحان کنید.") from e
    finally:
        store.close()


# ── products as the engine sees them ──────────────────────────────────────────
def click_url(product, ref: str) -> str | None:
    """Link used in drafts: <base>/r/<id>/?ref=… → counts the click → the seller's own page. None without a page."""
    if not (product.url or "").strip():
        return None
    return f"{settings.PUBLIC_BASE_URL}/r/{product.id}/?ref={ref}"


def product_line(product) -> str:
    """Same shape as need_engine.catalog.Catalog.line."""
    price = f"{int(product.price):,} تومان" if product.price else "قیمت نامشخص"
    desc = " ".join(x for x in [product.description or "", product.target_customer or ""] if x)
    return f"[{product.id}] {product.name} | {product.product_type} | {price} | آنلاین (ارسال سراسری) | {desc[:140]}"


def engine_style(style) -> EngineStyle:
    """Django MessageStyle (saved or not) → the engine's validated style."""
    return EngineStyle.from_dict(style.as_engine_dict() if style is not None else None)


# ── drafts ────────────────────────────────────────────────────────────────────
def sample_reply(business, style, product) -> str:
    """A draft with the (unsaved) style for one of the seller's products. Uses the latest real opportunity of that
    product when there is one, otherwise a typical question from a group."""
    from apps.discovery.models import Opportunity

    opp = (Opportunity.objects.filter(business=business, product_matches__product=product)
           .select_related("ai_analysis").order_by("-created_at").first())
    if opp and opp.source_raw_message.strip():
        person = opp.source_raw_message[:1500]
        situation = getattr(getattr(opp, "ai_analysis", None), "need", "") or ""
    else:
        person = f"سلام بچه‌ها، کسی {product.name} یا چیزی شبیهش سراغ داره؟ دنبال یه گزینه‌ی خوب و مطمئنم."
        situation = "یک نفر در گروه دنبال این نوع محصول است (پیام نمونه)."
    with panel_llm() as (cfg, llm):
        text, _ = write_reply(llm, cfg, person_messages=person, situation=situation, product_line=product_line(product),
                              conflicts=[], style=engine_style(style), link=click_url(product, "sample"),
                              ref=f"sample:{business.pk}:{product.pk}", businesses=[str(business.pk)], stage="reply_sample")
    return text


def rewrite_reply(opportunity, match) -> str:
    """«دوباره بنویس»: a new wording for one matched product, with the seller's saved style. Kept in
    ``trace_metadata['seller_drafts']`` so a later engine update does not overwrite it."""
    from apps.businesses.models import MessageStyle

    product = match.product
    business = opportunity.business
    meta = dict(opportunity.trace_metadata or {})
    need = (meta.get("engine") or {}).get("need") or {}
    person = "\n".join(e.content for e in opportunity.evidence_items.filter(evidence_type="customer_message")) \
        or opportunity.source_raw_message
    conflicts = list((match.matching_attributes or {}).get("conflicts") or [])
    variant = int((meta.get("seller_drafts_n") or {}).get(str(product.id), 0)) + 1
    with panel_llm() as (cfg, llm):
        text, _ = write_reply(llm, cfg, person_messages=person[:3000], situation=need.get("situation") or "",
                              product_line=product_line(product), conflicts=conflicts,
                              style=engine_style(MessageStyle.for_business(business)),
                              link=click_url(product, opportunity.engine_opportunity_id or str(opportunity.pk)),
                              ref=f"{opportunity.engine_opportunity_id}:{product.id}", businesses=[str(business.pk)],
                              stage="reply_rewrite", variant=variant)
    meta.setdefault("seller_drafts", {})[str(product.id)] = text
    meta.setdefault("seller_drafts_n", {})[str(product.id)] = variant
    opportunity.trace_metadata = meta
    opportunity.save(update_fields=["trace_metadata", "updated_at"])
    if match.rank == 1 and hasattr(opportunity, "ai_analysis"):
        opportunity.ai_analysis.suggested_reply = text
        opportunity.ai_analysis.save(update_fields=["suggested_reply"])
    return text


def drafts_for(opportunity) -> dict[str, str]:
    """product id → current draft (seller's rewrite first, then the engine's)."""
    meta = opportunity.trace_metadata or {}
    out = {str(m.get("product_id")): m.get("reply_draft") or "" for m in meta.get("matched_products") or []}
    out.update({k: v for k, v in (meta.get("seller_drafts") or {}).items() if v})
    return out
