from __future__ import annotations

import json
import os
import math
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
        self.max_calls = max(0, int(os.getenv("MAX_GROK_REQUESTS", "40")))
        self.input_price = float(os.getenv("XAI_INPUT_USD_PER_MILLION_TOKENS", "0"))
        self.output_price = float(os.getenv("XAI_OUTPUT_USD_PER_MILLION_TOKENS", "0"))
        self.calls = 0
        self.prompt_tokens = 0
        self.completion_tokens = 0
        self.estimated_cost_usd = 0.0

    def _request(self, system: str, payload: Any) -> list[dict[str, Any]]:
        if not self.api_key:
            raise RuntimeError("XAI_API_KEY is required for live AI analysis")
        if self.calls >= self.max_calls:
            raise RuntimeError("MAX_GROK_REQUESTS budget exhausted")
        body = json.dumps({
            "model": self.model,
            "temperature": 0,
            "response_format": {"type": "json_object"},
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": json.dumps(payload, ensure_ascii=False)}],
        }).encode()
        request = urllib.request.Request(self.base_url + "/chat/completions", data=body, headers={"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"})
        self.calls += 1
        with urllib.request.urlopen(request, timeout=self.timeout) as response:
            result = json.loads(response.read())
        usage = result.get("usage", {})
        self.prompt_tokens += int(usage.get("prompt_tokens", 0) or 0)
        self.completion_tokens += int(usage.get("completion_tokens", 0) or 0)
        self.estimated_cost_usd = (self.prompt_tokens * self.input_price + self.completion_tokens * self.output_price) / 1_000_000
        content = json.loads(result["choices"][0]["message"]["content"])
        items = content.get("results", [])
        if not isinstance(items, list) or any(not isinstance(item, dict) for item in items):
            raise ValueError("Grok returned invalid structured result list")
        return items

    @staticmethod
    def _validate_items(items: list[dict[str, Any]], ids: list[str], required: tuple[str, ...], enums: dict[str, set[str]]) -> list[dict[str, Any]]:
        by_id: dict[str, dict[str, Any]] = {}
        for item in items:
            record_id = str(item.get("id", ""))
            if record_id not in ids or record_id in by_id:
                raise ValueError("Grok response contains unknown or duplicate record ids")
            if any(key not in item for key in required):
                raise ValueError("Grok response is missing required fields")
            if "reason" in item and not isinstance(item["reason"], str):
                raise ValueError("Grok returned invalid reason")
            for key, values in enums.items():
                if item.get(key) not in values:
                    raise ValueError(f"Grok returned invalid {key}")
            confidence = item.get("confidence")
            if not isinstance(confidence, (int, float)) or not math.isfinite(confidence) or not 0 <= confidence <= 1:
                raise ValueError("Grok returned invalid confidence")
            by_id[record_id] = item
        if set(by_id) != set(ids):
            raise ValueError("Grok response omitted records")
        return [by_id[record_id] for record_id in ids]

    def classify_batch(self, records: list[SourceRecord]) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        for offset in range(0, len(records), self.batch_size):
            batch = records[offset:offset + self.batch_size]
            payload = [{"id": r.source_id, "text": r.text} for r in batch]
            try:
                items = self._request(
                    'Classify each post. Return JSON object {"results":[{"id":"...","intent":"buy|sell|question|advertisement|irrelevant|unknown","lead_direction":"buyer|seller|unknown","confidence":0.0,"reason":"short"}]}. Preserve each id.', payload)
                items = self._validate_items(items, [r.source_id for r in batch], ("intent", "lead_direction", "confidence"), {"intent": {"buy", "sell", "question", "advertisement", "irrelevant", "unknown"}, "lead_direction": {"buyer", "seller", "unknown"}})
                for item in items:
                    if not isinstance(item.get("reason", ""), str):
                        raise ValueError("Grok returned invalid reason")
            except (ValueError, KeyError, IndexError):
                if len(batch) == 1:
                    out.append({"id": batch[0].source_id, "intent": "unknown", "lead_direction": "unknown", "confidence": 0.0, "reason": "Invalid model output; retained for conservative local handling"})
                    continue
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
                items = self._validate_items(items, [r.source_id for r, _ in batch], ("confidence", "buyer_type", "transaction_type", "attributes"), {"buyer_type": {"person", "business", "retailer", "wholesaler", "unknown"}, "transaction_type": {"retail", "wholesale", "unknown"}})
                for item in items:
                    for key in ("quantity_min", "quantity_max", "budget_min", "budget_max"):
                        if item.get(key) is not None and (not isinstance(item[key], (int, float)) or isinstance(item[key], bool) or not math.isfinite(item[key]) or item[key] < 0):
                            raise ValueError(f"Grok returned invalid {key}")
                    if not isinstance(item["attributes"], dict):
                        raise ValueError("Grok returned invalid attributes")
                    for key in ("product_name", "normalized_product", "quantity_unit", "currency", "city", "province", "reason"):
                        if item.get(key) is not None and not isinstance(item[key], str):
                            raise ValueError(f"Grok returned invalid {key}")
            except (ValueError, KeyError, IndexError):
                if len(batch) == 1:
                    continue
                midpoint = len(batch) // 2
                out.extend(self.extract_batch(batch[:midpoint], profile))
                out.extend(self.extract_batch(batch[midpoint:], profile))
                continue
            out.extend(items)
        return out
