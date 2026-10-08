"""Load the seller's product catalog for the agent (JSON file or Django agent feed)."""

from __future__ import annotations

import json
import logging
import urllib.request
from pathlib import Path
from typing import Any

from analysis.config import AnalysisSettings, get_settings
from analysis.schemas import ProductProfile

log = logging.getLogger("analysis.catalog")


def _price(raw: Any) -> int | None:
    if isinstance(raw, dict):
        raw = raw.get("raw")
    try:
        return int(float(raw)) if raw not in (None, "") else None
    except (TypeError, ValueError):
        return None


def parse_catalog(data: Any, public_base_url: str) -> list[ProductProfile]:
    """Accepts either a list of products or the Django feed shape {"products": [...]}."""
    items = data.get("products", []) if isinstance(data, dict) else data
    products: list[ProductProfile] = []
    for item in items or []:
        try:
            pid = int(item["id"] if "id" in item else item["product_id"])
            url = (item.get("url") or "").strip() or f"{public_base_url}/p/{pid}/"
            products.append(
                ProductProfile(
                    product_id=pid,
                    name=item["name"],
                    description=item.get("description") or "",
                    target_customer=item.get("target_customer") or "",
                    price=_price(item.get("price")),
                    url=url,
                    attributes=item.get("attributes") or {},
                )
            )
        except (KeyError, TypeError, ValueError) as exc:
            log.warning("Skipping malformed catalog item %r: %s", item, exc)
    return products


def load_catalog(settings: AnalysisSettings | None = None, source: str | None = None) -> list[ProductProfile]:
    settings = settings or get_settings()
    source = source or settings.catalog_source
    if source.startswith(("http://", "https://")):
        headers = {"Accept": "application/json"}
        if settings.catalog_token:
            headers["X-Agent-Token"] = settings.catalog_token
        req = urllib.request.Request(source, headers=headers)
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    else:
        data = json.loads(Path(source).read_text(encoding="utf-8"))
    products = parse_catalog(data, settings.public_base_url)
    if not products:
        raise ValueError(f"Catalog at {source!r} contains no valid products")
    missing = [p.name for p in products if not p.target_customer.strip()]
    if missing:
        log.warning("Products without target_customer (matching will be weaker): %s", ", ".join(missing))
    return products
