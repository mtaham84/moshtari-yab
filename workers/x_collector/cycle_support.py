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
