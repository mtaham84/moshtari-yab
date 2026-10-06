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
NUMBER_WORDS = {
    "یک": 1, "یه": 1, "دو": 2, "سه": 3, "چهار": 4, "پنج": 5, "شش": 6,
    "هفت": 7, "هشت": 8, "نه": 9, "ده": 10, "یازده": 11, "دوازده": 12,
    "سیزده": 13, "چهارده": 14, "پانزده": 15, "شانزده": 16, "هفده": 17,
    "هجده": 18, "نوزده": 19, "بیست": 20, "سی": 30, "چهل": 40, "پنجاه": 50,
    "شصت": 60, "هفتاد": 70, "هشتاد": 80, "نود": 90, "صد": 100,
    "دویست": 200, "سیصد": 300, "چهارصد": 400, "پانصد": 500,
    "ششصد": 600, "هفتصد": 700, "هشتصد": 800, "نهصد": 900, "هزار": 1000,
}
BUY_PHRASES = (
    "می خوام", "میخوام", "می خواهم", "میخواهم", "میخام", "می خواستم", "میخواستم", "میخوام بخرم", "می خوام بخرم",
    "میخرم", "می خرم", "خریدارم", "خریدار هستم", "قصد خرید", "قصد دارم بخرم",
    "نیاز دارم", "لازم دارم", "احتیاج دارم", "دنبال خرید", "دنبال .* هستم",
    "برای کافه", "برای مغازه", "برای رستوران", "کسی داره", "کسی سراغ داره",
    "کسی میفروشه", "کسی .* سراغ داره", "سراغ .* میگردم", "می خرید", "می خریدم", "خریداری میکنم", "looking for",
    "need to buy", "want to buy", "for my cafe", "anyone selling",
)
SELL_PHRASES = (
    "فروشنده", "فروش", "برای فروش", "فروشی", "موجود داریم", "موجوده",
    "موجود است", "فروش عمده", "تخفیف", "ارسال داریم", "خرید از ما", "نمایندگی فروش",
    "قیمت همکاری", "فروشگاه هستیم", "فروشگاه .* داریم", "می فروشم", "میفروشم", "بفروشم", "for sale", "selling",
    "in stock", "wholesale supplier", "we sell",
)
QUESTION_PHRASES = ("قیمت", "چنده", "چند است", "پیشنهاد", "سراغ دارید", "کسی میدونه", "recommend", "how much")
GENERIC_PRODUCT_WORDS = {"دستگاه", "machine", "product", "محصول", "کالا", "مدل", "نوع"}


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
    if match:
        return int(match.group(1)), int(match.group(1))
    number_pattern = "|".join(sorted(map(re.escape, NUMBER_WORDS), key=len, reverse=True))
    number_phrase = rf"(?:{number_pattern})(?:\s+و\s+(?:{number_pattern}))*"
    unit = r"(?:تا|عدد|دستگاه|متر|کیلو|مورد)"
    range_match = re.search(rf"({number_phrase})\s*(?:تا|الی)\s*({number_phrase})\s*{unit}?", normalized)
    if range_match:
        return _parse_number_words(range_match.group(1)), _parse_number_words(range_match.group(2))
    word_match = re.search(rf"({number_phrase})\s*{unit}", normalized)
    if word_match:
        number = _parse_number_words(word_match.group(1))
        return number, number
    return None, None


def _parse_number_words(value: str) -> int:
    total = 0
    current = 0
    for word in value.split():
        if word == "و":
            continue
        number = NUMBER_WORDS[word]
        if number == 1000:
            total += (current or 1) * number
            current = 0
        elif number >= 100:
            current += number
        else:
            current += number
    return total + current


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
    job_id: str | None = None
    status: str = "PENDING"
    current_phase: str = ""
    started_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    finished_at: str | None = None
    records_collected: int = 0
    buyers_found: int = 0
    sellers_filtered: int = 0
    duplicates_removed: int = 0
    grok_calls: int = 0
    grok_prompt_tokens: int = 0
    grok_completion_tokens: int = 0
    grok_estimated_cost_usd: float = 0.0
    cache_hits: int = 0
    errors: list[str] = field(default_factory=list)
    buyers: list[dict[str, Any]] = field(default_factory=list)
    raw_records: list[dict[str, Any]] = field(default_factory=list)


def cheap_candidate(record: SourceRecord, profile: ProductProfile) -> bool:
    text = normalize_text(record.text)
    terms = [term for term in (profile.normalized_product, normalize_text(profile.category)) if term]
    tokens = [token for token in re.findall(r"[\wآ-ی]+", profile.normalized_product) if len(token) > 3 and token not in GENERIC_PRODUCT_WORDS]
    return any(term in text for term in terms) or any(token in text for token in tokens)


def local_classify(record: SourceRecord, profile: ProductProfile) -> Analysis:
    text = normalize_text(record.text)
    has_buy = any(re.search(phrase, text) for phrase in BUY_PHRASES)
    has_sell = any(re.search(phrase, text) for phrase in SELL_PHRASES)
    product_terms = [term for term in (profile.normalized_product, normalize_text(profile.category), *(normalize_text(str(x)) for x in profile.attributes)) if len(term) > 1]
    product_tokens = [token for token in re.findall(r"[\wآ-ی]+", profile.normalized_product) if len(token) > 3 and token not in GENERIC_PRODUCT_WORDS]
    product_match = any(term in text for term in product_terms) or any(token in text for token in product_tokens)
    explicit_buy = any(re.search(phrase, text) for phrase in ("بخرم", "می خرم", "میخرم", "قصد خرید", "خریدارم", "خریدار .* هستم", "نیاز دارم", "لازم دارم", "احتیاج دارم", "looking for", "need to buy", "want to buy"))
    explicit_sell = any(re.search(phrase, text) for phrase in ("می فروشم", "میفروشم", "برای فروش", "فروش دارم", "فروش میرسد", "for sale", "we sell"))
    is_question = any(term in text for term in QUESTION_PHRASES) or "چیه" in text or "چیست" in text
    if not product_match:
        intent, direction, confidence, relevance = "irrelevant", "unknown", 0.85, 0.0
    elif has_sell and has_buy:
        if explicit_buy:
            intent, direction, confidence, relevance = "buy", "buyer", 0.67, 1.0
        elif explicit_sell:
            intent, direction, confidence, relevance = "sell", "seller", 0.93, 0.85
        else:
            intent, direction, confidence, relevance = "unknown", "unknown", 0.5, 0.8
    elif has_sell:
        intent, direction, confidence, relevance = "sell", "seller", 0.93, 0.85
    elif is_question and not explicit_buy:
        intent, direction, confidence, relevance = "question", "unknown", 0.68, 0.75
    elif has_buy:
        intent, direction, confidence, relevance = "buy", "buyer", 0.88, 1.0
    elif is_question:
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
    elif analysis.intent == "buy" and analysis.confidence >= 0.8 and analysis.total_score >= 75:
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
