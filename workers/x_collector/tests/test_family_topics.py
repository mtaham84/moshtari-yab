from types import SimpleNamespace

from workers.x_collector.families import family_topic_contexts
from workers.x_collector.intent_queries import product_queries


def _family(fid, leaf, title):
    return SimpleNamespace(
        product_id=f"f6-{fid}", business_id="6", title=leaf, category_path=f"پوشاک > {leaf}",
        titles=[title], product_type=leaf, category_keywords=[], member_product_ids=[fid],
        discovery_priority=1,
    )


def test_family_queries_reject_reported_drift_but_keep_good_phrases():
    shirts = _family("shirt", "پیراهن مردانه", "پیراهن مردانه پنبه‌ای")
    blouses = _family("blouse", "شومیز زنانه", "شومیز زنانه حریر")
    contexts = family_topic_contexts([shirts, blouses])
    terms = {"names": ["پیراهن مردانه", "پوشاک مردانه", "خرید لباس مردانه"],
             "pain_symptoms": ["تنگی شلوار", "گرانی شومیز", "پیدا نکردن شومیز", "شومیز مردانه", "پیراهن پاره"],
             "advice_requests": [], "implicit_solutions": []}

    shirt_queries = [row["query"] for row in product_queries(shirts, terms, topic_context=contexts[shirts.product_id])]
    assert any("پیراهن مردانه" in query for query in shirt_queries)
    assert any("پیراهن پاره" in query for query in shirt_queries)
    for bad in ("تنگی شلوار", "گرانی شومیز", "پیدا نکردن شومیز", "شومیز مردانه", "پوشاک مردانه", "خرید لباس مردانه"):
        assert not any(bad in query for query in shirt_queries), bad

    blouse_queries = [row["query"] for row in product_queries(blouses,
        {"names": ["شومیز زنانه"], "pain_symptoms": [], "advice_requests": [], "implicit_solutions": []},
        topic_context=contexts[blouses.product_id])]
    assert any("شومیز زنانه" in query for query in blouse_queries)


def test_family_queries_use_colloquial_clothing_and_dental_terms_and_cap_at_ten(monkeypatch):
    from workers.x_collector.families import family_topic_contexts

    shirt = _family("shirt", "پیراهن مردانه", "پیراهن مردانه پنبه‌ای")
    dental = _family("implant", "ایمپلنت", "ایمپلنت دندان")
    dental.category_path = "خدمات > دندان پزشکی > ایمپلنت"
    contexts = family_topic_contexts([shirt, dental])
    monkeypatch.setenv("X_QUERY_MAX_PER_FAMILY", "99")

    shirt_queries = [row["query"] for row in product_queries(shirt, {}, topic_context=contexts[shirt.product_id])]
    dental_queries = [row["query"] for row in product_queries(dental, {}, topic_context=contexts[dental.product_id])]
    assert len(shirt_queries) <= 10
    assert any("لباس خنک" in query for query in shirt_queries)
    assert any("لباس تنگ" in query for query in shirt_queries)
    assert any("دندون درد" in query for query in dental_queries)
    assert all("دندان" not in query for query in dental_queries)
    assert all("-تخفیف" not in query and "-ارسال" not in query for query in shirt_queries + dental_queries)


def test_llm_prompt_contains_family_context_and_prompt_version_is_bumped():
    import json
    import workers.x_collector.llm_terms as terms_module
    from workers.x_collector.llm_terms import X_QUERY_TERMS_SYSTEM, _user

    shirts = _family("shirt", "پیراهن مردانه", "پیراهن مردانه")
    blouses = _family("blouse", "شومیز زنانه", "شومیز زنانه")
    ctx = family_topic_contexts([shirts, blouses])[shirts.product_id]
    message = json.dumps(_user(shirts, None, ctx), ensure_ascii=False)
    assert "شومیز زنانه" in message
    assert "پیراهن" in message
    assert "OTHER FAMILIES" in message and "ANCHOR WORDS" in message
    assert "گرانی شومیز" in X_QUERY_TERMS_SYSTEM and "تنگی شلوار" in X_QUERY_TERMS_SYSTEM
    assert terms_module.PROMPT_VERSION == "9"


def test_blocked_tenant_is_removed_before_x_terms_or_llm(monkeypatch):
    from workers.x_collector import query_source

    product = SimpleNamespace(product_id="51", business_id="4", title="پیراهن مردانه", x_search_enabled=True)
    config = SimpleNamespace(x_mode="intent")
    source = SimpleNamespace(db=object())

    class Registry:
        def __init__(self, _config):
            pass

        def blocked(self, force=False):
            assert force
            return frozenset({"4"})

    monkeypatch.setattr("need_engine.registry.ModelRegistry", Registry)
    monkeypatch.setattr(query_source, "_terms_and_cards", lambda *_: (_ for _ in ()).throw(AssertionError("LLM called")))
    assert query_source._load_intent_queries(config, source, [product], "intent", None) == []
