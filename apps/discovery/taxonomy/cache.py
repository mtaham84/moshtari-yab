from __future__ import annotations

import logging
from typing import Any, Optional
from django.core.cache import cache

from apps.businesses.models import Business
from apps.products.models import Category, Product
from .config import TAXONOMY_CACHE_TIMEOUT
from .embeddings import (
    get_embedding_provider,
    format_category_semantic_text,
)

logger = logging.getLogger(__name__)

# Process-level fast cache
_LOCAL_TAXONOMY_CACHE: dict[str, dict[str, Any]] = {}


def get_taxonomy_cache_key(business_id: int, taxonomy_version: int) -> str:
    return f"taxonomy:seller:{business_id}:version:{taxonomy_version}"


def ensure_category_embeddings(business: Business) -> None:
    """
    Ensures all categories for the business have an embedding generated.
    Does NOT regenerate existing embeddings unless empty.
    """
    categories = Category.objects.filter(business=business)
    provider = get_embedding_provider()
    updated = []

    for cat in categories:
        if not cat.embedding or len(cat.embedding) == 0:
            semantic_text = format_category_semantic_text(
                name=cat.name,
                full_path=cat.get_full_path(),
                description=cat.description,
                keywords=cat.keywords or []
            )
            cat.embedding = provider.embed_text(semantic_text)
            cat.full_path = cat.get_full_path()
            cat.depth = len(cat.get_ancestors()) + 1
            updated.append(cat)

    if updated:
        for c in updated:
            Category.objects.filter(id=c.id).update(
                embedding=c.embedding,
                full_path=c.full_path,
                depth=c.depth
            )


def build_seller_taxonomy_tree(business: Business) -> dict[str, Any]:
    """
    Constructs the in-memory tree representation of the seller's taxonomy.
    Attaches descendant names, branch keywords, and branch product names for hierarchical routing.
    """
    ensure_category_embeddings(business)

    categories = list(
        Category.objects.filter(business=business, is_active=True)
        .order_by("depth", "id")
    )

    categories_by_id: dict[int, dict[str, Any]] = {}
    children_map: dict[int, list[int]] = {}
    roots: list[dict[str, Any]] = []

    for cat in categories:
        cat_data = {
            "id": cat.id,
            "name": cat.name,
            "normalized_name": cat.normalized_name,
            "full_path": cat.full_path or cat.get_full_path(),
            "depth": cat.depth,
            "parent_id": cat.parent_id,
            "description": cat.description or "",
            "keywords": cat.keywords or [],
            "embedding": cat.embedding or [],
            "product_type": cat.product_type,
            "is_leaf": True,  # Will update below
            "descendant_ids": [],
            "descendant_names": [],
            "branch_keywords": [],
            "branch_product_names": [],
        }
        categories_by_id[cat.id] = cat_data
        children_map[cat.id] = []

    for cat in categories:
        if cat.parent_id and cat.parent_id in children_map:
            children_map[cat.parent_id].append(cat.id)
            if cat.parent_id in categories_by_id:
                categories_by_id[cat.parent_id]["is_leaf"] = False
        elif not cat.parent_id:
            roots.append(categories_by_id[cat.id])

    # Fetch active products for the business to attach branch product names
    active_products = list(
        Product.objects.filter(business=business, status="ACTIVE", is_discovery_active=True)
        .values("id", "category_id", "name")
    )
    products_by_cat: dict[int, list[str]] = {}
    for p in active_products:
        cid = p["category_id"]
        if cid:
            products_by_cat.setdefault(cid, []).append(p["name"])

    # Compute recursive descendant IDs, names, keywords, and product names for each node
    def get_all_descendants(cid: int) -> list[int]:
        res = []
        for child_id in children_map.get(cid, []):
            res.append(child_id)
            res.extend(get_all_descendants(child_id))
        return res

    for cid, c_data in categories_by_id.items():
        desc_ids = get_all_descendants(cid)
        c_data["descendant_ids"] = desc_ids
        c_data["descendant_names"] = [categories_by_id[d]["name"] for d in desc_ids if d in categories_by_id]

        # Branch keywords: own keywords + all descendants' keywords
        all_kw = list(c_data.get("keywords") or [])
        for d in desc_ids:
            if d in categories_by_id:
                all_kw.extend(categories_by_id[d].get("keywords") or [])
        c_data["branch_keywords"] = list(dict.fromkeys(all_kw))

        # Branch product names: own products + all descendants' products
        branch_pnames = list(products_by_cat.get(cid, []))
        for d in desc_ids:
            branch_pnames.extend(products_by_cat.get(d, []))
        c_data["branch_product_names"] = list(dict.fromkeys(branch_pnames))

    return {
        "seller_id": business.id,
        "taxonomy_version": business.taxonomy_version,
        "roots": roots,
        "categories_by_id": categories_by_id,
        "children_map": children_map,
        "total_categories": len(categories),
    }


def get_seller_taxonomy(business: Business) -> dict[str, Any]:
    """
    Retrieves seller taxonomy from fast cache, Django cache, or builds it.
    """
    cache_key = get_taxonomy_cache_key(business.id, business.taxonomy_version)

    # 1. Fast process memory
    if cache_key in _LOCAL_TAXONOMY_CACHE:
        return _LOCAL_TAXONOMY_CACHE[cache_key]

    # 2. Django cache
    cached_data = cache.get(cache_key)
    if cached_data:
        _LOCAL_TAXONOMY_CACHE[cache_key] = cached_data
        return cached_data

    # 3. Build from DB
    tree_data = build_seller_taxonomy_tree(business)
    cache.set(cache_key, tree_data, timeout=TAXONOMY_CACHE_TIMEOUT)
    _LOCAL_TAXONOMY_CACHE[cache_key] = tree_data
    return tree_data


def invalidate_seller_taxonomy_cache(business: Business) -> None:
    """
    Invalidates taxonomy cache for the business and clears process-level memory.
    """
    cache_key = get_taxonomy_cache_key(business.id, business.taxonomy_version)
    cache.delete(cache_key)
    _LOCAL_TAXONOMY_CACHE.pop(cache_key, None)
    logger.info(f"Invalidated taxonomy cache for business {business.id}")
