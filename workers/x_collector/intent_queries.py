"""Deterministic buyer-intent query generation for X collection."""
from __future__ import annotations

import os
import re
from typing import Any

from need_engine.text import norm, tokens

INTENT_TEMPLATES = (
    "دنبال {t} می‌گردم", "دنبال {t} میگردم", "دنبال {t} هستم", "{t} می‌خوام",
    "{t} میخوام", "قصد خرید {t} دارم", "کسی {t} سراغ داره", "{t} پیشنهاد بدید",
    "{t} کجا بخرم", "برای خرید {t} راهنمایی", "{t} چی بخرم", "ممنون میشم {t} معرفی کنید",
)


def core_terms(product: Any) -> list[str]:
    path = str(getattr(product, "category_path", "") or "")
    candidates = [path.split("/")[-1].strip()] if path else []
    candidates += list(getattr(product, "category_keywords", []) or [])
    title = [token for token in tokens(getattr(product, "title", "")) if re.search(r"[آ-ی]", token)]
    candidates.extend(" ".join(title[:size]) for size in (2, 3, 4))
    card = getattr(product, "card", None)
    candidates += list(getattr(card, "aliases", []) or [])
    out, seen = [], set()
    for value in candidates:
        words = tokens(value)
        term = " ".join(words[:4])
        key = norm(term)
        if 2 <= len(words) <= 4 and any(re.search(r"[آ-ی]", word) for word in words) and key not in seen:
            seen.add(key)
            out.append(term)
    return out


def generate_queries(product: Any, mode: str | None = None) -> list[dict[str, str]]:
    mode = (mode or os.getenv("X_QUERY_MODE", "both")).strip().lower()
    if mode not in {"intent", "product", "both"}:
        mode = "both"
    suffix = os.getenv("X_QUERY_SUFFIX", "").strip()
    max_chars = int(os.getenv("X_QUERY_MAX_CHARS", "100"))
    max_count = int(os.getenv("X_QUERY_MAX_PER_PRODUCT", "12"))
    legacy = [str(getattr(product, key, "") or "").strip() for key in ("title", "category_path")]
    legacy += [str(x).strip() for x in (getattr(product, "category_keywords", []) or [])]
    if mode == "product":
        return [{"query": query, "kind": "product"} for query in legacy if len(query) >= 2]
    values: list[tuple[str, str]] = []
    if mode == "both":
        values.extend((q, "product") for q in legacy if len(q) >= 2)
        values.extend((template.format(t=term), "intent") for term in core_terms(product) for template in INTENT_TEMPLATES)
    elif mode == "intent":
        values.extend((template.format(t=term), "intent") for term in core_terms(product) for template in INTENT_TEMPLATES)
    out, seen = [], set()
    for query, kind in values:
        query = " ".join(query.split())
        query = f"{query} {suffix}".strip() if suffix else query
        key = norm(query)
        if not query or query.lstrip().startswith("-") or len(query) > max_chars or key in seen:
            continue
        seen.add(key)
        out.append({"query": query, "kind": kind})
        if len(out) >= max_count:
            break
    return out
