import json
import sqlite3

import pytest

from need_engine.registry import ModelRegistry
from need_engine.tests.test_registry import FakeDB, cfg
from workers.x_collector.worker import XCollector
from workers.x_collector import query_source


@pytest.fixture
def setup_run(tmp_path, monkeypatch):
    db = FakeDB(balances={'9': 100, '10': 0})
    monkeypatch.setattr('workers.x_collector.balance_gate.ModelRegistry', lambda *a, **kw: ModelRegistry(cfg(), db=db))
    monkeypatch.delenv('X_SELLER_ALLOWLIST', raising=False)
    monkeypatch.delenv('X_REQUIRE_WALLET', raising=False)
    class FakeClient:
        def __init__(self):
            self.calls = []
            self.after = lambda: None
        def search(self, query, limit, account=None):
            self.calls.append(query)
            self.after()
            return []
    client = FakeClient()
    collector = XCollector(client=client, output_dir=tmp_path / 'out', state_path=tmp_path / 'state',
                           sleeper=lambda _: None, sleep_min=0, sleep_max=0)
    return db, client, collector


def queries(owner='9'):
    return [{'query': f'q{i}', 'business_ids': [owner], 'product_ids': ['p1']} for i in range(1, 4)]


def saved_queries(collector):
    with sqlite3.connect(collector.state_path) as db:
        return db.execute('SELECT query,runs FROM query_runs ORDER BY query').fetchall()


def test_balance_exhaustion_skips_later_queries_without_history_or_penalties(setup_run):
    db, client, collector = setup_run
    client.after = lambda: db.balances.update({'9': 0})
    result = collector.run(queries())
    assert len(client.calls) == 1
    assert saved_queries(collector) == [('q1', 1)]
    assert result['skipped_queries_by_balance'] == 2
    assert result['paused_businesses'] == ['9']
    assert collector.state.get_query_bandit_stats('q2')['beta'] == 1
    assert [s['query'] for s in collector.searches] == ['q1']


def test_topup_between_cycles_resumes_without_persistent_pause(setup_run):
    db, client, collector = setup_run
    db.balances['9'] = 0
    paused = collector.run(queries())
    assert client.calls == []
    assert saved_queries(collector) == []
    assert paused['skipped_queries_by_balance'] == 3
    db.balances['9'] = 100
    resumed = collector.run(queries())
    assert len(client.calls) == 3
    assert resumed['skipped_queries_by_balance'] == 0
    assert resumed['paused_businesses'] == []


def test_shared_query_runs_for_funded_owner_only(setup_run):
    _, client, collector = setup_run
    collector.run([{'query': 'shared', 'business_ids': ['9', '10'], 'product_ids': ['p1', 'p2'],
                    'product_business_ids': {'p1': '10', 'p2': '9'}}])
    assert len(client.calls) == 1
    assert collector.searches[0]['business_ids'] == ['9']
    assert collector.searches[0]['product_ids'] == ['p2']


@pytest.mark.parametrize('require,called', [('true', False), ('false', True)])
def test_missing_wallet_flag(setup_run, monkeypatch, caplog, require, called):
    _, client, collector = setup_run
    monkeypatch.setenv('X_REQUIRE_WALLET', require)
    collector.run(queries('99'))
    assert bool(client.calls) is called
    if require == 'true':
        assert 'wallet' in caplog.text.lower()


def test_wallet_failure_mid_run_skips_rest(setup_run):
    db, client, collector = setup_run
    def disconnect():
        def fail(*a, **kw):
            raise RuntimeError('disconnected')
        db.all = fail
    client.after = disconnect
    result = collector.run(queries())
    assert len(client.calls) == 1
    assert result['skipped_queries_by_balance'] == 2
    assert saved_queries(collector) == [('q1', 1)]


def test_static_override_with_owner_is_gated(setup_run, tmp_path):
    _, _, _ = setup_run
    path = tmp_path / 'queries.json'
    path.write_text(json.dumps([{'query': 'funded', 'business_id': '9'}, {'query': 'blocked', 'business_id': '10'}]))
    result = query_source.collector_queries(str(path))
    assert [r['query'] for r in result] == ['funded']
    assert result[0]['business_ids'] == ['9']


def test_unowned_static_override_is_blocked_with_warning(setup_run, tmp_path, caplog):
    path = tmp_path / 'queries.txt'
    path.write_text('legacy query\n')
    assert query_source.collector_queries(str(path)) == []
    assert 'unowned' in caplog.text.lower()


def test_direct_unowned_query_never_calls_search(setup_run):
    _, client, collector = setup_run
    result = collector.run(['unowned'])
    assert client.calls == []
    assert saved_queries(collector) == []
    assert result['skipped_queries_by_balance'] == 1


def test_explicit_mock_records_need_no_wallet_owner(setup_run):
    _, client, collector = setup_run
    result = collector.run(['offline'], mock_records=[])
    assert client.calls == []
    assert result['status'] == 'COMPLETED'


def test_direct_static_owner_is_checked_by_worker(setup_run):
    _, client, collector = setup_run
    result = collector.run([{'query': 'blocked', 'business_id': '10'}])
    assert client.calls == []
    assert result['skipped_queries_by_balance'] == 1
    assert saved_queries(collector) == []
