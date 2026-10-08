"""Command-line worker for the shared analysis agent.

    python -m analysis.worker run [--limit 200] [--loop --interval 30] [--mock-llm]
    python -m analysis.worker stats
    python -m analysis.worker verdicts --decision respond [--out results.csv]
    python -m analysis.worker show telegram:123:456
    python -m analysis.worker replay --source telegram | --channel 123 | --all
    python -m analysis.worker enqueue-file data/sample/messages.jsonl [--source x]
"""

from __future__ import annotations

import argparse
import csv
import json
import logging
import sys
import time
from pathlib import Path

from analysis.catalog import load_catalog
from analysis.config import get_settings
from analysis.llm import LLMError, build_llm
from analysis.pipeline import AnalysisPipeline
from analysis.store import AnalysisStore

log = logging.getLogger("analysis.worker")


def _fmt_toman(v: float) -> str:
    return f"{v:,.2f}"


def cmd_run(args, settings, store) -> int:
    catalog = load_catalog(settings, args.catalog)
    try:
        llm = build_llm(settings, mock=args.mock_llm, max_calls=args.max_calls)
    except LLMError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 2
    if args.mock_llm:
        print("⚠️  MOCK LLM: heuristic decisions and estimated tokens — for demos/tests only.\n")
    if not settings.pricing_configured:
        print("ℹ️  MODEL_PRICES / USD_TO_TOMAN not set: token counts are real but costs will show 0.\n")
    recovered = store.recover_stale()
    if recovered:
        log.info("Recovered %s messages left in 'processing' by a previous run", recovered)
    pipeline = AnalysisPipeline(store, llm, catalog, settings, few_shot=args.few_shot)
    while True:
        stats = pipeline.run_once(limit=args.limit, source=args.source)
        if stats.claimed:
            llm_s = stats.llm
            print(
                f"[run] claimed={stats.claimed} prefilter_drop={stats.dropped_prefilter} no_match={stats.no_match_triage} "
                f"deep_pairs={stats.deep_pairs} opportunities={stats.opportunities} released={stats.released} "
                f"| calls={llm_s.get('llm_calls', 0)} tokens={llm_s.get('prompt_tokens', 0) + llm_s.get('completion_tokens', 0)} "
                f"cost={_fmt_toman(llm_s.get('cost_toman', 0))} toman | status={stats.status}"
            )
        if stats.error:
            print(f"[run] LLM error: {stats.error}", file=sys.stderr)
            if stats.fatal_error:
                print("[run] Configuration problem (key / model name / base URL / region). "
                      "Messages were returned to the queue without using an attempt. Fix .env and run again.", file=sys.stderr)
                return 2
        if not args.loop:
            if not stats.claimed:
                print("Inbox is empty — nothing to analyse.")
            return 0 if stats.status == "ok" else 1
        if stats.status == "budget_exhausted":
            print("Call budget for this run exhausted; waiting for the next cycle.")
        time.sleep(args.interval if stats.claimed < args.limit or stats.status != "ok" else 0)


def cmd_stats(args, settings, store) -> int:
    s = store.stats()
    if args.json:
        print(json.dumps(s, ensure_ascii=False, indent=2))
        return 0
    f, c = s["funnel"], s["cost"]
    print("Inbox:", ", ".join(f"{k}={v}" for k, v in sorted(s["inbox"].items())) or "empty")
    print(f"Funnel: entered={f['entered']} → passed_prefilter={f['passed_prefilter']} → reached_deep={f['reached_deep']} "
          f"→ messages_with_opportunity={f['messages_with_opportunity']} (opportunities={f['opportunities']})")
    print(f"Cost:   tokens={c['total_tokens']}  usd={c['total_usd']}  toman={_fmt_toman(c['total_toman'])}  "
          f"avg/message={c['avg_toman_per_message']}  per opportunity={c['toman_per_opportunity']}")
    return 0


VERDICT_COLUMNS = ["message_uid", "source", "product_id", "reached_stage", "final_decision", "skip_reason", "need_type",
                   "intent_stage", "user_level", "fit_score", "reason", "reply_draft", "total_tokens", "cost_toman", "text", "url"]


def _verdict_row(v, store) -> dict:
    a = v.analysis
    m = store.get_message(v.message_uid)
    return {
        "message_uid": v.message_uid, "source": v.source, "product_id": v.product_id, "reached_stage": v.reached_stage,
        "final_decision": v.final_decision, "skip_reason": v.skip_reason, "need_type": a.need_type if a else None,
        "intent_stage": a.intent_stage if a else None, "user_level": a.user_level if a else None,
        "fit_score": a.fit_score if a else None, "reason": a.reason if a else None, "reply_draft": v.reply_draft,
        "total_tokens": v.total_tokens, "cost_toman": v.total_cost_toman, "text": m.text if m else None, "url": m.url if m else None,
    }


