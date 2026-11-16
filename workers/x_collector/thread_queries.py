"""Schedule public replies to accepted X leads through the existing search cycle.

Replies share wallet checks, account rotation, Top/week filtering and daily caps
with topic searches. No separate client or unbounded thread loop is used.
"""
import json

from need_engine.sources import _ident
from .cli_mapping import THREAD_SEARCH_QUERY_PREFIX


def load_thread_queries(config, db, products):
    schema, crawler = _ident(config.state_schema), _ident(config.crawler_schema)
    if not products:
        return []
    rows = db.all(f'''SELECT r.root_tweet_id, o.payload
        FROM {schema}.x_thread_requests r
        JOIN {schema}.opportunities o ON o.opportunity_id = r.opportunity_id
        JOIN {crawler}.x_posts p ON p.tweet_id = r.root_tweet_id
        WHERE o.status = 'open'
          AND p.created_at >= now() - (%s * interval '1 hour')
        ORDER BY r.created_at DESC LIMIT 100''', (config.x_thread_max_age_hours,))
    owners = {str(p.product_id): str(p.business_id) for p in products}
    queries = []
    for row in rows:
        payload = row['payload']
        if isinstance(payload, str):
            payload = json.loads(payload)
        ids = sorted({str(item['product_id']) for item in payload.get('matched_products', [])
                      if str(item.get('product_id')) in owners})
        root = str(row['root_tweet_id'])
        if not ids or not root.isascii() or not root.isdecimal():
            continue
        queries.append({'query': THREAD_SEARCH_QUERY_PREFIX + root, 'kind': 'reply', 'priority': 1.0,
                        'thread_root_id': root, 'product_ids': ids,
                        'business_ids': sorted({owners[pid] for pid in ids}),
                        'product_business_ids': {pid: owners[pid] for pid in ids}})
    return queries
