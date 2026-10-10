"""Offline tests: fake LLM (need_engine.mock) + hash embeddings; state + vectors in PostgreSQL/pgvector (throw-away schemas)."""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import pytest

from need_engine.config import EngineConfig
from need_engine.extract import _confirmed_x_buyer
from need_engine.engine import NeedEngine
from need_engine.mock import mock_llm
from need_engine.retrieve import select_candidates
from need_engine.schemas import ChatMessage, Constraints, NeedCard, Product, Requirement
from need_engine.scoring import score_match
from need_engine.sources import JsonlProductSource, SQLMessageSource, XMessageSource
from workers.x_collector.query_source import queries_from_products
from need_engine.store import Store
from need_engine.windowing import build_windows, ready_batch, render
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
        c.source_access = "open"
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
def test_triggers_are_count_only(cfg_for):
    c = cfg_for(trigger_count=3, max_wait_minutes=0)
    p = [msg(1, "a", minutes=0), msg(2, "b", minutes=1)]
    assert ready_batch(p, T0 + timedelta(days=30), c)[0] == []          # never time based: waits for 3 messages
    p5 = p + [msg(i, "x", minutes=i) for i in range(3, 6)]
    batch, why = ready_batch(list(reversed(p5)), T0 + timedelta(minutes=6), c)
    assert [m.message_id for m in batch] == [1, 2, 3] and why == "3 new messages"   # whole batches, oldest first
    p6 = p5 + [msg(6, "y", minutes=6)]
    assert len(ready_batch(p6, T0, c)[0]) == 6
    fallback = cfg_for(trigger_count=3, max_wait_minutes=30)                         # optional, off by default
    assert len(ready_batch(p, T0 + timedelta(minutes=31), fallback)[0]) == 2
    assert ready_batch(p, T0 + timedelta(minutes=29), fallback)[0] == []


def test_x_source_ordering_namespace_and_url(cfg_for):
    class FakeDB:
        def all(self, sql, params):
            assert "ORDER BY row_id" in sql
            assert params == (4, 10)
            return [{"row_id": 5, "tweet_id": 1990000000000001001, "author_id": "81001",
                     "author_handle": "@buyer", "author_name": "خریدار", "text": "نیاز دارم",
                     "created_at": T0, "url": "https://x.com/buyer/status/1990000000000001001"}]

    source = XMessageSource(cfg_for(), conn=FakeDB())
    rows = source.fetch_after(4, 10)
    assert len(rows) == 1
    assert rows[0].chat_id == "x:public"
    assert rows[0].message_id == 1990000000000001001
    assert rows[0].author_id == "x_81001"
    assert rows[0].url == "https://x.com/buyer/status/1990000000000001001"
    assert rows[0].profile_url == "https://x.com/buyer"


def test_x_queries_come_from_products_and_category_keywords():
    product = Product(product_id="1", title="قهوه‌ساز صنعتی", category_path="کافه / تجهیزات",
                      category_keywords=["اسپرسوساز", "قهوه", "اسپرسوساز"])
    assert queries_from_products([product]) == ["قهوه‌ساز صنعتی", "کافه / تجهیزات", "اسپرسوساز", "قهوه"]


def test_x_buyer_gate_requires_confirmed_intent_medium_strength_and_author_evidence():
    author_post = ChatMessage(chat_id="x:public", message_id=1, author_id="x_1", text="قصد خرید دارم", date=T0, platform="x")
    other_post = ChatMessage(chat_id="x:public", message_id=2, author_id="x_2", text="پیشنهاد خرید", date=T0, platform="x")
    candidate = {"buyer_intent_confirmed": True, "is_opportunity": True, "label": "explicit_need",
                 "strength": "strong", "need": "قهوه‌ساز", "solution_queries": ["قهوه‌ساز"],
                 "author_type": "individual", "promotional_content": False}
    assert _confirmed_x_buyer(candidate, [1], "x_1", {1: author_post})
    assert not _confirmed_x_buyer({**candidate, "buyer_intent_confirmed": False}, [1], "x_1", {1: author_post})
    assert not _confirmed_x_buyer({**candidate, "strength": "weak"}, [1], "x_1", {1: author_post})
    assert not _confirmed_x_buyer(candidate, [2], "x_1", {2: other_post})
    assert not _confirmed_x_buyer({**candidate, "label": "curiosity"}, [1], "x_1", {1: author_post})
    assert not _confirmed_x_buyer({**candidate, "author_type": "organization"}, [1], "x_1", {1: author_post})
    assert _confirmed_x_buyer({**candidate, "author_type": "unknown"}, [1], "x_1", {1: author_post})
    assert not _confirmed_x_buyer({**candidate, "author_type": "unknown"}, [1], "x_1", {1: author_post}, allow_unknown=False)
    assert not _confirmed_x_buyer({**candidate, "promotional_content": True}, [1], "x_1", {1: author_post})
    verified = author_post.model_copy(update={"author_verified": True})
    assert not _confirmed_x_buyer(candidate, [1], "x_1", {1: verified})
    corporate_bio = author_post.model_copy(update={"author_bio": "Official company store"})
    assert not _confirmed_x_buyer(candidate, [1], "x_1", {1: corporate_bio})


