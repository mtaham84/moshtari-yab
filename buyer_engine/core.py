from __future__ import annotations

import csv
import json
import re
import unicodedata
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

PERSIAN_DIGITS = str.maketrans("۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩", "01234567890123456789")


def normalize_text(value: str) -> str:
    value = unicodedata.normalize("NFKC", value or "").translate(PERSIAN_DIGITS)
    value = value.replace("ي", "ی").replace("ك", "ک").replace("ـ", "")
    value = re.sub(r"[\u200c\u200d\ufeff]", " ", value)
    value = re.sub(r"[^\w@#./+-]+", " ", value, flags=re.UNICODE)
    return re.sub(r"\s+", " ", value).strip().casefold()


def extract_quantity(text: str) -> tuple[int | None, int | None]:
    normalized = normalize_text(text)
    match = re.search(r"(?<!\w)(\d{1,9})\s*(?:تا|عدد|دستگاه|متر|کیلو)?\s*(?:تا|الی|-)\s*(\d{1,9})", normalized)
    if match:
        return int(match.group(1)), int(match.group(2))
    match = re.search(r"(?<!\w)(\d{1,9})\s*(?:تا|عدد|دستگاه|متر|کیلو)", normalized)
    return (int(match.group(1)), int(match.group(1))) if match else (None, None)


@dataclass
class ProductProfile:
    product_name: str
    category: str = ""
    attributes: dict[str, Any] = field(default_factory=dict)
    city: str | None = None
    quantity: int | None = None
    transaction_type: str = "unknown"

    @property
    def normalized_product(self) -> str:
        return normalize_text(self.product_name)


@dataclass
class SourceRecord:
    source: str
    source_id: str
    text: str
    source_url: str | None = None
    author_id: str | None = None
    username: str | None = None
    created_at: str | None = None
    query_used: str | None = None
    raw_data: dict[str, Any] = field(default_factory=dict)


@dataclass
class Analysis:
    intent: str = "unknown"
    lead_direction: str = "unknown"
    buyer_type: str = "unknown"
    transaction_type: str = "unknown"
    product_name: str = ""
    normalized_product: str = ""
    quantity_min: int | None = None
    quantity_max: int | None = None
    quantity_unit: str | None = None
    budget_min: float | None = None
    budget_max: float | None = None
    currency: str | None = None
    city: str | None = None
    province: str | None = None
    attributes: dict[str, Any] = field(default_factory=dict)
    confidence: float = 0.0
    reason: str = ""
    product_score: float = 0.0
    intent_score: float = 0.0
    quantity_score: float = 40.0
    location_score: float = 40.0
    recency_score: float = 20.0
    total_score: float = 0.0
    category: str = "REJECTED"


@dataclass
class JobResult:
    product: dict[str, Any]
    status: str = "PENDING"
    current_phase: str = ""
    started_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    finished_at: str | None = None
    records_collected: int = 0
    buyers_found: int = 0
    sellers_filtered: int = 0
    duplicates_removed: int = 0
    grok_calls: int = 0
    errors: list[str] = field(default_factory=list)
    buyers: list[dict[str, Any]] = field(default_factory=list)
    raw_records: list[dict[str, Any]] = field(default_factory=list)


def cheap_candidate(record: SourceRecord, profile: ProductProfile) -> bool:
    text = normalize_text(record.text)
    terms = [term for term in (profile.normalized_product, normalize_text(profile.category)) if term]
    return any(term in text for term in terms)


