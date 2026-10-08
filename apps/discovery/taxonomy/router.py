from __future__ import annotations

import logging
from typing import Any, Optional

from apps.businesses.models import Business
from apps.products.models import Category
from .config import (
    CATEGORY_TOP_K,
    CATEGORY_HIGH_CONFIDENCE,
    CATEGORY_MIN_CONFIDENCE,
    CATEGORY_AMBIGUITY_MARGIN,
    MAX_HIERARCHY_DEPTH,
)
from .cache import get_seller_taxonomy
from .embeddings import cosine_similarity
from .normalizer import normalize_persian_text
from .resolver import LLMAmbiguityResolver

logger = logging.getLogger(__name__)


def compute_category_match_score(
    need_text: str,
    need_embedding: list[float],
    category_data: dict[str, Any]
) -> float:
    """
    Computes a robust hybrid match score between the customer's need and a category.
    Combines dense semantic vector similarity with token overlap, descendant subcategories,
    branch products, and exact keyword/name matching.
    """
    c_emb = category_data.get("embedding") or []
    cos_sim = cosine_similarity(need_embedding, c_emb) if c_emb else 0.50

    norm_need = normalize_persian_text(need_text)
    c_name_norm = category_data.get("normalized_name") or normalize_persian_text(category_data.get("name", ""))

    # 1. Direct match of category name in normalized need
    if c_name_norm and c_name_norm in norm_need:
        return max(cos_sim, 0.94)

    # 2. Match with descendant category names (the branch contains a matching subcategory)
    descendant_names = category_data.get("descendant_names") or []
    for dn in descendant_names:
        dn_norm = normalize_persian_text(dn)
        if dn_norm and dn_norm in norm_need:
            return max(cos_sim, 0.93)

    # 3. Match with product names in this branch
    product_names = category_data.get("branch_product_names") or []
    for pn in product_names:
        pn_norm = normalize_persian_text(pn)
        if pn_norm and pn_norm in norm_need:
            return max(cos_sim, 0.92)
        # Check if 2+ word product phrase matches
        pn_tokens = [t for t in pn_norm.split() if len(t) >= 2]
        need_tokens = [t for t in norm_need.split() if len(t) >= 2]
        if len(pn_tokens) >= 2:
            overlap = sum(1 for pt in pn_tokens if pt in need_tokens)
            if overlap >= 2:
                return max(cos_sim, 0.91)

    # 4. Check category keywords or branch keywords
    keywords = category_data.get("keywords") or []
    for kw in keywords:
        kw_norm = normalize_persian_text(kw)
        if kw_norm and kw_norm in norm_need:
            return max(cos_sim, 0.90)

    branch_keywords = category_data.get("branch_keywords") or []
    for bkw in branch_keywords:
        bkw_norm = normalize_persian_text(bkw)
        if bkw_norm and bkw_norm in norm_need:
            return max(cos_sim, 0.89)

    # 5. Token overlap coverage with category name
    cat_tokens = [t for t in c_name_norm.split() if len(t) >= 2]
    need_tokens = [t for t in norm_need.split() if len(t) >= 2]
    if cat_tokens and need_tokens:
        overlap = sum(1 for ct in cat_tokens if any(ct in nt or nt in ct for nt in need_tokens))
        coverage = overlap / len(cat_tokens)
        if coverage >= 0.5:
            token_score = 0.70 + (coverage * 0.24)
            return max(cos_sim, token_score)

    # 6. Token overlap coverage with descendant subcategories
    if descendant_names and need_tokens:
        for dn in descendant_names:
            dn_tokens = [t for t in normalize_persian_text(dn).split() if len(t) >= 2]
            if dn_tokens:
                overlap = sum(1 for dt in dn_tokens if any(dt in nt or nt in dt for nt in need_tokens))
                coverage = overlap / len(dn_tokens)
                if coverage >= 0.5:
                    token_score = 0.68 + (coverage * 0.24)
                    return max(cos_sim, token_score)

    return cos_sim


