import json
from datetime import datetime, timezone

import pytest
from django.db import connection
from psycopg.conninfo import make_conninfo

from apps.discovery.models import XCrawledPost
from workers.x_collector.worker import filter_uncrawled_tweets
from x_ingest.__main__ import _load

from workers.x_collector.tests.helpers import owned_queries

pytestmark = pytest.mark.usefixtures("funded_x_seller")


@pytest.mark.django_db(transaction=True)
def test_ingest_deduplicates_per_product_and_preserves_legacy_hash(tmp_path, monkeypatch):
    params = connection.get_connection_params()
    dsn = make_conninfo(**{k: params[k] for k in ('dbname', 'user', 'password', 'host', 'port') if params.get(k)})
    monkeypatch.setenv('NE_CRAWLER_SCHEMA', 'test_product_dedup')
    XCrawledPost.objects.create(tweet_id='101', post_hash='ca50b4fe50a7be78e1bf5b355d39ae9655990b85cc57cc3d49f2728557767019')
    base = {'source': 'x', 'id': '101', 'text': 'دنبال لپتاپ هستم', 'author_id': '1',
            'author_handle': '@buyer', 'metadata': {}, 'created_at': datetime.now(timezone.utc).isoformat()}
    rows = [{**base, 'product_id': pid} for pid in ('p1', 'p2')]
    assert filter_uncrawled_tweets(rows, dsn) == rows
    path = tmp_path / 'posts.jsonl'
    path.write_text('\n'.join(json.dumps(r) for r in rows))
    try:
        assert _load(path, dsn) == (2, 0)
        assert filter_uncrawled_tweets(rows, dsn) == []
        assert XCrawledPost.objects.filter(tweet_id='101').count() == 3
        assert list(XCrawledPost.objects.filter(tweet_id='101').order_by('product_id').values_list('product_id', flat=True)) == ['', 'p1', 'p2']
    finally:
        with connection.cursor() as cursor:
            cursor.execute('DROP SCHEMA IF EXISTS test_product_dedup CASCADE')


def test_native_search_keeps_same_tweet_for_two_products(tmp_path, monkeypatch):
    from types import SimpleNamespace
    from workers.x_collector.tests.test_search_errors import install_client
    from workers.x_collector.account_pool import TwitterAccount, TwitterAccountPool
    from workers.x_collector.client import XCliClient
    from workers.x_collector.worker import XCollector

    tweet = SimpleNamespace(id='101', text='دنبال لپتاپ هستم', lang='fa',
                            created_at=datetime.now(timezone.utc).isoformat(),
                            author=SimpleNamespace(id='buyer', screen_name='buyer', name='Buyer'))
    install_client(monkeypatch, iter([[tweet], [tweet]]))
    monkeypatch.setattr('workers.x_collector.worker.is_political_x_post', lambda _: False)
    pool = TwitterAccountPool.__new__(TwitterAccountPool)
    pool.dsn = None
    pool.accounts = [TwitterAccount(1, 'one', '', 'token', 'cookie', '', 'ACTIVE')]
    monkeypatch.setattr(pool, 'refresh', lambda: None)
    collector = XCollector(client=XCliClient(), account_pool=pool, output_dir=tmp_path / 'out',
                           state_path=tmp_path / 'state', sleeper=lambda _: None)
    result = collector.run(owned_queries([{'query': 'one', 'product_ids': ['p1']}, {'query': 'two', 'product_ids': ['p2']}]))
    assert result['collected'] == 2
    rows = [json.loads(line) for line in open(result['output'])]
    assert {row['product_id'] for row in rows} == {'p1', 'p2'}


@pytest.mark.django_db(transaction=True)
def test_panel_insert_failure_rolls_back_record_and_keeps_neighbors(tmp_path, monkeypatch, caplog):
    params = connection.get_connection_params()
    dsn = make_conninfo(**{k: params[k] for k in ('dbname', 'user', 'password', 'host', 'port') if params.get(k)})
    monkeypatch.setenv('NE_CRAWLER_SCHEMA', 'test_panel_savepoint')
    base = {'source': 'x', 'text': 'دنبال لپتاپ هستم', 'author_id': '1', 'author_handle': '@buyer',
            'product_id': 'p1', 'metadata': {}}
    records = [{**base, 'id': '201'}, {**base, 'id': '202', 'author_handle': '@' + 'x' * 101}, {**base, 'id': '203'}]
    path = tmp_path / 'posts.jsonl'
    path.write_text('\n'.join(json.dumps(row) for row in records))
    try:
        assert _load(path, dsn) == (2, 1)
        with connection.cursor() as cursor:
            for table in ['x_posts', 'x_post_products']:
                cursor.execute(f'SELECT tweet_id FROM test_panel_savepoint.{table} ORDER BY tweet_id')
                assert cursor.fetchall() == [(201,), (203,)]
        assert list(XCrawledPost.objects.order_by('tweet_id').values_list('tweet_id', flat=True)) == ['201', '203']
        assert 'line 2' in caplog.text
    finally:
        with connection.cursor() as cursor:
            cursor.execute('DROP SCHEMA IF EXISTS test_panel_savepoint CASCADE')
