from collections import Counter
import sqlite3

import pytest

from shared.collector_kit.seen import SeenStore


@pytest.fixture
def x_state(state_store):
    state_store.platform = 'x'
    return state_store


def seed(store, queries):
    store.schedule(queries, 1)
    if isinstance(store, SeenStore):
        with sqlite3.connect(store.path) as db:
            for query, leads in [('good', 20), ('weak', 5)]:
                db.execute('UPDATE query_runs SET runs=100,leads_count=? WHERE platform=? AND query=?', (leads, 'x', query))
    else:
        with store._connect(store.dsn) as db:
            for query, leads in [('good', 20), ('weak', 5)]:
                db.execute(f'UPDATE {store.schema}.collector_query_runs SET runs=100,leads_count=%s WHERE platform=%s AND query=%s', (leads, 'x', query))


def test_lead_yield_controls_100_allocations_despite_priority_and_kind(x_state):
    queries = [{'query': 'good', 'kind': 'product', 'priority': 1}, {'query': 'weak', 'kind': 'intent', 'priority': 100}]
    seed(x_state, queries)
    counts = Counter()
    for _ in range(100):
        selected = x_state.schedule(queries, 1, persist=False)[0]['query']
        counts[selected] += 1
        x_state.record_query_allocation(selected, queries)
    assert counts == {'good': 80, 'weak': 20}


def test_error_attempts_do_not_lower_leads_per_completed_search(x_state):
    x_state.schedule([{'query': 'topic'}], 1)
    for _ in range(3):
        x_state.record_query('topic', 0, 0, error='auth')
    x_state.record_query('topic', 5, 0)
    x_state.record_query('topic', 5, 0)
    x_state.record_feedback('topic', True, lead_id='tweet-1')
    stats = x_state.get_query_bandit_stats('topic')
    assert stats['searches'] == 2
    assert stats['lead_rate'] == .5


def test_shared_tweet_is_one_query_lead_across_products_and_sellers(x_state):
    x_state.schedule([{'query': 'topic'}], 1)
    assert x_state.record_feedback('topic', True, lead_id='tweet-1')
    assert not x_state.record_feedback('topic', True, lead_id='tweet-1')
    assert x_state.get_query_bandit_stats('topic')['leads'] == 1


def test_new_and_zero_yield_queries_keep_an_exploration_share(x_state):
    queries = [{'query': 'good'}, {'query': 'weak'}, {'query': 'new'}]
    seed(x_state, queries)
    counts = Counter()
    for _ in range(100):
        query = x_state.schedule(queries, 1, persist=False)[0]['query']
        counts[query] += 1
        x_state.record_query_allocation(query, queries)
    assert counts == {'good': 76, 'weak': 19, 'new': 5}


def test_same_query_is_deferred_to_later_cycle_and_allocation_survives_reload(x_state):
    queries = [{'query': 'good'}, {'query': 'weak'}]
    seed(x_state, queries)
    counts = Counter()
    for _ in range(30):
        if isinstance(x_state, SeenStore):
            x_state = SeenStore(x_state.path, platform='x')
        else:
            x_state = type(x_state)(platform='x', dsn=x_state.dsn, schema=x_state.schema)
        selected = x_state.schedule(queries, 12, persist=False)
        assert len({item['query'] for item in selected}) == len(selected)
        for item in selected:
            counts[item['query']] += 1
            x_state.record_query_allocation(item['query'], queries)
    assert abs(counts['good'] - 4 * counts['weak']) <= 2


def test_dropped_query_cannot_distort_remaining_yield_allocation(x_state):
    queries = [{'query': 'good'}, {'query': 'weak'}, {'query': 'dropped'}]
    seed(x_state, queries)
    x_state.record_query('dropped', 1, 1)
    x_state.record_feedback('dropped', True, lead_id='old-lead')
    x_state.record_feedback('dropped', False, weight=100)
    counts = Counter()
    for _ in range(100):
        query = x_state.schedule(queries, 1, persist=False)[0]['query']
        counts[query] += 1
        x_state.record_query_allocation(query, queries)
    assert counts == {'good': 80, 'weak': 20}


def test_preview_reads_same_yield_for_both_backends(x_state, monkeypatch, tmp_path):
    from types import SimpleNamespace
    from workers.x_collector.cycle_support import preview_searches

    queries = [{'query': 'good'}, {'query': 'weak', 'priority': 100}]
    seed(x_state, queries)
    if isinstance(x_state, SeenStore):
        backend, path = 'sqlite', x_state.path
        before = path.read_bytes()
    else:
        backend, path = 'postgres', tmp_path / 'unused'
        monkeypatch.setenv('TG_DATABASE_URL', x_state.dsn)
        monkeypatch.setenv('TG_DB_SCHEMA', x_state.schema)
    collector = SimpleNamespace(state_backend=backend, state_path=path, queries_per_cycle=12)
    planned, weights, stats, count = preview_searches(collector, queries)
    assert planned[0]['query'] == 'good'
    assert weights == pytest.approx({'good': .8, 'weak': .2})
    assert stats['good']['lead_rate'] == .2
    assert count == 0
    if backend == 'sqlite':
        assert path.read_bytes() == before
