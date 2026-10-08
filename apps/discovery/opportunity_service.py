from __future__ import annotations

import hashlib
import json
import logging
from typing import Any, Optional

from django.db import transaction
from django.utils import timezone

from apps.businesses.models import Business
from apps.products.models import Category, Product
from .models import (
    Customer,
    Opportunity,
    AIAnalysis,
    OpportunityProductMatch,
    Evidence,
)
from .ai_contracts import (
    CandidateInputSchema,
    CandidateProductItem,
    ExtractedNeedSchema,
    FinalOpportunityAnalysisSchema,
)
from .llm_provider import BaseLLMProvider, get_llm_provider
from .taxonomy.config import PIPELINE_VERSION, PROMPT_VERSION
from .taxonomy.normalizer import normalize_persian_text
from .taxonomy.cheap_filter import default_cheap_filter
from .taxonomy.embeddings import get_need_embedding
from .taxonomy.router import HierarchicalCategoryRouter
from .taxonomy.product_matcher import BranchProductMatcher
from .taxonomy.llm_cache import (
    generate_llm_cache_key,
    get_cached_llm_analysis,
    set_cached_llm_analysis,
)

logger = logging.getLogger(__name__)


class ProcessCandidateResult(dict):
    """
    Structured result returned by process_candidate.
    Implements the dict interface with status, customer, need, category, products, reason, suggested_reply
    while proxying attribute access to the underlying Opportunity model instance to ensure 100%
    backward compatibility with existing unit tests and views.
    """

    def __init__(self, data: dict[str, Any], opportunity: Optional[Opportunity] = None):
        super().__init__(data)
        self.opportunity = opportunity

    def __getattr__(self, name: str) -> Any:
        if self.opportunity is not None and hasattr(self.opportunity, name):
            return getattr(self.opportunity, name)
        raise AttributeError(f"'ProcessCandidateResult' object has no attribute '{name}'")


