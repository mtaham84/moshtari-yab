from __future__ import annotations

import hashlib
import json
import logging
from typing import Any, Optional
from django.core.cache import cache

from .config import LLM_ANALYSIS_CACHE_TIMEOUT, PROMPT_VERSION

logger = logging.getLogger(__name__)

# Process-level memory cache for ultra-fast hits
_LOCAL_LLM_CACHE: dict[str, dict[str, Any]] = {}


def generate_llm_cache_key(
    normalized_message: str,
    business_id: int,
    taxonomy_version: int,
    catalog_version: int = 1,
    prompt_version: str = PROMPT_VERSION
) -> str:
    raw = (
        f"llm:b_{business_id}:t_{taxonomy_version}:c_{catalog_version}:"
        f"p_{prompt_version}:msg_{normalized_message.strip()}"
    )
    return "llm_cache:" + hashlib.sha256(raw.encode("utf-8")).hexdigest()


def get_cached_llm_analysis(cache_key: str) -> Optional[dict[str, Any]]:
    """Retrieves cached LLM analysis if available."""
    if cache_key in _LOCAL_LLM_CACHE:
        return _LOCAL_LLM_CACHE[cache_key]

    val = cache.get(cache_key)
    if val:
        _LOCAL_LLM_CACHE[cache_key] = val
        return val

    return None


def set_cached_llm_analysis(cache_key: str, analysis_data: dict[str, Any]) -> None:
    """Stores LLM analysis result in cache."""
    _LOCAL_LLM_CACHE[cache_key] = analysis_data
    cache.set(cache_key, analysis_data, timeout=LLM_ANALYSIS_CACHE_TIMEOUT)
