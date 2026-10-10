from types import SimpleNamespace

from need_engine.schemas import Product
from workers.x_collector.intent_queries import core_terms, generate_queries


def test_core_terms_avoid_long_marketing_titles_and_codes():
    product = Product(product_id="1", title="فوق العاده بهترین مدل X9000 دستکش گرم موتور سواری", category_path="پوشاک / دستکش موتور")
    terms = core_terms(product)
    assert terms[0] == "دستکش موتور"
    assert all(len(term.split()) <= 4 for term in terms)
    assert all("9000" not in term and "x9000" not in term.lower() for term in terms)


def test_product_mode_is_exact_legacy_order_and_both_adds_intent():
    product = SimpleNamespace(title="کاپشن گرم", category_path="پوشاک / کاپشن", category_keywords=["کاپشن", "لباس"])
    legacy = [product.title, product.category_path, *product.category_keywords]
    assert [item["query"] for item in generate_queries(product, "product")] == legacy
    both = generate_queries(product, "both")
    assert any(item["kind"] == "intent" for item in both)
    assert [item["query"] for item in both[:4]] == legacy
    assert all(item["kind"] == "product" for item in both[:4])


def test_intent_queries_are_stable_capped_and_persian_variants_exist():
    product = SimpleNamespace(title="دستکش موتور گرم", category_path="پوشاک / دستکش موتور", category_keywords=[])
    first = generate_queries(product, "intent")
    assert first == generate_queries(product, "intent")
    assert len(first) <= 12
    assert any("می‌خوام" in item["query"] for item in first)
    assert any("میخوام" in item["query"] for item in first)
    assert all(not item["query"].startswith("-") and len(item["query"]) <= 100 for item in first)
