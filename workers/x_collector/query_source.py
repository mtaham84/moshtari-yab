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
    if os.getenv("X_QUERY_LLM", "true").strip().lower() in {"1", "true", "yes", "on"}:
        try:
            from need_engine.llm import LLMClient
            from workers.x_collector.llm_terms import llm_terms
            from workers.x_collector.proqe import expand_queries_with_proqe, ExemplarStore

            llm_inst = LLMClient(config, store)
            terms = llm_terms(products, cards, store, llm_inst, config.extract_model,
                              batch_size=int(os.getenv("X_QUERY_LLM_BATCH", "6")))

            state_file = os.getenv("X_COLLECT_STATE_FILE", "output/x_collector_state.sqlite3")
            exemplar_store = ExemplarStore(state_file)
            for product in products:
                pid = str(getattr(product, "product_id", ""))
                if exemplar_store.expansion_due(pid):
                    try:
                        expand_queries_with_proqe(product, exemplar_store, llm=llm_inst, model=config.extract_model)
                    except Exception as p_exc:
                        log.debug("ProQE expansion skipped for product %s: %s", pid, p_exc)
        except Exception as exc:
            log.warning("LLM X query terms unavailable, using deterministic queries: %s", exc)
    try:
        store.close()
    except Exception:
        pass
    return terms, cards


def targeted_queries_from_families(families: list[Any], terms: dict[str, dict] | None = None,
                                   cards: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    """Queries per family, interleaved per seller up to ``X_QUERY_MAX_PER_TENANT``, exact duplicates merged (one search,
    tagged with every product/seller it belongs to)."""
    from workers.x_collector.intent_queries import product_queries

    terms, cards = terms or {}, cards or {}
    cap = int(os.getenv("X_QUERY_MAX_PER_TENANT", "40"))
    by_business: dict[str, list[list[dict[str, Any]]]] = {}
    for fam in sorted(families, key=lambda f: (-int(getattr(f, "discovery_priority", 1)), str(f.title).casefold())):
        if not getattr(fam, "x_search_enabled", False):
            continue
        fid = str(fam.product_id)
        items = product_queries(fam, terms=terms.get(fid), card=cards.get(fid))
        if items:
            by_business.setdefault(str(fam.business_id), []).append(items)
    merged: dict[str, dict[str, Any]] = {}
    for biz, lists in by_business.items():
        picked = 0
        for rank in range(max((len(x) for x in lists), default=0)):     # round robin: no family starves the others
            for items in lists:
                if rank >= len(items) or picked >= cap:
                    continue
                item = items[rank]
                key = item["query"].casefold()
                picked += 1
                if key in merged:
                    hit = merged[key]
                    hit["product_ids"] = sorted(set(hit["product_ids"]) | set(item["product_ids"]))
                    hit["business_ids"] = sorted(set(hit["business_ids"]) | set(item["business_ids"]))
                    hit["priority"] = max(hit["priority"], item["priority"])
                else:
                    merged[key] = item
    return list(merged.values())


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
        notes_db = source.db
        x_mode = (config.x_mode or "product").strip().lower()
        return _load_intent_queries(config, source, products, x_mode, mode)
    finally:
        try:
            source.db.conn.close()
        except Exception:
            pass


def _load_intent_queries(config, source, products, x_mode, mode):
    if x_mode in {"product", "both"}:
        products_on = [p for p in products if p.x_search_enabled]
        try:   # sellers whose balance is used up: no search (and no search fee) until they top up
            from need_engine.registry import ModelRegistry

            blocked = ModelRegistry(config).blocked()
        except Exception as exc:
            log.warning("Seller balances unavailable, searching all X-enabled products: %s", exc)
            blocked = frozenset()
        if blocked:
            skipped = [p.product_id for p in products_on if p.business_id and str(p.business_id) in blocked]
            if skipped:
                log.info("X search paused for %d products (seller balance used up)", len(skipped))
            products_on = [p for p in products_on if not (p.business_id and str(p.business_id) in blocked)]
        from workers.x_collector.families import build_families, load_notes

        families = build_families(products_on, load_notes(source.db))
        terms, cards = _terms_and_cards(config, families) if families else ({}, {})
        return targeted_queries_from_families(families, terms=terms, cards=cards)   # "both" now equals "product"
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
