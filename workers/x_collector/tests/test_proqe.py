from types import SimpleNamespace
from unittest.mock import MagicMock

from shared.collector_kit.seen import SeenStore
from workers.x_collector.proqe import (ExemplarStore, expand_queries_with_proqe, load_proqe_queries,
                                       purge_legacy_product_state)
from workers.x_collector.intent_queries import product_queries


def test_exemplar_store_add_and_retrieve(tmp_path):
    db_file = tmp_path / "test_exemplars.sqlite3"
    store = ExemplarStore(db_file)

    # 1. Add exemplars
    added = store.add_exemplar(
        tweet_id="1001",
        product_id="prod_1",
        category="سنگ / سنگ ساختمانی",
        query='[problem] "سنگ نما شوره زده"',
        tweet_text="سلام دوستان، سنگ نمای ساختمونمون شوره زده و زشت شده، کسی میدونه چیکار باید بکنم؟",
        reason="کاربر دقیقاً مشکل شوره زدگی سنگ دارد و نیاز به راهکار فوری دارد."
    )
    assert added is True

    # Duplicate should update, not fail
    assert store.add_exemplar("1001", "prod_1", "سنگ نمای ساختمانی شوره زده") is True

    # 2. Add category match for different product
    store.add_exemplar(
        tweet_id="1002",
        product_id="prod_2",
        category="سنگ / اسلب",
        query='[problem] "ترک خوردن سنگ کف"',
        tweet_text="سنگ کف پذیرایی ترک خورده، تعویض کنم یا رزین بزنم؟",
        reason="کاربر نیازمند مشاوره سنگ و رزین است."
    )

    # Test exact product match
    exact = store.get_exemplars(product_id="prod_1", limit=3)
    assert len(exact) >= 1
    assert exact[0]["tweet_id"] == "1001"

    # Test category match for product with no direct exemplars
    cat_match = store.get_exemplars(product_id="prod_3", category="سنگ / گرانیت", limit=3)
    assert len(cat_match) >= 1
    assert any(e["tweet_id"] in {"1001", "1002"} for e in cat_match)


def test_exemplar_store_synced_decisions(tmp_path):
    store = ExemplarStore(tmp_path / "sync.sqlite3")
    assert store.is_decision_synced("x:p:10", 999) is False
    store.mark_decision_synced("x:p:10", 999, "customer")
    assert store.is_decision_synced("x:p:10", 999) is True


def test_legacy_product_state_purge_is_dry_run_safe_and_keeps_family_rows(tmp_path):
    path = tmp_path / "families.sqlite3"
    store = ExemplarStore(path)
    store.add_expanded_queries("17", [{"query": "تیشرت مردانه", "kind": "intent"}])
    store.add_expanded_queries("f17-a1b2c3d4", [{"query": "پیراهن مردانه", "kind": "intent"}])
    store.add_exemplar("1", "17", "خریدار عمده جوراب")
    store.add_exemplar("2", "f17-a1b2c3d4", "خریدار پیراهن مردانه")
    store.mark_expanded("17")
    store.mark_expanded("f17-a1b2c3d4")

    report = purge_legacy_product_state(path, dry_run=True)
    assert report == {"proqe_expanded_queries": 1, "proqe_exemplars": 1, "proqe_expansion_state": 1}
    assert len(store.get_expanded_queries("17")) == 1
    assert purge_legacy_product_state(path, dry_run=False) == report
    assert store.get_expanded_queries("17") == []
    assert len(store.get_expanded_queries("f17-a1b2c3d4")) == 1


