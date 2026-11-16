from collections import Counter
from datetime import datetime, timezone
from types import SimpleNamespace
import sqlite3

import pytest

from shared.collector_kit.seen import SeenStore
from workers.x_collector.worker import XCollector, collect_forever


def raw_post(identifier):
    return {'id': str(identifier), 'author_id': 'buyer', 'username': 'buyer',
            'text': 'دنبال شومیز هستم', 'lang': 'fa', 'created_at': datetime.now(timezone.utc).isoformat()}


def test_worker_executes_yield_weighted_searches_across_cycles(monkeypatch, tmp_path):
    monkeypatch.setenv('COLLECTOR_STATE_BACKEND', 'sqlite')
    monkeypatch.setenv('X_COLLECT_QUERIES_PER_CYCLE', '1')
    monkeypatch.setattr('workers.x_collector.worker.is_political_x_post', lambda _: False)
    counts = Counter()
    class FakeClient:
        def search(self, query, limit, account=None):
            counts[query.split()[0]] += 1
            return [raw_post(sum(counts.values()))]
    path = tmp_path / 'state'
    state = SeenStore(path, platform='x')
    queries = [{'query': 'good'}, {'query': 'weak'}]
    state.schedule(queries, 1)
    with sqlite3.connect(path) as db:
        db.execute("UPDATE query_runs SET runs=100,leads_count=20 WHERE query='good'")
        db.execute("UPDATE query_runs SET runs=100,leads_count=5 WHERE query='weak'")
    collector = XCollector(client=FakeClient(), output_dir=tmp_path / 'out', state_path=path, sleeper=lambda _: None)
    for _ in range(100):
        before = dict(counts)
        collector.run(queries)
        query = next(name for name in counts if counts[name] > before.get(name, 0))
        every = 5 if query == 'good' else 20
        if counts[query] % every == 0:
            collector.state.record_feedback(query, True, lead_id=f'{query}-{counts[query]}')
    assert sum(counts.values()) == 100
    assert 78 <= counts['good'] <= 82
    assert 18 <= counts['weak'] <= 22


def test_loop_recovers_query_reload_failure_and_runs_next_cycle(monkeypatch, tmp_path):
    class StopLoop(Exception):
        pass
    calls, sleeps = [], []
    def provider():
        calls.append('load')
        if len(calls) == 1:
            raise RuntimeError('temporary query DB failure')
        return ['topic']
    def sleep(seconds):
        sleeps.append(seconds)
        if len(sleeps) == 2:
            raise StopLoop
    monkeypatch.setattr('workers.x_collector.worker.time.sleep', sleep)
    collector = XCollector(output_dir=tmp_path / 'out', state_path=tmp_path / 'state')
    with pytest.raises(StopLoop):
        collect_forever(collector, provider, 300, mock_records=[raw_post(1)])
    assert calls == ['load', 'load']
    assert sleeps == [120, 300]
    assert collector.state.count_today() == 1


def test_loop_keeps_running_after_an_empty_success(monkeypatch, tmp_path):
    class StopLoop(Exception):
        pass
    calls = []
    def run(queries, **kwargs):
        calls.append(queries)
        return {'status': 'COMPLETED', 'collected': 0 if len(calls) == 1 else 1}
    def sleep(_):
        if len(calls) == 2:
            raise StopLoop
    collector = SimpleNamespace(output_dir=tmp_path, run=run)
    monkeypatch.setattr('workers.x_collector.worker.time.sleep', sleep)
    with pytest.raises(StopLoop):
        collect_forever(collector, ['topic'], 300, mock_records=[])
    assert calls == [['topic'], ['topic']]


def test_dry_run_uses_lead_yield_without_changing_state(monkeypatch, tmp_path):
    monkeypatch.setenv('COLLECTOR_STATE_BACKEND', 'sqlite')
    path = tmp_path / 'state'
    state = SeenStore(path, platform='x')
    queries = [{'query': 'good'}, {'query': 'weak', 'priority': 100}]
    state.schedule(queries, 1)
    with sqlite3.connect(path) as db:
        db.execute("UPDATE query_runs SET runs=100,leads_count=20 WHERE query='good'")
        db.execute("UPDATE query_runs SET runs=100,leads_count=5 WHERE query='weak'")
    before = path.read_bytes()
    collector = XCollector(output_dir=tmp_path / 'out', state_path=path, account_pool=False)
    result = collector.run(queries, dry_run=True)
    planned = result['planned_queries']
    assert planned[0]['query'] == 'good'
    assert planned[0]['weight'] == pytest.approx(0.8)
    assert planned[0]['lead_rate'] == pytest.approx(0.2)
    assert path.read_bytes() == before
    preview = collector.run([*queries, {'query': 'new'}], dry_run=True)
    new = next(item for item in preview['planned_queries'] if item['query'] == 'new')
    assert new['score'] == pytest.approx(.125)
    assert new['weight'] == pytest.approx(1 / 3)
    assert new['lead_rate'] == 0
    assert path.read_bytes() == before
