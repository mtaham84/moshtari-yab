"""Stage 3 — deep analysis of one (message, product) pair with the large model."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from pathlib import Path

from pydantic import ValidationError

from analysis.config import AnalysisSettings
from analysis.llm import BaseLLM
from analysis.schemas import DeepAnalysis, ProductProfile, SocialMessage, StageCost
from analysis.text import truncate

log = logging.getLogger("analysis.deep")
_PROMPT_DIR = Path(__file__).parent / "prompts"
SYSTEM_PROMPT = (_PROMPT_DIR / "deep_system.md").read_text(encoding="utf-8")
EXAMPLES = json.loads((_PROMPT_DIR / "deep_examples.json").read_text(encoding="utf-8"))


def render_conversation(msg: SocialMessage, char_budget: int) -> dict:
    """Compact, budgeted view of the conversation. The target text always fits;
    older 'previous' messages are dropped first when over budget."""
    def item(c, limit):
        return {"author": c.author_name or "کاربر", "text": truncate(c.text, limit)}

    conv = {
        "source": msg.source,
        "channel": msg.channel_title,
        "parents": [item(c, 300) for c in msg.context_of("parent")[-3:]],
        "previous": [item(c, 250) for c in msg.context_of("previous")[-5:]],
        "target": {"author": msg.author.display_name or msg.author.handle or "کاربر", "text": truncate(msg.text, 1500)},
        "replies": [item(c, 250) for c in msg.context_of("reply")[:5]],
    }

    def size() -> int:
        return len(json.dumps(conv, ensure_ascii=False))

    for key in ("previous", "replies", "parents"):
        while size() > char_budget and conv[key]:
            conv[key].pop(0 if key != "replies" else -1)
    return conv


def product_view(p: ProductProfile) -> dict:
    view = {"name": p.name, "description": truncate(p.description, 600), "target_customer": truncate(p.target_customer, 600)}
    if p.price:
        view["price"] = p.price
    return view


@dataclass
class DeepOutcome:
    analysis: DeepAnalysis | None
    cost: StageCost
    error: str | None = None


class DeepAnalyzer:
    def __init__(self, llm: BaseLLM, settings: AnalysisSettings, few_shot: int = 4) -> None:
        self.llm = llm
        self.settings = settings
        self.few_shot = EXAMPLES[: max(0, few_shot)]

    def build_messages(self, msg: SocialMessage, product: ProductProfile) -> list[dict[str, str]]:
        messages = [{"role": "system", "content": SYSTEM_PROMPT}]
        for ex in self.few_shot:
            messages.append({"role": "user", "content": json.dumps(ex["input"], ensure_ascii=False)})
            messages.append({"role": "assistant", "content": json.dumps(ex["output"], ensure_ascii=False)})
        payload = {"product": product_view(product), "conversation": render_conversation(msg, self.settings.context_char_budget)}
        messages.append({"role": "user", "content": json.dumps(payload, ensure_ascii=False)})
        return messages

    def analyze(self, msg: SocialMessage, product: ProductProfile) -> DeepOutcome:
        messages = self.build_messages(msg, product)
        cost = StageCost(stage="deep", model=self.settings.deep_model)
        error = None
        for attempt in range(2):  # one corrective retry on invalid output
            resp = self.llm.complete_json("deep", self.settings.deep_model, messages, max_tokens=700)
            c = self.llm.costs.stage_cost("deep", resp)
            cost.model = c.model
            cost.prompt_tokens += c.prompt_tokens
            cost.completion_tokens += c.completion_tokens
            cost.cost_usd += c.cost_usd
            cost.cost_toman += c.cost_toman
            cost.latency_ms += c.latency_ms
            try:
                if resp.data is None:
                    raise ValueError("output is not a JSON object")
                return DeepOutcome(DeepAnalysis.model_validate(resp.data), cost)
            except (ValidationError, ValueError) as exc:
                self.llm.mark_last_invalid()
                error = f"invalid_deep_output: {str(exc)[:200]}"
                log.info("Deep output invalid for %s/%s (attempt %s): %s", msg.uid, product.product_id, attempt + 1, exc)
                messages = messages + [
                    {"role": "assistant", "content": resp.raw_content[:2000]},
                    {"role": "user", "content": f"Your previous answer was invalid: {str(exc)[:300]}. Return only the JSON object with the required keys."},
                ]
        return DeepOutcome(None, cost, error)
