from __future__ import annotations

import logging
import os
from typing import Any

from need_engine.sources import SQLProductSource
from need_engine.config import EngineConfig
from .balance_gate import BalanceGate

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
        log.warning("X queries without product cards: %s", exc, exc_info=True)
        return {}, {}
    try:
        cards = store.product_cards()
    except Exception as exc:
        log.warning("Could not read product cards: %s", exc, exc_info=True)
        cards = {}
    if products and hasattr(products[0], "member_product_ids"):
        from workers.x_collector.families import family_cards
        cards = family_cards(products, cards)
    terms: dict = {}
    if os.getenv("X_QUERY_LLM", "true").strip().lower() in {"1", "true", "yes", "on"}:
        try:
            from need_engine.llm import LLMClient
            from workers.x_collector.llm_terms import llm_terms

            llm_inst = LLMClient(config, store)
            terms = llm_terms(products, cards, store, llm_inst, config.extract_model,
                              batch_size=int(os.getenv("X_QUERY_LLM_BATCH", "6")))

        except Exception as exc:
            log.warning("LLM X query terms unavailable, using deterministic queries: %s", exc, exc_info=True)
    try:
        store.close()
    except Exception:
        log.debug("Could not close the query store", exc_info=True)
    return terms, cards


def targeted_queries_from_families(families: list[Any], terms: dict[str, dict] | None = None,
                                   cards: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    """Queries per family, interleaved per seller up to ``X_QUERY_MAX_PER_TENANT``, exact duplicates merged (one search,
    tagged with every product/seller it belongs to)."""
    from workers.x_collector.intent_queries import product_queries

    terms, cards = terms or {}, cards or {}
    from workers.x_collector.families import family_cards, family_topic_contexts
    member_card_keys = {str(pid) for fam in families for pid in getattr(fam, "member_product_ids", [])}
    if member_card_keys.intersection(cards):
        cards = family_cards(families, cards)
    contexts = family_topic_contexts(families, cards)
    for family in families:
        contexts[str(family.product_id)]["family"] = family
    from .config import query_limit
    cap = query_limit()
    from need_engine.text import norm
    categories: dict[str, list[tuple[Any, list[dict]]]] = {}
    for fam in sorted(families, key=lambda f: (-int(getattr(f, "discovery_priority", 1)), str(f.product_id))):
        if not getattr(fam, "x_search_enabled", False):
            continue
        fid = str(fam.product_id)
        items = product_queries(fam, terms=terms.get(fid), card=cards.get(fid), topic_context=contexts.get(fid))
        key = norm(getattr(fam, "category_path", "")) or fid
        categories.setdefault(key, []).append((fam, items))
    by_business: dict[str, list[list[dict[str, Any]]]] = {}
    for group in categories.values():
        selected, seen = [], set()
        owners = sorted({str(f.business_id) for f, _ in group})
        products = sorted({str(pid) for f, _ in group for pid in f.member_product_ids})
        for rank in range(max((len(items) for _, items in group), default=0)):
            for _, items in group:
                if rank >= len(items) or len(selected) >= 6:
                    continue
                item = items[rank]
                key = norm(item['query'])
                if key not in seen:
                    selected.append({**item, 'business_ids': owners, 'product_ids': products})
                    seen.add(key)
        for owner in owners:
            by_business.setdefault(owner, []).append(selected)
    ownership = {str(pid): str(fam.business_id) for fam in families
                 for pid in getattr(fam, "member_product_ids", [fam.product_id])}
    merged: dict[str, dict[str, Any]] = {}
    for biz, lists in by_business.items():
        count = 0
        for rank in range(max((len(x) for x in lists), default=0)):     # round robin: no family starves the others
            for items in lists:
                if rank >= len(items) or count >= cap:
                    continue
                item = {**items[rank], 'business_ids': [biz],
                        'product_ids': [pid for pid in items[rank]['product_ids'] if ownership.get(pid) == biz]}
                count += 1
                key = item["query"].casefold()
                if key in merged:
                    hit = merged[key]
                    hit["product_ids"] = sorted(set(hit["product_ids"]) | set(item["product_ids"]))
                    hit["business_ids"] = sorted(set(hit["business_ids"]) | set(item["business_ids"]))
                    hit["priority"] = max(hit["priority"], item["priority"])
                else:
                    merged[key] = item
    return [{**item, "product_business_ids": {pid: ownership[pid] for pid in item["product_ids"] if pid in ownership}}
            for item in merged.values()]


def targeted_queries_from_products(products: list[Any], terms: dict[str, dict] | None = None,
                                   cards: dict[str, Any] | None = None, notes: dict[str, str] | None = None) -> list[dict[str, Any]]:
    """Groups the products into families, then builds the family queries."""
    from workers.x_collector.families import build_families

    on = [p for p in products if getattr(p, "x_search_enabled", False)]
    return targeted_queries_from_families(build_families(on, notes), terms=terms, cards=cards)


def load_intent_queries(mode: str | None = None) -> list[dict[str, str]]:
    config = EngineConfig()
    source = SQLProductSource(config)
    try:
        products = source.all()
        x_mode = (config.x_mode or "product").strip().lower()
        return _load_intent_queries(config, source, products, x_mode, mode)
    finally:
        try:
            source.db.conn.close()
        except Exception:
            log.debug("Could not close the engine connection", exc_info=True)


def _load_intent_queries(config, source, products, x_mode, mode):
    products_on = [p for p in products if p.x_search_enabled]
    with BalanceGate(config, db=source.db) as gate:
        funded = gate.funded(p.business_id for p in products_on)
    from workers.x_collector.families import build_families, load_notes
    from .portfolio import manage_portfolio

    families = build_families(products_on, load_notes(source.db))
    paid_families = [f for f in families if str(f.business_id) in funded]
    terms, cards = _terms_and_cards(config, paid_families) if paid_families else ({}, {})
    candidates = targeted_queries_from_families(families, terms=terms, cards=cards)
    portfolio = manage_portfolio(config, families, candidates, enabled_businesses=funded)
    return [item for item in portfolio if set(item['business_ids']) & funded]


def _collector_queries(path: str | None = None) -> list[str | dict[str, str]]:
    override = path or os.getenv("X_COLLECT_QUERIES_FILE")
    if override:
        from workers.x_collector.worker import XCollector

        with BalanceGate() as gate:
            return gate.filter_queries(XCollector.load_queries(override))
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
            products = [p for p in source.all() if p.x_search_enabled]
            by_business = {}
            for product in products:
                by_business.setdefault(str(product.business_id), []).append(str(product.product_id))
            with BalanceGate(config, db=db) as gate:
                funded = gate.funded(by_business)
            return [{"query": row["text"], "priority": float(row["weight"] or 1),
                     "kind": "intent" if "intent" in row["kind"] else "product",
                     "business_ids": [str(row["business_id"])],
                     "product_ids": by_business[str(row["business_id"])],
                     "product_business_ids": {pid: str(row["business_id"]) for pid in by_business[str(row["business_id"])]}}
                    for row in rows if str(row["business_id"]) in funded
                    and not any(term and term in row["text"].casefold() for term in blocked.get(int(row["business_id"]), set()))]
        finally:
            source.db.close()
    try:
        return load_intent_queries()
    except Exception as exc:
        raise RuntimeError("Could not load active seller products for X search. Set X_COLLECT_QUERIES_FILE for a static override.") from exc


def collector_queries(path: str | None = None) -> list[str | dict[str, str]]:
    queries = _collector_queries(path)
    from .config import thread_collection_enabled
    if not thread_collection_enabled():
        return queries
    from .thread_queries import load_thread_queries
    config = EngineConfig()
    source = SQLProductSource(config)
    try:
        products = [p for p in source.all() if p.x_search_enabled]
        with BalanceGate(config, db=source.db) as gate:
            threads = gate.filter_queries(load_thread_queries(config, source.db, products))
        return [*queries, *threads]
    finally:
        source.close()