def test_independent_source_ingest_uses_distinct_cursors(cfg_for):
    class FakeStore:
        def __init__(self):
            self.values = {"fetch_cursor": 12, "fetch_cursor_x": 4}
            self.added = []

        def get(self, key, default=0):
            return self.values.get(key, default)

        def set(self, key, value):
            self.values[key] = value

        def add_pending(self, rows):
            self.added.extend(rows)
            return len(rows)

    class Source:
        def __init__(self, row):
            self.row = row

        def fetch_after(self, cursor, limit):
            assert cursor in (12, 4)
            return [self.row] if self.row.row_id > cursor else []

    from need_engine.engine import NeedEngine
    engine = NeedEngine.__new__(NeedEngine)
    engine.cfg = cfg_for(fetch_batch=10)
    engine.store = FakeStore()
    from need_engine.access import SourceAccess
    engine.access = SourceAccess(engine.cfg)   # source_access=open in tests
    telegram = ChatMessage(chat_id="-1001", message_id=13, row_id=13, author_id="a", text="تلگرام", date=T0)
    post = ChatMessage(chat_id="x:public", message_id=99, row_id=5, author_id="x_a", text="پست", date=T0, platform="x")
    assert engine._ingest_source(Source(telegram), "fetch_cursor") == 1
    assert engine._ingest_source(Source(post), "fetch_cursor_x", 2) == 1
    assert engine.store.values == {"fetch_cursor": 13, "fetch_cursor_x": 5}


def test_x_windows_never_include_other_posts_as_context(cfg_for):
    c = cfg_for(window_size=1, context_messages=5)
    st = Store(c.database_url, c.state_schema)
    st.mark_analysed("x:public", [ChatMessage(chat_id="x:public", message_id=1, author_id="a", text="old post", date=T0, platform="x")], 10)
    current = ChatMessage(chat_id="x:public", message_id=2, author_id="b", text="new post", date=T0, platform="x")
    from need_engine.windowing import build_windows
    window = build_windows("x:public", [current], st, c)[0]
    assert window.context == []
    assert "different authors" in render(window, c)
    st.close()


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
    c = cfg_for(crawler_schema=crawler.schema, trigger_count=4)
    snapshot = lambda: crawler.conn.execute(f"SELECT md5(string_agg(t::text, '|' ORDER BY id)) AS h FROM {crawler.schema}.tg_messages t").fetchone()["h"]
    eng = NeedEngine(c, mock_llm=mock_llm)

    r1 = eng.run_once(now=T0 + timedelta(days=1))              # 3 < 4 messages → nothing analysed, however long we wait
    assert r1.ingested == 3 and r1.analysed_messages == 0
    add(crawler, 4, 12, "👍", 3, name="سارا", username=None)
    before = snapshot()
    r2 = eng.run_once(now=T0 + timedelta(minutes=4))           # 4th message → batch analysed immediately
    assert r2.analysed_messages == 4 and r2.needs_new == 1 and len(r2.opportunities) == 1
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
    assert vec_rows >= 2 and eng.store.totals()["messages_analysed"] == 4

    # same person later says it's solved → status change is emitted for the same opportunity
    add(crawler, 5, 11, "گرفتمش ممنون، دستکش", 60)
    for i in (6, 7, 8):
        add(crawler, i, 12, "👌", 60 + i, name="سارا", username=None)
    r3 = eng.run_once(now=T0 + timedelta(minutes=80))
    assert r3.analysed_messages == 4
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
    eng = NeedEngine(cfg_for(crawler_schema=crawler.schema, trigger_count=1), mock_llm=mock_llm)
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


