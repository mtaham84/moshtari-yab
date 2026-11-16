"""New X queries start at the exposure-weighted yield of eligible sampled queries."""
import pytest

from shared.collector_kit.query_scheduler import query_stats, shares


def observed(leads, runs, errors=0):
    return query_stats((1, 1, leads, runs, 0, errors))


def test_new_query_uses_search_weighted_mean_not_equal_average():
    queries = [{'query': name} for name in ('good', 'weak', 'new')]
    stats = {'good': observed(20, 100), 'weak': observed(5, 10)}
    weights = shares(queries, stats)
    # (20 + 5) / (100 + 10), rather than (.2 + .5) / 2.
    mean = 25 / 110
    assert weights['new'] / weights['good'] == pytest.approx(mean / .2)


def test_failed_requests_only_query_still_receives_starting_mean():
    queries = [{'query': name} for name in ('known', 'new')]
    weights = shares(queries, {'known': observed(20, 100), 'new': observed(0, 8, errors=8)})
    assert weights == pytest.approx({'known': .5, 'new': .5})


def test_successfully_sampled_zero_yield_keeps_existing_exploration_floor():
    queries = [{'query': name} for name in ('known', 'empty')]
    weights = shares(queries, {'known': observed(20, 100), 'empty': observed(0, 1)})
    assert weights == pytest.approx({'known': .95, 'empty': .05})


def test_all_fresh_queries_get_equal_allocation():
    queries = [{'query': name} for name in ('one', 'two', 'three')]
    assert shares(queries, {}) == pytest.approx(dict.fromkeys(('one', 'two', 'three'), 1 / 3))


def test_first_completed_search_replaces_starting_score_without_fake_counters():
    from shared.collector_kit.query_scheduler import scores

    queries = [{'query': 'known'}, {'query': 'new'}]
    stats = {'known': observed(20, 100), 'new': observed(0, 0)}
    before = {query: dict(row) for query, row in stats.items()}
    assert scores(queries, stats)['new'] == .2
    assert stats == before
    stats['new'] = observed(0, 1)
    assert scores(queries, stats)['new'] == 0


def test_no_history_starts_with_neutral_half_yield():
    from shared.collector_kit.query_scheduler import scores

    assert scores([{'query': 'new'}], {}) == {'new': .5}
