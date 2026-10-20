from __future__ import annotations

import json
import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from .analyzer import normalize_text

_LEXICON_PATH = Path(__file__).resolve().parents[1] / "data" / "ig_lexicon_fa.json"
_LABEL_ORDER = ("other", "interest", "price_question", "contact_request")
_RULE_NORMALIZER = re.compile(r"[\wآ-ی]+|[^\w\s]", re.UNICODE)
_ARABIC_DIGITS = str.maketrans("٠١٢٣٤٥٦٧٨٩", "0123456789")


@dataclass(frozen=True)
class CommentIntent:
    label: str
    cue: str | None


@lru_cache(maxsize=4)
def _load_rules(path: Path = _LEXICON_PATH) -> dict[str, list[tuple[str, re.Pattern[str]]]]:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    section = payload.get("seller_post_comment")
    if not isinstance(section, dict):
        raise ValueError("seller_post_comment lexicon section is required")

    rules: dict[str, list[tuple[str, re.Pattern[str]]]] = {}
    for label in _LABEL_ORDER:
        rows = section.get(label, [])
        if not isinstance(rows, list):
            raise ValueError(f"seller_post_comment.{label} must be a list")
        rules[label] = []
        for row in rows:
            if isinstance(row, str):
                normalized = normalize_text(row)
                tokens = _RULE_NORMALIZER.findall(normalized)
                cue, pattern = row, r"(?<![\wآ-ی])" + r"\s+".join(map(re.escape, tokens)) + r"(?![\wآ-ی])"
            elif isinstance(row, dict) and isinstance(row.get("cue"), str) and isinstance(row.get("pattern"), str):
                cue, pattern = row["cue"], row["pattern"]
            else:
                raise ValueError(f"invalid seller_post_comment.{label} rule")
            rules[label].append((cue, re.compile(pattern, re.IGNORECASE)))
    return rules


def classify_comment(text: str, *, lexicon_path: str | Path = _LEXICON_PATH) -> CommentIntent:
    normalized = normalize_text(text).translate(_ARABIC_DIGITS)
    tokens = _RULE_NORMALIZER.findall(normalized)
    normalized = " ".join(tokens).replace(" ?", "?").replace(" ؟", "؟")
    rules = _load_rules(Path(lexicon_path).resolve())
    matches = {label: next((cue for cue, pattern in rules[label] if pattern.search(normalized)), None)
               for label in _LABEL_ORDER}
    if matches["other"]:
        return CommentIntent("other", matches["other"])
    for label in ("interest", "price_question", "contact_request"):
        if matches[label]:
            return CommentIntent(label, matches[label])
    return CommentIntent("other", None)