def test_source_access_rule_is_applied_before_the_llm(tmp_path, cfg_for, crawler):
    """no source → chat not analysed; PRIVATE → only its owners' products; GLOBAL → everyone's."""
    t = f"{crawler.schema}.communities"
    crawler.conn.execute(f"CREATE TABLE {t} (telegram_chat_id bigint, scope text, business_id text, is_active boolean)")
    products_file(tmp_path / "products.jsonl")
    add(crawler, 1, 11, "دنبال دستکش گرم موتورم بودجه 1 میلیون", 0)
    calls: list[str] = []

    def counting_llm(stage, system, user):
        calls.append(stage)
        return mock_llm(stage, system, user)

    def engine():
        return NeedEngine(cfg_for(crawler_schema=crawler.schema, trigger_count=1, source_access="panel",
                                  communities_table=t),       # fresh state schema each time
                          mock_llm=counting_llm)

    r = engine().run_once(now=T0 + timedelta(hours=1))
    assert r.ingested == 0 and r.analysed_messages == 0 and set(calls) == {"product_cards"}   # no message LLM call

    crawler.conn.execute(f"INSERT INTO {t} VALUES (%s, 'PRIVATE', 'S2', TRUE)", (int(CHAT),))
    eng = engine()
    r = eng.run_once(now=T0 + timedelta(hours=1))
    assert r.analysed_messages == 1
    payers = {(x["stage"], x["business_id"]) for x in eng.store._all("SELECT stage, business_id FROM {s}.costs")}
    assert ("need_extraction", "S2") in payers and ("need_extraction", None) not in payers   # private chat: owner pays
    assert {b for st, b in payers if st == "product_cards"} == {"S1", "S2"}                  # each product: its seller
    assert eng.store.totals(business_id="S2", chat_ids=[str(CHAT)])["messages_analysed"] == 1
    assert all(m.product_id != "P1" for o in r.opportunities for m in o.matched_products)   # S1 does not watch this chat

    crawler.conn.execute(f"INSERT INTO {t} VALUES (%s, 'GLOBAL', NULL, TRUE)", (int(CHAT),))
    r = engine().run_once(now=T0 + timedelta(hours=1))
    assert any(m.product_id == "P1" for o in r.opportunities for m in o.matched_products)


def test_source_access_rules():
    from need_engine.access import SourceAccess

    class FakeDB:
        def __init__(self, rows):
            self.rows = rows

        def all(self, sql, params=()):
            return [{"t": "x"}] if "to_regclass" in sql else self.rows

    c = EngineConfig()
    c.source_access, c.messages_source = "panel", "db"
    a = SourceAccess(c, FakeDB([{"telegram_chat_id": -1, "scope": "PRIVATE", "business_id": 7},
                                {"telegram_chat_id": -1, "scope": "PRIVATE", "business_id": 8},
                                {"telegram_chat_id": -2, "scope": "GLOBAL", "business_id": None},
                                {"telegram_chat_id": -2, "scope": "PRIVATE", "business_id": 7}]))
    a.refresh()
    assert a.sellers("-1") == {"7", "8"} and a.owners("-1") == ["7", "8"]
    assert a.sellers("-2") is None and a.owners("-2") == ["7"]       # global + private copy: everyone matched, 7 pays
    assert not a.analysed("-3") and a.analysed("-1")
    assert a.allows("-1", "7") and not a.allows("-1", "9") and a.allows("-2", "9") and not a.allows("-3", "7")


class _Wallets:
    """Stand-in for ModelRegistry.blocked(): sellers whose balance is used up."""
    def __init__(self, *blocked):
        self.ids = set(blocked)

    def blocked(self):
        return frozenset(self.ids)


def _paying_engine(tmp_path, cfg_for, crawler, scope, wallets, calls):
    t = f"{crawler.schema}.communities"
    crawler.conn.execute(f"CREATE TABLE IF NOT EXISTS {t} (telegram_chat_id bigint, scope text, business_id text, is_active boolean)")
    crawler.conn.execute(f"INSERT INTO {t} VALUES (%s, %s, %s, TRUE)", (int(CHAT), scope, "S1" if scope == "PRIVATE" else None))
    products_file(tmp_path / "products.jsonl")

    def counting_llm(stage, system, user):
        calls.append(stage)
        return mock_llm(stage, system, user)

    eng = NeedEngine(cfg_for(crawler_schema=crawler.schema, trigger_count=1, source_access="panel", communities_table=t),
                     mock_llm=counting_llm)
    eng.access.registry = wallets
    return eng


