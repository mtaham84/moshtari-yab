"""Search transport selection and account failover used by the X collection cycle."""
import logging

from shared.collector_kit.limits import retry_delay
from shared.x_dedup import seen_tweet_ids

log = logging.getLogger('x_collector')


def fetch_search(collector, query, search_query, products, account, already_written,
                 remaining, min_inspect, mock_records, is_test_client):
    if hasattr(collector.client, 'search_top_past_week') and mock_records is None and not is_test_client:
        items, stats = collector.client.search_top_past_week(
            query, target_posts=min(40, remaining), account=account,
            global_seen_ids=seen_tweet_ids(already_written, products),
        )
        return items, stats, min(40, remaining)
    fetch_limit = min(collector.max_per_query, remaining) if is_test_client else min(max(min_inspect, collector.max_per_query), remaining)
    raw_items = mock_records if mock_records is not None else collector.client.search(search_query, fetch_limit, account=account)
    return raw_items, None, fetch_limit


def handle_search_error(collector, exc, account, breaker, cooldown, retry):
    if exc.kind in {'rate_limit', 'auth'} and account and collector.account_pool:
        if exc.kind == 'auth':
            collector.account_pool.record_auth_error(account, str(exc))
        else:
            collector.account_pool.record_rate_limit(account)
        next_account = collector.account_pool.get_next_account()
        if next_account:
            log.info('%s on @%s; rotating to @%s', exc.kind, account.username, next_account.username)
            return 'rotate', None, 0
    if exc.kind == 'rate_limit':
        until = cooldown.start()
        log.warning('All accounts rate limited; collection paused until %s', until)
        return 'stop', 'RATE_LIMITED', 0
    if exc.kind in {'auth', 'fatal'}:
        log.error('X search stopped (%s): %s', exc.kind, exc)
        return 'stop', 'AUTH_FAILED' if exc.kind == 'auth' else 'FAILED', 1
    if breaker.failures >= collector.circuit_breaker:
        log.warning('Stopping collection after consecutive CLI failures: %s', exc)
        return 'stop', None, 0
    if exc.kind != 'transient' or retry >= collector.max_retries:
        log.warning('Query failed after retries: %s', exc)
        return 'next', None, 1
    collector.sleeper(retry_delay(retry, collector.jitter))
    return 'retry', None, 0


def unseen_candidates(records, already_written, state):
    candidates, seen_count = [], 0
    for record in records:
        key = record["id"] + (f"|{record['product_id']}" if record.get("product_id") else "")
        if key in already_written or state.has_seen(record):
            seen_count += 1
        else:
            candidates.append(record)
    return candidates, seen_count


def preview_searches(collector, queries):
    """Read allocation history without initializing or modifying collector state."""
    import sqlite3
    from contextlib import closing
    from datetime import date

    import psycopg
    from psycopg import sql
    from shared.collector_kit.query_scheduler import CREDIT_KEY, plan, query_stats, shares
    from shared.collector_kit.state_store import _dsn

    stats, saved, count, dropped = {}, None, 0, set()
    if collector.state_backend == 'sqlite' and not collector.state_path.is_file():
        return plan(queries, collector.queries_per_cycle, stats), shares(queries, stats), stats, count
    try:
        if collector.state_backend == 'sqlite':
            connection = closing(sqlite3.connect(collector.state_path.resolve().as_uri() + '?mode=ro', uri=True))
            prefix, placeholder = '', '?'
        else:
            import os
            schema = os.getenv('TG_DB_SCHEMA', 'crawler')
            connection = psycopg.connect(_dsn(), options='-c default_transaction_read_only=on')
            prefix, placeholder = sql.Identifier(schema).as_string() + '.collector_', '%s'
        with connection as db:
            columns = {row[1] for row in db.execute('PRAGMA table_info(query_runs)')} if collector.state_backend == 'sqlite' else {'error_runs'}
            errors = 'error_runs' if 'error_runs' in columns else '0'
            rows = db.execute(f'SELECT query,alpha,beta,leads_count,runs,fetched,{errors},dropped FROM {prefix}query_runs WHERE platform={placeholder}', ('x',)).fetchall()
            stats = {row[0]: query_stats(row[1:7]) for row in rows}
            dropped = {row[0] for row in rows if row[7]}
            settings_table = f'{prefix}settings' if prefix else 'collector_settings'
            row = db.execute(f'SELECT value FROM {settings_table} WHERE platform={placeholder} AND key={placeholder}', ('x', CREDIT_KEY)).fetchone()
            saved = row[0] if row else None
            row = db.execute(f'SELECT collected FROM {prefix}daily_counts WHERE platform={placeholder} AND day={placeholder}', ('x', date.today().isoformat())).fetchone()
            count = row[0] if row else 0
    except (sqlite3.Error, psycopg.Error, OSError):
        log.warning('Could not read search preview history; using equal exploration weights', exc_info=True)
        stats, saved, count, dropped = {}, None, 0, set()
    stats.update({item['query']: item['weekly_stats'] for item in queries if 'weekly_stats' in item})
    eligible = [item for item in queries if item.get('portfolio_managed') or item['query'] not in dropped]
    return plan(eligible, collector.queries_per_cycle, stats, saved), shares(eligible, stats), stats, count