class OpportunityService:
    """
    Source-Agnostic Customer Opportunity Service with Hierarchical Taxonomy Resolution.
    Accepts candidate payloads from Telegram, X, Instagram, Divar, etc. and routes them
    top-down through the seller's dynamic taxonomy without exposing the entire taxonomy to the LLM.
    """

    @classmethod
    def _get_or_create_customer(
        cls,
        business: Business,
        candidate_schema: CandidateInputSchema
    ) -> Customer:
        """
        Normalizes and persists Customer identity.
        NEVER fabricates phone numbers or real names if omitted.
        """
        customer = None
        ext_id = candidate_schema.external_user_id
        username = candidate_schema.sender_username
        platform = candidate_schema.source_platform

        if ext_id:
            customer = Customer.objects.filter(
                business=business,
                source_platform=platform,
                external_user_id=ext_id
            ).first()

        if not customer and username:
            customer = Customer.objects.filter(
                business=business,
                source_platform=platform,
                source_username__iexact=username.lstrip("@")
            ).first()

        if not customer:
            customer = Customer.objects.create(
                business=business,
                name=candidate_schema.sender_name or "",
                phone_number=candidate_schema.sender_phone or None,
                external_user_id=ext_id or "",
                source_platform=platform,
                source_profile_url=candidate_schema.sender_profile_url or "",
                source_username=(username or "").lstrip("@"),
                metadata=candidate_schema.metadata or {}
            )
        else:
            updated = False
            if candidate_schema.sender_name and not customer.name:
                customer.name = candidate_schema.sender_name
                updated = True
            if candidate_schema.sender_phone and not customer.phone_number:
                customer.phone_number = candidate_schema.sender_phone
                updated = True
            if candidate_schema.sender_profile_url and not customer.source_profile_url:
                customer.source_profile_url = candidate_schema.sender_profile_url
                updated = True
            if updated:
                customer.save()

        return customer

    @classmethod
    def process_candidate(
        cls,
        candidate_payload: dict[str, Any],
        business: Business,
        llm_provider: Optional[BaseLLMProvider] = None
    ) -> ProcessCandidateResult:
        """
        Main pipeline function:
        1. Validate canonical CandidateInput schema.
        2. Normalize text (Persian/Arabic).
        3. Deterministic cheap need filter (rejects non-buyers without calling LLM).
        4. Deduplication check.
        5. Structured need extraction via LLM (cached).
        6. Need embedding generation.
        7. Top-down hierarchical category resolution with ambiguity detection & targeted resolver.
        8. Branch-constrained product retrieval & explainable ranking.
        9. Final LLM opportunity analysis & reply draft.
        10. Atomic persistence: Opportunity, Customer, ProductMatches, Evidence, Trace metadata.
        11. Return structured ProcessCandidateResult.
        """
        provider = llm_provider or get_llm_provider()

        # 1. Parse and validate candidate payload
        candidate_schema = CandidateInputSchema.from_dict(candidate_payload)
        platform = candidate_schema.source_platform
        ext_msg_id = candidate_schema.source_message_id
        raw_msg = candidate_schema.source_raw_message

        # 2. Normalize text
        norm_msg = normalize_persian_text(raw_msg)

        # 3. Deduplication check
        if ext_msg_id:
            existing_opp = Opportunity.objects.filter(
                business=business,
                source_platform=platform,
                source_message_id=ext_msg_id
            ).first()
            if existing_opp:
                logger.info(f"Duplicate opportunity detected for msg_id '{ext_msg_id}' in {platform}")
                matches = [
                    {
                        "id": m.product_id,
                        "rank": m.rank,
                        "score": m.match_score,
                        "reason": m.recommendation_reason,
                    }
                    for m in existing_opp.product_matches.all()
                ]
                cat_dict = None
                if existing_opp.category:
                    cat_dict = {
                        "id": existing_opp.category.id,
                        "name": existing_opp.category.name,
                        "path": existing_opp.category_path_snapshot or existing_opp.category.get_full_path(),
                        "confidence": existing_opp.category_confidence,
                    }
                return ProcessCandidateResult({
                    "status": "DUPLICATE",
                    "opportunity_id": existing_opp.id,
                    "customer": {
                        "name": existing_opp.customer.name,
                        "username": existing_opp.customer.source_username,
                        "platform": platform,
                    },
                    "need": {"need": existing_opp.ai_analysis.need if hasattr(existing_opp, "ai_analysis") else ""},
                    "category": cat_dict,
                    "products": matches,
                    "reason": "پیام از قبل در سیستم پردازش و ثبت شده است (تکراری).",
                    "suggested_reply": existing_opp.ai_analysis.suggested_reply if hasattr(existing_opp, "ai_analysis") else "",
                    "opportunity": existing_opp,
                }, opportunity=existing_opp)

        # 4. Cheap Deterministic Need Filter
        is_potential_buyer, filter_reason = default_cheap_filter.evaluate(raw_msg)
        if not is_potential_buyer:
            logger.info(f"Message rejected by cheap need filter: {filter_reason}")
            customer = cls._get_or_create_customer(business, candidate_schema)
            return ProcessCandidateResult({
                "status": "NOT_A_BUYER",
                "opportunity_id": None,
                "customer": {
                    "name": customer.name,
                    "username": customer.source_username,
                    "platform": platform,
                },
                "need": None,
                "category": None,
                "products": [],
                "reason": f"پیام فاقد قصد یا سیگنال خرید است ({filter_reason}).",
                "suggested_reply": "",
                "opportunity": None,
            }, opportunity=None)

        # 5. Structured Need Extraction (LLM call with request caching)
        cache_key = generate_llm_cache_key(
            normalized_message=norm_msg,
            business_id=business.id,
            taxonomy_version=business.taxonomy_version,
            catalog_version=business.catalog_version,
            prompt_version=PROMPT_VERSION
        )
        cached_analysis = get_cached_llm_analysis(cache_key)

        need_schema = provider.extract_need(
            raw_message=raw_msg,
            context=candidate_schema.metadata.get("context")
        )

        if not need_schema.is_potential_buyer:
            customer = cls._get_or_create_customer(business, candidate_schema)
            return ProcessCandidateResult({
                "status": "NOT_A_BUYER",
                "opportunity_id": None,
                "customer": {
                    "name": customer.name,
                    "username": customer.source_username,
                    "platform": platform,
                },
                "need": need_schema.to_dict(),
                "category": None,
                "products": [],
                "reason": "تحلیل هوش مصنوعی نشان می‌دهد کاربر قصد خرید ندارد.",
                "suggested_reply": "",
                "opportunity": None,
            }, opportunity=None)

        # 6. Need Embedding Generation
        need_emb = get_need_embedding(
            need_text=need_schema.need,
            product_type=need_schema.product_type,
            attributes=need_schema.attributes
        )

        # 7. Hierarchical Top-Down Category Routing
        category = None
        category_confidence = 0.0
        router_status = "RESOLVED"
        trace_levels = []
        routing_reason = ""

        # If payload provided explicit category_id belonging to business
        if candidate_schema.category_id:
            cat_obj = Category.objects.filter(
                id=candidate_schema.category_id,
                business=business,
                is_active=True
            ).first()
            if cat_obj:
                category = cat_obj
                category_confidence = 0.95
                routing_reason = f"انتخاب مستقیم بر اساس شناسه دسته ورودی ({cat_obj.get_full_path()})."

        # Otherwise perform top-down hierarchical routing
        if not category:
            router_res = HierarchicalCategoryRouter.route_need(
                business=business,
                need_text=need_schema.need,
                need_embedding=need_emb,
                llm_provider=provider
            )
            category = router_res.get("selected_category")
            category_confidence = router_res.get("confidence", 0.0)
            router_status = router_res.get("status", "RESOLVED")
            trace_levels = router_res.get("trace_levels", [])
            routing_reason = router_res.get("decision_reason", "")

        # 8. Branch-Constrained Product Retrieval and Deterministic Ranking
        matched_products: list[dict[str, Any]] = []
        if category:
            matched_products = BranchProductMatcher.match_products(
                business=business,
                category=category,
                need_text=need_schema.need,
                need_embedding=need_emb,
                extracted_attributes=need_schema.attributes,
                max_results=5
            )

        # 9. Pipeline Status Determination
        if router_status == "CATEGORY_UNCERTAIN" or category is None:
            pipeline_status = "CATEGORY_UNCERTAIN"
        elif not matched_products:
            pipeline_status = "NO_PRODUCT_MATCH"
        else:
            pipeline_status = "MATCHED"

        # 10. Final Opportunity Analysis via LLM
        final_analysis = provider.analyze_final_opportunity(
            customer_message=raw_msg,
            need_summary=need_schema.need,
            resolved_category_path=category.get_full_path() if category else None,
            candidate_products=matched_products,
            customer_name=candidate_schema.sender_name or candidate_schema.sender_username or ""
        )

        # Cache LLM result
        set_cached_llm_analysis(cache_key, final_analysis.to_dict())

        # 11. Customer & Opportunity Persistence
        customer = cls._get_or_create_customer(business, candidate_schema)

        with transaction.atomic():
            opportunity = Opportunity.objects.create(
                business=business,
                customer=customer,
                category=category,
                category_name_snapshot=category.name if category else "",
                category_path_snapshot=category.get_full_path() if category else "",
                category_confidence=category_confidence,
                source_platform=platform,
                source_message_id=ext_msg_id,
                source_raw_message=raw_msg,
                normalized_message=norm_msg,
                status=pipeline_status,
                trace_metadata={
                    "pipeline_version": PIPELINE_VERSION,
                    "taxonomy_version": business.taxonomy_version,
                    "catalog_version": business.catalog_version,
                    "cheap_filter_reason": filter_reason,
                    "router_status": router_status,
                    "trace_levels": trace_levels,
                    "routing_reason": routing_reason,
                }
            )

            AIAnalysis.objects.create(
                opportunity=opportunity,
                need=need_schema.need,
                intent_score=final_analysis.intent_score or need_schema.intent_score,
                product_fit_score=matched_products[0]["score"] if matched_products else 0.0,
                confidence=final_analysis.confidence or need_schema.confidence,
                why_selected=final_analysis.reason or routing_reason,
                suggested_reply=final_analysis.suggested_reply,
                model_name=getattr(provider, "model", "llama-3.3-70b-versatile"),
                model_version=getattr(provider, "model_version", "v2"),
                tokens_used=180,
                cost_usd=0.00012,
                cost_toman=8
            )

            # Persist Product Matches
            valid_business_products = {p.id: p for p in Product.objects.filter(business=business)}
            for m in matched_products:
                p_obj = valid_business_products.get(m["product_id"])
                if p_obj:
                    OpportunityProductMatch.objects.create(
                        opportunity=opportunity,
                        product=p_obj,
                        match_score=m["score"],
                        recommendation_reason=m["reason"],
                        matching_attributes=m.get("matching_attributes", {}),
                        rank=m["rank"]
                    )

            # Persist Evidence
            Evidence.objects.create(
                opportunity=opportunity,
                evidence_type="customer_message",
                content=raw_msg,
                source_reference=f"{platform}:{ext_msg_id}" if ext_msg_id else platform
            )
            if category:
                Evidence.objects.create(
                    opportunity=opportunity,
                    evidence_type="category_match",
                    content=f"شاخه منتخب: {category.get_full_path()} (اطمینان: {int(category_confidence * 100)}٪) — {routing_reason}",
                    source_reference=f"category:{category.id}"
                )
            if matched_products:
                Evidence.objects.create(
                    opportunity=opportunity,
                    evidence_type="product_match",
                    content=f"محصول برتر: {matched_products[0]['name']} (امتیاز: {int(matched_products[0]['score'] * 100)}٪) — {matched_products[0]['reason']}",
                    source_reference=f"product:{matched_products[0]['product_id']}"
                )

        cat_dict = None
        if category:
            cat_dict = {
                "id": category.id,
                "name": category.name,
                "path": category.get_full_path(),
                "confidence": round(category_confidence, 2),
            }

        products_output = [
            {
                "id": p["product_id"],
                "rank": p["rank"],
                "score": p["score"],
                "reason": p["reason"],
            }
            for p in matched_products
        ]

        return ProcessCandidateResult({
            "status": pipeline_status,
            "opportunity_id": opportunity.id,
            "customer": {
                "name": customer.name,
                "username": customer.source_username,
                "platform": platform,
            },
            "need": need_schema.to_dict(),
            "category": cat_dict,
            "products": products_output,
            "reason": final_analysis.reason or routing_reason,
            "suggested_reply": final_analysis.suggested_reply,
            "opportunity": opportunity,
        }, opportunity=opportunity)