def test_private_chat_of_a_seller_without_balance_is_held_then_processed(tmp_path, cfg_for, crawler):
    wallets, calls = _Wallets("S1"), []
    eng = _paying_engine(tmp_path, cfg_for, crawler, "PRIVATE", wallets, calls)
    add(crawler, 1, 11, "دنبال دستکش گرم موتورم بودجه 1 میلیون", 0)
    r = eng.run_once(now=T0 + timedelta(hours=1))
    assert r.ingested == 1 and r.analysed_messages == 0 and "need_extraction" not in calls   # collected, not processed
    assert eng.store.get("waiting_payment")["sellers"]["S1"]["messages"] == 1
    assert not eng.store._all("SELECT 1 FROM {s}.costs WHERE business_id = 'S1'")             # S1 paid nothing

    wallets.ids.clear()                                                                        # topped up
    r = eng.run_once(now=T0 + timedelta(hours=2))
    assert r.analysed_messages == 1 and any(m.product_id == "P1" for o in r.opportunities for m in o.matched_products)
    assert eng.store.get("waiting_payment")["sellers"] == {}


def test_held_messages_older_than_the_need_ttl_are_dropped(tmp_path, cfg_for, crawler):
    eng = _paying_engine(tmp_path, cfg_for, crawler, "PRIVATE", _Wallets("S1"), [])
    add(crawler, 1, 11, "دنبال دستکش گرم موتورم", 0)
    eng.run_once(now=T0 + timedelta(hours=1))
    assert eng.store.pending_count(str(CHAT)) == 1
    eng.run_once(now=T0 + timedelta(days=eng.cfg.need_ttl_days + 1))
    assert eng.store.pending_count(str(CHAT)) == 0


def test_global_need_is_queued_for_a_blocked_seller_and_matched_after_top_up(tmp_path, cfg_for, crawler):
    wallets, calls = _Wallets(), []
    eng = _paying_engine(tmp_path, cfg_for, crawler, "GLOBAL", wallets, calls)
    eng.run_once(now=T0)                                       # catalog built while everyone can pay
    wallets.ids.add("S1")
    add(crawler, 1, 11, "دنبال دستکش گرم موتورم بودجه 1 میلیون", 0)
    r = eng.run_once(now=T0 + timedelta(hours=1))
    assert r.analysed_messages == 1
    assert all(m.product_id != "P1" for o in r.opportunities for m in o.matched_products)
    assert eng.store.deferred_counts() == {"S1": 1}
    assert eng.store.get("waiting_payment")["sellers"]["S1"]["needs"] == 1
    assert not eng.store._all("SELECT 1 FROM {s}.costs WHERE business_id = 'S1' AND stage <> 'product_cards'")

    wallets.ids.clear()
    r = eng.run_once(now=T0 + timedelta(hours=2))
    assert r.released == 1 and r.analysed_messages == 0
    assert any(m.product_id == "P1" for o in r.opportunities for m in o.matched_products)
    assert eng.store.deferred_counts() == {}
    assert eng.store._all("SELECT 1 FROM {s}.costs WHERE business_id = 'S1' AND stage = 'verify'")   # now S1 pays


def test_products_of_a_blocked_seller_wait_for_the_top_up(tmp_path, cfg_for, crawler):
    wallets, calls = _Wallets(), []
    eng = _paying_engine(tmp_path, cfg_for, crawler, "GLOBAL", wallets, calls)
    eng.run_once(now=T0)
    wallets.ids.add("S1")
    with (tmp_path / "products.jsonl").open("a", encoding="utf-8") as f:
        f.write("\n" + json.dumps({"product_id": "P3", "seller_id": "S1", "title": "کوله سفری", "product_type": "کوله"},
                                  ensure_ascii=False))
    r = eng.run_once(now=T0 + timedelta(hours=1))
    assert r.products_changed == 0 and "P3" not in eng.catalog.pid_index
    assert eng.store.get("waiting_payment")["sellers"]["S1"]["products"] == 1
    wallets.ids.clear()
    r = eng.run_once(now=T0 + timedelta(hours=2))
    assert r.products_changed == 1 and "P3" in eng.catalog.pid_index


