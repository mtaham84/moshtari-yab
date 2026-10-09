"""Admin-panel model registry: role → model, per-provider endpoint/key, prices, rate limits and wallet blocking."""
from __future__ import annotations

import json

import pytest

from need_engine.access import SourceAccess
from need_engine.config import EngineConfig
from need_engine.llm import LLMClient
from need_engine.registry import ModelRegistry, role_of
from need_engine.store import Store

MODELS = [
    {"name": "cheap-1", "base_url": "https://a.example/v1", "api_key": "KA", "input_price_usd": 0.1, "output_price_usd": 0.4,
     "priority": 10, "use_extract": True, "use_verify": False, "use_reply": True, "rpm": 7, "tpm": None, "rpd": 100},
    {"name": "smart-2", "base_url": "https://b.example/v1", "api_key": "KB", "input_price_usd": 2.0, "output_price_usd": 8.0,
     "priority": 20, "use_extract": True, "use_verify": True, "use_reply": False, "rpm": None, "tpm": None, "rpd": None},
]


class FakeDB:
    """Answers the registry's queries like the panel tables would."""

    def __init__(self, models=MODELS, enforce=True, balances=None, tables=True):
        self.models, self.enforce, self.balances, self.tables = models, enforce, balances or {}, tables

    def all(self, sql, params=()):
        if "to_regclass" in sql:
            return [{"t": params[0] if self.tables else None}]
        if "billing_aimodel" in sql:
            return list(self.models)
        if "billing_billingsettings" in sql:
            return [{"usd_to_toman": 50000, "enforce_balance": self.enforce, "min_balance_toman": 0}]
        if "billing_wallet" in sql:
            return [{"business_id": b, "balance_toman": v} for b, v in self.balances.items()]
        return []


def cfg(**kw) -> EngineConfig:
    c = EngineConfig()
    c.products_source, c.messages_source, c.llm_api_key, c.llm_base_url = "db", "db", "ENVKEY", "https://env.example/v1"
    c.prices = {"env-model": [1.0, 1.0]}
    for k, v in kw.items():
        setattr(c, k, v)
    return c


def test_role_of_stages():
    assert role_of("need_extraction") == role_of("product_cards") == role_of("product_extract") == "extract"
    assert role_of("verify") == role_of("new_product_verify") == "verify"
    assert role_of("reply") == role_of("reply_sample") == role_of("reply_rewrite") == "reply"
    assert role_of("embed_needs") is None


def test_registry_picks_models_endpoints_and_prices():
    r = ModelRegistry(cfg(), db=FakeDB())
    assert r.model_for("need_extraction") == "cheap-1"          # lowest priority number wins
    assert r.model_for("verify") == "smart-2"
    assert r.model_for("reply") == "cheap-1"
    assert r.endpoint("smart-2") == ("https://b.example/v1", "KB")
    assert r.endpoint("env-model") == ("https://env.example/v1", "ENVKEY")   # unknown model → .env
    assert r.price("cheap-1") == (0.1, 0.4) and r.price("env-model") == (1.0, 1.0)
    assert r.rate_limit("cheap-1") == {"rpm": 7, "tpm": None, "rpd": 100} and r.rate_limit("smart-2") is None
    assert r.usd_to_toman() == 50000


def test_registry_falls_back_without_panel_tables():
    r = ModelRegistry(cfg(), db=FakeDB(tables=False))
    assert r.model_for("verify") is None and r.endpoint("x") == ("https://env.example/v1", "ENVKEY")
    assert r.usd_to_toman() == EngineConfig().usd_to_toman and r.blocked() == frozenset()
    assert not ModelRegistry(cfg(products_source="jsonl:x")).enabled


def test_blocked_sellers_and_access_rule():
    reg = ModelRegistry(cfg(), db=FakeDB(balances={"1": 0, "2": 5000, "3": -10}))
    assert reg.blocked() == frozenset({"1", "3"})
    assert ModelRegistry(cfg(), db=FakeDB(enforce=False, balances={"1": 0})).blocked() == frozenset()

    acc = SourceAccess(cfg(), db=FakeDB(), registry=reg)
    acc.enabled, acc.loaded = True, True
    acc.blocked = reg.blocked()
    acc.rules = {"-100": "*", "-200": frozenset({"1"}), "-300": frozenset({"1", "2"})}
    assert acc.analysed("-100") and not acc.analysed("-200") and acc.analysed("-300")
    assert acc.owners("-300") == ["2"]                      # blocked owner pays nothing
    assert not acc.allows("-100", "1") and acc.allows("-100", "2")


def test_llm_client_uses_panel_model_endpoint_and_price(pg_dsn, pg_schema, monkeypatch):
    store = Store(pg_dsn, pg_schema("reg"))
    c = cfg(database_url=pg_dsn)
    llm = LLMClient(c, store, registry=ModelRegistry(c, db=FakeDB()))
    seen = {}

    class Resp:
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self):
            return json.dumps({"choices": [{"message": {"content": '{"ok": true}'}}],
                               "usage": {"prompt_tokens": 1_000_000, "completion_tokens": 0, "total_tokens": 1_000_000}}).encode()

    def fake_urlopen(req, timeout=0):
        seen["url"], seen["auth"] = req.full_url, req.headers.get("Authorization")
        seen["model"] = json.loads(req.data)["model"]
        return Resp()

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    data, usage = llm.complete_json("verify", "env-model", "sys", "user", businesses=["7"])
    assert data == {"ok": True}
    assert seen == {"url": "https://b.example/v1/chat/completions", "auth": "Bearer KB", "model": "smart-2"}
    assert usage["toman"] == pytest.approx(2.0 * 50000)       # 1M input tokens × $2 × 50,000
    row = store._all("SELECT model, business_id, usd FROM {s}.costs")[0]
    assert row["model"] == "smart-2" and row["business_id"] == "7" and row["usd"] == pytest.approx(2.0)
    store.close()
