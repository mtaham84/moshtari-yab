"""Offline tests: fake LLM (need_engine.mock) + hash embeddings; state + vectors in PostgreSQL/pgvector (throw-away schemas)."""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import pytest

from need_engine.config import EngineConfig
from need_engine.engine import NeedEngine
from need_engine.mock import mock_llm
from need_engine.retrieve import select_candidates
from need_engine.schemas import ChatMessage, Constraints, NeedCard, Product, Requirement
from need_engine.scoring import score_match
from need_engine.sources import JsonlProductSource, SQLMessageSource
from need_engine.store import Store
from need_engine.windowing import build_windows, is_ready, render
from telegram_crawler.db import Archive

T0 = datetime(2026, 10, 8, 10, 0, tzinfo=timezone.utc)
CHAT = -1001001


@pytest.fixture
def cfg_for(tmp_path, pg_dsn, pg_schema):
    def make(**kw) -> EngineConfig:
        c = EngineConfig()
        c.embed_backend = "hash"
        c.database_url = pg_dsn
        c.state_schema = pg_schema("ne")
        c.crawler_schema = kw.pop("crawler_schema", "crawler")
        c.products_source = f"jsonl:{tmp_path / 'products.jsonl'}"
        c.max_workers = 1
        for k, v in kw.items():
            setattr(c, k, v)
        return c
    return make


@pytest.fixture
def crawler(pg_dsn, pg_schema):
    """A real crawler archive (telegram_crawler.db) in its own schema, with one monitored group."""
    a = Archive(pg_dsn, pg_schema("cr"))
    a.upsert_chat({"chat_id": CHAT, "type": "supergroup", "title": "گروه تست", "username": "testgroup"}, monitored=True)
    yield a
    a.close()


def add(a: Archive, mid: int, uid: int, text: str, minutes: int, *, name: str = "علی", username: str | None = "ali",
        bot: bool = False, reply: int | None = None, **kw) -> None:
    a.add_user({"user_id": uid, "first_name": name, "username": username, "is_bot": bot})
    a.add_message({"chat_id": CHAT, "message_id": mid, "sender_user_id": uid, "text": text,
                   "date": T0 + timedelta(minutes=minutes), "reply_to_msg_id": reply, **kw})


def msg(i, text, author="u1", minutes=0, chat="-1001", reply=None, bot=False) -> ChatMessage:
    return ChatMessage(chat_id=chat, message_id=i, row_id=i, author_id=author, author_name=f"name_{author}", text=text,
                       date=T0 + timedelta(minutes=minutes), reply_to=reply, is_bot=bot)


def products_file(path: Path) -> None:
    items = [
        {"product_id": "P1", "seller_id": "S1", "title": "دستکش گرم موتور", "description": "دستکش زمستانی", "product_type": "دستکش",
         "price_toman": 900000, "city": "تهران", "ships_nationwide": True},
        {"product_id": "P2", "seller_id": "S2", "title": "کاپشن ضد باد", "description": "کاپشن", "product_type": "کاپشن",
         "price_toman": 4000000, "city": "کرج", "ships_nationwide": False},
    ]
    path.write_text("\n".join(json.dumps(x, ensure_ascii=False) for x in items), encoding="utf-8")


# ── windowing ────────────────────────────────────────────────────────────────
def test_triggers(cfg_for):
    c = cfg_for(trigger_count=3, silence_minutes=10, max_wait_minutes=30)
    p = [msg(1, "a", minutes=0), msg(2, "b", minutes=1)]
    assert not is_ready(p, T0 + timedelta(minutes=5), c)[0]
    assert is_ready(p, T0 + timedelta(minutes=12), c)[0]                 # quiet
    p3 = p + [msg(3, "c", minutes=2)]
    assert is_ready(p3, T0 + timedelta(minutes=3), c)[0]                 # count
    busy = [msg(i, "x", minutes=i * 5) for i in range(1, 3)]
    assert is_ready(busy, T0 + timedelta(minutes=31), c)[0]               # max wait (oldest at 5 min)


def test_windows_context_noise_and_gaps(cfg_for):
    c = cfg_for(window_size=2, context_messages=1, gap_marker_minutes=60)
    st = Store(c.database_url, c.state_schema)
    st.mark_analysed("-1001", [msg(1, "قبلاً گفتم موتور دارم")], keep_recent=10)
    pend = [msg(2, "مرسی"), msg(3, "فردا میرم سفر", minutes=5), msg(4, "[sticker]", bot=True, minutes=6),
            msg(5, "دستام یخ می‌زنه", minutes=200), msg(6, "دنبال دستکشم", minutes=201)]
    ws = build_windows("-1001", pend, st, c)
    assert [m.message_id for m in ws[0].new] == [3, 5]                    # noise and bot removed
    assert [m.message_id for m in ws[0].context] == [1]
    assert {m.message_id for w in ws for m in w.consumed} == {2, 3, 4, 5, 6}
    txt = render(ws[0], c)
    assert "EARLIER messages" in txt and "⏸" in txt and "#1 " in txt


