"""Reply links go to the seller's own page through /r/; no product page → no link. Seller-edited product cards are
used directly (no LLM call); the url is not part of the card hash but always current."""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

from need_engine.config import EngineConfig
from need_engine.engine import NeedEngine
from need_engine.mock import mock_llm
from need_engine.reply import write_reply
from need_engine.schemas import Product
from telegram_crawler.db import Archive

T0 = datetime(2026, 10, 8, 10, 0, tzinfo=timezone.utc)
CHAT = -1001002


def _cfg(tmp_path, pg_dsn, schema, crawler_schema) -> EngineConfig:
    c = EngineConfig()
    c.embed_backend, c.database_url, c.state_schema, c.crawler_schema = "hash", pg_dsn, schema, crawler_schema
    c.products_source = f"jsonl:{tmp_path / 'products.jsonl'}"
    c.max_workers, c.source_access, c.trigger_count = 1, "open", 1
    c.public_base_url = "https://panel.example.ir/"
    return c


def _products(path, items):
    path.write_text("\n".join(json.dumps(x, ensure_ascii=False) for x in items), encoding="utf-8")


def test_click_url_and_no_hardcoded_domain():
    c = EngineConfig()
    c.public_base_url = "https://panel.example.ir/"
    assert c.click_url("7", "need_3") == "https://panel.example.ir/r/7/?ref=need_3"
    assert not hasattr(c, "product_url_template")


def test_url_is_not_part_of_the_card_hash_but_override_is():
    a = Product(product_id="1", title="دستکش", url="https://a.ir/x")
    assert a.content_hash() == Product(product_id="1", title="دستکش", url="https://b.ir/y").content_hash()
    assert a.content_hash() != Product(product_id="1", title="دستکش", card_override={"what_it_is": "x"}).content_hash()


def test_rewrite_variant_bypasses_cache_and_changes_temperature():
    class LLM:
        calls = []

        def complete_json(self, stage, model, system, user, **kw):
            self.calls.append((stage, user, kw))
            return {"reply": "سلام {{LINK}}"}, {"toman": 0.0}

    llm = LLM()
    args = dict(person_messages="x", situation="y", product_line="z", conflicts=[], style=None, link="L", ref="r")
    write_reply(llm, EngineConfig(), **args)
    write_reply(llm, EngineConfig(), stage="reply_rewrite", variant=2, **args)
    assert "write_a_new_version" not in llm.calls[0][1] and '"write_a_new_version": 2' in llm.calls[1][1]
    assert llm.calls[0][2]["temperature"] < llm.calls[1][2]["temperature"]


def test_drafts_link_only_products_with_a_page_and_overrides_skip_the_llm(tmp_path, pg_dsn, pg_schema):
    arch = Archive(pg_dsn, pg_schema("cr"))
    arch.upsert_chat({"chat_id": CHAT, "type": "supergroup", "title": "گروه", "username": "g2"}, monitored=True)
    _products(tmp_path / "products.jsonl", [
        {"product_id": "10", "seller_id": "1", "title": "دستکش گرم موتور", "product_type": "دستکش", "price_toman": 900000,
         "url": "https://shop.example.ir/gloves"},
        {"product_id": "11", "seller_id": "2", "title": "دستکش گرم زمستانی موتور", "product_type": "دستکش", "price_toman": 800000,
         "card_override": {"what_it_is": "دستکش موتورسواری گرم", "problems_solved": ["سرمای دست موقع موتورسواری"]}},
    ])
    stages = []

    def spy(stage, system, user):
        stages.append((stage, user))
        return mock_llm(stage, system, user)

    eng = NeedEngine(_cfg(tmp_path, pg_dsn, pg_schema("ne"), arch.schema), mock_llm=spy)
    arch.add_user({"user_id": 5, "first_name": "علی", "username": "ali", "is_bot": False})
    arch.add_message({"chat_id": CHAT, "message_id": 1, "sender_user_id": 5, "text": "دنبال دستکش گرم موتورم",
                      "date": T0, "reply_to_msg_id": None})
    r = eng.run_once(now=T0 + timedelta(minutes=1))

    cards_calls = [u for s, u in stages if s == "product_cards"]
    assert cards_calls and all('"product_id": "11"' not in u for u in cards_calls)     # edited card: no LLM call
    assert eng.catalog.cards["11"].what_it_is == "دستکش موتورسواری گرم"
    drafts = {m.product_id: m.reply_draft for o in r.opportunities for m in o.matched_products}
    assert "https://panel.example.ir/r/10/?ref=" in drafts["10"]
    assert "http" not in drafts["11"] and "{{LINK}}" not in drafts["11"]                  # no page → no link
    assert all("customerweb" not in (d or "") for d in drafts.values())

    # changing only the url does not rebuild the card, but the new url is used
    _products(tmp_path / "products.jsonl", [
        {"product_id": "10", "seller_id": "1", "title": "دستکش گرم موتور", "product_type": "دستکش", "price_toman": 900000,
         "url": "https://shop.example.ir/new"},
        {"product_id": "11", "seller_id": "2", "title": "دستکش گرم زمستانی موتور", "product_type": "دستکش", "price_toman": 800000,
         "card_override": {"what_it_is": "دستکش موتورسواری گرم", "problems_solved": ["سرمای دست موقع موتورسواری"]}},
    ])
    r2 = eng.run_once(now=T0 + timedelta(minutes=2))
    assert r2.products_changed == 0 and eng.catalog.products[eng.catalog.pid_index["10"]].url == "https://shop.example.ir/new"
    eng.store.close()
    arch.close()
