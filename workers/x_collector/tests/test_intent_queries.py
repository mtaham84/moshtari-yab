from types import SimpleNamespace

from need_engine.schemas import Product
from workers.x_collector.intent_queries import core_terms, generate_queries, model_term


def test_core_terms_avoid_long_marketing_titles_and_codes():
    product = Product(product_id="1", title="فوق العاده بهترین مدل X9000 دستکش گرم موتور سواری", category_path="پوشاک / دستکش موتور")
    terms = core_terms(product)
    assert terms[0] == "دستکش موتور"
    assert all(len(term.split()) <= 4 for term in terms)
    assert all("9000" not in term and "x9000" not in term.lower() for term in terms)


def test_product_mode_is_exact_legacy_order():
    product = SimpleNamespace(title="کاپشن گرم", category_path="پوشاک / کاپشن", category_keywords=["کاپشن", "لباس"])
    legacy = [product.title, product.category_path, *product.category_keywords]
    assert [item["query"] for item in generate_queries(product, "product")] == legacy


def test_every_generated_query_has_at_most_three_words(monkeypatch):
    from workers.x_collector.intent_queries import positive_word_count

    monkeypatch.delenv("X_QUERY_NEGATIVE_OPERATORS", raising=False)
    product = SimpleNamespace(title="آستین کنترل کننده انگشت Dagel Silver Pro - فروشگاه پدارول", product_type="",
                              category_path="کالای دیجیتال/لوازم گیمینگ", category_keywords=[],
                              card=SimpleNamespace(aliases=["انگشتی گیمینگ", "کاور انگشت", "آستین انگشت دست گیمینگ حرفه ای"]))
    queries = generate_queries(product, "both")
    assert queries
    assert all(1 <= positive_word_count(q["query"]) <= 3 for q in queries)
    assert all("فروشگاه" not in q["query"] for q in queries)
    assert any(q["query"].startswith('"انگشتی گیمینگ"') for q in queries)
    assert not any("حرفه ای" in q["query"] for q in queries)          # 5 words: rejected


def test_queries_are_stable_capped_one_phrase_each():
    names = [f"اسم شماره {i}" for i in range(12)]
    product = SimpleNamespace(title="دستکش موتور گرم", category_path="پوشاک / دستکش موتور", category_keywords=[])
    first = generate_queries(product, "intent", terms={"names": names})
    assert first == generate_queries(product, "intent", terms={"names": names})
    assert 1 <= len(first) <= 12
    assert all(" OR " not in q["query"] for q in first)       # one phrase per query, never an OR blob


def test_llm_terms_become_short_queries_and_x_syntax_is_stripped(monkeypatch):
    monkeypatch.setenv("X_QUERY_NEGATIVE_OPERATORS", "true")
    monkeypatch.delenv("X_QUERY_NEGATIVE_TERMS", raising=False)
    product = SimpleNamespace(title="ایرفون بلوتوثی Riversong مدل Airfly M7 EA251", product_type="", category_path="", category_keywords=[])
    terms = {"names": ['هندزفری" OR lang:en', "ایرفون بی سیم"], "problems": ["صدا قطع میشه", "صدای هندزفریم قطع میشه"], "model": "Airfly M7"}
    queries = generate_queries(product, "both", terms=terms)
    texts = [q["query"] for q in queries]
    assert any(q.startswith('"ایرفون بی سیم"') for q in texts)            # 3 words are allowed now
    assert any(q.startswith('"صدا قطع میشه"') for q in texts)
    assert not any("OR lang:en" in q for q in texts)                         # X syntax stripped
    assert not any("صدای هندزفریم" in q for q in texts)                      # 4 words: rejected, never truncated
    assert any(q.startswith('"Airfly M7"') and "lang:fa" in q for q in texts)


def test_model_term_needs_brand_and_model():
    assert model_term("ایرفون بلوتوثی Riversong مدل Airfly M7 EA251") == "Airfly M7"
    assert model_term("کفش Nike") == ""
    assert model_term("فوق العاده بهترین مدل X9000 دستکش") == ""


def test_broadness_is_data_driven_and_keeps_specific_topics():
    from workers.x_collector.intent_queries import is_too_broad

    assert is_too_broad(["لباس"])
    assert is_too_broad("خرید آنلاین".split())
    assert is_too_broad("محصولات جدید".split())
    for phrase in ("پوشاک مردانه", "خرید لباس مردانه", "لباس مجلسی مردانه", "پیراهن مردانه", "جوراب ورزشی", "خرید پیراهن"):
        assert not is_too_broad(phrase.split())