# ── scoring / selection ──────────────────────────────────────────────────────
def need(**kw) -> NeedCard:
    base = dict(need_id="need_1", chat_id="c", author_id="u", label="explicit_need", is_opportunity=True,
                created_at=T0, updated_at=T0)
    base.update(kw)
    return NeedCard(**base)


def test_score_budget_city_and_must():
    c = EngineConfig()
    n = need(requirements=[Requirement(text="ضد آب", must=True)], constraints=Constraints(budget_toman=1_000_000, city="تهران"))
    ok = Product(product_id="a", title="x", price_toman=1_100_000, city="تهران")
    s1, v1 = score_match(n, ok, "yes", ["met"], c)
    assert s1 == 1.0 and v1.unmet == 0 and v1.total == 3
    pricey = Product(product_id="b", title="x", price_toman=1_900_000, city="کرج", ships_nationwide=False)
    s2, v2 = score_match(n, pricey, "yes", ["met"], c)
    assert s2 == pytest.approx(0.6 * 0.6) and v2.unmet == 2 and len(v2.conflicts) == 2
    s3, _ = score_match(n, ok, "partly", ["unmet"], c)
    assert s3 == pytest.approx(0.6 * 0.5 * 0.6)


def test_adaptive_selection_floor_min_and_bm25():
    c = EngineConfig()
    c.sim_floor, c.sim_gap, c.sim_top_n, c.cand_min, c.bm25_extra = 0.3, 0.1, 3, 2, 1
    dense = np.array([0.9, 0.85, 0.8, 0.5, 0.35, 0.1], dtype=np.float32)
    sel = select_candidates(dense, [4, 5], c)                   # mean(top3)=0.85 → thr 0.75
    assert sel[:3] == [0, 1, 2] and 4 in sel and 5 not in sel   # bm25 extra above floor only
    nothing = np.array([0.2, 0.1], dtype=np.float32)
    assert select_candidates(nothing, [0], c) == []             # catalog has nothing → no LLM call


# ── end to end ───────────────────────────────────────────────────────────────
def iso(minutes: int) -> str:
    return (T0 + timedelta(minutes=minutes)).isoformat()


def test_streaming_end_to_end_read_only(tmp_path, cfg_for, crawler):
    add(crawler, 1, 11, "سلام بچه‌ها", 0)
    add(crawler, 2, 11, "دنبال دستکش گرم موتورم بودجه 1 میلیون", 1)
    add(crawler, 3, 12, "😂", 2, name="سارا", username=None)
    products_file(tmp_path / "products.jsonl")
    c = cfg_for(crawler_schema=crawler.schema, trigger_count=100, silence_minutes=10)
    snapshot = lambda: crawler.conn.execute(f"SELECT md5(string_agg(t::text, '|' ORDER BY id)) AS h FROM {crawler.schema}.tg_messages t").fetchone()["h"]
    before = snapshot()
    eng = NeedEngine(c, mock_llm=mock_llm)

    r1 = eng.run_once(now=T0 + timedelta(minutes=3))           # not quiet long enough → nothing analysed
    assert r1.ingested == 3 and r1.analysed_messages == 0
    r2 = eng.run_once(now=T0 + timedelta(minutes=20))          # quiet → analysed
    assert r2.analysed_messages == 3 and r2.needs_new == 1 and len(r2.opportunities) == 1
    o = r2.opportunities[0]
    assert o.candidate.username == "ali" and o.candidate.profile_url == "https://t.me/ali"
    assert o.source.evidence[0].url == "https://t.me/testgroup/2" and o.source.chat_title == "گروه تست"
    assert o.need.constraints["budget_toman"] == 1_000_000
    assert all(0 <= m.match_score <= 1 for m in o.matched_products)
    assert o.cost.toman > 0 and o.cost.llm_calls >= 2
    ids = [m.product_id for m in o.matched_products]
    assert "P1" in ids and "P2" not in ids   # P2 costs 4x the budget -> dropped before the LLM
    assert snapshot() == before              # crawler archive untouched
    published = eng.store.published_after(0)
    assert [p["payload"]["opportunity_id"] for p in published] == [o.opportunity_id]
    vec_rows = eng.store._all("SELECT count(*) AS n FROM {s}.product_vectors")[0]["n"]
    assert vec_rows >= 2 and eng.store.totals()["messages_analysed"] == 3

    # same person later says it's solved → status change is emitted for the same opportunity
    add(crawler, 4, 11, "گرفتمش ممنون، دستکش", 60)
    r3 = eng.run_once(now=T0 + timedelta(minutes=80))
    assert r3.analysed_messages == 1
    assert [x.status for x in r3.opportunities] == ["resolved"] and r3.opportunities[0].opportunity_id == o.opportunity_id
    latest = eng.store.published_after(published[0]["seq"])
    assert len(latest) == 1 and latest[0]["payload"]["status"] == "resolved"


