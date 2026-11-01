"""
Shared form helpers for list splitting, normalization, and bounded integer parsing (P08.04 / R40).

Deduplicates cloned form list and integer parsing logic across apps/products and apps/discovery.
"""

from __future__ import annotations

import re
from typing import Any, List, Optional, Set


def parse_form_list(
    value: Any,
    *,
    hashtags: bool = False,
    max_items: Optional[int] = None,
    max_item_length: int = 120,
) -> List[str]:
    """
    Parses newline- and comma-separated form text inputs into a deduplicated list of clean strings.
    Handles Persian commas (،), semicolons (;), standard commas (,), and newlines.
    Preserves original insertion order while deduplicating case-insensitively.
    """
    if not value:
        return []

    raw = str(value)
    # Normalize separators: Persian comma, semicolon, carriage returns
    normalized = raw.replace("\r", "\n").replace("،", ",").replace(";", ",")
    lines = normalized.split("\n")

    parts: List[str] = []
    for line in lines:
        if "," in line:
            parts.extend(line.split(","))
        else:
            parts.append(line)

    output: List[str] = []
    seen: Set[str] = set()

    for part in parts:
        item = part.strip()
        if hashtags and item.startswith("#"):
            item = item.lstrip("#").strip()
        if not item:
            continue

        key = item.casefold()
        if key not in seen:
            seen.add(key)
            output.append(item[:max_item_length])
            if max_items is not None and len(output) >= max_items:
                break

    return output


def parse_bounded_int(
    value: Any,
    default: int,
    minimum: int,
    maximum: int,
) -> int:
    """
    Parses an integer form input safely with bounding constraints.
    Returns default if value is None, empty, or unparseable.
    Clamps between minimum and maximum.
    """
    if value is None:
        return default
    raw_str = str(value).strip()
    if not raw_str:
        return default

    try:
        n = int(raw_str)
        return max(minimum, min(n, maximum))
    except (ValueError, TypeError):
        return default