def test_thompson_sampling_feedback_and_scoring(tmp_path):
    state_file = tmp_path / "bandit.sqlite3"
    seen = SeenStore(state_file, platform="x")

    q_good = "سنگ نما شوره زده lang:fa"
    q_bad = "خرید سنگ ارزان lang:fa"

    # Initial stats
    stats_good = seen.get_query_bandit_stats(q_good)
    assert stats_good["alpha"] == 1.0
    assert stats_good["beta"] == 1.0

    # Record rewards on q_good
    seen.record_feedback(q_good, success=True, weight=2.0)
    stats_good = seen.get_query_bandit_stats(q_good)
    assert stats_good["alpha"] == 3.0
    assert stats_good["leads"] == 1

    # Record penalties on q_bad
    seen.record_feedback(q_bad, success=False, weight=3.0)
    stats_bad = seen.get_query_bandit_stats(q_bad)
    assert stats_bad["beta"] == 4.0

    # Test scheduling draws from Beta distribution
    queries = [{"query": q_good, "kind": "problem", "priority": 1.0},
               {"query": q_bad, "kind": "product", "priority": 1.0}]
    # Mark them as already run once so Thompson Sampling is active
    seen.record_query(q_good, fetched=5, new_posts=2)
    seen.record_query(q_bad, fetched=5, new_posts=0)

    # Over 10 schedules, q_good (alpha=3, beta=1) should be chosen first most of the time compared to q_bad (alpha=1, beta=4)
    good_first = 0
    for _ in range(10):
        sched = seen.schedule(queries, budget=1)
        if sched and sched[0]["query"] == q_good:
            good_first += 1
    assert good_first >= 7


def test_expand_queries_with_proqe_uses_exemplars(tmp_path):
    db_file = tmp_path / "proqe_test.sqlite3"
    store = ExemplarStore(db_file)
    store.add_exemplar(
        tweet_id="2001", product_id="prod_stone", category="سنگ ساختمانی", query="شوره سنگ",
        tweet_text="نمای ساختمونمون شوره زده چیکار کنیم؟", reason="نیاز به ضد شوره سنگ",
    )
    product = SimpleNamespace(product_id="prod_stone", title="محلول ضد شوره و نانو سنگ",
                              category_path="مصالح / سنگ ساختمانی", discovery_priority=1)
    mock_llm = MagicMock()
    mock_llm.complete_json.return_value = ({
        "expanded_queries": [
            {"query": "شوره نما", "kind": "problem"},
            {"query": "پیشنهاد ضد شوره", "kind": "alternative"},                 # 3 words: accepted
            {"query": "چیکار کنم سنگ نما شوره نزنه", "kind": "alternative"},    # 6 words: rejected
            {"query": "خرید نانو", "kind": "solution"},
        ]
    }, {})
    expanded = expand_queries_with_proqe(product, store, llm=mock_llm, model="test-model")
    assert {q["query"] for q in expanded} == {"شوره نما", "پیشنهاد ضد شوره", "خرید نانو"}
    assert all(len(q["query"].split()) <= 3 for q in expanded)
    prompt = mock_llm.complete_json.call_args[0][3]
    assert "نمای ساختمونمون شوره زده چیکار کنیم" in prompt and "Real Confirmed Customer Exemplars" in prompt
    assert "1, 2 or 3 Persian words" in mock_llm.complete_json.call_args[0][2]


def test_product_queries_includes_proqe_queries(tmp_path, monkeypatch):
    db_file = tmp_path / "state.sqlite3"
    monkeypatch.setenv("X_COLLECT_STATE_FILE", str(db_file))
    store = ExemplarStore(db_file)
    store.add_expanded_queries("stone_1", [{"query": "شوره نما", "kind": "problem"},
                                           {"query": "شوره زدن سنگ نما در زمستان", "kind": "problem"}])   # old long one
    product = SimpleNamespace(product_id="stone_1", business_id="b_1", title="نانو سنگ نما", category_path="سنگ / نانو",
                              category_keywords=["سنگ"], discovery_priority=1, x_search_enabled=True)
    texts = [q["query"] for q in product_queries(product, terms={"names": ["نانو سنگ"]})]
    assert any(q.startswith('"شوره نما"') for q in texts)
    assert not any("زمستان" in q for q in texts)


