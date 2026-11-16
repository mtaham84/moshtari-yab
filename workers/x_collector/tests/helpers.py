"""Owned query inputs for transport and scheduling tests."""


def owned_queries(items):
    queries = []
    for item in items:
        query = dict(item) if isinstance(item, dict) else {'query': item}
        if not (query.get('business_ids') or query.get('business_id')):
            query['business_id'] = '9'
        queries.append(query)
    return queries
