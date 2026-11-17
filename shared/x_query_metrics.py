"""Rolling-week X evidence shared by the panel and collector; one credit per seller/post."""
import re


def weekly_query_metrics(fetch, crawler, engine, product_ids, since):
    if not all(re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]*', name) for name in (crawler, engine)):
        raise ValueError('Invalid X metrics schema')
    pids = [str(pid) for pid in product_ids]
    if not pids:
        return {'queries': {}, 'reviewed': 0, 'received': 0}
    chats = ['x:p:' + pid for pid in pids]
    runs = fetch(f'''SELECT query, COUNT(*) AS runs, COALESCE(SUM(fetched),0) AS seen
        FROM (SELECT DISTINCT ran_at, query, fetched FROM {crawler}.x_search_runs
        WHERE product_id=ANY(%s) AND ran_at >= %s) r GROUP BY query''', (pids, since))
    queries = {r['query']: {**r, 'leads': 0} for r in runs}
    leads = fetch(f'''SELECT first_hit.query, COUNT(*) AS leads FROM
        (SELECT DISTINCT ON (h.tweet_id) h.tweet_id,h.query FROM {crawler}.x_post_products h
         JOIN {crawler}.x_posts p USING(tweet_id)
         WHERE h.product_id=ANY(%s) AND h.found_at >= %s AND p.created_at >= %s
         ORDER BY h.tweet_id,h.found_at,h.product_id) first_hit
        WHERE EXISTS (SELECT 1 FROM {engine}.x_hit_results r
            WHERE r.message_id=first_hit.tweet_id AND r.chat_id=ANY(%s)
            AND r.decided_at >= %s AND r.outcome='customer') GROUP BY first_hit.query''',
                  (pids, since, since, chats, since))
    for r in leads:
        queries.setdefault(r['query'], {'query': r['query'], 'runs': 0, 'seen': 0, 'leads': 0})['leads'] = r['leads']
    received = fetch(f'''SELECT COUNT(DISTINCT h.tweet_id) AS n FROM {crawler}.x_post_products h
        JOIN {crawler}.x_posts p USING(tweet_id) WHERE h.product_id=ANY(%s)
        AND h.found_at >= %s AND p.created_at >= %s''', (pids, since, since))[0]['n']
    review = fetch(f'''SELECT COUNT(DISTINCT h.message_id) AS n,MAX(h.decided_at) AS latest FROM {engine}.x_hit_results h
        JOIN {crawler}.x_posts p ON p.tweet_id=h.message_id
        WHERE h.chat_id=ANY(%s) AND h.decided_at >= %s AND p.created_at >= %s
        AND h.outcome IN ('customer','not_customer','not_fit')''', (chats, since, since))[0]
    return {'queries': queries, 'reviewed': int(review['n']), 'received': int(received),
            'watermark': str(review['latest']) if review['latest'] else None}
