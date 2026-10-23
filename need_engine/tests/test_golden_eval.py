from dataclasses import replace

from need_engine.config import EngineConfig
from need_engine.eval.golden import GoldenRow, MockClient, evaluate, estimate_live_cost, load_golden
from need_engine.__main__ import main


def test_golden_fixture_loads_and_mock_evaluation_counts_errors():
    rows = load_golden("data/golden/need_golden.jsonl")
    result = evaluate(rows[:4], EngineConfig(), MockClient(), 50)

    assert len(rows) == 30
    assert result["tp"] + result["fn"] == sum(row.expected["is_need"] for row in rows[:4])
    assert result["precision"] == 1.0
    assert result["false_positives"] == []


def test_eval_sweep_prints_one_row_per_threshold(capsys):
    code = main(["eval", "--golden", "data/golden/need_golden.jsonl", "--mock", "--sweep", "50,60"])

    output = capsys.readouterr().out
    assert code == 0
    assert "threshold=50" in output
    assert "threshold=60" in output
    assert "False negatives:" in output


def test_shadow_prefilter_does_not_remove_x_examples():
    rows = [row for row in load_golden("data/golden/need_golden.jsonl") if row.platform == "x"]
    rows[0] = replace(rows[0], text="دنبال لپتاپ هستم، چی بخرم؟")
    cfg = EngineConfig(x_prefilter="shadow", x_min_chars=1000, products_source="jsonl:unused")

    result = evaluate(rows[:1], cfg, MockClient(), 50)

    assert result["predictions"][0]["predicted"] is True


def test_on_prefilter_drops_x_example():
    rows = [row for row in load_golden("data/golden/need_golden.jsonl") if row.platform == "x"]
    rows[0] = replace(rows[0], text="دنبال لپتاپ هستم، چی بخرم؟")
    cfg = EngineConfig(x_prefilter="on", x_min_chars=1000, products_source="jsonl:unused")

    result = evaluate(rows[:1], cfg, MockClient(), 50)

    assert result["predictions"][0]["predicted"] is False


def test_live_cost_estimate_uses_rendered_input_and_configured_prices():
    rows = load_golden("data/golden/need_golden.jsonl")[:1]
    cfg = EngineConfig(products_source="jsonl:unused", prices={"gemini-3.5-flash-lite": [0.30, 2.50]})

    model, estimate = estimate_live_cost(rows, cfg)

    assert model == cfg.extract_model
    assert estimate > 0


def test_feedback_exports_are_not_scored_until_reviewed():
    rows = [GoldenRow("candidate", "telegram", "دنبال گوشی هستم", {"is_need": True}, reviewed=False)]

    result = evaluate(rows, EngineConfig(), MockClient(), 50)

    assert result["predictions"] == []
    assert result["tp"] == result["fp"] == result["fn"] == 0
from dataclasses import replace

