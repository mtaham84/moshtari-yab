"""Seller-scoped weekly query evidence and the collector's actual published weights."""
from datetime import timedelta

from django.db import DatabaseError
from django.db.models import Count
from django.utils import timezone
import logging

from apps.billing.x_stats import x_collector_status
from apps.products.models import Product
from .models import Opportunity
from .x_activity import _all, _crawler_schema, _exists

log = logging.getLogger(__name__)


def combine_rows(business_id, runs, leads, generated):
    rows = {r['query']: {**r, 'leads': 0, 'score': None, 'weight_percent': None, 'eligible': None}
            for r in runs}
    for item in generated:
        if str(business_id) not in {str(v) for v in item.get('business_ids', [])}:
            continue
        row = rows.setdefault(item['query'], {'query': item['query'], 'runs': 0, 'seen': 0, 'leads': 0})
        row.update(score=item.get('score'), weight_percent=100 * item.get('weight', 0), eligible=item.get('eligible'))
    for item in leads:
        row = rows.setdefault(item['source_query'], {'query': item['source_query'], 'runs': 0, 'seen': 0,
                                                    'score': None, 'weight_percent': None, 'eligible': None})
        row['leads'] = int(item['leads'])
    for row in rows.values():
        row['weekly_score'] = row['leads'] / row['runs'] if row['runs'] else None
        row['is_thread'] = row['query'].startswith('conversation_id:')
    return sorted(rows.values(), key=lambda r: (-r.get('runs', 0), r['query']))


def query_report(business):
    since = timezone.now() - timedelta(days=7)
    pids = [str(v) for v in Product.objects.filter(business=business).values_list('pk', flat=True)]
    cs = _crawler_schema()
    runs, reviewed, unique_posts = [], 0, 0
    try:
        if pids and _exists(cs, 'x_search_runs'):
            runs = _all(f'''SELECT query, COUNT(*) AS runs, COALESCE(SUM(fetched),0) AS seen
                FROM (SELECT DISTINCT ran_at, query, fetched FROM {cs}.x_search_runs
                      WHERE product_id=ANY(%s) AND ran_at >= %s) r GROUP BY query''', (pids, since))
        if pids and _exists(cs, 'x_post_products') and _exists(cs, 'x_posts'):
            unique_posts = int(_all(f'''SELECT COUNT(DISTINCT h.tweet_id) AS n FROM {cs}.x_post_products h
                JOIN {cs}.x_posts p USING(tweet_id) WHERE h.product_id=ANY(%s)
                AND h.found_at >= %s AND p.created_at >= %s''', (pids, since, since))[0]['n'])
        from .x_activity import _engine_schema
        es = _engine_schema()
        if pids and _exists(es, 'x_hit_results') and _exists(cs, 'x_posts'):
            reviewed = int(_all(f'''SELECT COUNT(DISTINCT h.message_id) AS n FROM {es}.x_hit_results h
                JOIN {cs}.x_posts p ON p.tweet_id=h.message_id
                WHERE h.chat_id=ANY(%s) AND h.decided_at >= %s AND p.created_at >= %s
                AND h.outcome IN ('customer','not_customer','not_fit')''',
                (['x:p:' + pid for pid in pids], since, since))[0]['n'])
    except DatabaseError:
        log.warning('X weekly query report unavailable for business %s', business.pk, exc_info=True)
        return {'rows': [], 'available': False}
    leads = list(Opportunity.objects.filter(business=business, source_platform='x', created_at__gte=since)
                 .exclude(source_query='').values('source_query').annotate(leads=Count('id')))
    status = x_collector_status() or {}
    generated = status.get('generated_queries', [])
    return {'rows': combine_rows(business.pk, runs, leads, generated), 'available': True,
            'unique_posts': unique_posts, 'reviewed_posts': reviewed, 'minimum_posts': 3000,
            'replacement_ready': reviewed >= 3000, 'queue_updated': status.get('updated')}