class HierarchicalCategoryRouter:
    """
    Top-down hierarchical semantic routing pipeline.
    Traverses the seller's dynamic taxonomy from roots down to leaves (1 to 5 levels).
    """

    @classmethod
    def route_need(
        cls,
        business: Business,
        need_text: str,
        need_embedding: list[float],
        llm_provider: Optional[Any] = None
    ) -> dict[str, Any]:
        """
        Executes hierarchical top-down routing.
        Returns a dict:
        {
            "selected_category": Optional[Category],
            "category_id": Optional[int],
            "full_path": str,
            "confidence": float,
            "status": "RESOLVED" | "CATEGORY_UNCERTAIN" | "NO_TAXONOMY",
            "decision_reason": str,
            "trace_levels": list[dict],
            "llm_resolver_called": bool,
        }
        """
        taxonomy = get_seller_taxonomy(business)
        roots = taxonomy.get("roots", [])
        categories_by_id = taxonomy.get("categories_by_id", {})
        children_map = taxonomy.get("children_map", {})

        if not roots:
            return {
                "selected_category": None,
                "category_id": None,
                "full_path": "",
                "confidence": 0.0,
                "status": "NO_TAXONOMY",
                "decision_reason": "کسب‌وکار فاقد هرگونه دسته‌بندی فعال است.",
                "trace_levels": [],
                "llm_resolver_called": False,
            }

        trace_levels: list[dict[str, Any]] = []
        llm_resolver_called = False
        current_candidates = roots
        current_depth = 1
        last_winner: Optional[dict[str, Any]] = None
        final_confidence: float = 0.0
        final_reason: str = ""

        while current_candidates and current_depth <= MAX_HIERARCHY_DEPTH:
            # 1. Calculate similarity for each candidate at this level
            scored_candidates: list[dict[str, Any]] = []
            for c in current_candidates:
                sim = compute_category_match_score(need_text, need_embedding, c)
                scored_candidates.append({
                    **c,
                    "similarity": round(sim, 4),
                })

            scored_candidates.sort(key=lambda x: -x["similarity"])
            top_k_candidates = scored_candidates[:CATEGORY_TOP_K]

            top1 = top_k_candidates[0]
            top2 = top_k_candidates[1] if len(top_k_candidates) > 1 else None

            level_trace = {
                "depth": current_depth,
                "candidates_evaluated": len(scored_candidates),
                "top_candidates": [
                    {"id": c["id"], "name": c["name"], "similarity": c["similarity"]}
                    for c in top_k_candidates
                ],
                "ambiguity_resolved": False,
            }

            # 2. Check for Ambiguity between Top 1 and Top 2
            winner = top1
            if top2 and (top1["similarity"] - top2["similarity"]) <= CATEGORY_AMBIGUITY_MARGIN:
                # Ambiguous: Call targeted LLM resolver with ONLY top candidates
                level_trace["ambiguity_detected"] = True
                llm_resolver_called = True
                selected_id, conf, reason = LLMAmbiguityResolver.resolve_ambiguity(
                    customer_need=need_text,
                    candidates=top_k_candidates,
                    llm_provider=llm_provider
                )
                level_trace["ambiguity_resolved"] = True
                level_trace["resolver_selected_id"] = selected_id
                level_trace["resolver_reason"] = reason

                if selected_id and selected_id in categories_by_id:
                    winner = categories_by_id[selected_id]
                    winner["similarity"] = max(conf, winner.get("similarity", 0.85))
                else:
                    winner = top1

            level_trace["winner_id"] = winner["id"]
            level_trace["winner_name"] = winner["name"]
            level_trace["winner_similarity"] = winner["similarity"]
            trace_levels.append(level_trace)

            last_winner = winner
            final_confidence = winner["similarity"]

            # 3. Check Confidence Threshold
            # At root level, if confidence is clearly below MIN_CONFIDENCE, abstain immediately
            if current_depth == 1 and winner["similarity"] < CATEGORY_MIN_CONFIDENCE:
                logger.info(
                    f"Root confidence {winner['similarity']} below minimum {CATEGORY_MIN_CONFIDENCE}. Abstaining."
                )
                return {
                    "selected_category": None,
                    "category_id": None,
                    "full_path": "",
                    "confidence": final_confidence,
                    "status": "CATEGORY_UNCERTAIN",
                    "decision_reason": f"اطمینان تطابق با دسته‌بندی‌های اصلی ({final_confidence:.2f}) کمتر از حد مجاز است.",
                    "trace_levels": trace_levels,
                    "llm_resolver_called": llm_resolver_called,
                }

            # 4. Check if Winner is a Leaf or if children exist
            child_ids = children_map.get(winner["id"], [])
            if not child_ids:
                # Reached leaf at current depth (can be depth 1, 2, 3, 4, 5)
                final_reason = f"شاخه نهایی «{winner['full_path']}» در سطح {current_depth} بدون فرزند انتخاب گردید."
                break

            # 5. Move down to children of winner
            current_candidates = [categories_by_id[cid] for cid in child_ids if cid in categories_by_id]
            current_depth += 1

        if not last_winner:
            return {
                "selected_category": None,
                "category_id": None,
                "full_path": "",
                "confidence": 0.0,
                "status": "CATEGORY_UNCERTAIN",
                "decision_reason": "عدم یافتن دسته‌بندی منطبق.",
                "trace_levels": trace_levels,
                "llm_resolver_called": llm_resolver_called,
            }

        # 6. Validate Category in Database
        validated_category = cls._validate_category(business, last_winner["id"])
        if not validated_category:
            return {
                "selected_category": None,
                "category_id": None,
                "full_path": "",
                "confidence": 0.0,
                "status": "CATEGORY_UNCERTAIN",
                "decision_reason": "شناسه دسته‌بندی توسط سیستم اعتبارسنجی پایتون رد شد.",
                "trace_levels": trace_levels,
                "llm_resolver_called": llm_resolver_called,
            }

        return {
            "selected_category": validated_category,
            "category_id": validated_category.id,
            "full_path": validated_category.get_full_path(),
            "confidence": round(final_confidence, 2),
            "status": "RESOLVED",
            "decision_reason": final_reason or f"تطابق موفق با شاخه «{validated_category.get_full_path()}».",
            "trace_levels": trace_levels,
            "llm_resolver_called": llm_resolver_called,
        }

    @classmethod
    def _validate_category(cls, business: Business, category_id: int) -> Optional[Category]:
        """
        Strict Python verification:
        1. Category exists.
        2. Belongs to current business (seller isolation).
        3. Is active.
        4. Valid depth <= 5.
        """
        cat = Category.objects.filter(
            id=category_id,
            business=business,
            is_active=True
        ).first()

        if not cat:
            logger.warning(f"Category {category_id} does not exist or belong to business {business.id}")
            return None

        if cat.depth > MAX_HIERARCHY_DEPTH:
            logger.warning(f"Category {category_id} exceeds maximum depth {MAX_HIERARCHY_DEPTH}")
            return None

        return cat
