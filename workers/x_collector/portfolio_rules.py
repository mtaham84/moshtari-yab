"""Pure tenant-wide weekly evaluation; persistence and model calls live in portfolio.py."""
MIN_REVIEWED_POSTS = 3000
MIN_QUERY_SEARCHES = 5


def weekly_scores(rows):
    searches = sum(r.get('runs', 0) for r in rows)
    initial = sum(r.get('leads', 0) for r in rows) / searches if searches else .5
    return {r['query']: r.get('leads', 0) / r['runs'] if r.get('runs') else initial for r in rows}


def replacement_plan(rows, reviewed, candidates):
    if reviewed < MIN_REVIEWED_POSTS or len(rows) < 2:
        return []
    scores = weekly_scores(rows)
    tested = [r for r in rows if r.get('runs', 0) >= MIN_QUERY_SEARCHES]
    if not tested:
        return []
    best = max(scores[r['query']] for r in tested)
    anchor = max(tested, key=lambda r: (scores[r['query']], r['query']))['query']
    losers = sorted((r for r in tested if not r.get('required') and r['query'] != anchor
                     and (scores[r['query']] < best or best == 0)),
                    key=lambda r: (scores[r['query']], -r['runs'], r['query']))
    losers = losers[:max(1, len(rows) // 5)]
    existing = {r['query'] for r in rows}
    used, plan = set(), []
    for loser in losers:
        choice = next((c for c in candidates if c['domain'] == loser['domain'] and c['query'] not in existing | used), None)
        if choice is None:
            return []
        used.add(choice['query'])
        plan.append((loser['query'], choice))
    return plan
