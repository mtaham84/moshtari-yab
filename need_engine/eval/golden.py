from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
import os
from need_engine.config import EngineConfig
from need_engine.extract import extract_window
from need_engine.mock import mock_llm
from need_engine.platforms.x import PUBLIC_CHAT_ID
from need_engine.prompts import NEED_SYSTEM, NEED_SYSTEM_X
from need_engine.schemas import ChatMessage, STRENGTH_WEIGHT
from need_engine.windowing import Window
from need_engine.x_filters import cheap_x_filter


@dataclass(frozen=True)
class GoldenRow:
    id: str
    platform: str
    text: str
    expected: dict
    parent_text: str | None = None
    reviewed: bool = True


class MockClient:
    def complete_json(self, stage: str, model: str, system: str, user: str, **kwargs):
        data = mock_llm(stage, system, user)
        if stage == "need_extraction":
            for need in data.get("needs", []):
                need.update(buyer_intent_confirmed=True, author_type="individual", promotional_content=False)
        return data, {"toman": 0.0}


def load_golden(path: str | Path) -> list[GoldenRow]:
    rows = []
    for line_no, line in enumerate(Path(path).read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            item = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ValueError(f"line {line_no}: invalid JSON") from exc
        if not isinstance(item, dict):
            raise ValueError(f"line {line_no}: expected a JSON object")
        expected = item.get("expected")
        if not isinstance(item.get("id"), str) or item.get("platform") not in {"telegram", "x"}:
            raise ValueError(f"line {line_no}: id and supported platform are required")
        if not isinstance(item.get("text"), str) or not isinstance(expected, dict) or not isinstance(expected.get("is_need"), bool):
            raise ValueError(f"line {line_no}: text and expected.is_need are required")
        parent_text = item.get("parent_text")
        if parent_text is not None and not isinstance(parent_text, str):
            raise ValueError(f"line {line_no}: parent_text must be a string")
        reviewed = item.get("reviewed", True)
        if not isinstance(reviewed, bool):
            raise ValueError(f"line {line_no}: reviewed must be a boolean")
        rows.append(GoldenRow(item["id"], item["platform"], item["text"], expected, parent_text, reviewed))
    return rows


def _message(row: GoldenRow, index: int) -> ChatMessage:
    chat_id = PUBLIC_CHAT_ID if row.platform == "x" else f"-100eval{index}"
    return ChatMessage(chat_id=chat_id, message_id=index, row_id=index, author_id=f"author-{index}",
                       author_name="کاربر نمونه", text=row.text, date=datetime.now(timezone.utc),
                       platform=row.platform)


def estimate_live_cost(rows: list[GoldenRow], cfg: EngineConfig) -> tuple[str, float]:
    """Estimate the one-pass extraction cost using the rendered prompts and model prices."""
    from need_engine.llm import _est_tokens
    from need_engine.registry import ModelRegistry
    from need_engine.windowing import render

    registry = ModelRegistry(cfg)
    model = cfg.extract_model
    estimated_usd = 0.0
    for index, row in enumerate(rows, 1):
        if not row.reviewed:
            continue
        message = _message(row, index)
        system = NEED_SYSTEM_X if row.platform == "x" else NEED_SYSTEM
        parents = []
        if row.parent_text:
            parents.append(message.model_copy(update={"message_id": index + 100000, "text": row.parent_text}))
        window = Window(message.chat_id, "Golden set", [message], parents=parents)
        input_tokens = _est_tokens(system, render(window, cfg))
        model = registry.model_for("need_extraction") or cfg.extract_model
        input_price, output_price = registry.price(model)
        estimated_usd += input_tokens / 1_000_000 * input_price + 6000 / 1_000_000 * output_price
    return model, estimated_usd


def evaluate(rows: list[GoldenRow], cfg: EngineConfig, llm, min_score: float) -> dict:
    if min_score < 0 or min_score > 100:
        raise ValueError("min_score must be between 0 and 100")
    predictions = []
    strength_score = {key: value * 100 for key, value in STRENGTH_WEIGHT.items()}
    for index, row in enumerate(rows, 1):
        if not row.reviewed:
            continue
        message = _message(row, index)
        if row.platform == "x" and cfg.x_prefilter == "on" and not cheap_x_filter(message, cfg).keep:
            predictions.append({"id": row.id, "text": row.text, "expected": row.expected["is_need"],
                                "predicted": False, "score": 0.0,
                                "reference": row.expected.get("product_id") or row.expected.get("category")})
            continue
        parents = []
        if row.parent_text:
            parents.append(message.model_copy(update={"message_id": index + 100000, "text": row.parent_text}))
        cards, _ = extract_window(Window(message.chat_id, "Golden set", [message], parents=parents), llm, cfg, message.date)
        score = max((strength_score[card.strength] for card in cards if card.is_opportunity), default=0.0)
        predicted = score >= min_score
        predictions.append({"id": row.id, "text": row.text, "expected": row.expected["is_need"],
                            "predicted": predicted, "score": score,
                            "reference": row.expected.get("product_id") or row.expected.get("category")})
    tp = sum(item["expected"] and item["predicted"] for item in predictions)
    fp = sum(not item["expected"] and item["predicted"] for item in predictions)
    fn = sum(item["expected"] and not item["predicted"] for item in predictions)
    precision = tp / (tp + fp) if tp + fp else 0.0
    recall = tp / (tp + fn) if tp + fn else 0.0
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    return {"precision": precision, "recall": recall, "f1": f1, "tp": tp, "fp": fp, "fn": fn,
            "false_positives": [item for item in predictions if not item["expected"] and item["predicted"]],
            "false_negatives": [item for item in predictions if item["expected"] and not item["predicted"]],
            "predictions": predictions}


def export_feedback(path: str | Path) -> int:
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
    import django
    from django.apps import apps

    if not apps.ready:
        django.setup()
    from apps.discovery.models import Opportunity

    output = Path(path)
    output.parent.mkdir(parents=True, exist_ok=True)
    rows = Opportunity.objects.filter(
        feedback__in=["relevant", "irrelevant"], source_platform__in=["telegram", "x"]
    ).exclude(source_raw_message="").exclude(trace_metadata__retention_scrubbed=True)
    count = 0
    with output.open("w", encoding="utf-8") as stream:
        for opportunity in rows.iterator():
            item = {"id": f"opportunity-{opportunity.pk}", "platform": opportunity.source_platform,
                    "text": opportunity.source_raw_message,
                    "expected": {"is_need": opportunity.feedback == "relevant"},
                    "parent_text": None, "reviewed": False}
            stream.write(json.dumps(item, ensure_ascii=False) + "\n")
            count += 1
    return count

