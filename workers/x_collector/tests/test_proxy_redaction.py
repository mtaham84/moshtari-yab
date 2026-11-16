import json

import pytest
from django.db import connection
from psycopg.conninfo import make_conninfo

from workers.x_collector.account_pool import TwitterAccount
from workers.x_collector.client import XCliClient
from workers.x_collector.tests.test_search_errors import install_client
from x_ingest.__main__ import record_search_runs


@pytest.mark.parametrize('url,want', [
    ('http://user:password@proxy:8080', 'http://***@proxy:8080'),
    ('socks5h://user:p%40ss@[::1]:1080', 'socks5h://***@[::1]:1080'),
    ('http://proxy:8080', 'http://proxy:8080'),
    ('http://user@proxy', 'http://***@proxy'),
    ('', ''), (None, None),
])
def test_mask_proxy(url, want):
    from shared.tele_logging.formatter import mask_proxy
    assert mask_proxy(url) == want


def test_search_logs_and_stats_hide_proxy_credentials(monkeypatch, caplog):
    install_client(monkeypatch, iter([[]]))
    account = TwitterAccount(1, 'one', '', 'token', 'cookie', 'http://user:password@proxy:8080', 'ACTIVE')
    _, stats = XCliClient().search_top_past_week('لپتاپ', account=account)
    assert stats['proxy_url'] == 'http://***@proxy:8080'
    assert 'user:password' not in caplog.text


@pytest.mark.django_db(transaction=True)
def test_search_run_storage_redacts_stats(monkeypatch):
    params = connection.get_connection_params()
    dsn = make_conninfo(**{k: params[k] for k in ('dbname', 'user', 'password', 'host', 'port') if params.get(k)})
    monkeypatch.setenv('NE_CRAWLER_SCHEMA', 'test_redaction')
    stats = {'proxy_url': 'http://user:password@proxy:8080', 'nested': {'proxy': 'socks5://u:p@host:1080'}}
    try:
        assert record_search_runs([{'product_ids': ['p1'], 'query': 'لپتاپ', 'stats': stats}], dsn) == 1
        with connection.cursor() as cursor:
            cursor.execute('SELECT stats FROM test_redaction.x_search_runs')
            stored = cursor.fetchone()[0]
        rendered = json.dumps(stored)
        assert 'password' not in rendered and 'u:p@' not in rendered
        assert stats['proxy_url'] == 'http://user:password@proxy:8080'
    finally:
        with connection.cursor() as cursor:
            cursor.execute('DROP SCHEMA IF EXISTS test_redaction CASCADE')
