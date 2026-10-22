"""Build explicit hashtag seeds from seller category and product settings.
The panel seed command calls this before writing the collector contract.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, TypedDict


class HashtagSeed(TypedDict):
    tag: str
    limit: int
    window_days: int
    business_id: int
    category_id: int
    category: str
    category_path: str
    product_ids: list[int]
    products: list[str]
    terms: list[str]


@dataclass(frozen=True)
class HashtagTarget:
    business_id: int
    category_id: int
    category_name: str
    category_path: str
    product_ids: tuple[int, ...]
    product_names: tuple[str, ...]
    terms: tuple[str, ...]
    hashtags: tuple[str, ...]
    max_posts: int
    window_days: int


def build_hashtag_seeds(targets: Iterable[HashtagTarget]) -> list[HashtagSeed]:
    grouped: dict[tuple[int, int, str], HashtagSeed] = {}
    for target in targets:
        for tag in target.hashtags:
            normalized = tag.strip().lstrip("#")
            if not normalized:
                continue
            key = (target.business_id, target.category_id, normalized.casefold())
            seed = grouped.setdefault(key, {
                "tag": normalized, "limit": target.max_posts,
                "window_days": target.window_days, "business_id": target.business_id,
                "category_id": target.category_id, "category": target.category_name,
                "category_path": target.category_path, "product_ids": [],
                "products": [], "terms": [],
            })
            seed["limit"] = min(seed["limit"], target.max_posts)
            seed["window_days"] = min(seed["window_days"], target.window_days)
            seed["product_ids"] = list(dict.fromkeys([*seed["product_ids"], *target.product_ids]))
            seed["products"] = list(dict.fromkeys([*seed["products"], *target.product_names]))
            seed["terms"] = list(dict.fromkeys([*seed["terms"], *target.terms]))
    return list(grouped.values())
