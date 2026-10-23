"""CLI for running the engine as a backend worker.

    python -m need_engine run --once            # one pass (ingest → analyse ready chats → match)
    python -m need_engine run                   # loop every NE_POLL_SECONDS
    python -m need_engine run --once --flush    # analyse everything pending now (backfill / tests)
    python -m need_engine stats                 # cost ledger + messages analysed + cost per message
    python -m need_engine stats --business 7    # one seller: its share of the cost / messages reviewed for it
    python -m need_engine demo --chats data/chats.jsonl --products data/products.jsonl [--mock]
                                                # throw-away schema in the same database, output to JSONL
"""
from __future__ import annotations

import argparse
import json
import logging
import secrets
import sys

from need_engine.config import EngineConfig
from need_engine.engine import NeedEngine
from need_engine.mock import mock_llm


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="need_engine")
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run")
    r.add_argument("--once", action="store_true")
    r.add_argument("--flush", action="store_true", help="analyse all pending messages regardless of triggers")
    r.add_argument("--mock", action="store_true", help="offline fake LLM + hash embeddings")
    st_p = sub.add_parser("stats")
    st_p.add_argument("--business", help="Django business id: that seller's cost and cost per reviewed message")
    sub.add_parser("x-prefilter-report")
    d = sub.add_parser("demo", help="run the full pipeline on JSONL files into a temporary state")
    d.add_argument("--chats", required=True)
    d.add_argument("--products", required=True)
    d.add_argument("--mock", action="store_true")
    d.add_argument("--out", default="data/opportunities_demo.jsonl")
    ev = sub.add_parser("eval", help="measure need detection against a reviewed JSONL set")
    ev.add_argument("eval_command", nargs="?", choices=["export-feedback"])
    ev.add_argument("--golden", default="data/golden/need_golden.jsonl")
    ev.add_argument("--out", default="data/golden/candidates.jsonl")
    ev.add_argument("--mock", action="store_true")
    ev.add_argument("--live", action="store_true")
    ev.add_argument("--min-score", type=float, default=50)
    ev.add_argument("--sweep", help="comma-separated thresholds, e.g. 50,55,60,65,70")
    ap.add_argument("-v", "--verbose", action="store_true")
    a = ap.parse_args(argv)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    logging.basicConfig(level=logging.DEBUG if a.verbose else logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

    cfg = EngineConfig()
    if getattr(a, "mock", False):
        cfg.embed_backend = "hash"
    if a.cmd == "eval":
        from need_engine.eval.golden import MockClient, estimate_live_cost, evaluate, export_feedback, load_golden

        if a.eval_command == "export-feedback":
            if a.mock or a.live:
                ap.error("eval export-feedback does not accept --mock or --live")
            print(f"exported={export_feedback(a.out)} path={a.out}")
            return 0
        rows = load_golden(a.golden)
        if a.live and a.mock:
            ap.error("choose either --mock or --live")
        thresholds = [float(value.strip()) for value in a.sweep.split(",") if value.strip()] if a.sweep else [a.min_score]
        if not thresholds or any(value < 0 or value > 100 for value in thresholds):
            ap.error("--sweep requires at least one threshold")
        if a.live:
            model, estimated_usd = estimate_live_cost(rows, cfg)
            reviewed_count = sum(row.reviewed for row in rows)
            print(f"Estimated maximum LLM cost: ${estimated_usd:.6f} for {reviewed_count} reviewed examples using {model}.")
            if input("Type yes to continue with live requests: ").strip().lower() != "yes":
                print("cancelled")
                return 1
            from need_engine.llm import LLMClient
            from need_engine.store import Store

            cfg.state_schema = f"need_engine_eval_{secrets.token_hex(4)}"
            store = Store(cfg.database_url, cfg.state_schema)
            llm = LLMClient(cfg, store)
            try:
                for threshold in thresholds:
                    _print_eval(evaluate(rows, cfg, llm, threshold), threshold)
            finally:
                store.conn.execute(f"DROP SCHEMA IF EXISTS {cfg.state_schema} CASCADE")
                store.close()
        else:
            for threshold in thresholds:
                _print_eval(evaluate(rows, cfg, MockClient(), threshold), threshold)
        return 0
    if a.cmd in {"stats", "x-prefilter-report"}:
        from need_engine.store import Store

        st = Store(cfg.database_url, cfg.state_schema)
        if a.cmd == "x-prefilter-report":
            for row in st.x_filter_summary():
                print(json.dumps(row, ensure_ascii=False))
            return 0
        biz = a.business
        for row in st.cost_summary(business_id=biz):
            print(json.dumps(row, ensure_ascii=False))
        if biz:
            from need_engine.access import SourceAccess

            acc = SourceAccess(cfg)
            acc.refresh()
            chats = [c for c in acc.rules if acc.allows(c, biz)]
            print(json.dumps({"business_id": biz, **st.totals(business_id=biz, chat_ids=chats)}, ensure_ascii=False))
        else:
            print(json.dumps(st.totals(), ensure_ascii=False))
        shadow = {row["reason"]: row["count"] for row in st.x_filter_summary() if row["mode"] == "shadow"}
        print(json.dumps({"x_prefilter_would_drop": shadow}, ensure_ascii=False))
        return 0
    if a.cmd == "demo":
        cfg.messages_source, cfg.products_source = f"jsonl:{a.chats}", f"jsonl:{a.products}"
        cfg.state_schema = f"need_engine_demo_{secrets.token_hex(4)}"
        cfg.output_jsonl = a.out
        eng = NeedEngine(cfg, mock_llm=mock_llm if a.mock else None)
        try:
            rep = eng.run_once(flush=True)
        finally:
            eng.store.conn.execute(f"DROP SCHEMA IF EXISTS {cfg.state_schema} CASCADE")
        print(rep.summary())
        print(f"→ {len(rep.opportunities)} opportunities written to {a.out}")
        return 0
    eng = NeedEngine(cfg, mock_llm=mock_llm if a.mock else None)
    if a.once:
        rep = eng.run_once(flush=a.flush)
        return 1 if rep.errors else 0
    eng.run_forever()
    return 0


def _print_eval(result: dict, threshold: float) -> None:
    print(f"threshold={threshold:g} precision={result['precision']:.3f} recall={result['recall']:.3f} f1={result['f1']:.3f} tp={result['tp']} fp={result['fp']} fn={result['fn']}")
    for title, key in (("False positives", "false_positives"), ("False negatives", "false_negatives")):
        print(title + ":")
        for item in result[key]:
            print(f"  {item['id']}: {item['text']}")


if __name__ == "__main__":
    sys.exit(main())