def cmd_verdicts(args, settings, store) -> int:
    rows = [_verdict_row(v, store) for _, v in store.list_verdicts(decision=args.decision, limit=args.limit)]
    if args.out:
        out = Path(args.out)
        out.parent.mkdir(parents=True, exist_ok=True)
        if out.suffix.lower() == ".csv":
            with out.open("w", encoding="utf-8-sig", newline="") as fh:
                w = csv.DictWriter(fh, fieldnames=VERDICT_COLUMNS)
                w.writeheader()
                w.writerows(rows)
        else:
            out.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\n", encoding="utf-8")
        print(f"Wrote {len(rows)} verdicts to {out}")
        return 0
    for r in rows:
        print("-" * 70)
        print(f"{r['message_uid']}  product={r['product_id']}  stage={r['reached_stage']}  decision={r['final_decision']}  "
              f"fit={r['fit_score']}  cost={r['cost_toman']} toman  ({r['skip_reason'] or ''})")
        print(f"  msg:   {(r['text'] or '')[:160]}")
        if r["reason"]:
            print(f"  why:   {r['reason']}")
        if r["reply_draft"]:
            print(f"  reply: {r['reply_draft']}")
    print(f"\n{len(rows)} verdict(s).")
    return 0


def cmd_show(args, settings, store) -> int:
    m = store.get_message(args.uid)
    if not m:
        print("Not found.", file=sys.stderr)
        return 1
    print(m.model_dump_json(indent=2))
    for _, v in store.list_verdicts():
        if v.message_uid == args.uid:
            print(v.model_dump_json(indent=2))
    return 0


def cmd_replay(args, settings, store) -> int:
    if not (args.all or args.source or args.channel or args.uid):
        print("Choose what to replay: --all, --source, --channel or --uid", file=sys.stderr)
        return 2
    n = store.reset_for_replay(source=args.source, channel_id=args.channel, uids=args.uid or None)
    print(f"{n} message(s) moved back to the queue (old verdicts deleted). Run `python -m analysis.worker run` next.")
    return 0


def cmd_enqueue_file(args, settings, store) -> int:
    from sources.file_adapter import load_file

    msgs = load_file(args.path, args.source)
    added = store.enqueue_many(msgs)
    print(f"Loaded {len(msgs)} messages, enqueued {added} (duplicates skipped: {len(msgs) - added}).")
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="python -m analysis.worker", description="Moshtari-Yab shared analysis agent")
    p.add_argument("--db-path", default=None, help="Analysis SQLite path (default: ANALYSIS_DB_PATH)")
    p.add_argument("-v", "--verbose", action="store_true")
    sub = p.add_subparsers(dest="cmd", required=True)

    r = sub.add_parser("run", help="Analyse pending messages")
    r.add_argument("--limit", type=int, default=200, help="Messages per run (default 200)")
    r.add_argument("--source", default=None, help="Only this source (telegram, x, file...)")
    r.add_argument("--catalog", default=None, help="Catalog JSON path or feed URL (default: CATALOG_SOURCE)")
    r.add_argument("--mock-llm", action="store_true", help="Offline heuristic model (demo/test only)")
    r.add_argument("--max-calls", type=int, default=None, help="Override MAX_LLM_CALLS_PER_RUN")
    r.add_argument("--few-shot", type=int, default=4, help="Number of examples in the deep prompt (0-4)")
    r.add_argument("--loop", action="store_true", help="Keep polling the inbox")
    r.add_argument("--interval", type=int, default=30, help="Seconds between polls with --loop")

    s = sub.add_parser("stats", help="Funnel and cost overview")
    s.add_argument("--json", action="store_true")

    v = sub.add_parser("verdicts", help="List or export verdicts")
    v.add_argument("--decision", choices=["respond", "discard"], default=None)
    v.add_argument("--limit", type=int, default=None)
    v.add_argument("--out", default=None, help="Write to .csv or .jsonl instead of printing")

    sh = sub.add_parser("show", help="Show one message and its verdicts")
    sh.add_argument("uid")

    rp = sub.add_parser("replay", help="Re-queue already analysed messages")
    rp.add_argument("--all", action="store_true")
    rp.add_argument("--source")
    rp.add_argument("--channel")
    rp.add_argument("--uid", action="append", default=[])

    ef = sub.add_parser("enqueue-file", help="Enqueue messages from JSONL/CSV")
    ef.add_argument("path")
    ef.add_argument("--source", default="file")
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s", datefmt="%H:%M:%S")
    settings = get_settings()
    if args.db_path:
        settings.db_path = args.db_path
    store = AnalysisStore(settings.db_path)
    handlers = {"run": cmd_run, "stats": cmd_stats, "verdicts": cmd_verdicts, "show": cmd_show, "replay": cmd_replay, "enqueue-file": cmd_enqueue_file}
    try:
        return handlers[args.cmd](args, settings, store)
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    sys.exit(main())
