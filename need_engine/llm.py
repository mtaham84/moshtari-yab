"""OpenAI-compatible JSON client: rate limiting, retries, response cache and cost ledger (stdlib HTTP only)."""
from __future__ import annotations

import hashlib
import json
import logging
import re
import socket
import threading
import time
import urllib.error
import urllib.request
from collections import defaultdict
from datetime import datetime, timezone
from typing import Any, Callable

from need_engine.config import EngineConfig
from need_engine.store import Store

log = logging.getLogger("need_engine.llm")


class LLMError(RuntimeError):
    pass


class QuotaExhausted(LLMError):
    pass


def parse_json(text: str | None) -> dict | None:
    if not text:
        return None
    t = text.strip()
    t = re.sub(r"^```(?:json)?\s*|\s*```$", "", t)
    try:
        v = json.loads(t)
        return v if isinstance(v, dict) else {"items": v}
    except Exception:
        m = re.search(r"\{.*\}", t, re.S)
        if m:
            try:
                return json.loads(m.group(0))
            except Exception:
                return None
    return None


def _pacific_day() -> str:
    try:
        from zoneinfo import ZoneInfo

        return datetime.now(ZoneInfo("America/Los_Angeles")).strftime("%Y-%m-%d")
    except Exception:  # pragma: no cover
        return datetime.now(timezone.utc).strftime("%Y-%m-%d")


class RateLimiter:
    """Sliding-window RPM/TPM + persisted daily RPD per model (Gemini free tier resets at Pacific midnight)."""

    def __init__(self, cfg: EngineConfig, store: Store):
        self.cfg, self.store = cfg, store
        self.lock = threading.Lock()
        self.win: dict[str, list[tuple[float, int]]] = defaultdict(list)
        self.cool: dict[str, float] = defaultdict(float)
        self.scale: dict[str, float] = defaultdict(lambda: 1.0)
        self.n429 = 0
        self.waited = 0.0

    def _limits(self, model: str) -> tuple[int, float, int | None]:
        lim = dict(self.cfg.rate_limits.get(model) or self.cfg.default_rate_limit)
        f = self.cfg.rate_safety * self.scale[model]
        rpm = max(1, int((lim.get("rpm") or 10**9) * f))
        tpm = (lim.get("tpm") or 10**12) * self.cfg.rate_safety
        return rpm, tpm, lim.get("rpd")

    def acquire(self, model: str, est_tokens: int) -> None:
        last_note = 0.0
        while True:
            with self.lock:
                rpm, tpm, rpd = self._limits(model)
                now, day = time.time(), _pacific_day()
                if rpd is not None and self.store.quota_used(day, model) >= rpd * self.cfg.rate_safety:
                    raise QuotaExhausted(f"daily quota of {model} reached ({self.store.quota_used(day, model)} requests today)")
                w = self.win[model] = [(t, k) for t, k in self.win[model] if now - t < 60]
                wait = max(0.0, self.cool[model] - now)
                if not wait and len(w) >= rpm:
                    wait = 60 - (now - w[0][0]) + 0.05
                if not wait and w and sum(k for _, k in w) + est_tokens > tpm:
                    wait = 60 - (now - w[0][0]) + 0.05
                if not wait:
                    w.append((now, est_tokens))
                    self.store.quota_add(day, model)
                    return
            if wait > 3 and time.time() - last_note > 20:
                log.info("rate limit %s: waiting %.0fs", model, wait)
                last_note = time.time()
            self.waited += min(wait, 5)
            time.sleep(min(wait, 5))

    def on_429(self, model: str, body: str) -> None:
        self.n429 += 1
        if re.search(r"PerDay|per day|daily", body, re.I) and not re.search(r"PerMinute", body):
            lim = self.cfg.rate_limits.get(model) or {}
            self.store.quota_add(_pacific_day(), model, set_to=int(lim.get("rpd") or 10**6))
            raise QuotaExhausted(f"provider says daily quota of {model} is exhausted: {body[:300]}")
        m = re.search(r'"retryDelay"\s*:\s*"(\d+(?:\.\d+)?)s"', body) or re.search(r"retry in (\d+(?:\.\d+)?)\s*s", body, re.I)
        delay = float(m.group(1)) + 1 if m else 30.0
        with self.lock:
            self.cool[model] = max(self.cool[model], time.time() + delay)
            self.scale[model] = max(0.3, self.scale[model] * 0.8)
        log.warning("429 from %s: waiting %.0fs, slowing down to %.0f%%", model, delay, self.scale[model] * 100)

    def on_success(self, model: str) -> None:
        with self.lock:
            self.scale[model] = min(1.0, self.scale[model] * 1.02)


def _est_tokens(*texts: str) -> int:
    return int(sum(len(t) for t in texts) / 2.5) + 50


