from datetime import datetime, timedelta, timezone

import pytest
from django.db import connection
from psycopg.conninfo import make_conninfo

from apps.discovery.models import TwitterEngineAccount
from workers.x_collector.account_pool import TwitterAccountPool


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize('refresh', [True, False])
def test_daily_counter_resets_in_database_at_utc_midnight(monkeypatch, refresh):
    from workers.x_collector import account_pool

    now = datetime(2026, 11, 14, 23, 59, tzinfo=timezone.utc)
    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return now
    monkeypatch.setattr(account_pool, 'datetime', Clock)
    monkeypatch.setenv('X_ACCOUNT_MAX_DAILY_ACTIONS', '2')
    obj = TwitterEngineAccount.objects.create(username='midnight', daily_action_count=2,
                                               total_action_count=12, last_action_at=now)
    params = connection.get_connection_params()
    dsn = make_conninfo(**{k: params[k] for k in ('dbname', 'user', 'password', 'host', 'port') if params.get(k)})
    pool = TwitterAccountPool(dsn)
    assert pool.get_next_account() is None
    now += timedelta(minutes=2)
    if refresh:
        pool.refresh()
        obj.refresh_from_db()
        assert obj.daily_action_count == 0
    account = pool.get_next_account()
    assert account is not None
    pool.record_success(account)
    obj.refresh_from_db()
    assert obj.daily_action_count == 1
    assert obj.total_action_count == 13
    pool.refresh()
    assert pool.get_next_account().daily_actions == 1