def test_engine_cannot_write_to_the_crawler_archive(cfg_for, crawler):
    import psycopg

    src = SQLMessageSource(cfg_for(crawler_schema=crawler.schema))
    with pytest.raises(psycopg.errors.ReadOnlySqlTransaction):
        src.db.conn.execute(f"DELETE FROM {crawler.schema}.tg_messages")


def test_new_product_back_matches_stored_needs(tmp_path, cfg_for, crawler):
    add(crawler, 1, 11, "دنبال یه کوله سفری سبکم", 0)
    products_file(tmp_path / "products.jsonl")
    eng = NeedEngine(cfg_for(crawler_schema=crawler.schema), mock_llm=mock_llm)
    eng.run_once(now=T0 + timedelta(hours=1))
    with (tmp_path / "products.jsonl").open("a", encoding="utf-8") as f:
        f.write("\n" + json.dumps({"product_id": "P3", "title": "کوله سفری سبک", "product_type": "کوله", "price_toman": 700000}, ensure_ascii=False))
    r = eng.run_once(now=T0 + timedelta(hours=2))
    assert r.products_changed == 1 and r.analysed_messages == 0          # messages are not re-read
    assert any("P3" in [m.product_id for m in o.matched_products] for o in r.opportunities)


def test_jsonl_sources_and_person_merge(tmp_path, cfg_for):
    chats = tmp_path / "chats.jsonl"
    chats.write_text(json.dumps({"chat_id": "C1", "group_title": "g", "messages": [
        {"message_id": 1, "date": iso(0), "author_id": "u1", "author_name": "a", "text": "دنبال دستکش موتورم", "reply_to": None},
        {"message_id": 2, "date": iso(300), "author_id": "u1", "author_name": "a", "text": "هنوز دنبال دستکش موتورم", "reply_to": None},
    ], "labels": []}, ensure_ascii=False), encoding="utf-8")
    products_file(tmp_path / "products.jsonl")
    c = cfg_for(messages_source=f"jsonl:{chats}", window_size=1, context_messages=0, person_merge_sim=0.5)
    r = NeedEngine(c, mock_llm=mock_llm).run_once(flush=True)
    assert r.windows == 2 and r.needs_new == 1 and r.needs_updated == 1   # same person, same need → one card
    assert len(JsonlProductSource(str(tmp_path / "products.jsonl")).all()) == 2


def test_sql_source_reads_archive_with_users_chats_and_old_parents(cfg_for, crawler):
    add(crawler, 3, 12, "کسی کوله کوهنوردی سراغ داره؟", -3000, name="سارا", username=None, is_context=True)  # old parent
    add(crawler, 6, 5, "bot says hi", 0, name="Bot", username="promo_bot", bot=True)
    add(crawler, 7, 11, "منم دنبالشم", 1, reply=3)
    add(crawler, 8, 11, "", 2, is_service=True, service_action="ChatAddUser")
    c = cfg_for(crawler_schema=crawler.schema, context_messages=0)
    src = SQLMessageSource(c)
    m = src.fetch_after(0, 10)
    assert [x.message_id for x in m] == [6, 7]                           # context-only parent and service message skipped
    assert m[0].is_bot and m[0].chat_username == "testgroup" and m[0].chat_id == str(CHAT)
    assert (m[1].author_name, m[1].author_username, m[1].reply_to_text, m[1].reply_to_author_name) == \
        ("علی", "ali", "کسی کوله کوهنوردی سراغ داره؟", "سارا")
    assert src.fetch_after(m[-1].row_id, 10) == []
    w = build_windows(str(CHAT), [m[1]], Store(c.database_url, c.state_schema), c)[0]
    assert [p.message_id for p in w.parents] == [3] and "کوله کوهنوردی" in render(w, c)   # old parent shown to the model


def test_store_keeps_vectors_in_pgvector(cfg_for):
    c = cfg_for()
    st = Store(c.database_url, c.state_schema)
    n = NeedCard(need_id="need_x", chat_id="c", author_id="u", label="explicit_need", is_opportunity=True, created_at=T0, updated_at=T0)
    q = np.random.default_rng(0).normal(size=(3, 16)).astype(np.float32)
    st.save_need(n, q[0], q)
    assert np.allclose(st.query_vecs("need_x"), q, atol=1e-6)
    (card, vec), = st.person_needs("c", "u")
    assert card.need_id == "need_x" and np.allclose(vec, q[0], atol=1e-6)
    typ = st._all("SELECT format_type(atttypid, atttypmod) AS t FROM pg_attribute WHERE attrelid = '{s}.need_vectors'::regclass AND attname = 'vec'")
    assert typ[0]["t"] == "vector"
