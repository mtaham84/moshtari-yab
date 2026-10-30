"""Product families: queries are generated per (seller × category node) instead of per product.

A family groups the products of one seller that sit in the same node of his category tree (collapsed to the first three
levels). One model call writes the queries of the whole family, the seller's free-text notes steer them, and every query
is tagged with all the products of the family. Products without a category stay single-product families.
"""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from typing import Any

FAMILY_VERSION = "2"
SEARCH_DEPTH = 3


def path_head(category_path: str | None) -> str:
    parts = [p.strip() for p in str(category_path or "").split(">") if p.strip()][:SEARCH_DEPTH]
    return " > ".join(parts)


def family_id(business_id: Any, head: str, product_id: Any = None) -> str:
    seed = head or f"p{product_id}"
    return f"f{business_id}-{hashlib.md5(seed.encode()).hexdigest()[:8]}"


@dataclass
class Family:
    product_id: str                      # the family id (kept under this name so the existing LLM/ProQE code can use it)
    business_id: str
    title: str
    category_path: str
    description: str
    member_product_ids: list[str]
    titles: list[str] = field(default_factory=list)
    notes: str = ""
    discovery_priority: int = 1
    product_type: str = ""
    category_keywords: list[str] = field(default_factory=list)
    x_search_enabled: bool = True

    def content_hash(self) -> str:
        payload = [FAMILY_VERSION, self.category_path, sorted(self.titles)[:12], self.notes]
        return hashlib.sha1(json.dumps(payload, ensure_ascii=False, default=str).encode()).hexdigest()


def build_families(products: list[Any], notes_by_business: dict[str, str] | None = None) -> list[Family]:
    notes_by_business = notes_by_business or {}
    groups: dict[str, list[Any]] = {}
    heads: dict[str, str] = {}
    for p in products:
        head = path_head(getattr(p, "category_path", ""))
        fid = family_id(getattr(p, "business_id", None), head, getattr(p, "product_id", None))
        groups.setdefault(fid, []).append(p)
        heads[fid] = head
    out: list[Family] = []
    for fid, members in groups.items():
        head = heads[fid]
        biz = str(getattr(members[0], "business_id", "") or "")
        notes = (notes_by_business.get(biz) or "").strip()[:1000]
        titles = [str(getattr(m, "title", "") or "") for m in members]
        leaf = head.split(" > ")[-1] if head else (titles[0] if titles else "")
        desc = []
        if notes:
            desc.append("SELLER NOTES (ideal customers and sales model; follow them): " + re.sub(r"\s+", " ", notes))
        desc.append("Category path: " + (head or "—"))
        desc.append("Products in this family: " + " | ".join(t[:60] for t in titles[:10]))
        out.append(Family(
            product_id=fid, business_id=biz, title=leaf, category_path=head, description="\n".join(desc)[:1400],
            member_product_ids=[str(getattr(m, "product_id", "")) for m in members], titles=titles, notes=notes,
            discovery_priority=max(int(getattr(m, "discovery_priority", 1) or 1) for m in members),
            product_type=str(getattr(members[0], "product_type", "") or ""),
            category_keywords=list(getattr(members[0], "category_keywords", []) or [])[:6],
        ))
    return out


def load_notes(db) -> dict[str, str]:
    """business id -> seller notes (empty when the column does not exist yet)."""
    try:
        rows = db.all("SELECT id, search_notes FROM public.businesses_business WHERE COALESCE(search_notes, '') <> ''")
    except Exception:
        return {}
    return {str(r["id"]): str(r["search_notes"]) for r in rows}
