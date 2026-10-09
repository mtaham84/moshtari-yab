from __future__ import annotations


def score_query(opportunities: int, contacted: int = 0, ordered: int = 0, good_feedback: int = 0,
                bad_feedback: int = 0, prior_p: float = 0.03, prior_n: float = 30.0) -> float:
    successes = max(0.0, opportunities + contacted + 3 * ordered + 0.5 * good_feedback - 0.5 * bad_feedback)
    observations = max(0.0, opportunities + contacted + ordered + good_feedback + bad_feedback)
    return min(1.0, max(0.0, (prior_p * prior_n + successes) / (prior_n + observations)))


def query_weight(score: float, mean_score: float) -> float:
    if mean_score <= 0:
        return 1.0
    return min(3.0, max(0.2, score / mean_score))
