from __future__ import annotations

import logging
from typing import Any, Optional

from apps.businesses.models import Business
from apps.products.models import Category, Product
from .config import (
    PRODUCT_SEMANTIC_WEIGHT,
    PRODUCT_CATEGORY_WEIGHT,
    PRODUCT_ATTRIBUTE_WEIGHT,
    PRODUCT_AVAILABILITY_WEIGHT,
    PRODUCT_PRICE_WEIGHT,
    PRODUCT_OTHER_WEIGHT,
)
from .embeddings import get_embedding_provider, cosine_similarity
from .normalizer import normalize_persian_text

logger = logging.getLogger(__name__)


def get_category_descendant_ids(category: Category) -> list[int]:
    """Returns IDs of category and all its descendants recursively."""
    descendant_ids = [category.id]
    to_visit = list(category.children.filter(is_active=True))
    while to_visit:
        child = to_visit.pop(0)
        descendant_ids.append(child.id)
        to_visit.extend(list(child.children.filter(is_active=True)))
    return descendant_ids


class BranchProductMatcher:
    """
    Branch-constrained product retriever and deterministic ranker.
    Constrains candidate products strictly to the selected category branch and ranks them
    using an explainable scoring formula.
    """

    @classmethod
    def match_products(
        cls,
        business: Business,
        category: Optional[Category],
        need_text: str,
        need_embedding: list[float],
        extracted_attributes: Optional[dict[str, Any]] = None,
        max_results: int = 5
    ) -> list[dict[str, Any]]:
        """
        Retrieves products strictly inside category branch and calculates deterministic ranking.
        """
        extracted_attributes = extracted_attributes or {}

        # 1. Base Queryset Constrained to Business and Active status
        qs = Product.objects.filter(
            business=business,
            status="ACTIVE",
            is_discovery_active=True
        ).select_related("category")

        # 2. Branch Isolation: Search ONLY inside selected category and descendants
        if category:
            branch_cat_ids = get_category_descendant_ids(category)
            qs = qs.filter(category_id__in=branch_cat_ids)
        else:
            # If no category resolved, return empty list (no hallucinated products)
            return []

        products = list(qs)
        if not products:
            logger.info(f"No active products found in category branch {category.get_full_path()}")
            return []

        provider = get_embedding_provider()
        ranked_items: list[dict[str, Any]] = []

        for p in products:
            # A. Semantic similarity (35%)
            prod_text = f"{p.name} {p.description or ''} {p.target_customer or ''}"
            p_emb = provider.embed_text(prod_text)
            semantic_score = cosine_similarity(need_embedding, p_emb)

            # B. Category compatibility (20%)
            # Exact category gets 1.0; direct parent gets 0.8
            if p.category_id == category.id:
                category_score = 1.0
            else:
                category_score = 0.80

            # C. Attribute match (20%)
            matching_attrs: dict[str, Any] = {}
            p_attrs = p.attributes or {}
            attr_score = 0.50  # Default neutral score if no attributes requested
            if extracted_attributes:
                matched_count = 0
                for k, v in extracted_attributes.items():
                    k_norm = normalize_persian_text(str(k))
                    v_norm = normalize_persian_text(str(v))
                    # Check in product attributes
                    for pk, pv in p_attrs.items():
                        if k_norm in normalize_persian_text(str(pk)) or v_norm in normalize_persian_text(str(pv)):
                            matching_attrs[pk] = pv
                            matched_count += 1
                            break
                    # Also check in product name / description
                    if v_norm in normalize_persian_text(prod_text):
                        matching_attrs[k] = v
                        matched_count += 1

                attr_score = min(1.0, matched_count / max(1, len(extracted_attributes)))
            elif p_attrs:
                # If customer had no specific attributes, grant moderate compatibility
                attr_score = 0.70

            # D. Availability & Priority (10%)
            priority = getattr(p, "discovery_priority", 1) or 1
            availability_score = min(1.0, 0.6 + (priority * 0.08))

            # E. Price compatibility (10%)
            price_score = 0.85  # Default reasonable price score

            # F. Other bonuses (5%)
            other_score = 0.80

            # Final weighted composite score
            final_score = (
                (semantic_score * PRODUCT_SEMANTIC_WEIGHT) +
                (category_score * PRODUCT_CATEGORY_WEIGHT) +
                (attr_score * PRODUCT_ATTRIBUTE_WEIGHT) +
                (availability_score * PRODUCT_AVAILABILITY_WEIGHT) +
                (price_score * PRODUCT_PRICE_WEIGHT) +
                (other_score * PRODUCT_OTHER_WEIGHT)
            )
            final_score = round(max(0.0, min(1.0, final_score)), 3)

            # Build explainable Persian reason
            reasons = []
            if p.category_id == category.id:
                reasons.append(f"تطابق مستقیم با دسته «{category.name}»")
            else:
                reasons.append(f"تعلق به زیرشاخه «{p.category.name if p.category else ''}»")

            if matching_attrs:
                attr_desc = "، ".join(f"{k}: {v}" for k, v in list(matching_attrs.items())[:2])
                reasons.append(f"همخوانی ویژگی‌ها ({attr_desc})")
            elif semantic_score >= 0.75:
                reasons.append("همپوشانی معنایی قوی با متن تقاضا")

            explanation = " • ".join(reasons)

            ranked_items.append({
                "product_id": p.id,
                "product_obj": p,
                "name": p.name,
                "price": float(p.price) if p.price else 0.0,
                "score": final_score,
                "semantic_score": round(semantic_score, 3),
                "matching_attributes": matching_attrs,
                "reason": explanation,
            })

        # Sort by score descending
        ranked_items.sort(key=lambda x: -x["score"])

        # Assign rank 1..K
        results = []
        for idx, item in enumerate(ranked_items[:max_results], start=1):
            item["rank"] = idx
            results.append(item)

        return results
