"""Embedding backends (remote APIs only; ``hash`` is a deterministic offline stand-in for tests)."""
from __future__ import annotations

import hashlib
import json
import logging
import urllib.error
import urllib.request

import numpy as np

from need_engine.config import EngineConfig
from need_engine.llm import LLMClient, LLMError
from need_engine.text import norm, tokens

log = logging.getLogger("need_engine.embeddings")


class Embedder:
    def __init__(self, cfg: EngineConfig, llm: LLMClient):
        self.cfg, self.llm = cfg, llm
        self.backend = cfg.embed_backend
        self.dim: int | None = None

    def encode(self, texts: list[str], stage: str = "embed") -> np.ndarray:
        texts = [norm(t) for t in texts]
        if not texts:
            return np.zeros((0, self.dim or 8), dtype=np.float32)
        if self.backend == "hash":
            v = np.stack([self._hash_vec(t) for t in texts])
        elif self.backend == "gemini":
            v = self._openai_compatible(texts, stage)
        elif self.backend == "cloudflare":
            v = self._cloudflare(texts, stage)
        else:
            raise LLMError(f"unknown embed backend: {self.backend}")
        v = np.asarray(v, dtype=np.float32)
        v /= np.linalg.norm(v, axis=1, keepdims=True) + 1e-9
        self.dim = v.shape[1]
        return v

    def _post(self, url: str, body: dict, headers: dict) -> dict:
        req = urllib.request.Request(url, data=json.dumps(body).encode(), headers={**headers, "Content-Type": "application/json"})
        for attempt in range(5):
            try:
                with urllib.request.urlopen(req, timeout=120) as r:
                    return json.loads(r.read().decode())
            except urllib.error.HTTPError as e:
                text = e.read().decode("utf-8", "replace")
                if e.code == 429:
                    self.llm.rl.on_429(self.cfg.embed_model, text)
                    continue
                if e.code >= 500:
                    import time

                    time.sleep(2 ** attempt)
                    continue
                raise LLMError(f"embedding HTTP {e.code}: {text[:300]}")
        raise LLMError("embedding request failed repeatedly")

    def _openai_compatible(self, texts: list[str], stage: str) -> np.ndarray:
        out: list[list[float]] = []
        for i in range(0, len(texts), 100):
            chunk = texts[i:i + 100]
            self.llm.rl.acquire(self.cfg.embed_model, 0)
            base_url, api_key = self.llm.registry.endpoint(self.cfg.embed_model)
            j = self._post(base_url.rstrip("/") + "/embeddings", {"model": self.cfg.embed_model, "input": chunk},
                           {"Authorization": f"Bearer {api_key}"})
            out += [d["embedding"] for d in j["data"]]
            pt = int((j.get("usage") or {}).get("prompt_tokens") or 0)
            entry = self.llm.registry.entry(self.cfg.embed_model)
            usd = pt / 1e6 * (entry.price_in if entry else self.cfg.embed_price_per_m)
            self.llm.store.add_cost(stage, self.cfg.embed_model, pt, 0, False, usd, self.llm.toman(usd))
        return np.array(out)

    def _cloudflare(self, texts: list[str], stage: str) -> np.ndarray:
        if not (self.cfg.cf_account_id and self.cfg.cf_api_token):
            raise LLMError("NE_CF_ACCOUNT_ID / NE_CF_API_TOKEN are not set")
        url = f"https://api.cloudflare.com/client/v4/accounts/{self.cfg.cf_account_id}/ai/run/{self.cfg.cf_model}"
        out: list[list[float]] = []
        for i in range(0, len(texts), 50):
            j = self._post(url, {"text": texts[i:i + 50]}, {"Authorization": f"Bearer {self.cfg.cf_api_token}"})
            res = j.get("result") or {}
            out += res.get("data") or []
            self.llm.store.add_cost(stage, self.cfg.cf_model, 0, 0, False, 0.0, 0.0)
        return np.array(out)

    @staticmethod
    def _hash_vec(t: str, d: int = 256) -> np.ndarray:
        v = np.zeros(d, dtype=np.float32)
        toks = tokens(t)
        grams = toks + [a + "_" + b for a, b in zip(toks, toks[1:])]
        for g in grams:
            h = int(hashlib.md5(g.encode()).hexdigest(), 16)
            v[h % d] += 1.0 if (h >> 8) % 2 else -1.0
        return v if v.any() else np.ones(d, dtype=np.float32) * 1e-3
