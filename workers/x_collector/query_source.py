from __future__ import annotations

import logging
import os
from typing import Any

from need_engine.sources import SQLProductSource
from need_engine.config import EngineConfig

log = logging.getLogger("x_collector.queries")


def queries_from_products(products: list[Any]) -> list[str]:
    queries = []
    seen = set()
    for product in sorted(products, key=lambda item: (-int(getattr(item, "discovery_priority", 1)), item.title.casefold())):
        terms = [product.title, product.category_path, *product.category_keywords]
        for term in terms:
            query = " ".join(str(term or "").split())
            if len(query) < 2 or query.casefold() in seen:
                continue
            seen.add(query.casefold())
            queries.append(query)
    return queries


def intent_queries_from_products(products: list[Any], mode: str | None = None, terms: dict[str, dict] | None = None,
                                 cards: dict[str, Any] | None = None) -> list[dict[str, str]]:
    from workers.x_collector.intent_queries import generate_queries

    terms, cards = terms or {}, cards or {}
    result, seen = [], set()
    for product in sorted(products, key=lambda item: (-int(getattr(item, "discovery_priority", 1)), item.title.casefold())):
        pid = str(getattr(product, "product_id", ""))
        for item in generate_queries(product, mode=mode, terms=terms.get(pid), card=cards.get(pid)):
            key = item["query"].casefold()
            if key not in seen:
                seen.add(key)
                result.append(item)
    return result


def load_product_queries() -> list[str]:
    config = EngineConfig()
    source = SQLProductSource(config)
    try:
        return queries_from_products(source.all())
    finally:
        source.db.conn.close()


def _terms_and_cards(config: EngineConfig, products: list[Any]) -> tuple[dict, dict]:
    """Saved product cards (aliases) and, with X_QUERY_LLM=true, LLM-written search terms. Never raises."""
    try:
        from need_engine.store import Store

        store = Store(config.database_url, config.state_schema)
    except Exception as exc:  # engine schema not created yet: deterministic queries from products only
        log.warning("X queries without product cards: %s", exc)
        return {}, {}
    try:
        cards = store.product_cards()
    except Exception as exc:
        log.warning("Could not read product cards: %s", exc)
        cards = {}
    terms: dict = {}
    if os.getenv("X_QUERY_LLM", "false").strip().lower() in {"1", "true", "yes", "on"}:
        try:
            from need_engine.llm import LLMClient
            from workers.x_collector.llm_terms import llm_terms

            terms = llm_terms(products, cards, store, LLMClient(config, store), config.extract_model,
                              batch_size=int(os.getenv("X_QUERY_LLM_BATCH", "15")))
        except Exception as exc:
            log.warning("LLM X query terms unavailable, using deterministic queries: %s", exc)
    try:
        store.close()
    except Exception:
        pass
    return terms, cards


def load_intent_queries(mode: str | None = None) -> list[dict[str, str]]:
    config = EngineConfig()
    source = SQLProductSource(config)
    try:
        products = source.all()
    finally:
        source.db.conn.close()
    terms, cards = _terms_and_cards(config, products)
    return intent_queries_from_products(products, mode=mode, terms=terms, cards=cards)


def collector_queries(path: str | None = None) -> list[str | dict[str, str]]:
    override = path or os.getenv("X_COLLECT_QUERIES_FILE")
    if override:
        from workers.x_collector.worker import XCollector

        return XCollector.load_queries(override)
    if os.getenv("X_QUERY_SOURCE", "generated").casefold() == "registry":
        config = EngineConfig()
        source = SQLProductSource(config)
        try:
            db = source.db
            rows = db.all("SELECT q.text, q.weight, q.kind, b.id AS business_id FROM public.discovery_xsearchquery q JOIN public.businesses_business b ON b.id=q.business_id WHERE q.state='active' ORDER BY b.id,q.text")
            negatives = db.all("SELECT n.normalized_term, n.business_id FROM public.discovery_xnegativeterm n")
            blocked: dict[int, set[str]] = {}
            for item in negatives:
                blocked.setdefault(int(item["business_id"]), set()).add(str(item["normalized_term"]).casefold())
            return [{"query": row["text"], "priority": float(row["weight"] or 1),
                     "kind": "intent" if "intent" in row["kind"] else "product"}
                    for row in rows if not any(term and term in row["text"].casefold() for term in blocked.get(int(row["business_id"]), set()))]
        finally:
            source.db.close()
    try:
        return load_intent_queries()
    except Exception as exc:
        raise RuntimeError("Could not load active seller products for X search. Set X_COLLECT_QUERIES_FILE for a static override.") from exc
