"""CLI for running the engine as a backend worker.

    python -m need_engine run --once            # one pass (ingest → analyse ready chats → match)
    python -m need_engine run                   # loop every NE_POLL_SECONDS
    python -m need_engine run --once --flush    # analyse everything pending now (backfill / tests)
    python -m need_engine stats                 # cost ledger
    python -m need_engine demo --chats data/chats.jsonl --products data/products.jsonl [--mock]
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import tempfile

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
    sub.add_parser("stats")
    d = sub.add_parser("demo", help="run the full pipeline on JSONL files into a temporary state")
    d.add_argument("--chats", required=True)
    d.add_argument("--products", required=True)
    d.add_argument("--mock", action="store_true")
    d.add_argument("--out", default="data/opportunities_demo.jsonl")
    ap.add_argument("-v", "--verbose", action="store_true")
    a = ap.parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if a.verbose else logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

    cfg = EngineConfig()
    if getattr(a, "mock", False):
        cfg.embed_backend = "hash"
    if a.cmd == "stats":
        from need_engine.store import Store

        for row in Store(cfg.state_path).cost_summary():
            print(json.dumps(row, ensure_ascii=False))
        return 0
    if a.cmd == "demo":
        cfg.messages_dsn, cfg.products_source = f"jsonl:{a.chats}", f"jsonl:{a.products}"
        cfg.state_path = os.path.join(tempfile.mkdtemp(prefix="need_engine_"), "state.db")
        cfg.output_path = a.out
        eng = NeedEngine(cfg, mock_llm=mock_llm if a.mock else None)
        rep = eng.run_once(flush=True)
        print(rep.summary())
        print(f"→ {len(rep.opportunities)} opportunities written to {a.out}")
        return 0
    eng = NeedEngine(cfg, mock_llm=mock_llm if a.mock else None)
    if a.once:
        rep = eng.run_once(flush=a.flush)
        return 1 if rep.errors else 0
    eng.run_forever()
    return 0


if __name__ == "__main__":
    sys.exit(main())
