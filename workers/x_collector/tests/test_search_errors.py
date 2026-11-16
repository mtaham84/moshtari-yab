import sys
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from workers.x_collector.account_pool import TwitterAccount, TwitterAccountPool
from workers.x_collector.client import XCliClient, XCliError
from workers.x_collector.worker import XCollector

from workers.x_collector.tests.helpers import owned_queries

pytestmark = pytest.mark.usefixtures("funded_x_seller")


def install_client(monkeypatch, responses):
    for key in ('TWITTER_AUTH_TOKEN', 'TWITTER_CT0', 'TWITTER_PROXY'):
        monkeypatch.setenv(key, 'initial')
    class NativeClient:
        def __init__(self, **kwargs):
            pass
        def _graphql_get(self, *args):
            result = next(responses)
            if isinstance(result, Exception):
                raise result
            return result
    module = SimpleNamespace(TwitterClient=NativeClient, FEATURES={},
                             parse_timeline_response=lambda data, _: (data, None), _deep_get=lambda *a: None)
    monkeypatch.setitem(sys.modules, 'twitter_cli', SimpleNamespace(client=module))
    monkeypatch.setitem(sys.modules, 'twitter_cli.client', module)


@pytest.mark.parametrize('message,kind', [('401 Unauthorized', 'auth'), ('session expired', 'auth'),
                                         ('429 Too Many Requests', 'rate_limit'), ('503 unavailable', 'transient'),
                                         ('Twitter API network error: upstream unavailable', 'transient')])
def test_native_errors_are_explicit_and_do_not_fall_back(monkeypatch, message, kind):
    install_client(monkeypatch, iter([RuntimeError(message)]))
    account = TwitterAccount(1, 'one', '', 'token', 'cookie', '', 'ACTIVE')
    client = XCliClient()
    monkeypatch.setattr(client, 'search', lambda *a, **kw: pytest.fail('must not retry via another transport'))
    with pytest.raises(XCliError) as exc:
        client.search_top_past_week('لپتاپ', account=account)
    assert exc.value.kind == kind


def test_cli_error_is_not_an_empty_search(monkeypatch):
    client = XCliClient()
    def fail(*a, **kw):
        raise XCliError('expired cookies', kind='auth')
    monkeypatch.setattr(client, 'search', fail)
    with pytest.raises(XCliError) as exc:
        client.search_top_past_week('لپتاپ')
    assert exc.value.kind == 'auth'


def test_worker_rotates_auth_and_rate_limited_accounts_then_handles_empty_and_good(monkeypatch, tmp_path):
    monkeypatch.setattr('workers.x_collector.worker.is_political_x_post', lambda _: False)
    now = datetime.now(timezone.utc).isoformat()
    tweet = SimpleNamespace(id='101', text='دنبال لپتاپ هستم', lang='fa', created_at=now,
                            author=SimpleNamespace(id='buyer', screen_name='buyer', name='Buyer'))
    install_client(monkeypatch, iter([RuntimeError('401 Unauthorized'), RuntimeError('429'), [], [tweet]]))
    pool = TwitterAccountPool.__new__(TwitterAccountPool)
    pool.accounts = [TwitterAccount(i, str(i), '', 'token', 'cookie', '', 'ACTIVE') for i in (1, 2, 3)]
    monkeypatch.setattr(pool, '_get_pg_conn', lambda: None)
    monkeypatch.setattr(pool, 'refresh', lambda: None)
    collector = XCollector(client=XCliClient(), account_pool=pool, output_dir=tmp_path / 'out',
                           state_path=tmp_path / 'state', max_retries=3, circuit_breaker=10,
                           sleeper=lambda _: None, sleep_min=0, sleep_max=0)
    result = collector.run(owned_queries(['empty', 'good']))
    assert result['collected'] == 1
    assert result['failures'] == 2
    assert result['search_errors'] == {'auth': 1, 'rate_limit': 1}
    assert [a.status for a in pool.accounts] == ['CHALLENGED', 'COOLDOWN', 'ACTIVE']
    assert [a.total_actions for a in pool.accounts] == [0, 0, 2]


def test_expanded_legacy_search_error_is_not_recorded_as_success(monkeypatch, tmp_path):
    class LegacyTransport:
        def __init__(self):
            self.calls = 0
        def search(self, query, limit, account=None):
            self.calls += 1
            if self.calls == 2:
                raise XCliError('expired cookies', kind='auth')
            return []
    pool = TwitterAccountPool.__new__(TwitterAccountPool)
    pool.accounts = [TwitterAccount(1, 'one', '', 'token', 'cookie', '', 'ACTIVE')]
    monkeypatch.setattr(pool, '_get_pg_conn', lambda: None)
    monkeypatch.setattr(pool, 'refresh', lambda: None)
    monkeypatch.setenv('X_MIN_INSPECT_POSTS', '1')
    monkeypatch.setenv('X_MIN_QUALIFYING_POSTS', '1')
    monkeypatch.setenv('X_MAX_INSPECT_POSTS', '2')
    client = LegacyTransport()
    collector = XCollector(client=client, account_pool=pool, output_dir=tmp_path / 'out',
                           state_path=tmp_path / 'state', max_per_query=1, sleeper=lambda _: None)
    result = collector.run(owned_queries(['legacy']))
    assert client.calls == 2
    assert result['status'] == 'AUTH_FAILED'
    assert pool.accounts[0].status == 'CHALLENGED'
    assert pool.accounts[0].total_actions == 0


@pytest.mark.parametrize('code,kind', [('not_authenticated', 'auth'), ('rate_limited', 'rate_limit'), ('network_error', 'transient')])
def test_native_structured_error_code_does_not_depend_on_message(monkeypatch, code, kind):
    class NativeError(RuntimeError):
        error_code = code
    install_client(monkeypatch, iter([NativeError('opaque upstream failure')]))
    account = TwitterAccount(1, 'one', '', 'token', 'cookie', '', 'ACTIVE')
    with pytest.raises(XCliError) as exc:
        XCliClient().search_top_past_week('لپتاپ', account=account)
    assert exc.value.kind == kind
