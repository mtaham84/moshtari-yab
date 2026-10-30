"""One place for the web app's direct model calls (taxonomy building, product organising ...).

Reuses the Instagram engine's OpenAI-compatible caller (model/provider come from the admin-managed AIModel table, Luna
by default), parses JSON robustly and writes every call to ``LLMUsageLog`` so it appears in the model usage lists.
"""
from __future__ import annotations

import json
import logging
import re
from typing import Any

log = logging.getLogger("core.llm")


class LLMError(RuntimeError):
    pass


def _extract_json(text: str) -> Any:
    text = (text or "").strip()
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=re.S)
    try:
        return json.loads(text)
    except ValueError:
        m = re.search(r"\{.*\}", text, re.S)
        if m:
            return json.loads(m.group(0))
        raise


def chat_json(system: str, user: str, purpose: str, business_id: int | None = None, max_tokens: int = 3500,
              timeout: float = 90.0, attempts: int = 2) -> Any:
    """Runs one JSON-returning chat completion; raises LLMError when no usable JSON comes back."""
    from apps.core.models import LLMUsageLog

    try:
        from instagram_engine.analyzer import _call_llm_with_usage
    except Exception as exc:   # pragma: no cover - engine package missing
        raise LLMError(f"LLM client unavailable: {exc}") from exc

    last = "no response"
    for _ in range(max(1, attempts)):
        payload = {"messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
                   "temperature": 0.2, "max_tokens": max_tokens}
        res = _call_llm_with_usage(payload, timeout=timeout)
        content = res.get("content")
        try:
            LLMUsageLog.objects.create(model=str(res.get("model") or payload.get("model") or "unknown")[:80], purpose=purpose,
                                       business_id=business_id, tokens_in=int(res.get("tokens_in") or 0),
                                       tokens_out=int(res.get("tokens_out") or 0), cost_usd=res.get("cost_usd") or 0,
                                       ok=bool(content))
        except Exception:   # the ledger must never break the feature
            log.debug("could not write LLMUsageLog", exc_info=True)
        if not content:
            last = "empty response"
            continue
        try:
            return _extract_json(content)
        except ValueError as exc:
            last = f"invalid JSON ({exc})"
    raise LLMError(last)
