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


def test_both_packs_names_and_intent_words_into_one_or_query(monkeypatch):
    monkeypatch.delenv("X_QUERY_NEGATIVE_OPERATORS", raising=False)
    product = SimpleNamespace(title="آستین کنترل کننده انگشت Dagel Silver Pro - فروشگاه پدارول", product_type="",
                              category_path="کالای دیجیتال/لوازم گیمینگ", category_keywords=[],
                              card=SimpleNamespace(aliases=["انگشتی گیمینگ", "کاور انگشت"]))
    queries = generate_queries(product, "both")
    intent = [q["query"] for q in queries if q["kind"] == "intent"]
    assert len(intent) == 1
    assert intent[0].startswith('("انگشتی گیمینگ" OR "کاور انگشت" OR "لوازم گیمینگ"')
    assert "(بخرم OR " in intent[0] and "می‌خوام" in intent[0] and "میخوام" in intent[0] and intent[0].endswith("lang:fa")
    assert all("فروشگاه" not in q["query"] and "کالای دیجیتال" not in q["query"] for q in queries)
    assert {"query": '"Dagel Silver" (خوبه OR بخرم OR بگیرم OR پیشنهاد OR نظرتون OR تجربه OR ارزش) lang:fa', "kind": "product"} in queries


def test_intent_queries_are_stable_capped_and_split_by_length(monkeypatch):
    monkeypatch.setenv("X_QUERY_MAX_CHARS", "160")
    names = [f"اسم شماره {i}" for i in range(12)]
    product = SimpleNamespace(title="دستکش موتور گرم", category_path="پوشاک / دستکش موتور", category_keywords=[])
    first = generate_queries(product, "intent", terms={"names": names})
    assert first == generate_queries(product, "intent", terms={"names": names})
    assert len(first) > 1 and len(first) <= 12
    assert all(not q["query"].startswith("-") and len(q["query"]) <= 160 for q in first)
    joined = " ".join(q["query"] for q in first)
    assert all(f'"{name}"' in joined for name in names)


def test_llm_terms_become_problem_and_model_queries_and_x_syntax_is_stripped(monkeypatch):
    monkeypatch.setenv("X_QUERY_NEGATIVE_OPERATORS", "true")
    monkeypatch.delenv("X_QUERY_NEGATIVE_TERMS", raising=False)
    product = SimpleNamespace(title="ایرفون بلوتوثی Riversong مدل Airfly M7 EA251", product_type="", category_path="", category_keywords=[])
    terms = {"names": ['هندزفری" OR lang:en', "ایرفون بی سیم"], "problems": ["صدای هندزفریم قطع میشه"], "model": "Airfly M7"}
    queries = generate_queries(product, "both", terms=terms)
    kinds = {q["kind"]: q["query"] for q in queries}
    assert kinds["intent"].startswith('(هندزفری lang en OR "ایرفون بی سیم"') or kinds["intent"].startswith('("هندزفری lang en" OR')
    assert " OR lang:en" not in kinds["intent"]
    assert kinds["problem"].startswith('"صدای هندزفریم قطع میشه" lang:fa -تخفیف')
    assert kinds["product"].startswith('"Airfly M7" (خوبه OR ') and " lang:fa" in kinds["product"]


def test_model_term_needs_brand_and_model():
    assert model_term("ایرفون بلوتوثی Riversong مدل Airfly M7 EA251") == "Airfly M7"
    assert model_term("کفش Nike") == ""
    assert model_term("فوق العاده بهترین مدل X9000 دستکش") == ""


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
        return {"items": [{"product_id": i, "names": ["هندزفری", "یک دو سه چهار پنج"], "problems": ["خرید", "صدام قطع میشه"],
                           "model": "null"} for i in ids]}, {}


