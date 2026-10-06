from __future__ import annotations

import os
import re
import time
from dataclasses import asdict
from datetime import datetime, timezone
from typing import Any

from .ai import GrokAnalyzer
from .core import Analysis, JobResult, ProductProfile, SourceRecord, cheap_candidate, local_classify, normalize_text, rank_analysis
from .sources import DivarAdapter, MockAdapter, XAdapter

BUY_PHRASES = ["میخوام", "نیاز دارم", "دنبال", "برای کافه", "برای مغازه", "برای رستوران", "looking for", "need", "for my cafe"]
SELLER_QUERY_WORDS = ["فروش", "فروش عمده", "موجود", "for sale", "wholesale", "seller"]


def make_queries(profile: ProductProfile, vocabulary: list[str], max_queries: int = 8) -> list[str]:
    terms = [profile.product_name, *vocabulary]
    phrases = ["نیاز دارم", "دنبال خرید", "برای کافه", "for my cafe", "looking to buy"]
    queries = []
    for term in terms:
        for phrase in phrases:
            query = f'"{term}" {phrase}'
            if query not in queries:
                queries.append(query)
            if len(queries) >= max_queries:
                return queries
    return queries


def discover_vocabulary(records: list[SourceRecord], profile: ProductProfile) -> list[str]:
    vocab = []
    for record in records:
        text = record.text
        for candidate in re.findall(r"#[\wآ-ی]+|[\wآ-ی]+(?:\s+[\wآ-ی]+){0,2}", text, re.UNICODE):
            normalized = normalize_text(candidate)
            if len(normalized) > 3 and normalized not in profile.normalized_product and normalized not in vocab and len(vocab) < 12:
                vocab.append(candidate.strip())
    return vocab


def _merge_analysis(base: Analysis, values: dict[str, Any]) -> Analysis:
    allowed = set(Analysis.__dataclass_fields__)
    for key, value in values.items():
        if key in allowed and value is not None:
            setattr(base, key, value)
    base.confidence = max(0.0, min(1.0, float(base.confidence)))
    return base


