"""Milestone 2: prefilter, triage, deep analysis, guards, pipeline, costs, CLI."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from analysis.catalog import load_catalog
from analysis.config import AnalysisSettings
from analysis.deep import DeepAnalyzer, render_conversation
from analysis.guards import apply_guards, tracked_url
from analysis.labeling import export, validate
from analysis.llm import BaseLLM, LLMBudgetExceeded, LLMError, LLMResponse, parse_json_object
from analysis.pipeline import AnalysisPipeline
from analysis.prefilter import prefilter
from analysis.schemas import Author, ContextMessage, ProductProfile, SocialMessage
from analysis.store import AnalysisStore
from analysis.triage import Triage, allocate
from sources.file_adapter import load_file

ROOT = Path(__file__).resolve().parents[2]
SAMPLE_MESSAGES = ROOT / "data" / "sample" / "messages.jsonl"
SAMPLE_CATALOG = ROOT / "data" / "sample" / "catalog.json"


def settings(tmp_path: Path, **kw) -> AnalysisSettings:
    base = dict(db_path=str(tmp_path / "a.sqlite3"), catalog_source=str(SAMPLE_CATALOG), llm_api_key="test",
                model_prices={"small": (1.0, 2.0), "big": (10.0, 20.0)}, usd_to_toman=100_000,
                triage_model="small", deep_model="big", triage_batch_size=3, fit_threshold=60, max_llm_calls_per_run=100)
    base.update(kw)
    return AnalysisSettings(**base)


class ScriptedLLM(BaseLLM):
    """Returns whatever ``responder(stage_or_payload, messages)`` produces; fixed token usage per call."""

    def __init__(self, s, responder, prompt_tokens=1000, completion_tokens=100, fail_after: int | None = None, error_after: int | None = None):
        super().__init__(s)
        self.responder = responder
        self.pt, self.ct = prompt_tokens, completion_tokens
        self.fail_after, self.error_after = fail_after, error_after
        self.history: list[dict] = []

    def _complete(self, model, messages, max_tokens):
        if self.error_after is not None and self.calls_made >= self.error_after:
            raise LLMError("provider down")
        payload = json.loads(messages[-1]["content"]) if messages[-1]["content"].startswith("{") else {"retry": messages[-1]["content"]}
        self.history.append({"model": model, "payload": payload})
        out = self.responder(model, payload, messages)
        content = out if isinstance(out, str) else json.dumps(out, ensure_ascii=False)
        return LLMResponse(parse_json_object(content), content, model, self.pt, self.ct, 10)


def msg(uid, text, **kw) -> SocialMessage:
    return SocialMessage(uid=uid, source=kw.pop("source", "telegram"), text=text, author=Author(id=uid, display_name="علی"), **kw)


def respond(fit=85, draft="سلام! این دوره برای شروع عالیه: {{PRODUCT_LINK}}"):
    return {"need_type": "explicit", "intent_stage": "comparing", "user_level": "beginner", "fit_score": fit,
            "decision": "respond", "reason": "نیاز روشن", "reply_draft": draft}


def triage_all(product_id=1):
    def fn(model, payload, messages):
        return {"results": [{"id": m["id"], "matches": [{"product_id": product_id, "reason": "r"}]} for m in payload["messages"]]}
    return fn


@pytest.fixture
def catalog():
    return load_catalog(AnalysisSettings(catalog_source=str(SAMPLE_CATALOG)))


# ----------------------------------------------------------------- helpers
def test_allocate_is_exact_and_proportional():
    parts = allocate(1001, [1, 1, 2])
    assert sum(parts) == 1001 and parts[2] >= parts[0]
    assert allocate(5, [0, 0]) in ([3, 2], [2, 3])


def test_parse_json_object_handles_fences():
    assert parse_json_object('```json\n{"a": 1}\n```') == {"a": 1}
    assert parse_json_object("not json") is None


# --------------------------------------------------------------- prefilter
@pytest.mark.parametrize("text,reason", [
    ("مرسی 🙏", "too_short"),
    ("سلام صبح بخیر به همه دوستان", "chit_chat"),
    ("https://t.me/abc https://t.me/def", "link_only"),
    ("فروش ویژه عینک اورجینال، ارسال به سراسر کشور، سفارش در دایرکت", "advertisement"),
])
def test_prefilter_drops(text, reason):
    assert prefilter(msg("u", text)).reason == reason


def test_prefilter_keeps_implicit_need_and_buyer_question():
    assert prefilter(msg("u", "این روزا روزی ۱۰ ساعت پای لپ‌تاپم، شبا چشمام می‌سوزه")).passed
    assert prefilter(msg("u", "عینک بلوکات تخفیف داره؟ می‌خوام بخرم")).passed      # one ad-like word is not enough
    bot = SocialMessage(uid="b", source="telegram", text="پیام طولانی از طرف یک بات", author=Author(is_bot=True))
    assert prefilter(bot).reason == "bot_author"


# ------------------------------------------------------------------ guards
def test_guards(catalog):
    p = catalog[0]
    g = apply_guards("سلام! ببین: {{PRODUCT_LINK}} یا اینجا https://evil.com زنگ بزن 09121234567", p, "abc")
    assert g.ok and tracked_url(p.url, "abc") in g.text and "evil" not in g.text and "0912" not in g.text
    assert {"foreign_link_removed", "contact_info_removed"} <= set(g.issues)
    g2 = apply_guards("سلام، این دوره به کارت میاد", p, "r")
    assert g2.ok and "link_appended" in g2.issues and g2.text.endswith(tracked_url(p.url, "r"))
    assert apply_guards("Hello, buy this {{PRODUCT_LINK}}", p, "r").issues[-1] == "not_persian"
    long = apply_guards("سلام. " + "این یک جمله طولانی است. " * 60 + "{{PRODUCT_LINK}}", p, "r", max_chars=200)
    assert "truncated" in long.issues and len(long.text) < 320
    assert tracked_url("https://s.io/p/1/?a=1", "x") == "https://s.io/p/1/?a=1&src=agent&ref=x"


# ------------------------------------------------------------------ triage
def test_triage_costs_sum_to_call_usage_and_split_on_invalid(tmp_path, catalog):
    s = settings(tmp_path, triage_batch_size=4)
    calls = {"n": 0}

    def fn(model, payload, messages):
        calls["n"] += 1
        if calls["n"] == 1:
            return {"results": [{"id": "m1", "matches": []}]}           # invalid: ids missing -> split
        return {"results": [{"id": m["id"], "matches": [{"product_id": 2, "reason": "چشم"}] if "چشم" in m["text"] else []}
                            for m in payload["messages"]]}

    llm = ScriptedLLM(s, fn)
    msgs = [msg(f"u{i}", t) for i, t in enumerate(["چشمام می‌سوزه از لپ‌تاپ", "یک پیام عادی دیگر", "کافه داریم", "سلام به همه"])]
    out = Triage(llm, s).run(msgs, catalog)
    assert calls["n"] == 3 and llm.ledger.summary()["invalid_outputs"] == 1
    assert [t.product_id for t in out.matches["u0"]] == [2] and out.matches["u1"] == []
    total_pt = sum(c.prompt_tokens for c in out.costs.values())
    assert total_pt == 3 * 1000                                            # every spent token is attributed
    assert round(sum(c.cost_usd for c in out.costs.values()), 9) == round(llm.ledger.summary()["cost_usd"], 9)


def test_triage_rejects_unknown_product(tmp_path, catalog):
    s = settings(tmp_path, triage_batch_size=1)
    llm = ScriptedLLM(s, lambda *a: {"results": [{"id": "m1", "matches": [{"product_id": 99}]}]})
    out = Triage(llm, s).run([msg("u1", "متن تست برای محصول ناموجود")], catalog)
    assert out.failed == ["u1"]


# -------------------------------------------------------------------- deep
def test_deep_corrective_retry_and_cost(tmp_path, catalog):
    s = settings(tmp_path)
    answers = iter(['{"decision": "respond"}', respond()])
    llm = ScriptedLLM(s, lambda *a: next(answers))
    out = DeepAnalyzer(llm, s).analyze(msg("u1", "دنبال دوره پایتونم"), catalog[0])
    assert out.analysis and out.analysis.fit_score == 85
    assert out.cost.prompt_tokens == 2000 and out.cost.cost_toman == pytest.approx(2 * (1000 * 10 + 100 * 20) / 1e6 * 100_000)


def test_deep_gives_up_after_two_invalid(tmp_path, catalog):
    s = settings(tmp_path)
    out = DeepAnalyzer(ScriptedLLM(s, lambda *a: "nonsense"), s).analyze(msg("u1", "متن"), catalog[0])
    assert out.analysis is None and out.error.startswith("invalid_deep_output")


def test_render_conversation_respects_budget():
    ctx = [ContextMessage(id=str(i), relation="previous", text="پیام قبلی " * 30) for i in range(5)]
    conv = render_conversation(msg("u", "متن اصلی", context=ctx), char_budget=900)
    assert conv["target"]["text"] == "متن اصلی" and len(conv["previous"]) < 5


# ---------------------------------------------------------------- pipeline
def test_pipeline_funnel_and_per_message_costs(tmp_path, catalog):
    s = settings(tmp_path)
    store = AnalysisStore(s.db_path)
    store.enqueue_many([
        msg("telegram:1:1", "سلام، تازه می‌خوام پایتون یاد بگیرم، دوره با پشتیبانی سراغ دارید؟"),
        msg("telegram:1:2", "مرسی"),
        msg("telegram:1:3", "فردا جلسه ساعت چنده بچه‌ها؟"),
        msg("telegram:1:4", "دوره پایتون پیشرفته برای حرفه‌ای‌ها می‌خوام"),
    ])

    def fn(model, payload, messages):
        if model == "small":
            return {"results": [{"id": m["id"], "matches": [{"product_id": 1, "reason": "r"}] if "پایتون" in m["text"] else []}
                                for m in payload["messages"]]}
        text = payload["conversation"]["target"]["text"]
        return respond(fit=90) if "تازه" in text else respond(fit=40)

    llm = ScriptedLLM(s, fn)
    stats = AnalysisPipeline(store, llm, catalog, s).run_once()
    assert (stats.claimed, stats.dropped_prefilter, stats.no_match_triage, stats.deep_pairs, stats.opportunities) == (4, 1, 1, 2, 1)
    verdicts = {v.message_uid: v for _, v in store.list_verdicts()}
    assert verdicts["telegram:1:2"].reached_stage == "prefilter" and verdicts["telegram:1:2"].total_cost_toman == 0
    assert verdicts["telegram:1:3"].reached_stage == "triage" and verdicts["telegram:1:3"].total_tokens > 0
    assert verdicts["telegram:1:4"].skip_reason.startswith("below_threshold")
    good = verdicts["telegram:1:1"]
    assert good.final_decision == "respond" and "src=agent" in good.reply_draft
    # per-message costs add up exactly to what was actually spent
    assert sum(v.total_cost_usd for v in verdicts.values()) == pytest.approx(stats.llm["cost_usd"], rel=1e-6)
    assert store.stats()["funnel"] == {"entered": 4, "passed_prefilter": 3, "reached_deep": 2,
                                       "messages_with_opportunity": 1, "opportunities": 1}


def test_budget_exhaustion_releases_and_reuses_cached_triage(tmp_path, catalog):
    s = settings(tmp_path, triage_batch_size=10)
    store = AnalysisStore(s.db_path)
    store.enqueue_many([msg(f"telegram:1:{i}", f"دنبال دوره پایتون هستم شماره {i}") for i in range(3)])
    fn = lambda model, payload, messages: triage_all()(model, payload, messages) if model == "small" else respond()

    llm = ScriptedLLM(s, fn)
    llm.max_calls = 2                                   # 1 triage + 1 deep, then budget is gone
    stats = AnalysisPipeline(store, llm, catalog, s).run_once()
    assert stats.status == "budget_exhausted" and stats.completed == 1 and stats.released == 2
    assert store.stats()["inbox"] == {"done": 1, "pending": 2}

    llm2 = ScriptedLLM(s, fn)
    stats2 = AnalysisPipeline(store, llm2, catalog, s).run_once()
    assert stats2.completed == 2 and all(h["model"] == "big" for h in llm2.history)   # no second triage call
    for _, v in store.list_verdicts():
        assert any(c.stage == "triage" for c in v.costs)                              # cached triage cost kept


def test_llm_error_releases_messages_with_attempt(tmp_path, catalog):
    s = settings(tmp_path, max_attempts=1)
    store = AnalysisStore(s.db_path)
    store.enqueue(msg("telegram:1:1", "دنبال دوره پایتون هستم برای شروع"))
    stats = AnalysisPipeline(store, ScriptedLLM(s, triage_all(), error_after=0), catalog, s).run_once()
    assert stats.status == "llm_error" and store.stats()["inbox"] == {"error": 1}


def test_mock_llm_full_run_on_sample_data(tmp_path, catalog):
    from analysis.mock_llm import HeuristicMockLLM

    s = settings(tmp_path, model_prices={"llama-3.1-8b-instant": (0.05, 0.08), "llama-3.3-70b-versatile": (0.59, 0.79)},
                 triage_model="llama-3.1-8b-instant", deep_model="llama-3.3-70b-versatile", triage_batch_size=15)
    store = AnalysisStore(s.db_path)
    assert store.enqueue_many(load_file(SAMPLE_MESSAGES)) == 38
    stats = AnalysisPipeline(store, HeuristicMockLLM(s), catalog, s).run_once(limit=100)
    assert stats.completed == 38 and stats.status == "ok"
    st = store.stats()
    assert st["funnel"]["entered"] == 38 and 0 < st["funnel"]["reached_deep"] < 38
    assert st["cost"]["total_usd"] == pytest.approx(stats.llm["cost_usd"], rel=1e-4)
    seller = [v for _, v in store.list_verdicts() if v.message_uid == "telegram:1003:27"][0]
    assert seller.reached_stage == "prefilter" and seller.skip_reason == "advertisement"


# ------------------------------------------------------- labeling + worker
def test_labeling_export_and_validate(tmp_path, catalog):
    store = AnalysisStore(str(tmp_path / "a.sqlite3"))
    store.enqueue_many(load_file(SAMPLE_MESSAGES)[:5])
    out = tmp_path / "lab.csv"
    assert export(store, out) == 5
    import csv as _csv

    with out.open(encoding="utf-8-sig", newline="") as fh:
        data = list(_csv.DictReader(fh))
    data[0]["label_product_ids"], data[0]["label_need_type"] = "1", "explicit"
    data[1]["label_product_ids"], data[1]["label_need_type"] = "9", "none"
    with out.open("w", encoding="utf-8-sig", newline="") as fh:
        w = _csv.DictWriter(fh, fieldnames=list(data[0].keys()))
        w.writeheader()
        w.writerows(data)
    errors, counts = validate(out, {p.product_id for p in catalog})
    assert counts["rows"] == 5 and counts["positive"] == 2
    assert any("unknown product id '9'" in e for e in errors) and any("has products but need_type" in e for e in errors)


def test_worker_cli_mock_run(tmp_path, capsys, monkeypatch):
    from analysis import worker

    db = str(tmp_path / "w.sqlite3")
    monkeypatch.setenv("CATALOG_SOURCE", str(SAMPLE_CATALOG))
    assert worker.main(["--db-path", db, "enqueue-file", str(SAMPLE_MESSAGES)]) == 0
    assert worker.main(["--db-path", db, "run", "--mock-llm", "--catalog", str(SAMPLE_CATALOG)]) == 0
    assert worker.main(["--db-path", db, "stats"]) == 0
    assert worker.main(["--db-path", db, "verdicts", "--decision", "respond", "--out", str(tmp_path / "r.csv")]) == 0
    assert worker.main(["--db-path", db, "replay", "--source", "x"]) == 0
    out = capsys.readouterr().out
    assert "Funnel: entered=38" in out and "moved back to the queue" in out


def test_fatal_llm_error_keeps_messages_pending_without_attempt(tmp_path, catalog):
    class FatalLLM(ScriptedLLM):
        def _complete(self, model, messages, max_tokens):
            raise LLMError("HTTP 400: User location is not supported", fatal=True)

    s = settings(tmp_path, max_attempts=1)
    store = AnalysisStore(s.db_path)
    store.enqueue(msg("telegram:1:1", "دنبال دوره پایتون هستم برای شروع"))
    stats = AnalysisPipeline(store, FatalLLM(s, triage_all()), catalog, s).run_once()
    assert stats.status == "llm_error" and stats.fatal_error and "location" in stats.error
    assert store.stats()["inbox"] == {"pending": 1}
