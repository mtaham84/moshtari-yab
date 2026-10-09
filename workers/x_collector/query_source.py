from __future__ import annotations

import os
from typing import Any

from need_engine.sources import SQLProductSource
from need_engine.config import EngineConfig


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


def intent_queries_from_products(products: list[Any], mode: str | None = None) -> list[dict[str, str]]:
    from workers.x_collector.intent_queries import generate_queries

    result, seen = [], set()
    for product in sorted(products, key=lambda item: (-int(getattr(item, "discovery_priority", 1)), item.title.casefold())):
        for item in generate_queries(product, mode=mode):
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


def load_intent_queries(mode: str | None = None) -> list[dict[str, str]]:
    config = EngineConfig()
    source = SQLProductSource(config)
    try:
        return intent_queries_from_products(source.all(), mode=mode)
    finally:
        source.db.conn.close()


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
