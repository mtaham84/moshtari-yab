from datetime import datetime, timezone
from types import SimpleNamespace
from urllib.parse import parse_qs, urlparse

import pytest

from need_engine.config import EngineConfig
from need_engine.schemas import ChatMessage
from need_engine.x_filters import cheap_x_filter
from need_engine.x_replies import build_x_intent_url, fit_public_reply, sanitize_public_reply, x_weighted_length
from need_engine.contacts import extract_public_contacts
from need_engine.x_query_scoring import query_weight, score_query


def post(text, message_id=1, **kwargs):
    return ChatMessage(chat_id="x:public", message_id=message_id, author_id="x_user", text=text,
                       date=datetime.now(timezone.utc), platform="x", **kwargs)


def test_filter_keeps_explicit_and_on_behalf_intent_and_bio_alone():
    cfg = EngineConfig(x_min_chars=15)
    assert cheap_x_filter(post("برای مادرم دنبال ویلچر مناسب می‌گردم", author_bio="فروشگاه رسمی"), cfg).keep
    assert cheap_x_filter(post("دنبال کفش مدرسه برای بچه‌ام هستم", author_bio="shop"), cfg).keep


def test_filter_detects_promotions_short_and_language():
    cfg = EngineConfig(x_min_chars=15)
    assert cheap_x_filter(post("فروش ویژه خرید کنید ارسال رایگان https://x.test"), cfg).reason in {"shop_bio", "promotional"}
    assert cheap_x_filter(post("سلام"), cfg).reason == "too_short"
    assert cheap_x_filter(post("I need a new laptop", lang="ar"), cfg).reason == "lang"


def test_reply_sanitizing_weights_fallback_and_intent_link():
    assert "https://" not in sanitize_public_reply("سلام https://x.test 09123456789 a@b.com {{LINK}}")
    assert x_weighted_length("می‌خوام https://x.test") >= 23
    assert fit_public_reply("", 240)
    url = build_x_intent_url("https://x.com/intent/post", "123", "سلام خرید")
    assert parse_qs(urlparse(url).query)["text"] == ["سلام خرید"]
    assert build_x_intent_url("https://x.com/intent/post", None, "متن") is None


def test_contacts_require_platform_context_and_never_turn_email_into_website():
    contacts = extract_public_contacts("تلگرام: @my_public_id email me name@gmail.com instagram.com/p/post t.me/joinchat/abc",
                                      types=("telegram", "instagram", "website", "email"))
    assert [(item.type, item.value) for item in contacts] == [("telegram", "my_public_id"), ("email", "name@gmail.com")]


def test_query_score_prior_and_weight_clamp():
    assert score_query(0) == pytest.approx(0.03)
    assert score_query(50, ordered=5) > 0.5
    assert query_weight(0.01, 0.5) == pytest.approx(0.2)
    assert query_weight(1.0, 0.1) == pytest.approx(3.0)


def test_topic_gate_requires_both_topic_and_intent():
    cfg = EngineConfig(x_min_chars=15, x_filter_min_score=25.0)
    fake_catalog = SimpleNamespace(
        products=[SimpleNamespace(product_id="1", title="ویلچر برقی توانبخشی", product_type="ویلچر", description="", tags=[], category_keywords=[])],
        cards={},
    )
    # Both intent and topic match -> keep=True, reason="ok", score breakdown present
    res_ok = cheap_x_filter(post("قصد خرید ویلچر برقی برای مادرم دارم"), cfg, catalog=fake_catalog)
    assert res_ok.keep is True
    assert res_ok.reason == "ok"
    assert res_ok.signals["score"] >= 25.0
    assert res_ok.signals["intent_weight"] == 1.0
    assert res_ok.signals["score_breakdown"]["topic_score"] > 0
    assert res_ok.signals["score_breakdown"]["intent_weight"] == 1.0

    # Intent present, but NO topic match -> reason="no_topic", keep=False
    res_no_topic = cheap_x_filter(post("دنبال خرید پیتزا مخلوط برای شام هستم"), cfg, catalog=fake_catalog)
    assert res_no_topic.keep is False
    assert res_no_topic.reason == "no_topic"

    # Topic present, but NO intent -> reason="no_intent", keep=False
    res_no_intent = cheap_x_filter(post("من دیروز ویلچر برقی در نمایشگاه دیدم بسیار جالب بود"), cfg, catalog=fake_catalog)
    assert res_no_intent.keep is False
    assert res_no_intent.reason == "no_intent"

    # Generic words alone (پیشنهاد / راهنمایی / توصیه) -> reason="no_intent"
    res_generic = cheap_x_filter(post("لطفا یک پیشنهاد و راهنمایی و توصیه مفید به من بدهید"), cfg, catalog=fake_catalog)
    assert res_generic.keep is False
    assert res_generic.reason == "no_intent"