def test_x_is_a_platform_source_under_the_panel_access_rule(cfg_for):
    from need_engine.access import SourceAccess

    class FakeDB:
        def all(self, sql, params=()):
            if "to_regclass" in sql:
                return [{"t": "public.discovery_monitoredcommunity"}]
            return [{"telegram_chat_id": -1001, "scope": "PRIVATE", "business_id": 7}]

    c = cfg_for(source_access="panel", messages_source="db", x_enabled=True, x_mode="public")
    acc = SourceAccess(c, db=FakeDB())
    acc.refresh()
    assert acc.monitored("x:public") and acc.analysed("x:public") and not acc.held("x:public")
    assert acc.sellers("x:public") is None and acc.owners("x:public") == []   # every seller, platform pays
    assert acc.sellers("-1001") == frozenset({"7"}) and not acc.analysed("-1002")
    c.x_enabled = False
    acc.refresh()
    assert not acc.monitored("x:public")


def test_new_product_fields_keep_existing_hashes_and_x_flag_is_not_hashed():
    from need_engine.schemas import Product

    base = Product(product_id="1", title="کفش", description="d")
    legacy = hashlib.sha1(json.dumps(base.model_dump(exclude={"business_id", "url", "card_override", "category_path",
                                                                 "category_keywords", "discovery_priority",
                                                                 "x_outreach_enabled", "x_search_enabled"}),
                                     ensure_ascii=False, sort_keys=True, default=str).encode()).hexdigest()
    assert base.content_hash() == legacy
    assert Product(product_id="1", title="کفش", description="d", x_outreach_enabled=False, discovery_priority=5,
                   x_search_enabled=True).content_hash() == legacy
    assert Product(product_id="1", title="کفش", description="d", category_path="پوشاک > کفش").content_hash() != legacy


def test_x_opportunities_only_use_x_enabled_products(cfg_for):
    from need_engine.engine import NeedEngine
    from need_engine.schemas import MatchedProduct, Product, Verdict

    engine = NeedEngine.__new__(NeedEngine)
    engine.cfg = cfg_for(x_min_match_score=50)

    class Cat:
        products = [Product(product_id="a", title="a"), Product(product_id="b", title="b", x_outreach_enabled=False)]
        pid_index = {"a": 0, "b": 1}

    engine.catalog = Cat()
    mk = lambda pid, s: MatchedProduct(product_id=pid, match_score=s, similarity=0.5, verdict=Verdict(solves="yes"))
    kept = engine._x_allowed([mk("a", 0.9), mk("b", 0.9), mk("a", 0.3)])
    assert [(m.product_id, m.match_score) for m in kept] == [("a", 0.9)]


def test_x_batch_uses_short_local_ids_and_maps_them_back(cfg_for):
    from need_engine import extract as ex

    cfg = cfg_for()
    posts = [ChatMessage(chat_id="x:public", message_id=2108106745524502987 + i, author_id=f"18{i}9999999999999",
                         text="میخوام هندزفری بلوتوثی بخرم چی پیشنهاد میدید", date=T0, platform="x") for i in range(3)]
    seen = {}

    class LLM:
        def complete_json(self, stage, model, system, user, **kw):
            seen["payload"] = json.loads(user)
            return {"needs": [{"author_id": "a2", "evidence_message_ids": [2], "label": "explicit_need",
                               "buyer_intent_confirmed": True, "is_opportunity": True, "author_type": "unknown",
                               "promotional_content": False, "need": "هندزفری بلوتوثی", "solution_queries": ["هندزفری بلوتوثی"],
                               "strength": "strong"}]}, {"toman": 3.0}

    cards = ex.extract_x_batch(posts, LLM(), cfg, T0)[0]
    assert [p["post_id"] for p in seen["payload"]] == [1, 2, 3]
    assert [p["author_id"] for p in seen["payload"]] == ["a1", "a2", "a3"]
    assert len(cards) == 1 and cards[0].author_id == posts[1].author_id and cards[0].evidence_ids == [posts[1].message_id]


