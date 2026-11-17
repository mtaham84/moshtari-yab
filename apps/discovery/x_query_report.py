"""Seller-scoped weekly query evidence and the collector's actual published weights."""
from datetime import timedelta

from django.db import DatabaseError
from django.utils import timezone
import logging

from apps.billing.x_stats import x_collector_status
from apps.products.models import Product
from .models import XSearchQuery
from .x_activity import _all, _crawler_schema, _engine_schema, _exists
from shared.x_query_metrics import weekly_query_metrics

log = logging.getLogger(__name__)


def combine_rows(business_id, runs, leads, generated):
    rows = {r['query']: {**r, 'leads': 0, 'score': None, 'weight_percent': None, 'eligible': None}
            for r in runs}
    for item in generated:
        if str(business_id) not in {str(v) for v in item.get('business_ids', [])}:
            continue
        row = rows.setdefault(item['query'], {'query': item['query'], 'runs': 0, 'seen': 0, 'leads': 0})
        weight = item.get('weight')
        score = item.get('tenant_scores', {}).get(str(business_id), item.get('score'))
        row.update(score=score, weight_percent=100 * weight if weight is not None else None, eligible=item.get('eligible'))
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
    runs, reviewed, unique_posts, leads = [], 0, 0, []
    es = _engine_schema()
    try:
        if pids and all(_exists(s, t) for s, t in [(cs, 'x_search_runs'), (cs, 'x_posts'),
                        (cs, 'x_post_products'), (es, 'x_hit_results')]):
            metrics = weekly_query_metrics(_all, cs, es, pids, since)
            runs = list(metrics['queries'].values())
            leads = [{'source_query': q, 'leads': r['leads']} for q, r in metrics['queries'].items()]
            reviewed, unique_posts = metrics['reviewed'], metrics['received']
    except DatabaseError:
        log.warning('X weekly query report unavailable for business %s', business.pk, exc_info=True)
        return {'rows': [], 'available': False}
    catalog = [{'query': q.text, 'business_ids': [str(business.pk)], 'score': q.weight, 'weight': None,
                'eligible': None} for q in XSearchQuery.objects.filter(business=business,
                    generation_version='weekly-v1', state='active')]
    status = x_collector_status() or {}
    generated = catalog + status.get('generated_queries', [])
    return {'rows': combine_rows(business.pk, runs, leads, generated), 'available': True,
            'unique_posts': unique_posts, 'reviewed_posts': reviewed, 'minimum_posts': 3000,
            'replacement_ready': reviewed >= 3000, 'queue_updated': status.get('updated')}
