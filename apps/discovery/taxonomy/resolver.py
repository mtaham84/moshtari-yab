from __future__ import annotations

import json
import logging
from typing import Any, Optional

logger = logging.getLogger(__name__)


class LLMAmbiguityResolver:
    """
    Small, focused LLM resolver that is called ONLY when semantic category routing
    is ambiguous (e.g. difference between top two candidates <= AMBIGUITY_MARGIN).
    Sends ONLY the 2-3 candidate categories.
    The LLM must choose ONLY one of the supplied candidate IDs or return null.
    It can NEVER create a new category ID.
    """

    @classmethod
    def resolve_ambiguity(
        cls,
        customer_need: str,
        candidates: list[dict[str, Any]],
        llm_provider: Optional[Any] = None
    ) -> tuple[Optional[int], float, str]:
        """
        Resolves ambiguity among candidate categories.
        Returns:
            (selected_category_id: Optional[int], confidence: float, reason: str)
        """
        if not candidates:
            return None, 0.0, "NO_CANDIDATES"

        if len(candidates) == 1:
            return candidates[0]["id"], float(candidates[0].get("similarity", 0.90)), "SINGLE_CANDIDATE"

        candidate_ids = {c["id"] for c in candidates}

        # Build candidate representations for the LLM
        candidate_items = []
        for c in candidates:
            candidate_items.append({
                "id": c["id"],
                "name": c["name"],
                "full_path": c["full_path"],
                "description": c.get("description", ""),
                "keywords": c.get("keywords", []),
            })

        prompt_system = (
            "You are a strict category ambiguity resolver for Persian e-commerce.\n"
            "Given the customer's stated need and a small list of candidate categories, "
            "select the SINGLE MOST APPROPRIATE category ID from the list, or null if uncertain.\n"
            "CRITICAL: You MUST choose ONLY an ID from the candidate list or null. NEVER invent any ID.\n"
            "Return STRICT JSON only:\n"
            "{\n"
            '  "selected_category_id": <int or null>,\n'
            '  "confidence": <float between 0.0 and 1.0>,\n'
            '  "reason": "<short explanation in Persian>"\n'
            "}"
        )

        user_content = (
            f"Customer Need: {customer_need}\n\n"
            f"Candidate Categories:\n{json.dumps(candidate_items, ensure_ascii=False, indent=2)}\n\n"
            "Select the best category ID or null:"
        )

        # Execute resolution
        try:
            if llm_provider and hasattr(llm_provider, "resolve_category_ambiguity"):
                res = llm_provider.resolve_category_ambiguity(customer_need, candidate_items)
            else:
                # Deterministic fallback resolver based on highest keyword/path overlap
                res = cls._fallback_heuristic_resolve(customer_need, candidates)

            selected_id = res.get("selected_category_id")
            confidence = float(res.get("confidence", 0.0))
            reason = str(res.get("reason", ""))

            # Strict Python Validation
            if selected_id is not None:
                if selected_id not in candidate_ids:
                    logger.warning(
                        f"LLM hallucinated category ID {selected_id} not in candidate set {candidate_ids}. Rejecting."
                    )
                    return None, 0.0, f"REJECTED_HALLUCINATED_CATEGORY_ID_{selected_id}"

            return selected_id, confidence, reason

        except Exception as e:
            logger.error(f"Error in LLM ambiguity resolution: {e}")
            return cls._fallback_heuristic_resolve(customer_need, candidates)

    @classmethod
    def _fallback_heuristic_resolve(
        cls,
        customer_need: str,
        candidates: list[dict[str, Any]]
    ) -> dict[str, Any]:
        """Deterministic tie-breaker using token overlap with full_path."""
        tokens = set(customer_need.split())
        best_c = candidates[0]
        max_overlap = -1

        for c in candidates:
            c_text = f"{c['name']} {c['full_path']} {' '.join(c.get('keywords', []))}"
            overlap = sum(1 for tok in tokens if tok in c_text)
            if overlap > max_overlap:
                max_overlap = overlap
                best_c = c

        return {
            "selected_category_id": best_c["id"],
            "confidence": 0.88,
            "reason": f"انتخاب هوشمند دسته «{best_c['name']}» بر اساس انطباق دقیق‌تر با نیاز مشتری."
        }