def test_x_product_mode_analyses_each_product_separately_and_bills_its_seller(tmp_path, cfg_for, pg_dsn, pg_schema):
    """NE_X_MODE=product: a post found by two products' searches is judged per product; only products with X search
    on are analysed; the judging call is billed to that product's seller; replies («موجود دارین؟») are kept."""
    from x_ingest.__main__ import load

    crawler = pg_schema("xcr")
    items = [
        {"product_id": "P1", "seller_id": "S1", "title": "اکانت نوشن پلاس", "product_type": "اشتراک", "x_search_enabled": True},
        {"product_id": "P2", "seller_id": "S2", "title": "اکانت نوشن بیزینس", "product_type": "اشتراک", "x_search_enabled": False},
        {"product_id": "P3", "seller_id": "S3", "title": "اشتراک نوشن", "product_type": "اشتراک", "x_search_enabled": True},
    ]
    (tmp_path / "products.jsonl").write_text("\n".join(json.dumps(x, ensure_ascii=False) for x in items), encoding="utf-8")
    now = datetime.now(timezone.utc)
    base = {"source": "x", "author_id": "501", "author_handle": "buyer", "author_name": "خریدار", "url": "https://x.com/buyer/status/9001",
            "text": "@crypttopia اکانت notion هم موجود دارین؟", "created_at": (now - timedelta(hours=1)).isoformat(),
            "kind": "reply", "in_reply_to_tweet_id": "8000", "metadata": {"lang": "fa", "query": "q"}}
    parent = dict(base, id="8000", author_id="77", author_handle="shop", text="فروش اکانت نوشن بیزینس، دایرکت", kind="post",
                  in_reply_to_tweet_id=None)
    lines = [parent] + [dict(base, id="9001", product_id=p) for p in ("P1", "P2", "P3")]
    path = tmp_path / "x.jsonl"
    path.write_text("\n".join(json.dumps(x, ensure_ascii=False) for x in lines), encoding="utf-8")
    import os
    old = os.environ.get("NE_CRAWLER_SCHEMA")
    os.environ["NE_CRAWLER_SCHEMA"] = crawler
    try:
        assert load(path, pg_dsn) == 4
    finally:
        os.environ.pop("NE_CRAWLER_SCHEMA") if old is None else os.environ.__setitem__("NE_CRAWLER_SCHEMA", old)
    calls = []

    def spy(stage, system, user):
        if stage == "need_extraction_x_product":
            payload = json.loads(user)
            calls.append(payload)
            fit = "yes" if "پلاس" in payload["product"]["title"] else "no"
            return {"needs": [{"author_id": "a1", "evidence_message_ids": [1], "label": "explicit_need", "buyer_intent_confirmed": True,
                               "is_opportunity": True, "author_type": "individual", "promotional_content": False,
                               "situation": "دنبال اکانت نوشن است", "need": "اکانت نوشن", "solution_queries": ["اکانت نوشن"],
                               "strength": "medium", "fit": fit, "fit_reason": "همین را می‌خواهد"}]}
        if stage == "reply_x":
            return {"public": "سلام، داریم", "dm": "سلام", "short": "داریم"}
        return mock_llm(stage, system, user)

    eng = NeedEngine(cfg_for(crawler_schema=crawler, x_enabled=True, x_mode="product", x_prefilter="off"), mock_llm=spy)
    eng.messages = type("Empty", (), {"fetch_after": lambda self, c, n: []})()
    r = eng.run_once()
    assert len(calls) == 2                                            # P1 and P3; P2 has X search off
    assert {c["product"]["title"] for c in calls} == {"اکانت نوشن پلاس", "اشتراک نوشن"}
    assert all(len(c["posts"]) == 1 and c["posts"][0]["reply_to"] == "فروش اکانت نوشن بیزینس، دایرکت" for c in calls)
    assert len(r.opportunities) == 1                                   # P3 judged «no fit»
    o = r.opportunities[0]
    assert [m.product_id for m in o.matched_products] == ["P1"] and o.source.chat_id == "x:p:P1"
    assert o.matched_products[0].reply_variants["public"] == "سلام، داریم"
    billed = eng.store._all("SELECT stage, business_id FROM {s}.costs WHERE stage = 'need_extraction_x_product' ORDER BY business_id")
    assert [(b["stage"], b["business_id"]) for b in billed] == [("need_extraction_x_product", "S1"), ("need_extraction_x_product", "S3")]
    assert eng.run_once().opportunities == []                          # nothing new, nothing re-billed
    assert len(calls) == 2
