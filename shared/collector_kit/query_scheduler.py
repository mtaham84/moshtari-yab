"""X allocation by verified leads per completed search, with a small exploration share."""
from __future__ import annotations

import json

CREDIT_KEY = 'x_yield_allocation_v1'
EXPLORATION_SHARE = 0.05


def query_stats(row=None):
    alpha, beta, leads, runs, fetched, errors = row or (1.0, 1.0, 0, 0, 0, 0)
    searches = max(0, runs - errors)
    return {'alpha': alpha, 'beta': beta, 'leads': leads, 'runs': runs, 'fetched': fetched,
            'searches': searches, 'lead_rate': leads / searches if searches else 0.0}


def shares(queries, stats):
    rates = {item['query']: max(0.0, stats.get(item['query'], {}).get('lead_rate', 0.0)) for item in queries}
    if not rates:
        return {}
    total = sum(rates.values())
    if total == 0:
        return {query: 1 / len(rates) for query in rates}
    unexplored = sum(rate == 0 for rate in rates.values())
    reserve = EXPLORATION_SHARE if unexplored else 0.0
    return {query: (rate / total * (1 - reserve) if rate else reserve / unexplored)
            for query, rate in rates.items()}


def advance(credits, weights, selected=None):
    next_credits = {query: float(credits.get(query, 0)) + weight for query, weight in weights.items()}
    if not next_credits:
        return None, {}
    selected = selected or min(next_credits, key=lambda query: (-round(next_credits[query], 12), query))
    if selected not in next_credits:
        return None, credits
    next_credits[selected] -= 1
    return selected, next_credits


def plan(queries, budget, stats, saved_credits=None):
    weights = shares(queries, stats)
    credits = json.loads(saved_credits or '{}')
    by_query = {item['query']: item for item in queries}
    selected, used = [], set()
    for _ in range(max(0, budget)):
        query, next_credits = advance(credits, weights)
        # Preserve per-query spacing: continue the fair sequence in the next collection cycle.
        if query is None or query in used:
            break
        selected.append(by_query[query])
        used.add(query)
        credits = next_credits
    return selected


def allocated(queries, stats, saved_credits, selected):
    queries = [item for item in queries if not stats.get(item['query'], {}).get('dropped', False)]
    chosen, credits = advance(json.loads(saved_credits or '{}'), shares(queries, stats), selected)
    return json.dumps(credits, ensure_ascii=False, sort_keys=True) if chosen else None
