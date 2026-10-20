from __future__ import annotations

import json
import re
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import Any

_DIGITS = str.maketrans("۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩", "01234567890123456789")
_EMOJI = re.compile("[\U0001F000-\U0001FAFF\U00002600-\U000027BF\U0000FE0F]+")
_SPACE = re.compile(r"\s+")


def normalize_fa(value: Any) -> str:
    text = unicodedata.normalize("NFKC", str(value or "")).translate(_DIGITS)
    text = text.replace("ي", "ی").replace("ك", "ک").replace("ـ", "")
    text = text.replace("‌", " ").replace("‏", "")
    text = _EMOJI.sub(" ", text)
    # Collapse accidental repeated letter noise while preserving normal doubles.
    text = re.sub(r"([\wآ-ی])\1{2,}", r"\1\1", text, flags=re.IGNORECASE)
    return _SPACE.sub(" ", text).strip().casefold()


@dataclass(frozen=True)
class Intent:
    score: int
    labels: list[str]
    matched: dict[str, list[str]]


class PersianIntentLexicon:
    LABELS = ("request", "price_question", "giveaway", "advertisement")

    def __init__(self, data: dict[str, Any]):
        self.data = data
        self.phrases: dict[str, list[str]] = {}
        raw_phrases = data.get("phrases", {})
        for label in self.LABELS:
            values = raw_phrases.get(label, []) if isinstance(raw_phrases, dict) else []
            self.phrases[label] = [normalize_fa(item) for item in values if normalize_fa(item)]
        self.strong_buyer_phrases = [normalize_fa(item) for item in data.get("strong_buyer_phrases", []) if normalize_fa(item)]
        self.weak_request_phrases = [normalize_fa(item) for item in data.get("weak_request_phrases", []) if normalize_fa(item)]
        self.private_contact_terms = [normalize_fa(item) for item in data.get("private_contact_terms", []) if normalize_fa(item)]

    @classmethod
    def load(cls, path: str | Path | None = None) -> "PersianIntentLexicon":
        target = Path(path or Path(__file__).resolve().parents[2] / "data" / "ig_lexicon_fa.json")
        try:
            payload = json.loads(target.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ValueError(f"could not load Persian intent lexicon: {target}") from exc
        if not isinstance(payload, dict):
            raise ValueError("Persian intent lexicon root must be a JSON object")
        return cls(payload)

    def classify(self, text: str) -> Intent:
        normalized = normalize_fa(text)
        matched = {label: [phrase for phrase in phrases if _contains(normalized, phrase)]
                   for label, phrases in self.phrases.items()}
        strong = [phrase for phrase in self.strong_buyer_phrases if _contains(normalized, phrase)]
        weak = [phrase for phrase in self.weak_request_phrases if _contains(normalized, phrase)]
        contact = [phrase for phrase in self.private_contact_terms if _contains(normalized, phrase)]

        labels: list[str] = []
        strong_buyer = bool(strong)
        giveaway = bool(matched["giveaway"])
        advertisement = bool(matched["advertisement"])
        request = bool(matched["request"] or weak)
        if giveaway:
            labels.append("giveaway")
        if advertisement:
            labels.append("advertisement")
        if matched["price_question"]:
            labels.append("price_question")
        # Giveaway/seller language suppresses weak request-like words. A direct
        # buyer phrase is strong enough to retain request despite those signals.
        if request and (strong_buyer or not (giveaway or advertisement)):
            labels.append("request")
        # Direct/PM alone is ambiguous; its existence never raises the score.
        score = 3 if strong_buyer else 0
        if "request" in labels and score == 0:
            score = 2
        if "price_question" in labels and not (giveaway or advertisement):
            score = max(score, 1)
        if not labels:
            labels.append("other")
        matched["strong_request"] = strong
        matched["weak_request"] = weak
        matched["private_contact"] = contact
        return Intent(score=min(3, score), labels=labels, matched=matched)


def _contains(text: str, phrase: str) -> bool:
    if not phrase:
        return False
    # «...» marks a variable product/place in editable strong patterns.
    if "..." in phrase or "…" in phrase:
        pieces = re.split(r"(?:\.\.\.|…)", phrase)
        pattern = r".{1,100}".join(re.escape(piece.strip()) for piece in pieces)
        return bool(re.search(pattern, text))
    if re.search(r"[^\wآ-ی]", phrase, flags=re.UNICODE):
        return phrase in text
    return bool(re.search(rf"(?<![\wآ-ی]){re.escape(phrase)}(?![\wآ-ی])", text))
