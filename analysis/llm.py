"""OpenAI-compatible chat client with JSON mode, retries, call budget and cost tracking.

Works with Groq, xAI, OpenAI or any provider exposing ``POST {base_url}/chat/completions``.
Token counts always come from the provider's ``usage`` field (never estimated).
"""

from __future__ import annotations

import json
import logging
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Any

from analysis.config import AnalysisSettings
from analysis.schemas import Stage, StageCost

log = logging.getLogger("analysis.llm")


class LLMError(RuntimeError):
    """Transport/provider failure after all retries (message should be retried later)."""


class LLMBudgetExceeded(RuntimeError):
    """MAX_LLM_CALLS_PER_RUN reached (remaining messages stay in the queue)."""


class InvalidLLMOutput(ValueError):
    """The model answered, but the content does not match the expected structure."""


@dataclass
class LLMResponse:
    data: dict[str, Any] | None       # parsed JSON object (None if the content was not JSON)
    raw_content: str
    model: str
    prompt_tokens: int
    completion_tokens: int
    latency_ms: int


@dataclass
class CallRecord:
    stage: str
    model: str
    prompt_tokens: int
    completion_tokens: int
    cost_usd: float
    cost_toman: float
    latency_ms: int
    ok: bool


class CostCalculator:
    def __init__(self, settings: AnalysisSettings) -> None:
        self.settings = settings

    def cost(self, model: str, prompt_tokens: int, completion_tokens: int) -> tuple[float, float]:
        inp, out = self.settings.price_for(model)
        usd = (prompt_tokens * inp + completion_tokens * out) / 1_000_000
        return usd, usd * self.settings.usd_to_toman

    def stage_cost(self, stage: Stage, resp: LLMResponse, shared: bool = False) -> StageCost:
        usd, toman = self.cost(resp.model, resp.prompt_tokens, resp.completion_tokens)
        return StageCost(
            stage=stage, model=resp.model, prompt_tokens=resp.prompt_tokens,
            completion_tokens=resp.completion_tokens, cost_usd=usd, cost_toman=toman,
            latency_ms=resp.latency_ms, shared_call=shared,
        )


@dataclass
class UsageLedger:
    """Every LLM call made during a run (the ground truth for total spend)."""

    calls: list[CallRecord] = field(default_factory=list)

    def summary(self) -> dict[str, Any]:
        by_stage: dict[str, dict[str, float]] = {}
        for c in self.calls:
            s = by_stage.setdefault(c.stage, {"calls": 0, "prompt_tokens": 0, "completion_tokens": 0, "cost_usd": 0.0, "cost_toman": 0.0})
            s["calls"] += 1
            s["prompt_tokens"] += c.prompt_tokens
            s["completion_tokens"] += c.completion_tokens
            s["cost_usd"] += c.cost_usd
            s["cost_toman"] += c.cost_toman
        return {
            "llm_calls": len(self.calls),
            "invalid_outputs": sum(1 for c in self.calls if not c.ok),
            "prompt_tokens": sum(c.prompt_tokens for c in self.calls),
            "completion_tokens": sum(c.completion_tokens for c in self.calls),
            "cost_usd": round(sum(c.cost_usd for c in self.calls), 6),
            "cost_toman": round(sum(c.cost_toman for c in self.calls), 2),
            "by_stage": {k: {**v, "cost_usd": round(v["cost_usd"], 6), "cost_toman": round(v["cost_toman"], 2)} for k, v in by_stage.items()},
        }


class BaseLLM:
    """Common budget + ledger handling. Subclasses implement ``_complete``."""

    def __init__(self, settings: AnalysisSettings, max_calls: int | None = None) -> None:
        self.settings = settings
        self.costs = CostCalculator(settings)
        self.max_calls = settings.max_llm_calls_per_run if max_calls is None else max_calls
        self.ledger = UsageLedger()

    @property
    def calls_made(self) -> int:
        return len(self.ledger.calls)

    def reset_run(self) -> None:
        self.ledger = UsageLedger()

    def complete_json(self, stage: Stage, model: str, messages: list[dict[str, str]], max_tokens: int = 800) -> LLMResponse:
        if self.max_calls and self.calls_made >= self.max_calls:
            raise LLMBudgetExceeded(f"MAX_LLM_CALLS_PER_RUN={self.max_calls} reached")
        resp = self._complete(model, messages, max_tokens)
        usd, toman = self.costs.cost(resp.model, resp.prompt_tokens, resp.completion_tokens)
        self.ledger.calls.append(CallRecord(stage, resp.model, resp.prompt_tokens, resp.completion_tokens, usd, toman, resp.latency_ms, resp.data is not None))
        return resp

    def mark_last_invalid(self) -> None:
        if self.ledger.calls:
            self.ledger.calls[-1].ok = False

    def _complete(self, model: str, messages: list[dict[str, str]], max_tokens: int) -> LLMResponse:  # pragma: no cover
        raise NotImplementedError