class LLMClient:
    """``complete_json(stage, model, system, user)`` → (dict | None, usage). ``mock`` replaces the network (tests/demo)."""

    def __init__(self, cfg: EngineConfig, store: Store, mock: Callable[[str, str, str], dict] | None = None):
        self.cfg, self.store, self.mock = cfg, store, mock
        self.rl = RateLimiter(cfg, store)

    # cost helpers
    def price(self, model: str, pt: int, ct: int) -> tuple[float, float]:
        pin, pout = (self.cfg.prices.get(model) or (0.0, 0.0))
        usd = pt / 1e6 * pin + ct / 1e6 * pout
        return usd, usd * self.cfg.usd_to_toman

    def _record(self, stage: str, model: str, pt: int, ct: int, cached: bool, ref: str,
                businesses: list[str | None] | None = None) -> float:
        usd, toman = self.price(model, pt, ct)
        self.store.add_cost(stage, model, pt, ct, cached, usd, toman, ref, businesses)
        return toman

    def _post(self, path: str, body: dict, timeout: float) -> tuple[int, str]:
        req = urllib.request.Request(self.cfg.llm_base_url.rstrip("/") + path, data=json.dumps(body).encode(),
                                     headers={"Authorization": f"Bearer {self.cfg.llm_api_key}", "Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return r.status, r.read().decode("utf-8", "replace")
        except urllib.error.HTTPError as e:
            return e.code, e.read().decode("utf-8", "replace")

    def complete_json(self, stage: str, model: str, system: str, user: str, max_tokens: int = 4096,
                      temperature: float = 0.0, parse_retries: int = 1, ref: str = "",
                      businesses: list[str | None] | None = None) -> tuple[dict | None, dict]:
        """Returns (data, usage) where usage = {"toman", "cached", "pt", "ct"}. Equivalent cost is recorded even on cache hits.
        ``businesses``: sellers who pay for the call, split equally (None/empty = platform cost)."""
        key = hashlib.sha256(json.dumps([model, system, user, max_tokens, temperature, self.cfg.reasoning_effort],
                                        ensure_ascii=False).encode()).hexdigest()
        hit = self.store.cache_get(key)
        if hit is not None:
            toman = self._record(stage, model, hit["pt"], hit["ct"], True, ref, businesses)
            return parse_json(hit["content"]), {"toman": toman, "cached": True, "pt": hit["pt"], "ct": hit["ct"]}
        if self.mock is not None:
            content = json.dumps(self.mock(stage, system, user), ensure_ascii=False)
            pt, ct = len(system + user) // 3, len(content) // 3
            self.store.cache_put(key, {"content": content, "pt": pt, "ct": ct})
            toman = self._record(stage, model, pt, ct, False, ref, businesses)
            return parse_json(content), {"toman": toman, "cached": False, "pt": pt, "ct": ct}
        if not self.cfg.llm_api_key:
            raise LLMError("NE_LLM_API_KEY / GEMINI_API_KEY is not set")

        body: dict[str, Any] = {"model": model, "temperature": temperature, "max_tokens": max_tokens,
                                "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
                                "response_format": {"type": "json_object"}}
        if self.cfg.reasoning_effort:
            body["reasoning_effort"] = self.cfg.reasoning_effort
        est = _est_tokens(system, user)
        last = None
        spent = 0.0
        for attempt in range(self.cfg.llm_max_retries + 1):
            self.rl.acquire(model, est)
            t0 = time.time()
            try:
                status, text = self._post("/chat/completions", body, self.cfg.llm_timeout)
            except (urllib.error.URLError, socket.timeout, TimeoutError, ConnectionError) as e:
                pause = min(30, 2 ** attempt)
                last = f"network: {e}"
                log.warning("%s: network/timeout after %.0fs (attempt %d) – retry in %ss", stage, time.time() - t0, attempt + 1, pause)
                time.sleep(pause)
                continue
            if status == 429:
                self.rl.on_429(model, text)
                last = f"HTTP 429: {text[:200]}"
                continue
            if status in (500, 502, 503, 504):
                pause = min(60, 2 ** attempt * 2)
                last = f"HTTP {status}: {text[:200]}"
                log.warning("%s: provider HTTP %s – retry in %ss", stage, status, pause)
                time.sleep(pause)
                continue
            if status >= 400:
                if status == 400 and "response_format" in body and "json" in text.lower():
                    body.pop("response_format")
                    continue
                raise LLMError(f"HTTP {status}: {text[:400]}")
            self.rl.on_success(model)
            j = json.loads(text)
            ch = (j.get("choices") or [{}])[0]
            content = (ch.get("message") or {}).get("content") or ""
            u = j.get("usage") or {}
            pt, ct, tot = int(u.get("prompt_tokens") or 0), int(u.get("completion_tokens") or 0), int(u.get("total_tokens") or 0)
            if tot > pt + ct:
                ct = tot - pt  # thinking tokens are billed as output
            spent += self._record(stage, model, pt, ct, False, ref, businesses)
            data = parse_json(content)
            if data is not None:
                self.store.cache_put(key, {"content": content, "pt": pt, "ct": ct})
                return data, {"toman": spent, "cached": False, "pt": pt, "ct": ct}
            if ch.get("finish_reason") == "length" and body["max_tokens"] < 32000:
                body["max_tokens"] = min(32000, body["max_tokens"] * 2)
                log.warning("%s: output truncated, retrying with %d tokens", stage, body["max_tokens"])
                continue
            if parse_retries > 0:
                parse_retries -= 1
                log.warning("%s: invalid JSON (finish=%s, %d chars) – retrying once", stage, ch.get("finish_reason"), len(content))
                continue
            return None, {"toman": spent, "cached": False, "pt": pt, "ct": ct}
        raise LLMError(last or "unknown LLM error")