def test_negatives_reach_the_prompt_and_rank_by_similarity(tmp_path):
    store = ExemplarStore(tmp_path / "neg.sqlite3")
    store.add_exemplar("1", "p1", "سنگ نما شوره زده چیکار کنم", query="q1", category="سنگ")
    store.add_exemplar("2", "p9", "لپ تاپ هنگ میکنه", query="q2", category="لپ تاپ")
    store.add_negative("3", "p1", "خبر: قیمت سنگ در بازار جهانی", query="q1")
    top = store.get_exemplars("p1", "سنگ", limit=1, context="ضد شوره سنگ نما")
    assert top[0]["tweet_id"] == "1"

    product = SimpleNamespace(product_id="p1", title="ضد شوره سنگ", category_path="سنگ")
    llm = MagicMock()
    llm.complete_json.return_value = ({"expanded_queries": []}, {})
    expand_queries_with_proqe(product, store, llm=llm)
    prompt = llm.complete_json.call_args[0][3]
    assert "Rejected Tweets" in prompt and "قیمت سنگ در بازار جهانی" in prompt


def test_expansion_due_cold_start_then_needs_new_evidence(tmp_path):
    store = ExemplarStore(tmp_path / "due.sqlite3")
    assert store.expansion_due("p1") is True              # cold start: first expansion needs no exemplars
    store.mark_expanded("p1")
    assert store.expansion_due("p1", min_interval_hours=0) is False
    store.add_exemplar("1", "p1", "متن اول")
    assert store.expansion_due("p1", min_interval_hours=0) is False   # one new exemplar is not enough
    store.add_exemplar("2", "p1", "متن دوم")
    assert store.expansion_due("p1", min_interval_hours=0) is True


def test_prune_drops_losers_and_keeps_untested(tmp_path):
    state = tmp_path / "prune.sqlite3"
    seen = SeenStore(state, platform="x")
    store = ExemplarStore(state)
    store.add_expanded_queries("p1", [{"query": "بد", "kind": "problem"}, {"query": "خوب", "kind": "problem"},
                                      {"query": "نو", "kind": "problem"}])
    for _ in range(10):
        seen.record_feedback("بد lang:fa", success=False, weight=0.5)
    for _ in range(3):
        seen.record_feedback("خوب lang:fa", success=True)
    assert store.prune_expanded_queries(seen, "p1") == 1
    left = {q["query"] for q in store.get_expanded_queries("p1")}
    assert left == {"خوب", "نو"}


def test_product_queries_cover_three_formats_with_sane_count():
    from workers.x_collector.intent_queries import positive_word_count

    terms = {
        "names": ["سنگ نما", "سنگ ساختمانی"],
        "pain_symptoms": ["شوره سنگ", "ترک سنگ", "لکه سنگ", "سنگ کثیف", "رنگ سنگ"],
        "advice_requests": ["سنگ طبیعی", "سنگ ساختمانی", "سنگ نما"],
        "implicit_solutions": ["سنگ ساختمانی", "نمای سنگی", "سنگ طبیعی"],
    }
    product = SimpleNamespace(product_id="s1", business_id="b", title="سنگ نما", category_path="مصالح / سنگ",
                              category_keywords=["سنگ"], discovery_priority=1, x_search_enabled=True)
    qs = product_queries(product, terms=terms)
    assert 9 <= len(qs) <= 10
    assert {"problem", "alternative", "solution"} <= {q["kind"] for q in qs}
    assert all(positive_word_count(q["query"]) <= 3 and " OR " not in q["query"] for q in qs)


def test_ig_term_scoring_demotes_empty_terms_and_keeps_explorers():
    import random
    from apps.discovery.ig_query_scoring import pick, score

    hist = {"بد": {"runs": 8, "leads": 0, "penalty": 4.0}, "خوب": {"runs": 5, "leads": 4, "penalty": 0.5}}
    rng = random.Random(7)
    wins = sum(pick(["بد", "خوب", "تازه"], 1, hist, rng=rng) == ["خوب"] for _ in range(300))
    assert wins > 180
    # never-seen term is still explored sometimes, the empty term almost never
    rng = random.Random(3)
    firsts = [pick(["بد", "تازه"], 1, hist, rng=rng)[0] for _ in range(300)]
    assert firsts.count("تازه") > 240 and score(None, rng) > 0