def parse_json_object(content: str) -> dict[str, Any] | None:
    """Parse a JSON object, tolerating ```json fences that some models add."""
    text = (content or "").strip()
    if text.startswith("```"):
        text = text.strip("`")
        text = text[4:] if text.lower().startswith("json") else text
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end == -1:
        return None
    try:
        value = json.loads(text[start : end + 1])
    except json.JSONDecodeError:
        return None
    return value if isinstance(value, dict) else None


class OpenAICompatibleLLM(BaseLLM):
    RETRY_STATUS = {408, 409, 425, 429, 500, 502, 503, 504}

    def __init__(self, settings: AnalysisSettings, max_calls: int | None = None) -> None:
        super().__init__(settings, max_calls)
        if not settings.llm_api_key:
            raise LLMError("LLM_API_KEY is not set (use --mock-llm for an offline demo)")

    def _post(self, body: dict[str, Any]) -> dict[str, Any]:
        req = urllib.request.Request(
            f"{self.settings.llm_base_url}/chat/completions",
            data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
            headers={"Authorization": f"Bearer {self.settings.llm_api_key}", "Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=self.settings.llm_timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))

    def _complete(self, model: str, messages: list[dict[str, str]], max_tokens: int) -> LLMResponse:
        body = {
            "model": model,
            "messages": messages,
            "temperature": 0,
            "max_tokens": max_tokens,
            "response_format": {"type": "json_object"},
        }
        last_error: Exception | None = None
        for attempt in range(self.settings.llm_max_retries + 1):
            started = time.monotonic()
            try:
                result = self._post(body)
                latency = int((time.monotonic() - started) * 1000)
                content = result["choices"][0]["message"].get("content") or ""
                usage = result.get("usage") or {}
                return LLMResponse(
                    data=parse_json_object(content), raw_content=content, model=result.get("model") or model,
                    prompt_tokens=int(usage.get("prompt_tokens") or 0),
                    completion_tokens=int(usage.get("completion_tokens") or 0), latency_ms=latency,
                )
            except urllib.error.HTTPError as exc:
                detail = exc.read().decode("utf-8", "ignore")[:300] if hasattr(exc, "read") else ""
                last_error = LLMError(f"HTTP {exc.code}: {detail}")
                if exc.code == 400 and "json" in detail.lower() and "response_format" in body:
                    body.pop("response_format")  # provider/model without JSON mode: rely on the prompt
                    continue
                if exc.code not in self.RETRY_STATUS:
                    raise last_error from exc
                retry_after = exc.headers.get("retry-after") if exc.headers else None
                wait = float(retry_after) if retry_after and retry_after.replace(".", "", 1).isdigit() else 2 ** attempt
            except (urllib.error.URLError, TimeoutError, ConnectionError) as exc:
                last_error = LLMError(f"network error: {exc}")
                wait = 2 ** attempt
            except (KeyError, IndexError, json.JSONDecodeError) as exc:
                last_error = LLMError(f"malformed provider response: {exc}")
                wait = 2 ** attempt
            if attempt < self.settings.llm_max_retries:
                log.warning("LLM call failed (%s); retrying in %.1fs", last_error, wait)
                time.sleep(min(wait, 30))
        raise last_error or LLMError("unknown LLM failure")


def build_llm(settings: AnalysisSettings, mock: bool = False, max_calls: int | None = None) -> BaseLLM:
    if mock:
        from analysis.mock_llm import HeuristicMockLLM

        return HeuristicMockLLM(settings, max_calls=max_calls)
    return OpenAICompatibleLLM(settings, max_calls=max_calls)