def local_classify(record: SourceRecord, profile: ProductProfile) -> Analysis:
    text = normalize_text(record.text)
    buy = ("میخوام", "می خواهم", "میخوام بخرم", "نیاز دارم", "لازم دارم", "دنبال", "برای کافه", "برای مغازه", "برای رستوران", "کسی داره", "کسی میفروشه", "looking for", "need to buy", "want to buy", "for my cafe", "anyone selling")
    sell = ("فروشنده", "فروشی", "موجود", "فروش عمده", "تخفیف", "ارسال داریم", "خرید از ما", "for sale", "selling", "in stock", "wholesale supplier")
    question = ("قیمت", "چنده", "پیشنهاد", "سراغ دارید", "recommend", "how much")
    product_terms = [term for term in (profile.normalized_product, normalize_text(profile.category), *(normalize_text(str(x)) for x in profile.attributes)) if len(term) > 1]
    product_match = any(term in text for term in product_terms)
    buy_hit, sell_hit = any(term in text for term in buy), any(term in text for term in sell)
    if not product_match:
        intent, direction, confidence, relevance = "irrelevant", "unknown", 0.85, 0.0
    elif sell_hit and not buy_hit:
        intent, direction, confidence, relevance = "sell", "seller", 0.93, 0.85
    elif buy_hit:
        intent, direction, confidence, relevance = "buy", "buyer", 0.88, 1.0
    elif any(term in text for term in question):
        intent, direction, confidence, relevance = "question", "unknown", 0.68, 0.75
    else:
        intent, direction, confidence, relevance = "unknown", "unknown", 0.45, 0.65
    low, high = extract_quantity(record.text)
    city = profile.city if profile.city and normalize_text(profile.city) in text else None
    return Analysis(intent=intent, lead_direction=direction, confidence=confidence, quantity_min=low, quantity_max=high, city=city, product_name=profile.product_name if product_match else "", normalized_product=profile.normalized_product if product_match else "", product_score=relevance * 100, intent_score=100 if intent == "buy" else 55 if intent == "question" else 0, reason="Local rule-based classification")


def rank_analysis(analysis: Analysis, record: SourceRecord, profile: ProductProfile, weights: dict[str, float] | None = None) -> Analysis:
    weights = weights or {"product": 40, "intent": 20, "confidence": 15, "quantity": 10, "location": 5, "recency": 5, "business": 5}
    if profile.quantity is not None and analysis.quantity_min is not None:
        ratio = min(analysis.quantity_max or analysis.quantity_min, profile.quantity) / max(analysis.quantity_min, profile.quantity)
        analysis.quantity_score = max(0, ratio * 100)
    if profile.city and analysis.city:
        analysis.location_score = 100 if normalize_text(profile.city) == normalize_text(analysis.city) else 60
    elif profile.city:
        analysis.location_score = 40
    if record.created_at:
        try:
            created = datetime.fromisoformat(record.created_at.replace("Z", "+00:00"))
            age = max(0, (datetime.now(timezone.utc) - created.astimezone(timezone.utc)).total_seconds() / 86400)
            analysis.recency_score = 100 if age < 1 else 90 if age < 3 else 75 if age < 7 else 50 if age < 30 else 20
        except ValueError:
            pass
    components = {"product": analysis.product_score, "intent": analysis.intent_score, "confidence": analysis.confidence * 100, "quantity": analysis.quantity_score, "location": analysis.location_score, "recency": analysis.recency_score, "business": 50}
    total_weight = sum(weights.values()) or 1
    analysis.total_score = round(sum(components[key] * weight for key, weight in weights.items()) / total_weight)
    if analysis.lead_direction == "seller" or analysis.intent in {"sell", "advertisement", "irrelevant"} or analysis.confidence < 0.35:
        analysis.category = "REJECTED"
    elif analysis.intent == "question":
        analysis.category = "QUESTION"
    elif analysis.intent == "buy" and analysis.total_score >= 75:
        analysis.category = "HOT"
    elif analysis.intent == "buy" and analysis.total_score >= 50:
        analysis.category = "WARM"
    else:
        analysis.category = "COLD"
    return analysis


def export_result(result: JobResult, output_dir: Path) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "raw").mkdir(exist_ok=True)
    (output_dir / "analyzed").mkdir(exist_ok=True)
    (output_dir / "raw" / "records.json").write_text(json.dumps(result.raw_records, ensure_ascii=False, indent=2), encoding="utf-8")
    (output_dir / "analyzed" / "buyers.json").write_text(json.dumps(result.buyers, ensure_ascii=False, indent=2), encoding="utf-8")
    (output_dir / "buyers.json").write_text(json.dumps(asdict(result), ensure_ascii=False, indent=2), encoding="utf-8")
    columns = "rank source username author_id source_url product intent lead_direction buyer_type transaction_type quantity_min quantity_max quantity_unit city province budget_min budget_max confidence product_score intent_score quantity_score location_score recency_score total_score category evidence query_used".split()
    with (output_dir / "buyers.csv").open("w", newline="", encoding="utf-8-sig") as stream:
        writer = csv.DictWriter(stream, fieldnames=columns, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(result.buyers)
    summary = asdict(result)
    summary.pop("buyers", None)
    (output_dir / "run_summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
