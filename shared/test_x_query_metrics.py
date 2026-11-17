from datetime import datetime, timedelta, timezone

import psycopg
from psycopg.rows import dict_row

from shared.x_query_metrics import weekly_query_metrics


def test_first_discovery_gets_one_credit_across_queries_products_and_outcomes(pg_dsn, pg_schema):
    schema = pg_schema('query_metrics')
    now = datetime.now(timezone.utc)
    with psycopg.connect(pg_dsn, row_factory=dict_row) as db:
        db.execute(f'CREATE SCHEMA {schema}')
        db.execute(f'CREATE TABLE {schema}.x_posts (tweet_id bigint,created_at timestamptz)')
        db.execute(f'CREATE TABLE {schema}.x_post_products (tweet_id bigint,product_id text,query text,found_at timestamptz)')
        db.execute(f'CREATE TABLE {schema}.x_hit_results (message_id bigint,chat_id text,outcome text,decided_at timestamptz)')
        db.execute(f'CREATE TABLE {schema}.x_search_runs (product_id text,query text,ran_at timestamptz,fetched int)')
        for pid, query, when in [('11', 'first', now-timedelta(seconds=1)), ('12', 'second', now)]:
            db.execute(f'INSERT INTO {schema}.x_post_products VALUES (1,%s,%s,%s)', (pid, query, when))
            db.execute(f'INSERT INTO {schema}.x_search_runs VALUES (%s,%s,%s,10)', (pid, query, now))
        db.execute(f'INSERT INTO {schema}.x_search_runs VALUES (%s,%s,%s,10)', ('12', 'first', now))
        db.execute(f'INSERT INTO {schema}.x_posts VALUES (1,%s),(2,%s),(3,%s)', (now, now, now-timedelta(days=8)))
        db.execute(f'INSERT INTO {schema}.x_post_products VALUES (2,%s,%s,%s),(3,%s,%s,%s)',
                   ('99', 'private', now, '11', 'old', now))
        for mid, chat, outcome in [(1, 'x:p:11', 'not_fit'), (1, 'x:p:12', 'customer'),
                                    (2, 'x:p:99', 'customer'), (3, 'x:p:11', 'customer')]:
            db.execute(f'INSERT INTO {schema}.x_hit_results VALUES (%s,%s,%s,%s)', (mid, chat, outcome, now))
        metrics = weekly_query_metrics(lambda sql, args: db.execute(sql, args).fetchall(), schema, schema,
                                       ['11', '12'], now-timedelta(days=7))
    assert metrics['received'] == metrics['reviewed'] == 1
    assert metrics['queries']['first']['runs'] == 1
    assert metrics['queries']['first']['leads'] == 1
    assert metrics['queries']['second']['leads'] == 0
    assert 'private' not in metrics['queries']