def run_job(profile: ProductProfile, sources: list[str], mode: str, max_queries: int = 8) -> JobResult:
    result = JobResult(product=asdict(profile))
    started = time.monotonic()
    max_requests = int(os.getenv("MAX_SOURCE_REQUESTS", "100"))
    max_records = int(os.getenv("MAX_TOTAL_RECORDS", "3000"))
    max_runtime = int(os.getenv("MAX_JOB_RUNTIME_SECONDS", "300"))
    grok_budget = int(os.getenv("MAX_GROK_REQUESTS", "40"))
    result.status, result.current_phase = "PREPARING_PRODUCT", "PREPARING_PRODUCT"
    use_mock = mode == "mock" or os.getenv("USE_MOCK_SOURCES", "false").lower() == "true"
    adapters = {name: MockAdapter(name) if use_mock else XAdapter(max_requests=max_requests) if name == "x" else DivarAdapter(max_requests=max_requests) for name in sources}
    records: list[SourceRecord] = []
    queries = make_queries(profile, [], max_queries)
    try:
        if "x" in adapters:
            result.current_phase = "DISCOVERING_X_SELLERS"
            seller_queries = [f'"{profile.product_name}" {word}' for word in SELLER_QUERY_WORDS[:3]]
            for query in seller_queries:
                records.extend(adapters["x"].search(query))
                if time.monotonic() - started > max_runtime or len(records) >= max_records: break
        vocabulary = discover_vocabulary(records, profile)
        queries = make_queries(profile, vocabulary, max_queries)
        result.current_phase = "SEARCHING_X_BUYERS"
        for source_name, adapter in adapters.items():
            source_queries = queries if source_name == "x" else [profile.product_name, *vocabulary][:max_queries]
            for query in source_queries:
                if time.monotonic() - started > max_runtime or len(records) >= max_records:
                    break
                records.extend(adapter.search(query))
        result.records_collected = len(records)
        result.raw_records = [{"source": r.source, "source_id": r.source_id, "source_url": r.source_url, "author_id": r.author_id, "username": r.username, "text": r.text, "created_at": r.created_at, "query_used": r.query_used, "raw_data": r.raw_data} for r in records]
        result.current_phase = "DEDUPLICATING"
        unique: list[SourceRecord] = []
        seen: set[str] = set()
        for record in records:
            key = f"{record.source}:{record.source_id}" if record.source_id else f"{record.source}:{record.username}:{normalize_text(record.text)}"
            if key in seen:
                result.duplicates_removed += 1
            else:
                seen.add(key)
                unique.append(record)
        candidates = [record for record in unique if cheap_candidate(record, profile)]
        result.current_phase = "ANALYZING"
        analyzer = None if use_mock else GrokAnalyzer()
        if analyzer and not analyzer.api_key:
            raise RuntimeError("Set XAI_API_KEY or run with --mode mock")
        if analyzer:
            classifications = analyzer.classify_batch(candidates)
            result.grok_calls = analyzer.calls
            classification_map = {str(item.get("id")): item for item in classifications}
        else:
            classification_map = {r.source_id: {"intent": a.intent, "lead_direction": a.lead_direction, "confidence": a.confidence} for r in candidates if (a := local_classify(r, profile))}
        eligible: list[tuple[SourceRecord, Analysis]] = []
        for record in candidates:
            classification = classification_map.get(record.source_id, {})
            analysis = local_classify(record, profile)
            analysis.intent = classification.get("intent", analysis.intent)
            analysis.lead_direction = classification.get("lead_direction", analysis.lead_direction)
            analysis.confidence = float(classification.get("confidence", analysis.confidence) or 0)
            analysis.reason = classification.get("reason", analysis.reason)
            if analysis.lead_direction == "seller" or analysis.intent in {"sell", "advertisement"}:
                result.sellers_filtered += 1
                continue
            if analysis.intent in {"irrelevant"}:
                continue
            if analysis.intent in {"buy", "question"} or analysis.confidence >= 0.55:
                eligible.append((record, analysis))
        if analyzer and eligible and analyzer.calls < grok_budget:
            remaining_calls = grok_budget - analyzer.calls
            detail_capacity = remaining_calls * analyzer.batch_size
            details = analyzer.extract_batch(eligible[:detail_capacity], profile)
            result.grok_calls = analyzer.calls
            detail_map = {str(item.get("id")): item for item in details}
        else:
            detail_map = {}
        scored: list[dict[str, Any]] = []
        aggregated: dict[str, dict[str, Any]] = {}
        for record, analysis in eligible:
            analysis = _merge_analysis(analysis, detail_map.get(record.source_id, {}))
            analysis = rank_analysis(analysis, record, profile)
            if analysis.category in {"REJECTED", "QUESTION"}:
                continue
            identity = f"x:{record.author_id or record.username}" if record.source == "x" else f"divar:{record.source_id}"
            entry = aggregated.get(identity)
            evidence = {"source_id": record.source_id, "source": record.source, "evidence": record.text, "post_url": record.source_url, "query_used": record.query_used, "created_at": record.created_at, "analysis": asdict(analysis)}
            if entry is None:
                entry = {"source": record.source, "username": record.username, "author_id": record.author_id, "source_url": record.source_url, "product": profile.product_name, "intent": analysis.intent, "lead_direction": analysis.lead_direction, "buyer_type": analysis.buyer_type, "transaction_type": analysis.transaction_type, "quantity_min": analysis.quantity_min, "quantity_max": analysis.quantity_max, "quantity_unit": analysis.quantity_unit, "city": analysis.city, "province": analysis.province, "budget_min": analysis.budget_min, "budget_max": analysis.budget_max, "confidence": analysis.confidence, "product_score": analysis.product_score, "intent_score": analysis.intent_score, "quantity_score": analysis.quantity_score, "location_score": analysis.location_score, "recency_score": analysis.recency_score, "total_score": analysis.total_score, "category": analysis.category, "evidence": record.text, "query_used": record.query_used, "evidence_count": 0, "buyer_evidence_count": 0, "seller_evidence_count": 0, "evidences": []}
                aggregated[identity] = entry
            entry["evidence_count"] += 1
            entry["buyer_evidence_count"] += analysis.intent == "buy"
            entry["seller_evidence_count"] += analysis.intent == "sell"
            entry["evidences"].append(evidence)
            if analysis.total_score > entry["total_score"]:
                for key in ("source_url", "evidence", "query_used", "total_score", "category", "confidence", "product_score", "intent_score", "quantity_score", "location_score", "recency_score", "quantity_min", "quantity_max", "city"):
                    entry[key] = getattr(analysis, key) if key not in {"source_url", "evidence", "query_used"} else {"source_url": record.source_url, "evidence": record.text, "query_used": record.query_used}[key]
        result.buyers = sorted(aggregated.values(), key=lambda item: item["total_score"], reverse=True)
        for index, buyer in enumerate(result.buyers, 1): buyer["rank"] = index
        result.buyers_found = len(result.buyers)
        result.finished_at = datetime.now(timezone.utc).isoformat()
        result.status = "COMPLETED" if result.buyers else "PARTIAL_SUCCESS"
    except Exception as exc:
        result.errors.append(str(exc))
        result.status = "FAILED"
        result.finished_at = datetime.now(timezone.utc).isoformat()
    return result
