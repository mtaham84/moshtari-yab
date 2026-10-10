"""«ایجنت محصول شما را این‌طور فهمیده»: the card need_engine built for a product (read-only from its schema),
and the seller's edited version (``Product.agent_card_override``) which the engine then uses directly for matching."""
from __future__ import annotations

import json
import re

from django.db import connection

from apps.discovery.engine_bridge import _engine_table

FIELDS = ("what_it_is", "aliases", "problems_solved", "audience")


def engine_row(product) -> dict | None:
    table = _engine_table("products")
    if not table:
        return None
    with connection.cursor() as cur:
        cur.execute(f"SELECT payload, card FROM {table} WHERE product_id = %s", [str(product.pk)])
        row = cur.fetchone()
    if not row:
        return None
    payload, card = (json.loads(x) if isinstance(x, str) else x for x in row)
    return {"payload": payload or {}, "card": card or {}}


def card_state(product) -> dict:
    """{"state": seller | agent | refreshing | pending, "card": {...} | None}"""
    if product.agent_card_override:
        return {"state": "seller", "card": normalise(product.agent_card_override)}
    row = engine_row(product)
    if row is None:
        return {"state": "pending", "card": None}
    if row["payload"].get("card_override"):          # the seller just went back to the agent's version
        return {"state": "refreshing", "card": None}
    return {"state": "agent", "card": normalise(row["card"])}


def _list(v, n: int) -> list[str]:
    if isinstance(v, str):
        v = re.split(r"[\n،,]+", v)
    return [str(x).strip()[:120] for x in (v or []) if str(x).strip()][:n]


def normalise(c: dict) -> dict:
    c = c or {}
    return {"what_it_is": str(c.get("what_it_is") or "").strip()[:300],
            "aliases": _list(c.get("aliases"), 5),
            "problems_solved": _list(c.get("problems_solved"), 4),
            "audience": str(c.get("audience") or "").strip()[:200],
            "use": c.get("use"), "level": c.get("level")}


def from_form(post, base: dict | None) -> dict:
    """Seller's form → override card (use/level are kept from the agent's version)."""
    base = normalise(base or {})
    card = normalise({"what_it_is": post.get("what_it_is", ""), "aliases": post.get("aliases", ""),
                      "problems_solved": post.get("problems_solved", ""), "audience": post.get("audience", ""),
                      "use": base.get("use"), "level": base.get("level")})
    return card