def test_bm25_cached_and_invalidated_on_catalog_change():
    from need_engine.x_filters import get_catalog_bm25

    p1 = SimpleNamespace(product_id="1", title="ویلچر برقی", product_type="ویلچر", description="", tags=[], category_keywords=[])
    catalog = SimpleNamespace(products=[p1], cards={})

    bm25_first = get_catalog_bm25(catalog)
    bm25_second = get_catalog_bm25(catalog)
    assert bm25_first is not None
    assert bm25_first is bm25_second

    # Change catalog products -> invalidates cache and rebuilds
    p2 = SimpleNamespace(product_id="2", title="لپتاپ اداری", product_type="لپتاپ", description="", tags=[], category_keywords=[])
    catalog.products = [p2]
    bm25_third = get_catalog_bm25(catalog)
    assert bm25_third is not None
    assert bm25_third is not bm25_first


def test_embedding_gate_in_product_mode():
    import numpy as np
    from need_engine.engine import NeedEngine, RunReport

    engine = NeedEngine.__new__(NeedEngine)
    engine.cfg = EngineConfig(x_mode="product", x_prefilter="on", x_embed_gate="on", x_embed_min_sim=0.5, x_min_chars=15)

    p = SimpleNamespace(product_id="p1", title="ویلچر برقی تاشو", product_type="ویلچر", description="", tags=[], category_keywords=[])
    fake_catalog = SimpleNamespace(
        pid_index={"p1": 0},
        products=[p],
        cards={},
        product_vectors=lambda pid: np.array([[1.0, 0.0]]),
    )
    engine.catalog = fake_catalog

    dropped_calls = []
    recorded_decisions = []
    fake_store = SimpleNamespace(
        record_x_filter_decision=lambda msg, keep, reason, sigs, mode: recorded_decisions.append((msg.message_id, keep, reason)),
        drop_x_pending=lambda msgs, decs, mode: dropped_calls.extend([m.message_id for m in msgs]),
        record_x_hit_results=lambda hits: None,
    )
    engine.store = fake_store

    class FakeEmb:
        def encode(self, texts, stage="embed_x_gate"):
            vecs = []
            for t in texts:
                if "ویلچر" in t:
                    vecs.append([1.0, 0.0])
                else:
                    vecs.append([0.0, 1.0])
            return np.array(vecs)
    engine.emb = FakeEmb()

    # msg1 matches topic and high sim; msg2 matches topic (has ویلچر in text) but low sim in embedding
    msg1 = post("دنبال خرید ویلچر برقی برای بیمار هستم", message_id=101)
    msg2 = post("دنبال خرید ویلچر کتاب رمان برای مطالعه", message_id=102)

    # Mock FakeEmb so msg2 gets [0.0, 1.0] (sim = 0.0 < 0.5)
    class FakeEmb2:
        def encode(self, texts, stage="embed_x_gate"):
            return np.array([[1.0, 0.0] if "بیمار" in t else [0.0, 1.0] for t in texts])
    engine.emb = FakeEmb2()

    report = RunReport()
    kept = engine._select_x_pending([msg1, msg2], report, datetime.now(timezone.utc), product_id="p1")

    assert [m.message_id for m in kept] == [101]
    assert 102 in dropped_calls
    assert report.x_prefilter_dropped.get("embed_low_sim") == 1
    assert report.dropped_by_stage.get("embed_gate") == 1
    assert any(rec[0] == 102 and rec[1] is False and rec[2] == "embed_low_sim" for rec in recorded_decisions)


