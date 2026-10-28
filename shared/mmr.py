r"""
Maximal Marginal Relevance (MMR) for Query and Keyword Selection.

MMR balances relevance to a target context with diversity among selected candidates:
    MMR = argmax_{d in C \ S} [ lambda * Sim(d, Target) - (1 - lambda) * max_{s in S} Sim(d, s) ]

- lambda = 1.0 -> purely maximum relevance (no diversity)
- lambda = 0.0 -> purely maximum diversity
- lambda = 0.65 -> optimal balance of high domain relevance and varied topics
"""
from typing import Any, Callable, Dict, List, Optional, Set
import re
import unicodedata


def clean_persian_token(token: str) -> str:
    """Normalize Persian characters (ی/ک) and lowercase."""
    text = token.strip().lower()
    text = text.replace("ي", "ی").replace("ك", "ک").replace("ة", "ه")
    text = text.replace("‌", "")  # remove zero-width non-joiner for matching
    return text


def tokenize_for_similarity(text: str) -> Set[str]:
    """
    Tokenizes text into words and character tri-grams to capture
    both semantic words and Persian morphological variations.
    """
    if not text:
        return set()

    # Replace punctuation and special characters with spaces
    cleaned = re.sub(r"[\"“”«»()\[\]{}:*#@/\-_،,!?؟.;=+&^%$#!~`\\]", " ", text)
    words = [clean_persian_token(w) for w in cleaned.split() if len(w) >= 2]

    tokens: Set[str] = set()
    for w in words:
        tokens.add(w)
        # Add character tri-grams for words >= 4 characters
        if len(w) >= 4:
            for i in range(len(w) - 2):
                tokens.add(w[i:i + 3])

    return tokens


def jaccard_similarity(set_a: Set[str], set_b: Set[str]) -> float:
    """Computes Jaccard similarity coefficient between two token sets."""
    if not set_a or not set_b:
        return 0.0
    intersection = len(set_a & set_b)
    union = len(set_a | set_b)
    return intersection / union if union > 0 else 0.0


def select_by_mmr(
    candidates: List[Any],
    query_text_fn: Callable[[Any], str],
    reference_context: str,
    top_k: int = 5,
    lambda_param: float = 0.65,
) -> List[Any]:
    """
    Selects top_k items from candidates using Maximal Marginal Relevance (MMR).

    Args:
        candidates: List of candidate objects (strings, dicts, or model instances).
        query_text_fn: Function to extract textual representation from a candidate.
        reference_context: The reference target text (e.g. business products, description).
        top_k: Maximum number of diverse, relevant items to select.
        lambda_param: Weight between relevance (1.0) and diversity (0.0).

    Returns:
        List of selected candidates ordered by selection rank.
    """
    if not candidates:
        return []

    if len(candidates) <= top_k:
        return list(candidates)

    ref_tokens = tokenize_for_similarity(reference_context)

    # Pre-tokenize all candidates and compute relevance score to reference
    candidate_tokens: List[Set[str]] = []
    relevance_scores: List[float] = []

    for item in candidates:
        text = query_text_fn(item)
        tokens = tokenize_for_similarity(text)
        candidate_tokens.append(tokens)
        sim = jaccard_similarity(tokens, ref_tokens) if ref_tokens else 1.0
        relevance_scores.append(sim)

    selected_indices: List[int] = []
    remaining_indices = list(range(len(candidates)))

    # Step 1: Pick the candidate with the highest relevance to reference
    best_initial_idx = max(remaining_indices, key=lambda i: relevance_scores[i])
    selected_indices.append(best_initial_idx)
    remaining_indices.remove(best_initial_idx)

    # Step 2: Iteratively pick remaining candidates using MMR
    while remaining_indices and len(selected_indices) < top_k:
        best_mmr_score = -float("inf")
        best_candidate_idx = remaining_indices[0]

        for idx in remaining_indices:
            rel = relevance_scores[idx]

            # Max similarity to already selected candidates
            max_sim_to_selected = 0.0
            for sel_idx in selected_indices:
                sim = jaccard_similarity(candidate_tokens[idx], candidate_tokens[sel_idx])
                if sim > max_sim_to_selected:
                    max_sim_to_selected = sim

            # MMR equation
            mmr_score = (lambda_param * rel) - ((1.0 - lambda_param) * max_sim_to_selected)

            if mmr_score > best_mmr_score:
                best_mmr_score = mmr_score
                best_candidate_idx = idx

        selected_indices.append(best_candidate_idx)
        remaining_indices.remove(best_candidate_idx)

    return [candidates[i] for i in selected_indices]