def test_llm_terms_are_sanitized_cached_and_regenerated_on_change():
    from workers.x_collector.llm_terms import llm_terms

    store, llm = FakeStore(), FakeLLM()
    product = Product(product_id="7", title="ایرفون بلوتوثی")
    first = llm_terms([product], {}, store, llm, "m")
    assert first["7"]["names"] == ["هندزفری"]
    assert first["7"]["problems"] == ["صدام قطع میشه"]
    assert first["7"]["pain_symptoms"] == ["صدام قطع میشه"]
    assert first["7"]["model"] is None
    assert llm_terms([product], {}, store, llm, "m") == first and llm.calls == 1
    llm_terms([product.model_copy(update={"title": "ایرفون بلوتوثی جدید"})], {}, store, llm, "m")
    assert llm.calls == 2


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
    from workers.x_collector.intent_queries import product_queries

    product = Product(product_id="9", business_id="3", title="اشتراک نوشن پلاس", discovery_priority=2)
    terms = {"names": ["نوشن", "notion", "Notion AI", "اشتراک", "خرید اکانت نوشن"], "cues": ["پرمیوم", "موجود دارین", "از کجا"],
             "negatives": ["تحویل آنی"], "problems": ["جزوه هام بهم ریخته"], "model": None}
    queries = product_queries(product, terms)
    assert all(q["product_ids"] == ["9"] and q["business_ids"] == ["3"] and q["priority"] == 2.0 for q in queries)
    intent = next(q["query"] for q in queries if q["kind"] == "intent")
    assert intent.startswith('(نوشن OR notion)')   # at most 2 names per query keeps each query focused
    assert ' اشتراک ' not in intent.split(")")[0]                      # generic word alone is not a name
    assert '(پرمیوم OR "موجود دارین" OR "از کجا")' in intent
    assert '-"تحویل آنی"' in intent and "-تخفیف" in intent and intent.endswith("lang:fa")
    assert any(q["kind"] == "problem" and "جزوه هام بهم ریخته" in q["query"] for q in queries)


def test_product_queries_without_llm_use_default_cues():
    from workers.x_collector.intent_queries import PRODUCT_MODE_CUES, product_queries

    queries = product_queries(Product(product_id="1", title="هندزفری بلوتوثی", product_type="هندزفری"))
    assert queries and all(q["product_ids"] == ["1"] for q in queries)
    intent = next(q["query"] for q in queries if q["kind"] == "intent")
    assert all(c in intent for c in ("بخرم", "موجود", '"از کجا"'))
    assert len(PRODUCT_MODE_CUES) >= 10


def test_queries_generate_alternative_and_complaint_kinds():
    from workers.x_collector.intent_queries import product_queries

    product = Product(product_id="20", title="نرم‌افزار حسابداری آنلاین", business_id="5")

    # 1. Fallback cues when no LLM terms are provided
    gen = generate_queries(product, mode="both")
    kinds = {q["kind"] for q in gen}
    assert "alternative" in kinds
    assert "complaint" in kinds
    assert "intent" in kinds
    assert all(len(q["query"]) <= 400 for q in gen)

    prod = product_queries(product)
    p_kinds = {q["kind"] for q in prod}
    assert "alternative" in p_kinds
    assert "complaint" in p_kinds
    assert all(len(q["query"]) <= 400 for q in prod)

    # 2. With LLM terms provided
    terms = {
        "names": ["نرم‌افزار حسابداری"],
        "cues": ["خرید اشتراک"],
        "negatives": ["استخدام"],
        "problems": ["حساب کتابم دقیق نیست"],
        "alternatives": ["جایگزین هلو", "به جای سپیدار"],
        "complaints": ["از این برنامه حسابداری خسته شدم"],
    }
    prod_llm = product_queries(product, terms=terms)
    alt_q = next(q for q in prod_llm if q["kind"] == "alternative")
    cmp_q = next(q for q in prod_llm if q["kind"] == "complaint")
    assert "جایگزین هلو" in alt_q["query"] or "به جای سپیدار" in alt_q["query"]
    assert "خسته" in cmp_q["query"]
    assert all(len(q["query"]) <= 400 for q in prod_llm)


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
