from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from django.db import connection
from psycopg.conninfo import make_conninfo

from apps.discovery.models import XSearchQuery, XQueryScore
from apps.discovery.tests import seller
from apps.products.models import Product
from workers.x_collector.families import Family


@pytest.mark.django_db(transaction=True)
def test_portfolio_is_persistent_exactly_replaces_one_and_is_tenant_scoped():
    from workers.x_collector.portfolio import manage_portfolio
    _, business = seller('weekly@example.com', 'Weekly seller')
    _, other = seller('other-weekly@example.com', 'Other seller')
    product = Product.objects.create(business=business, name='پوست', price=100)
    family = Family('family', str(business.pk), 'پوست صورت', 'زیبایی > پوست', '', [str(product.pk)])
    settings = connection.settings_dict
    cfg = SimpleNamespace(database_url=make_conninfo(dbname=settings['NAME'], user=settings['USER'],
                          password=settings['PASSWORD'], host=settings['HOST'], port=settings['PORT']),
                          crawler_schema='crawler', state_schema='need_engine')
    seeds = [{'query': q, 'business_ids': [str(business.pk)], 'product_ids': [str(product.pk)], 'kind': 'problem'}
             for q in ['پوست خشک', 'کرم صورت', 'پوست صورتم']]
    XSearchQuery.objects.create(business=other, text='کرم صورت', normalized_text='کرم صورت')
    metrics = {'queries': {'پوست خشک': {'runs': 10, 'leads': 20}, 'کرم صورت': {'runs': 10, 'leads': 0}},
               'reviewed': 2999, 'watermark': 'one'}
    now = datetime(2026, 11, 17, tzinfo=timezone.utc)
    generate = lambda *args: [{'query': 'خشکی پوست', 'kind': 'problem', 'domain': 'family'}]
    with patch('workers.x_collector.portfolio.read_metrics', return_value=metrics):
        first = manage_portfolio(cfg, [family], seeds, generator=generate, now=now)
        assert {q['query'] for q in first} == {q['query'] for q in seeds}
        assert XSearchQuery.objects.filter(business=business, state='active').count() == 3
        assert all(q['portfolio_managed'] for q in first)
        metrics.update(reviewed=3000, watermark='two')
        later = now.replace(day=19)
        second = manage_portfolio(cfg, [family], seeds, generator=generate, now=later)
        assert {q['query'] for q in second} == {'پوست خشک', '"خشکی پوست" lang:fa', 'پوست صورتم'}
        assert XSearchQuery.objects.get(business=business, text='کرم صورت').state == 'archived'
        assert XSearchQuery.objects.filter(business=business, state='active').count() == 3
        assert XSearchQuery.objects.get(business=other).state == 'active'
        assert XQueryScore.objects.filter(business=business).exists()
        with patch('workers.x_collector.portfolio.replacement_plan', side_effect=AssertionError('same evidence')):
            again = manage_portfolio(cfg, [family], seeds, generator=generate, now=later.replace(day=21))
        assert {q['query'] for q in again} == {q['query'] for q in second}
        with patch('workers.x_collector.portfolio._evaluate', side_effect=AssertionError('unfunded expansion')):
            unpaid = manage_portfolio(cfg, [family], seeds, generator=generate, now=later, enabled_businesses=set())
        assert unpaid == []
        assert XSearchQuery.objects.filter(business=business, state='active').count() == 3
        with patch('workers.x_collector.portfolio.query_limit', return_value=2):
            limited = manage_portfolio(cfg, [family], seeds, generator=generate, now=later)
        assert len(limited) == 2
