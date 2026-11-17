from types import SimpleNamespace

import pytest

from need_engine.schemas import Product
from need_engine.registry import ModelRegistry
from need_engine.tests.test_registry import FakeDB, cfg
from workers.x_collector import query_source


@pytest.fixture
def query_setup(monkeypatch):
    config = cfg()
    db = FakeDB(balances={'9': 100})
    registry = ModelRegistry(config, db=db)
    monkeypatch.setattr('workers.x_collector.balance_gate.ModelRegistry', lambda *a, **kw: registry)
    monkeypatch.setattr(query_source, '_terms_and_cards', lambda *a: ({}, {}))
    monkeypatch.setattr('workers.x_collector.portfolio.manage_portfolio', lambda config, families, items, **kw: items)
    monkeypatch.setattr('workers.x_collector.families.load_notes', lambda _: {})
    monkeypatch.delenv('X_SELLER_ALLOWLIST', raising=False)
    product = Product(product_id='91', business_id='9', title='لپتاپ لنوو', category_path='کالای دیجیتال / لپتاپ',
                      x_search_enabled=True)
    return config, SimpleNamespace(db=db), [product], db


def load(setup):
    config, source, products, _ = setup
    return query_source._load_intent_queries(config, source, products, 'product', None)


def test_funded_non_dental_seller_gets_queries(query_setup):
    queries = load(query_setup)
    assert queries
    assert all(item['business_ids'] == ['9'] for item in queries)


@pytest.mark.parametrize('balance', [0, -10])
def test_unfunded_seller_gets_no_queries(query_setup, balance):
    query_setup[3].balances['9'] = balance
    assert load(query_setup) == []


@pytest.mark.parametrize('balance', [40, 50])
def test_enforced_minimum_balance_blocks_seller(query_setup, balance):
    db = query_setup[3]
    db.balances['9'] = balance
    original = db.all
    def all_rows(sql, params=()):
        rows = original(sql, params)
        if 'SELECT usd_to_toman' in sql:
            rows[0]['min_balance_toman'] = 50
        return rows
    db.all = all_rows
    assert load(query_setup) == []


def test_wallet_read_failure_fails_closed_and_logs(query_setup, caplog):
    def fail(*a, **kw):
        raise RuntimeError('wallet read failed')
    query_setup[3].all = fail
    assert load(query_setup) == []
    assert any(r.levelname == 'ERROR' and 'balances' in r.message for r in caplog.records)


@pytest.mark.parametrize('allowlist,eligible', [('', True), ('9, 11', True), ('4', False)])
def test_optional_seller_allowlist(query_setup, monkeypatch, allowlist, eligible):
    monkeypatch.setenv('X_SELLER_ALLOWLIST', allowlist)
    assert bool(load(query_setup)) is eligible


def test_disabled_x_search_stays_ineligible(query_setup):
    query_setup[2][0].x_search_enabled = False
    assert load(query_setup) == []


def test_registry_wallet_failure_after_a_success_does_not_reuse_old_balance():
    db = FakeDB(balances={'9': 100})
    registry = ModelRegistry(cfg(), db=db)
    assert registry.blocked(force=True) == frozenset()
    def fail(*a, **kw):
        raise RuntimeError('disconnected')
    db.all = fail
    with pytest.raises(RuntimeError, match='wallet balances unavailable'):
        registry.blocked(force=True)
