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
