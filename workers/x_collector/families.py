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

from need_engine.text import norm, tokens

FAMILY_VERSION = "3"
SEARCH_DEPTH = 3
_CLOTHING_ROOTS = {norm(x) for x in ("پوشاک", "لباس")}
_DENTAL_ROOTS = {norm(x) for x in ("دندان", "دندانپزشک", "دندانپزشکی", "دندان پزشکی", "دندان‌پزشکی",
                                   "دندونپزشک", "دندونپزشکی", "دندون پزشکی")}
_CATEGORY_SYNONYMS = {norm("پوشاک"): ("لباس",), norm("لباس"): ("پوشاک",),
                      norm("دندان"): ("دندون",), norm("دندانپزشکی"): ("دندونپزشکی", "دندون"),
                      norm("دندان پزشکی"): ("دندون پزشکی", "دندون"), norm("دندان‌پزشکی"): ("دندون پزشکی", "دندون")}
_APPAREL_DISCOVERY_TERMS = (
    "لباس زمستونی", "لباس بهاره", "لباس خنک", "لباس تابستونی", "لباس راحت", "لباس گشاد", "لباس تنگ",
    "لباس مجلسی", "لباس کژوال", "لباس اسپرت",
)
_DENTAL_DISCOVERY_TERMS = ("دندون درد", "دندون خراب", "دندون لق")
_TOPIC_MODIFIERS = {norm(x) for x in (
    "مردانه", "زنانه", "بچگانه", "دخترانه", "پسرانه", "ورزشی", "مجلسی", "تابستانی", "زمستانی",
    "تابستونی", "زمستونی", "پاییزه", "بهاره", "خنک", "گرم", "راحت", "گشاد", "تنگ", "شیک",
    "کژوال", "اسپرت", "روزمره", "چهارخانه", "راه راه", "لینن", "حریر", "آکسفورد", "استوک", "جدید",
)}
_TOPIC_FILLER = {norm(x) for x in ("و", "مدل", "لباس", "پوشاک", "فروشگاه", "خرید", "قیمت", "عمده")}


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
    anchor_words: list[str] = field(default_factory=list)
    other_family_names: list[str] = field(default_factory=list)

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


def _get(card: Any, key: str, default=None):
    if isinstance(card, dict):
        return card.get(key, default)
    return getattr(card, key, default)


def family_cards(families: list[Any], product_cards: dict[str, Any] | None = None) -> dict[str, Any]:
    """Collapse product-card aliases onto family IDs so family query generation sees every member's vocabulary."""
    from types import SimpleNamespace

    product_cards = product_cards or {}
    out = {}
    for family in families:
        member_cards = [product_cards.get(str(pid)) for pid in getattr(family, "member_product_ids", [])]
        member_cards = [card for card in member_cards if card is not None]
        aliases, problems, descriptions = [], [], []
        for card in member_cards:
            aliases.extend(_get(card, "aliases", []) or [])
            problems.extend(_get(card, "problems_solved", []) or [])
            what = _get(card, "what_it_is", "")
            if what:
                descriptions.append(str(what))
        out[str(family.product_id)] = SimpleNamespace(
            aliases=list(dict.fromkeys(str(x) for x in aliases if x)),
            problems_solved=list(dict.fromkeys(str(x) for x in problems if x)),
            what_it_is="؛ ".join(dict.fromkeys(descriptions))[:1400],
        )
    return out


def topic_head(title: str) -> str:
    """First real topic noun in a product title, ignoring audience, season, fabric and merchandising modifiers."""
    return next((word for word in tokens(title) if word not in _TOPIC_MODIFIERS and word not in _TOPIC_FILLER), "")


def family_discovery_terms(family: Any) -> list[tuple[str, str]]:
    """Useful colloquial category-level searches; family validation still blocks sibling-topic drift."""
    path = norm(str(getattr(family, "category_path", "") or ""))
    title = norm(str(getattr(family, "title", "") or ""))
    values = " ".join((path, title))
    out = []
    if any(root in values for root in _CLOTHING_ROOTS):
        out.extend((phrase, "problem" if any(x in phrase for x in ("تنگ", "گشاد", "خنک", "راحت")) else "intent")
                   for phrase in _APPAREL_DISCOVERY_TERMS)
    if any(root in values for root in _DENTAL_ROOTS):
        out.extend((phrase, "problem") for phrase in _DENTAL_DISCOVERY_TERMS)
    return out


