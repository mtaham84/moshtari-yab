from datetime import datetime, timezone
from types import SimpleNamespace

from workers.x_collector.worker import normalize_tweet


def test_reply_keeps_parent_author_and_conversation():
    raw = {'id': '102', 'author_id': '20', 'username': 'buyer', 'text': 'کلاس کنکور خوب کجاست؟',
           'created_at': datetime.now(timezone.utc).isoformat(),
           'parent': {'kind': 'post', 'conversation_id': '100', 'in_reply_to_tweet_id': '101',
                      'in_reply_to_user_id': '19'}}
    record = normalize_tweet(raw, query='conversation_id:100', thread_root_id='100')
    assert record['kind'] == 'reply'
    assert record['conversation_id'] == '100'
    assert record['in_reply_to_tweet_id'] == '101'
    assert record['in_reply_to_author_id'] == '19'
    assert record['parent']['kind'] == 'reply'
    assert record['url'] == 'https://x.com/buyer/status/102'


def test_thread_does_not_mislabel_unrelated_or_missing_parent():
    raw = {'id': '102', 'author_id': '20', 'username': 'buyer', 'text': 'کلاس کنکور خوب کجاست؟'}
    assert normalize_tweet(raw, thread_root_id='100') is None
    raw['parent'] = {'conversation_id': '999', 'in_reply_to_tweet_id': '998'}
    assert normalize_tweet(raw, thread_root_id='100') is None


def test_thread_queries_only_include_accepted_products(monkeypatch):
    from workers.x_collector.thread_queries import load_thread_queries
    class DB:
        def all(self, *args):
            return [{'root_tweet_id': 100, 'payload': {'matched_products': [{'product_id': '2'}]}}]
    config = SimpleNamespace(state_schema='need_engine', crawler_schema='crawler', x_thread_max_age_hours=36)
    products = [SimpleNamespace(product_id='1', business_id='7'), SimpleNamespace(product_id='2', business_id='8')]
    rows = load_thread_queries(config, DB(), products)
    assert rows == [{'query': 'conversation_id:100', 'kind': 'reply', 'priority': 1.0,
                     'thread_root_id': '100', 'product_ids': ['2'], 'business_ids': ['8'],
                     'product_business_ids': {'2': '8'}}]


def test_reply_uses_existing_wallet_gate_daily_cap_and_ingestion(funded_x_seller, tmp_path):
    import json
    from workers.x_collector.worker import XCollector
    from x_ingest.__main__ import validate_record
    raw = {'id': '102', 'author_id': '20', 'username': 'buyer', 'text': 'کلاس کنکور خوب کجاست؟',
           'created_at': datetime.now(timezone.utc).isoformat(),
           'parent': {'conversation_id': '100', 'in_reply_to_tweet_id': '100', 'in_reply_to_user_id': '19'}}
    query = {'query': 'conversation_id:100', 'thread_root_id': '100', 'kind': 'reply',
             'business_ids': ['9', '10'], 'product_ids': ['1', '2'],
             'product_business_ids': {'1': '9', '2': '10'}}
    collector = XCollector(output_dir=tmp_path / 'out', state_path=tmp_path / 'state', daily_cap=1,
                           sleeper=lambda _: None, account_pool=False)
    result = collector.run([query], mock_records=[raw])
    assert result['collected'] == 1
    record = json.loads((tmp_path / 'out' / 'latest.jsonl').read_text())
    ingested = validate_record(record)
    assert ingested['kind'] == 'reply'
    assert ingested['conversation_id'] == 100
    assert ingested['in_reply_to_tweet_id'] == 100
    assert ingested['in_reply_to_author_id'] == '19'
    assert ingested['product_id'] == '1'
    assert collector.run([query], mock_records=[raw])['status'] == 'DAILY_CAP_REACHED'


def test_thread_queue_reads_real_store_and_excludes_closed_and_old_roots(pg_dsn, pg_schema):
    from psycopg.types.json import Jsonb
    from need_engine.sources import _DB
    from need_engine.store import Store
    from workers.x_collector.thread_queries import load_thread_queries
    schema = pg_schema('threads')
    store = Store(pg_dsn, schema)
    try:
        store.conn.execute(f'CREATE TABLE {schema}.x_posts(tweet_id BIGINT PRIMARY KEY, created_at TIMESTAMPTZ)')
        for root, status, age in [(100, 'open', 1), (200, 'closed', 1), (300, 'open', 100)]:
            store.request_x_thread(root, 'author', str(root), str(root))
            store.conn.execute(f'INSERT INTO {schema}.x_posts VALUES (%s, now() - (%s * interval \'1 hour\'))', (root, age))
            store.conn.execute(f'INSERT INTO {schema}.opportunities(opportunity_id,seq,status,payload) VALUES (%s,%s,%s,%s)',
                               (str(root), root, status, Jsonb({'matched_products': [{'product_id': '2'}]})))
        db = _DB(pg_dsn)
        try:
            config = SimpleNamespace(state_schema=schema, crawler_schema=schema, x_thread_max_age_hours=36)
            rows = load_thread_queries(config, db, [SimpleNamespace(product_id='2', business_id='8')])
            assert [row['thread_root_id'] for row in rows] == ['100']
        finally:
            db.close()
    finally:
        store.close()
