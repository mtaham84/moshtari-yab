"""Query generation reads X_QUERY_* from the environment, and need_engine/telegram_crawler load a developer's .env
into it at import time (.env.example sets e.g. X_QUERY_MAX_PER_PRODUCT_TARGETED=4). Tests run against the code
defaults, like CI where there is no .env; a test that needs a value sets it with monkeypatch."""
import os

import pytest


@pytest.fixture
def funded_x_seller(monkeypatch):
    """Provide a funded owner through the registry DB boundary; retain the real wallet gate."""
    from need_engine.registry import ModelRegistry
    from need_engine.tests.test_registry import FakeDB, cfg

    db = FakeDB(balances={'9': 100})
    monkeypatch.setattr('workers.x_collector.balance_gate.ModelRegistry',
                        lambda *args, **kwargs: ModelRegistry(cfg(), db=db))


@pytest.fixture(autouse=True)
def _code_default_query_settings(monkeypatch):
    for name in [n for n in os.environ if n.startswith("X_QUERY_")]:
        monkeypatch.delenv(name)


@pytest.fixture(autouse=True)
def _isolated_account_pool(monkeypatch):
    """Tests must opt into PostgreSQL with a DSN; never mutate accounts from a developer's .env."""
    from workers.x_collector.account_pool import TwitterAccountPool

    connect = TwitterAccountPool._get_pg_conn
    monkeypatch.setattr(TwitterAccountPool, '_get_pg_conn', lambda self: connect(self) if self.dsn else None)
    for key in ('TWITTER_AUTH_TOKEN', 'TWITTER_CT0', 'TWITTER_PROXY', 'TWITTER_USERNAME'):
        monkeypatch.delenv(key, raising=False)