def test_incomplete_llm_terms_are_not_cached():
    from workers.x_collector.llm_terms import is_complete

    full = {"pain_symptoms": ["الف ب", "پ ت", "ث ج"], "advice_requests": ["الف ب", "پ ت", "ث ج"],
            "implicit_solutions": ["الف ب", "پ ت", "ث ج"]}
    assert is_complete(full)
    assert not is_complete({**full, "advice_requests": []})
    assert not is_complete(None)


def test_purge_long_queries_keeps_only_three_word_queries(tmp_path):
    from workers.x_collector.proqe import purge_long_queries

    state = tmp_path / "s.sqlite3"
    seen = SeenStore(state, platform="x")
    store = ExemplarStore(state)
    for q in ['"سنگ نما" -تخفیف lang:fa', '"سنگ لابی شکسته" lang:fa', '"سنگ تراورتن شکلاتی براق" lang:fa']:
        seen.record_feedback(q, success=True)
    store.add_expanded_queries("p", [{"query": "شوره نما", "kind": "problem"}, {"query": "چیکار کنم شوره سنگ", "kind": "problem"}])
    store.record_outcome('"سنگ تراورتن شکلاتی براق" lang:fa', is_proqe=True, customer=True)
    out = purge_long_queries(state)
    assert out == {"query_runs": 1, "proqe_expanded_queries": 1, "proqe_query_outcomes": 1}
    assert [q["query"] for q in store.get_expanded_queries("p")] == ["شوره نما"]


def test_empty_search_is_penalised(tmp_path):
    seen = SeenStore(tmp_path / "e.sqlite3", platform="x")
    seen.record_feedback('"نمونه یک" lang:fa', success=True, weight=0.0)
    before = seen.get_query_bandit_stats('"نمونه یک" lang:fa')["beta"]
    seen.record_query('"نمونه یک" lang:fa', fetched=0, new_posts=0)
    assert seen.get_query_bandit_stats('"نمونه یک" lang:fa')["beta"] == before + 0.5
    seen.record_query('"نمونه یک" lang:fa', fetched=5, new_posts=1)
    assert seen.get_query_bandit_stats('"نمونه یک" lang:fa')["beta"] == before + 0.5


def test_six_empty_runs_drop_query_and_proqe_is_told_not_to_repeat(tmp_path):
    from workers.x_collector.proqe import _format_expansion_prompt

    query = '"پیراهن پاره" lang:fa'
    seen = SeenStore(tmp_path / "drop.sqlite3", platform="x")
    seen.schedule([{"query": query, "kind": "problem", "priority": 1}], budget=1)
    for _ in range(6):
        seen.record_query(query, fetched=0, new_posts=0)
    assert seen.dropped_queries() == [query]
    assert seen.query_status()[0]["empty_runs"] == 6
    assert seen.schedule([{"query": query, "kind": "problem", "priority": 1}], budget=1) == []
    assert query in _format_expansion_prompt("پیراهن مردانه", "پیراهن", [], [], [], [], [query])


def test_repeated_bad_query_feedback_suppresses_phrase_and_good_feedback_restores_it(tmp_path):
    seen = SeenStore(tmp_path / "feedback-drop.sqlite3", platform="x")
    query = '"لباس ارزان" lang:fa'
    seen.schedule([{"query": query, "kind": "problem", "priority": 1}], budget=1)
    for _ in range(4):
        seen.record_feedback(query, success=False, weight=0.5)
    assert seen.dropped_queries() == [query]
    assert seen.schedule([{"query": query, "kind": "problem", "priority": 1}], budget=1) == []
    seen.record_feedback(query, success=True)
    assert seen.dropped_queries() == []
    assert seen.schedule([{"query": query, "kind": "problem", "priority": 1}], budget=1)
