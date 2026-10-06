from __future__ import annotations

import json
import os
import urllib.request
from typing import Any

from .core import Analysis, ProductProfile, SourceRecord


class GrokAnalyzer:
    def __init__(self) -> None:
        self.api_key = os.getenv("XAI_API_KEY", "")
        self.base_url = os.getenv("XAI_BASE_URL", "https://api.x.ai/v1").rstrip("/")
        self.model = os.getenv("XAI_MODEL", "grok-2-latest")
        self.timeout = int(os.getenv("XAI_TIMEOUT", "30"))
        self.batch_size = max(1, int(os.getenv("GROK_BATCH_SIZE", "25")))
        self.calls = 0

    def _request(self, system: str, payload: Any) -> list[dict[str, Any]]:
        if not self.api_key:
            raise RuntimeError("XAI_API_KEY is required for live AI analysis")
        body = json.dumps({
            "model": self.model,
            "temperature": 0,
            "response_format": {"type": "json_object"},
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": json.dumps(payload, ensure_ascii=False)}],
        }).encode()
        request = urllib.request.Request(self.base_url + "/chat/completions", data=body, headers={"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"})
        with urllib.request.urlopen(request, timeout=self.timeout) as response:
            result = json.loads(response.read())
        self.calls += 1
        content = json.loads(result["choices"][0]["message"]["content"])
        items = content.get("results", [])
        if not isinstance(items, list):
            raise ValueError("Grok returned invalid structured result")
        return items

    def classify_batch(self, records: list[SourceRecord]) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        for offset in range(0, len(records), self.batch_size):
            batch = records[offset:offset + self.batch_size]
            payload = [{"id": r.source_id, "text": r.text} for r in batch]
            try:
                items = self._request(
                    'Classify each post. Return JSON object {"results":[{"id":"...","intent":"buy|sell|question|advertisement|irrelevant|unknown","lead_direction":"buyer|seller|unknown","confidence":0.0,"reason":"short"}]}. Preserve each id.', payload)
            except (ValueError, KeyError, IndexError):
                if len(batch) == 1:
                    raise
                midpoint = len(batch) // 2
                out.extend(self.classify_batch(batch[:midpoint]))
                out.extend(self.classify_batch(batch[midpoint:]))
                continue
            out.extend(items)
        return out

    def extract_batch(self, pairs: list[tuple[SourceRecord, Analysis]], profile: ProductProfile) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        for offset in range(0, len(pairs), self.batch_size):
            batch = pairs[offset:offset + self.batch_size]
            payload = [{"id": r.source_id, "text": r.text} for r, _ in batch]
            schema = 'Return JSON object {"results":[{"id":"...","product_name":"","normalized_product":"","buyer_type":"person|business|retailer|wholesaler|unknown","transaction_type":"retail|wholesale|unknown","quantity_min":null,"quantity_max":null,"quantity_unit":null,"budget_min":null,"budget_max":null,"currency":null,"city":null,"province":null,"attributes":{},"confidence":0.0,"reason":""}]}. Numeric unknowns must be null, never guessed.'
            try:
                items = self._request(f"Extract buyer demand for target product {profile.product_name!r}. {schema}", payload)
            except (ValueError, KeyError, IndexError):
                if len(batch) == 1:
                    raise
                midpoint = len(batch) // 2
                out.extend(self.extract_batch(batch[:midpoint], profile))
                out.extend(self.extract_batch(batch[midpoint:], profile))
                continue
            out.extend(items)
        return out
