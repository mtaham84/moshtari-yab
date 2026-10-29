from types import SimpleNamespace
from unittest.mock import MagicMock

from shared.collector_kit.seen import SeenStore
from workers.x_collector.proqe import ExemplarStore, expand_queries_with_proqe, load_proqe_queries
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
        tweet_id="2001",
        product_id="prod_stone",
        category="سنگ ساختمانی",
        query="مشکل شوره سنگ",
        tweet_text="نمای ساختمونمون شوره زده چیکار کنیم؟",
        reason="نیاز به ضد شوره سنگ"
    )

    product = SimpleNamespace(
        product_id="prod_stone",
        title="محلول ضد شوره و نانو سنگ",
        category_path="مصالح / سنگ ساختمانی",
        discovery_priority=1
    )

    mock_llm = MagicMock()
    mock_llm.complete_json.return_value = ({
        "expanded_queries": [
            {"query": "شوره زدن نمای ساختمان", "kind": "problem"},
            {"query": "چیکار کنم سنگ نما شوره نزنه", "kind": "alternative"}
        ]
    }, {})

    expanded = expand_queries_with_proqe(product, store, llm=mock_llm, model="test-model")
    assert len(expanded) == 2
    assert any("شوره زدن" in q["query"] for q in expanded)

    # Check LLM call contained real customer exemplar
    call_args = mock_llm.complete_json.call_args
    user_prompt = call_args[0][3]
    assert "نمای ساختمونمون شوره زده چیکار کنیم" in user_prompt
    assert "Real Confirmed Customer Exemplars" in user_prompt


def test_product_queries_includes_proqe_queries(tmp_path, monkeypatch):
    db_file = tmp_path / "state.sqlite3"
    monkeypatch.setenv("X_COLLECT_STATE_FILE", str(db_file))

    store = ExemplarStore(db_file)
    store.add_expanded_queries("stone_1", [
        {"query": "شوره زدن سنگ نما در زمستان", "kind": "problem"}
    ])

    product = SimpleNamespace(
        product_id="stone_1",
        business_id="b_1",
        title="نانو سنگ نما",
        category_path="سنگ / نانو",
        category_keywords=["سنگ"],
        discovery_priority=1,
        x_search_enabled=True
    )

    queries = product_queries(product, terms={"names": ["نانو سنگ"]})
    queries_text = [q["query"] for q in queries]
    assert any("شوره زدن سنگ نما در زمستان" in q for q in queries_text)


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


def test_expansion_due_needs_new_evidence(tmp_path):
    store = ExemplarStore(tmp_path / "due.sqlite3")
    assert store.expansion_due("p1") is False
    store.add_exemplar("1", "p1", "متن اول")
    assert store.expansion_due("p1") is True
    store.mark_expanded("p1")
    store.add_exemplar("2", "p1", "متن دوم")
    assert store.expansion_due("p1", min_interval_hours=0) is False  # only 1 new exemplar
    store.add_exemplar("3", "p1", "متن سوم")
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
    terms = {
        "names": ["سنگ نما", "سنگ تراورتن"],
        "pain_symptoms": ["سنگ نما شوره زده", "سنگ کف ترک خورده", "دیوار لابی شوره زده", "نمای ساختمون کثیف شده", "سنگ پله لیز شده"],
        "advice_requests": ["کسی راهی بلده برای شوره سنگ", "چی پیشنهاد میدین برای ترک سنگ", "تجربه دارین دربارهٔ سنگ نما",
                            "برای سنگ کثیف چیکار کنم", "کدوم سنگ برای نما بهتره"],
        "implicit_solutions": ["راهکار رفع شوره سنگ", "روش جلوگیری از ترک سنگ", "دنبال سنگبر خوب", "ترمیم سنگ نمای قدیمی", "تمیز کردن سنگ نما"],
    }
    product = SimpleNamespace(product_id="s1", business_id="b", title="سنگ نما", category_path="مصالح / سنگ",
                              category_keywords=["سنگ"], discovery_priority=1, x_search_enabled=True)
    qs = product_queries(product, terms=terms)
    kinds = {q["kind"] for q in qs}
    assert 9 <= len(qs) <= 12
    assert {"problem", "alternative", "solution"} <= kinds
    assert all(q["query"].split(")")[0].count(" OR ") <= 1 for q in qs if q["kind"] not in {"intent", "complaint"})  # <=2 phrases per query


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
