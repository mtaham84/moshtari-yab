from __future__ import annotations

import os
import re
import time
from dataclasses import asdict
from datetime import datetime, timezone
from typing import Any

from .ai import GrokAnalyzer
from .cache import DiscoveryCache
from .jobs import JobStore
from .core import Analysis, JobResult, ProductProfile, SourceRecord, cheap_candidate, local_classify, normalize_text, rank_analysis
from .sources import DivarAdapter, MockAdapter, build_x_adapter

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


def run_job(profile: ProductProfile, sources: list[str], mode: str, max_queries: int = 8, job_id: str | None = None, job_store: JobStore | None = None) -> JobResult:
    result = JobResult(product=asdict(profile))
    store = job_store or JobStore()
    if job_id is None:
        job_id = store.create({"profile": asdict(profile), "sources": sources, "mode": mode, "max_queries": max_queries, "stage": "SELLER", "query_index": 0, "records": [], "vocabulary": [], "queries": []})
    state = store.get(job_id)
    if state is None:
        raise KeyError(f"Unknown job: {job_id}")
    result.job_id = job_id
    result.started_at = state["created_at"]
    previous_result = state.get("result") or {}
    for field_name in ("records_collected", "buyers_found", "sellers_filtered", "duplicates_removed", "grok_calls", "grok_prompt_tokens", "grok_completion_tokens", "grok_estimated_cost_usd", "cache_hits"):
        setattr(result, field_name, previous_result.get(field_name, getattr(result, field_name)))
    started = time.monotonic()
    max_requests = int(os.getenv("MAX_SOURCE_REQUESTS", "100"))
    max_records = int(os.getenv("MAX_TOTAL_RECORDS", "3000"))
    max_runtime = int(os.getenv("MAX_JOB_RUNTIME_SECONDS", "300"))
    grok_budget = int(os.getenv("MAX_GROK_REQUESTS", "40"))
    result.status, result.current_phase = "PREPARING_PRODUCT", "PREPARING_PRODUCT"
    use_mock = mode == "mock" or os.getenv("USE_MOCK_SOURCES", "false").lower() == "true"
    source_usage = state.get("source_usage", {})
    adapters = {name: MockAdapter(name) if use_mock else build_x_adapter(max_requests=max(0, max_requests - source_usage.get(name, 0))) if name == "x" else DivarAdapter(max_requests=max(0, int(os.getenv("DIVAR_MAX_REQUESTS_PER_JOB", "30")) - source_usage.get(name, 0))) for name in sources}
    records = [SourceRecord(**raw) for raw in state.get("records", [])]
    cache = DiscoveryCache()
    queries = state.get("queries", [])
    vocabulary = state.get("vocabulary", [])
    stage = state.get("stage", "SELLER")
    query_index = int(state.get("query_index", 0))

    def save_checkpoint(new_stage: str, next_index: int, phase: str, status: str = "RUNNING") -> None:
        raw_records = [asdict(record) for record in records]
        usage = {name: getattr(adapter, "requests_used", 0) + source_usage.get(name, 0) for name, adapter in adapters.items()}
        store.update(job_id, status=status, phase=phase, stage=new_stage, query_index=next_index, records=raw_records, vocabulary=vocabulary, queries=queries, result=asdict(result), source_usage=usage)

    def pause_if_requested(phase: str) -> bool:
        if store.stop_requested(job_id):
            result.status = "PAUSED"
            result.current_phase = phase
            result.records_collected = len(records)
            result.raw_records = [asdict(record) for record in records]
            result.finished_at = datetime.now(timezone.utc).isoformat()
            save_checkpoint(stage, query_index, phase, "PAUSED")
            return True
        return False
    try:
        if state["status"] in {"PAUSED", "STOP_REQUESTED", "PARTIAL_SUCCESS", "RUNNING"}:
            store.clear_stop(job_id)
        store.update(job_id, status="RUNNING", phase="PREPARING_PRODUCT")
        cached_vocabulary = cache.get(profile, context={"kind": "vocabulary"})
        result.cache_hits += int(bool(cached_vocabulary))
        if stage == "SELLER":
            seller_queries = [f'"{profile.product_name}" {word}' for word in SELLER_QUERY_WORDS[:3]] if "x" in adapters and not cached_vocabulary else []
            result.current_phase = "DISCOVERING_X_SELLERS"
            for index in range(query_index, len(seller_queries)):
                if pause_if_requested(result.current_phase): return result
                if time.monotonic() - started > max_runtime or len(records) >= max_records: break
                records.extend(adapters["x"].search(seller_queries[index]))
                query_index = index + 1
                save_checkpoint("SELLER", query_index, result.current_phase)
            vocabulary = cached_vocabulary.get("terms", []) if cached_vocabulary else discover_vocabulary(records, profile)
            if not cached_vocabulary:
                cache.set(profile, {"terms": vocabulary}, context={"kind": "vocabulary"})
            query_context = {"kind": "queries", "vocabulary": vocabulary, "max_queries": max_queries}
            cached_queries = cache.get(profile, context=query_context)
            result.cache_hits += int(bool(cached_queries))
            queries = cached_queries.get("queries", []) if cached_queries else make_queries(profile, vocabulary, max_queries)
            if not cached_queries:
                cache.set(profile, {"queries": queries}, context=query_context)
            stage, query_index = "BUYER", 0
            save_checkpoint(stage, query_index, "SEARCHING_X_BUYERS")
        else:
            query_context = {"kind": "queries", "vocabulary": vocabulary, "max_queries": max_queries}
            if not queries:
                cached_queries = cache.get(profile, context=query_context)
                queries = cached_queries.get("queries", []) if cached_queries else make_queries(profile, vocabulary, max_queries)
        source_query_pairs = [(source_name, query) for source_name in sources for query in (queries if source_name == "x" else [profile.product_name, *vocabulary][:max_queries])]
        result.current_phase = "SEARCHING_X_BUYERS"
        if stage != "ANALYSIS":
            for index in range(query_index, len(source_query_pairs)):
                if pause_if_requested(result.current_phase): return result
                if time.monotonic() - started > max_runtime or len(records) >= max_records: break
                source_name, query = source_query_pairs[index]
                records.extend(adapters[source_name].search(query))
                query_index = index + 1
                save_checkpoint("BUYER", query_index, result.current_phase)
        if query_index < len(source_query_pairs) and (len(records) >= max_records or time.monotonic() - started > max_runtime):
            result.status = "PARTIAL_SUCCESS"
            result.current_phase = "SEARCHING_X_BUYERS"
            save_checkpoint("BUYER", query_index, result.current_phase, "PARTIAL_SUCCESS")
            result.records_collected = len(records)
            result.raw_records = [asdict(record) for record in records]
            result.finished_at = datetime.now(timezone.utc).isoformat()
            return result
        stage, query_index = "ANALYSIS", 0
        result.records_collected = len(records)
        result.raw_records = [asdict(record) for record in records]
        save_checkpoint(stage, query_index, "ANALYZING")
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
        if pause_if_requested(result.current_phase): return result
        analyzer = None if use_mock else GrokAnalyzer()
        if analyzer and not analyzer.api_key:
            raise RuntimeError("Set XAI_API_KEY or run with --mode mock")
        if analyzer:
            analyzer.max_calls = max(0, grok_budget - result.grok_calls)
        if analyzer:
            classification_capacity = ((analyzer.max_calls + 1) // 2) * analyzer.batch_size
            ai_candidates = candidates[:classification_capacity]
            classifications = analyzer.classify_batch(ai_candidates)
            result.grok_calls += analyzer.calls
            classification_map = {str(item.get("id")): item for item in classifications}
            result.grok_prompt_tokens += analyzer.prompt_tokens
            result.grok_completion_tokens += analyzer.completion_tokens
            result.grok_estimated_cost_usd += analyzer.estimated_cost_usd
        else:
            classification_map = {r.source_id: {"intent": a.intent, "lead_direction": a.lead_direction, "confidence": a.confidence} for r in candidates if (a := local_classify(r, profile))}
        if analyzer:
            save_checkpoint("ANALYSIS", 0, "ANALYZING")
        eligible: list[tuple[SourceRecord, Analysis]] = []
        for record in candidates:
            classification = classification_map.get(record.source_id)
            analysis = local_classify(record, profile)
            if classification:
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
            result.grok_calls += analyzer.calls
            result.grok_prompt_tokens += analyzer.prompt_tokens
            result.grok_completion_tokens += analyzer.completion_tokens
            result.grok_estimated_cost_usd += analyzer.estimated_cost_usd
            detail_map = {str(item.get("id")): item for item in details}
            save_checkpoint("ANALYSIS", 0, "ANALYZING")
        else:
            detail_map = {}
        if pause_if_requested("RANKING"):
            return result
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
        store.update(job_id, status=result.status, phase="COMPLETED", stage="COMPLETED", query_index=0, result=asdict(result), records=[asdict(record) for record in records], vocabulary=vocabulary, queries=queries)
    except Exception as exc:
        result.errors.append(str(exc))
        if "RATE_LIMITED" in str(exc):
            result.status = "RATE_LIMITED"
        elif "QUOTA_EXCEEDED" in str(exc):
            result.status = "QUOTA_EXCEEDED"
        elif "AUTH" in str(exc) or "API key" in str(exc):
            result.status = "AUTH_REQUIRED"
        elif result.records_collected:
            result.status = "PARTIAL_SUCCESS"
        else:
            result.status = "FAILED"
        result.finished_at = datetime.now(timezone.utc).isoformat()
        try:
            save_checkpoint(stage, query_index, result.current_phase or "FAILED", result.status)
        except Exception:
            pass
    return result