class FakeStore:
    def __init__(self):
        self.kv = {}

    def get_prefix(self, prefix):
        return {k: v for k, v in self.kv.items() if k.startswith(prefix)}

    def set(self, k, v):
        self.kv[k] = v


class FakeLLM:
    def __init__(self, fail=False):
        self.calls, self.fail = 0, fail

    def complete_json(self, stage, model, system, user, **kw):
        self.calls += 1
        if self.fail:
            raise RuntimeError("quota")
        import json
        ids = [p["product_id"] for p in json.loads(user)["products"]]
        return {"items": [{"product_id": i, "names": ["هندزفری", "یک دو سه چهار پنج"], "problems": ["خرید", "صدام قطع"],
                           "model": "null"} for i in ids]}, {}


def test_llm_terms_are_sanitized_cached_and_regenerated_on_change(monkeypatch):
    import workers.x_collector.llm_terms as lt
    from workers.x_collector.llm_terms import llm_terms

    monkeypatch.setattr(lt, "MIN_PER_FORMAT", 0)   # this test's fake LLM returns a minimal answer

    store, llm = FakeStore(), FakeLLM()
    product = Product(product_id="7", title="ایرفون بلوتوثی")
    first = llm_terms([product], {}, store, llm, "m")
    assert first["7"]["names"] == ["هندزفری"]                    # the 5-word name is dropped
    assert first["7"]["problems"] == ["صدام قطع"] or first["7"]["problems"] == []
    assert all(len(x.split()) <= 2 for k in ("pain_symptoms", "advice_requests", "implicit_solutions", "names") for x in first["7"][k])
    assert first["7"]["model"] is None
    assert llm_terms([product], {}, store, llm, "m") == first and llm.calls == 1
    llm_terms([product.model_copy(update={"title": "ایرفون بلوتوثی جدید"})], {}, store, llm, "m")
    assert llm.calls == 2
    monkeypatch.setattr(lt, "PROMPT_VERSION", "10")
    llm_terms([product.model_copy(update={"title": "ایرفون بلوتوثی جدید"})], {}, store, llm, "m")
    assert llm.calls == 3


def test_llm_failure_falls_back_without_caching():
    from workers.x_collector.llm_terms import llm_terms

    store = FakeStore()
    assert llm_terms([Product(product_id="1", title="کیف")], {}, store, FakeLLM(fail=True), "m") == {}
    assert store.kv == {}


def test_time_window_first_run_lookback_then_since_last_success(monkeypatch):
    from datetime import datetime, timedelta, timezone
    from workers.x_collector.worker import time_window

    monkeypatch.delenv("X_QUERY_TIME_FILTER", raising=False)
    monkeypatch.delenv("X_QUERY_FIRST_LOOKBACK_HOURS", raising=False)
    now = datetime(2025, 6, 10, 12, 0, tzinfo=timezone.utc)
    assert time_window("q", None, now) == f"q since_time:{int((now - timedelta(days=10)).timestamp())}"
    last = now - timedelta(hours=1)
    assert time_window("q", last, now) == f"q since_time:{int((last - timedelta(minutes=10)).timestamp())}"
    assert time_window("q", now - timedelta(days=30), now) == f"q since_time:{int((now - timedelta(days=10)).timestamp())}"
    assert time_window("q since:2025-01-01", None, now) == "q since:2025-01-01"
    monkeypatch.setenv("X_QUERY_TIME_FILTER", "since")
    assert time_window("q", None, now) == "q since:2025-05-31"
    monkeypatch.setenv("X_QUERY_TIME_FILTER", "off")
    assert time_window("q", None, now) == "q"


def test_collector_advances_window_only_after_success(tmp_path):
    from workers.x_collector.worker import SeenStore, XCollector

    class Client:
        def __init__(self):
            self.calls = []

        def search(self, query, limit, account=None):
            self.calls.append(query)
            return []

    client = Client()
    collector = XCollector(client=client, output_dir=tmp_path / "o", state_path=tmp_path / "s.sqlite3", sleep_min=0, sleep_max=0,
                           sleeper=lambda _: None, jitter=lambda a, b: 0)
    collector.run(["coffee"])
    assert SeenStore(tmp_path / "s.sqlite3").last_ok("coffee") is not None
    collector.run(["coffee"])
    first, second = (int(c.rsplit(":", 1)[1]) for c in client.calls)
    assert second > first