def family_category_synonyms(family: Any) -> set[str]:
    """Common colloquial category words for category paths such as پوشاک > مردانه > پیراهن."""
    path = str(getattr(family, "category_path", "") or "")
    categories = {norm(part) for part in path.split(">")}
    return {word for category, synonyms in _CATEGORY_SYNONYMS.items() if category in categories
            for synonym in synonyms for word in tokens(synonym)}


def family_anchor_tokens(family: Any, card: Any = None) -> set[str]:
    """Normalized family anchors from its leaf, product type, aliases, keywords and member-title head nouns."""
    path = str(getattr(family, "category_path", "") or "")
    leaf = path.split(">")[-1].strip() if path else str(getattr(family, "title", "") or "")
    values = [leaf, getattr(family, "product_type", ""), *(getattr(family, "category_keywords", []) or []),
              *(_get(card, "aliases", []) or [])]
    values.extend(topic_head(title) for title in (getattr(family, "titles", []) or []))
    return ({word for value in values for word in tokens(str(value or "")) if len(word) > 1}
            | family_category_synonyms(family))


def family_topic_tokens(family: Any, card: Any = None) -> set[str]:
    """Topic nouns only, excluding audience/style modifiers that must not mask sibling-family drift."""
    path = str(getattr(family, "category_path", "") or "")
    leaf = path.split(">")[-1].strip() if path else str(getattr(family, "title", "") or "")
    values = [leaf, getattr(family, "product_type", ""), *(getattr(family, "category_keywords", []) or []),
              *(_get(card, "aliases", []) or [])]
    values.extend(topic_head(title) for title in (getattr(family, "titles", []) or []))
    return {word for value in values for word in tokens(str(value or ""))
            if len(word) > 1 and word not in _TOPIC_MODIFIERS and word not in _TOPIC_FILLER}


def family_topic_contexts(families: list[Any], cards: dict[str, Any] | None = None) -> dict[str, dict[str, Any]]:
    """Build family anchors plus sibling leaf nouns per tenant, for prompt context and hard query validation."""
    cards = cards or {}
    siblings: dict[str, list[tuple[str, str, set[str]]]] = {}
    for family in families:
        path = str(getattr(family, "category_path", "") or "")
        leaf = path.split(">")[-1].strip() if path else str(getattr(family, "title", "") or "")
        nouns = {w for w in tokens(leaf) if w not in _TOPIC_MODIFIERS and w not in _TOPIC_FILLER}
        siblings.setdefault(str(getattr(family, "business_id", "")), []).append(
            (str(family.product_id), leaf, nouns))
    contexts = {}
    for family in families:
        fid, business_id = str(family.product_id), str(getattr(family, "business_id", ""))
        other = [(name, nouns) for other_id, name, nouns in siblings.get(business_id, []) if other_id != fid]
        contexts[fid] = {
            "anchor_tokens": family_anchor_tokens(family, cards.get(fid)),
            "anchor_words": sorted(family_anchor_tokens(family, cards.get(fid))),
            "topic_tokens": family_topic_tokens(family, cards.get(fid)),
            "family": family,
            "other_family_names": [name for name, _ in other],
            "other_family_nouns": set().union(*(nouns for _, nouns in other)) if other else set(),
        }
    return contexts


def family_phrase_allowed(phrase: str, anchor_tokens: set[str], other_family_nouns: set[str] | None = None,
                          topic_tokens: set[str] | None = None) -> bool:
    phrase_tokens = set(tokens(phrase))
    if not phrase_tokens.intersection(anchor_tokens):
        return False
    other_family_nouns = other_family_nouns or set()
    matches_other = phrase_tokens.intersection(other_family_nouns)
    matches_this = phrase_tokens.intersection(topic_tokens if topic_tokens is not None else anchor_tokens)
    return not matches_other or bool(matches_this)


def load_notes(db) -> dict[str, str]:
    """business id -> seller notes (empty when the column does not exist yet)."""
    try:
        rows = db.all("SELECT id, search_notes FROM public.businesses_business WHERE COALESCE(search_notes, '') <> ''")
    except Exception:
        return {}
    return {str(r["id"]): str(r["search_notes"]) for r in rows}