def test_extract_window_parses_intent_type_and_harmonizes_strength():
    from need_engine.windowing import Window
    from need_engine.extract import extract_window

    class FakeLLM:
        def __init__(self, data):
            self.data = data
        def complete_json(self, *args, **kwargs):
            return self.data, {"toman": 15.0}

    cfg = EngineConfig()
    now = datetime.now(timezone.utc)
    m = post("از باتری این گوشی خسته شدم و مدام داغ میکنه", message_id=1)
    w = Window("x:public", "X", [m])

    # Complaint with weak strength should be harmonized to medium
    llm = FakeLLM({
        "needs": [{
            "author_id": "x_user",
            "evidence_message_ids": [1],
            "label": "explicit_need",
            "is_opportunity": True,
            "buyer_intent_confirmed": True,
            "promotional_content": False,
            "need": "تعویض گوشی",
            "solution_queries": ["خرید گوشی"],
            "intent_type": "complaint",
            "strength": "weak",
        }]
    })
    cards, cost = extract_window(w, llm, cfg, now)
    assert len(cards) == 1
    assert cards[0].intent_type == "complaint"
    assert cards[0].strength == "medium"

    # Unknown intent_type falls back to general
    llm2 = FakeLLM({
        "needs": [{
            "author_id": "x_user",
            "evidence_message_ids": [1],
            "label": "explicit_need",
            "is_opportunity": True,
            "buyer_intent_confirmed": True,
            "promotional_content": False,
            "need": "تعویض گوشی",
            "solution_queries": ["خرید گوشی"],
            "intent_type": "unrecognized_value",
            "strength": "strong",
        }]
    })
    cards2, _ = extract_window(w, llm2, cfg, now)
    assert len(cards2) == 1
    assert cards2[0].intent_type == "general"
    assert cards2[0].strength == "strong"


def test_x_batch_llm_post_caching_and_run_report_counters():
    from need_engine.extract import extract_x_product_batch
    from need_engine.engine import RunReport

    class FakeStore:
        def __init__(self):
            self.kv = {}
        def cache_get(self, key):
            return self.kv.get(key)
        def cache_put(self, key, val, ttl_hours=None):
            self.kv[key] = val

    class FakeLLM:
        def __init__(self):
            self.calls = 0
        def complete_json(self, *args, **kwargs):
            self.calls += 1
            return {
                "needs": [{
                    "post_id": 1, "author_id": "a1", "evidence_message_ids": [1],
                    "label": "explicit_need", "is_opportunity": True,
                    "buyer_intent_confirmed": True,
                    "promotional_content": False,
                    "need": "ویلچر برقی",
                    "solution_queries": ["خرید ویلچر برقی"],
                    "intent_type": "buy_signal", "strength": "strong",
                    "product_fit": "good", "fit_reason": "matched",
                }]
            }, {"toman": 10.0}

    store = FakeStore()
    llm = FakeLLM()
    llm.store = store
    cfg = EngineConfig()
    product = {"product_id": "p1", "title": "ویلچر"}
    messages = [post("دنبال خرید ویلچر برقی هستم", message_id=1)]

    report1 = RunReport()
    cards1, toman1, fits1 = extract_x_product_batch(messages, "x:public", product, llm, cfg, datetime.now(timezone.utc), report=report1)
    assert len(cards1) == 1
    assert llm.calls == 1
    assert report1.cache_hits == 0

    # Second run with identical post and product
    report2 = RunReport()
    cards2, toman2, fits2 = extract_x_product_batch(messages, "x:public", product, llm, cfg, datetime.now(timezone.utc), report=report2)
    assert len(cards2) == 1
    assert llm.calls == 1
    assert report2.cache_hits == 1
