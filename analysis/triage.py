"""Stage 2 — cheap batched screening with a small model.

One call screens a batch of messages against the whole (compact) catalog.
The call's real token usage is split across the batch proportionally to each
message's input size, so every message gets its own cost.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path

from analysis.config import AnalysisSettings
from analysis.llm import BaseLLM, InvalidLLMOutput, LLMResponse
from analysis.schemas import ProductProfile, SocialMessage, StageCost, TriageResult
from analysis.text import truncate

log = logging.getLogger("analysis.triage")
PROMPT = (Path(__file__).parent / "prompts" / "triage_system.md").read_text(encoding="utf-8")
TEXT_LIMIT = 600
HINT_LIMIT = 200


def allocate(total: int, weights: list[float]) -> list[int]:
    """Split an integer total proportionally (largest remainder); parts sum exactly to total."""
    if not weights:
        return []
    wsum = sum(weights)
    if wsum <= 0:
        weights, wsum = [1.0] * len(weights), float(len(weights))
    raw = [total * w / wsum for w in weights]
    parts = [int(r) for r in raw]
    for i in sorted(range(len(raw)), key=lambda i: raw[i] - parts[i], reverse=True)[: total - sum(parts)]:
        parts[i] += 1
    return parts


def context_hint(msg: SocialMessage) -> str:
    """The single most useful context line for screening: the parent, else the last previous message."""
    parents = msg.context_of("parent")
    if parents:
        return truncate(parents[-1].text, HINT_LIMIT)
    previous = msg.context_of("previous")
    return truncate(previous[-1].text, HINT_LIMIT) if previous else ""


@dataclass
class TriageOutcome:
    matches: dict[str, list[TriageResult]] = field(default_factory=dict)
    costs: dict[str, StageCost] = field(default_factory=dict)
    failed: list[str] = field(default_factory=list)


class Triage:
    def __init__(self, llm: BaseLLM, settings: AnalysisSettings) -> None:
        self.llm = llm
        self.settings = settings

    def run(self, messages: list[SocialMessage], catalog: list[ProductProfile], outcome: TriageOutcome | None = None) -> TriageOutcome:
        """Fill ``outcome`` batch by batch, so a caller keeps partial results if an exception interrupts the run."""
        outcome = outcome if outcome is not None else TriageOutcome()
        size = self.settings.triage_batch_size
        for start in range(0, len(messages), size):
            self._run_batch(messages[start : start + size], catalog, outcome)
        return outcome

    # ------------------------------------------------------------------
    def _payload(self, batch: list[SocialMessage], catalog: list[ProductProfile]) -> tuple[str, list[float]]:
        items, weights = [], []
        for i, m in enumerate(batch, 1):
            text, hint = truncate(m.text, TEXT_LIMIT), context_hint(m)
            item = {"id": f"m{i}", "text": text}
            if hint:
                item["context"] = hint
            items.append(item)
            weights.append(len(text) + len(hint) + 40)
        payload = {"products": [p.short() for p in catalog], "messages": items}
        return json.dumps(payload, ensure_ascii=False), weights

    def _charge(self, resp: LLMResponse, batch: list[SocialMessage], weights: list[float], outcome: TriageOutcome) -> None:
        pt = allocate(resp.prompt_tokens, weights)
        ct = allocate(resp.completion_tokens, weights)
        for m, p, c in zip(batch, pt, ct):
            usd, toman = self.llm.costs.cost(resp.model, p, c)
            prev = outcome.costs.get(m.uid)
            if prev is None:
                outcome.costs[m.uid] = StageCost(stage="triage", model=resp.model, prompt_tokens=p, completion_tokens=c,
                                                 cost_usd=usd, cost_toman=toman, latency_ms=resp.latency_ms, shared_call=len(batch) > 1)
            else:  # message was part of a batch that had to be retried
                prev.prompt_tokens += p
                prev.completion_tokens += c
                prev.cost_usd += usd
                prev.cost_toman += toman
                prev.latency_ms += resp.latency_ms

    def _validate(self, data: dict | None, batch: list[SocialMessage], catalog: list[ProductProfile]) -> dict[str, list[TriageResult]]:
        if not isinstance(data, dict) or not isinstance(data.get("results"), list):
            raise InvalidLLMOutput("missing results list")
        ids = {f"m{i}": m for i, m in enumerate(batch, 1)}
        valid_products = {p.product_id for p in catalog}
        out: dict[str, list[TriageResult]] = {}
        for item in data["results"]:
            if not isinstance(item, dict):
                raise InvalidLLMOutput("result item is not an object")
            mid = str(item.get("id", ""))
            if mid not in ids or ids[mid].uid in out:
                raise InvalidLLMOutput(f"unknown or duplicate id {mid!r}")
            matches = item.get("matches", [])
            if not isinstance(matches, list):
                raise InvalidLLMOutput("matches is not a list")
            results, seen = [], set()
            for mt in matches:
                try:
                    pid = int(mt["product_id"])
                except (KeyError, TypeError, ValueError) as exc:
                    raise InvalidLLMOutput("bad product_id") from exc
                if pid not in valid_products:
                    raise InvalidLLMOutput(f"product_id {pid} not in catalog")
                if pid in seen:
                    continue
                seen.add(pid)
                results.append(TriageResult(message_uid=ids[mid].uid, product_id=pid, reason=str(mt.get("reason", ""))[:200]))
            out[ids[mid].uid] = results
        if len(out) != len(batch):
            raise InvalidLLMOutput("some message ids are missing")
        return out

    def _run_batch(self, batch: list[SocialMessage], catalog: list[ProductProfile], outcome: TriageOutcome) -> None:
        user, weights = self._payload(batch, catalog)
        resp = self.llm.complete_json(
            "triage", self.settings.triage_model,
            [{"role": "system", "content": PROMPT}, {"role": "user", "content": user}],
            max_tokens=min(4000, 120 + 60 * len(batch)),
        )
        self._charge(resp, batch, weights, outcome)  # tokens were spent even if the output is invalid
        try:
            outcome.matches.update(self._validate(resp.data, batch, catalog))
        except InvalidLLMOutput as exc:
            self.llm.mark_last_invalid()
            if len(batch) == 1:
                log.warning("Triage output invalid for %s: %s", batch[0].uid, exc)
                outcome.failed.append(batch[0].uid)
                return
            log.info("Triage output invalid for a batch of %s (%s); splitting", len(batch), exc)
            mid = len(batch) // 2
            self._run_batch(batch[:mid], catalog, outcome)
            self._run_batch(batch[mid:], catalog, outcome)