def test_generic_words_are_not_search_terms():
    product = SimpleNamespace(title="قیمت خرید صندلی کمپینگ تاشو", product_type="اشتراک", category_path="", category_keywords=[],
                              card=SimpleNamespace(aliases=["خرید اکانت نوشن", "اکانت"]))
    terms = core_terms(product)
    assert "اشتراک" not in terms and "اکانت" not in terms
    assert all(not t.startswith(("قیمت", "خرید")) for t in terms)
    assert "صندلی کمپینگ" in terms and "اکانت نوشن" in terms


def test_product_queries_belong_to_one_product_and_keep_latin_names():
    from workers.x_collector.intent_queries import positive_word_count, product_queries

    product = Product(product_id="9", business_id="3", title="اشتراک نوشن پلاس", discovery_priority=2)
    terms = {"names": ["نوشن", "notion", "Notion AI", "اشتراک", "خرید اکانت نوشن"],
             "negatives": ["تحویل آنی"], "problems": ["جزوه شلوغ"], "model": None}
    queries = product_queries(product, terms)
    assert all(q["product_ids"] == ["9"] and q["business_ids"] == ["3"] and q["priority"] == 2.0 for q in queries)
    assert all(1 <= positive_word_count(q["query"]) <= 3 for q in queries)
    texts = " ".join(q["query"] for q in queries)
    assert '"Notion AI"' in texts and "notion" in texts
    assert '"اکانت نوشن"' in texts                               # leading «خرید» is stripped from product names
    assert "lang:fa" in texts
    assert "-تخفیف" not in texts and "-ارسال" not in texts
    assert any(q["kind"] == "problem" and "جزوه شلوغ" in q["query"] for q in queries)


def test_product_queries_without_llm_use_core_names_only():
    from workers.x_collector.intent_queries import positive_word_count, product_queries

    queries = product_queries(Product(product_id="1", title="هندزفری بلوتوثی", product_type="هندزفری"))
    assert queries and all(q["product_ids"] == ["1"] for q in queries)
    assert all(positive_word_count(q["query"]) <= 2 for q in queries)
    assert any(q["kind"] == "intent" for q in queries)


def test_three_formats_kinds_are_generated_from_llm_terms():
    from workers.x_collector.intent_queries import positive_word_count, product_queries

    product = Product(product_id="20", title="نرم‌افزار حسابداری آنلاین", business_id="5")
    terms = {
        "names": ["نرم‌افزار حسابداری"],
        "negatives": ["استخدام"],
        "pain_symptoms": ["حساب کتاب", "اشتباه حسابداری"],
            "advice_requests": ["حسابداری ساده", "حسابداری ابری"],
            "implicit_solutions": ["حسابداری مالی", "نرم افزار حسابداری"],
    }
    prod = product_queries(product, terms=terms)
    kinds = {q["kind"] for q in prod}
    assert {"intent", "problem", "alternative", "solution"} <= kinds
    assert all(1 <= positive_word_count(q["query"]) <= 2 and len(q["query"]) <= 400 for q in prod)
    gen = generate_queries(product, mode="both", terms=terms)
    assert {"problem", "alternative", "solution"} <= {q["kind"] for q in gen}


def test_kind_weights_across_schedulers_and_worker():
    import tempfile
    from pathlib import Path
    from shared.collector_kit.seen import SeenStore
    from workers.x_collector.intent_queries import KIND_WEIGHTS

    assert KIND_WEIGHTS == {
        "intent": 1.0,
        "problem": 0.8,
        "alternative": 0.7,
        "complaint": 0.6,
        "product": 0.4,
    }

    # Verify scheduler orders by weight when freshness is equal
    with tempfile.TemporaryDirectory() as td:
        db_path = Path(td) / "test_seen.sqlite3"
        seen = SeenStore(db_path, platform="x")
        queries = [
            {"query": "q_prod", "kind": "product", "priority": 1.0},
            {"query": "q_intent", "kind": "intent", "priority": 1.0},
            {"query": "q_complaint", "kind": "complaint", "priority": 1.0},
            {"query": "q_problem", "kind": "problem", "priority": 1.0},
            {"query": "q_alt", "kind": "alternative", "priority": 1.0},
        ]
        selected = seen.schedule(queries, budget=5)
        selected_kinds = [q["kind"] for q in selected]
        assert selected_kinds == ["intent", "problem", "alternative", "complaint", "product"]


def test_too_broad_phrases_are_rejected_but_specific_ones_pass():
    from workers.x_collector.intent_queries import short_terms

    out = short_terms(["لباس", "خرید لباس", "پوشاک", "جوراب", "لباس مجلسی مردانه", "دامن چرم", "قیمت کفش", "کت", "خدمات"], 10)
    assert out == ["خرید لباس", "جوراب", "لباس مجلسی مردانه", "دامن چرم", "قیمت کفش"]
