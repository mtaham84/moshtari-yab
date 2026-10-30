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
    import urllib.request
    from apps.core.models import LLMUsageLog
    from apps.billing.models import AIModel

    # Candidate active models ordered by priority (Luna priority 1 has highest priority)
    models = list(AIModel.objects.filter(is_active=True, provider__is_active=True).select_related("provider").order_by("priority"))

    candidates = []
    for m in models:
        if "embed" in m.name.lower():
            continue
        if m.provider and m.provider.base_url and m.provider.api_key:
            candidates.append((m.name, m.provider.base_url.rstrip("/"), m.provider.api_key))

    # Fallback to instagram_engine helper if candidates table is empty
    if not candidates:
        try:
            from instagram_engine.analyzer import _call_llm_with_usage
            payload = {"messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
                       "temperature": 0.2, "max_tokens": max_tokens}
            res = _call_llm_with_usage(payload, timeout=timeout)
            content = res.get("content")
            if content:
                return _extract_json(content)
        except Exception:
            pass
        raise LLMError("هیچ ارائه‌دهنده یا مدل هوش مصنوعی فعالی یافت نشد.")

    last = "no response"
    for model_name, base_url, api_key in candidates:
        url = f"{base_url}/chat/completions"
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }
        post_model = model_name
        if "generativelanguage.googleapis.com" in base_url and post_model == "gpt-6-luna":
            post_model = "gemini-3.1-flash-lite"
        body = json.dumps({
            "model": post_model,
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
            "temperature": 0.2,
            "max_tokens": max_tokens
        }).encode("utf-8")

        for attempt in range(max(1, attempts)):
            try:
                req = urllib.request.Request(url, data=body, headers=headers)
                with urllib.request.urlopen(req, timeout=timeout) as resp:
                    data = json.loads(resp.read().decode("utf-8"))
                ch = (data.get("choices") or [{}])[0]
                content = (ch.get("message") or {}).get("content") or ""
                u = data.get("usage") or {}
                tokens_in = int(u.get("prompt_tokens") or 0)
                tokens_out = int(u.get("completion_tokens") or 0)
                try:
                    LLMUsageLog.objects.create(
                        model=model_name[:80],
                        purpose=purpose,
                        business_id=business_id,
                        tokens_in=tokens_in,
                        tokens_out=tokens_out,
                        cost_usd=0,
                        ok=bool(content)
                    )
                except Exception:
                    pass
                if not content:
                    last = "empty response"
                    if "error" in data:
                        err_msg = data.get("error", {}).get("message") or str(data.get("error"))
                        last = f"API error: {err_msg}"
                        # If error mentions quota, credit, balance, billing, break to failover immediately
                        if any(k in err_msg.lower() for k in ("quota", "credit", "balance", "billing", "payment", "unauthorized")):
                            log.warning("Candidate model %s quota/credit exhausted: %s. Shifting to next model.", model_name, err_msg)
                            break
                    continue
                return _extract_json(content)
            except Exception as exc:
                last = str(exc)
                log.warning("LLM model %s failed on attempt %d: %s", model_name, attempt + 1, exc)
                exc_str = str(exc).lower()
                # Failover immediately on connection, network, or out-of-credit / quota errors
                if any(k in exc_str for k in (
                    "urlopen", "connection", "tlsv1", "not found", "404", "ssl", "refused", "timeout",
                    "402", "429", "401", "403", "quota", "credit", "balance", "payment", "billing", "unauthorized"
                )):
                    log.info("Model %s hit unrecoverable/quota error (%s); shifting to next model immediately.", model_name, last)
                    break
        log.warning("Candidate model %s failed (%s), failing over to next model...", model_name, last)

    raise LLMError(last)
