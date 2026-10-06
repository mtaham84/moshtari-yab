from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from buyer_engine.core import ProductProfile, export_result
from buyer_engine.engine import make_queries, run_job


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="Standalone product-specific buyer discovery and ranking")
    parser.add_argument("--product", required=True)
    parser.add_argument("--category", default="")
    parser.add_argument("--city", default=None)
    parser.add_argument("--quantity", type=int, default=None)
    parser.add_argument("--transaction", default="unknown", choices=["unknown", "retail", "wholesale"])
    parser.add_argument("--sources", nargs="+", choices=["x", "divar"], default=["x", "divar"])
    parser.add_argument("--mode", choices=["mock", "live", "dry-run"], default="mock")
    parser.add_argument("--output", type=Path, default=Path("output"))
    parser.add_argument("--max-queries", type=int, default=int(os.getenv("MAX_X_QUERIES", "8")))
    args = parser.parse_args()
    profile = ProductProfile(args.product, args.category, city=args.city, quantity=args.quantity, transaction_type=args.transaction)
    if args.mode == "dry-run":
        print(json.dumps({"product": profile.__dict__, "sources": args.sources, "queries": make_queries(profile, [], args.max_queries), "mode": "dry-run"}, ensure_ascii=False, indent=2))
        return 0
    result = run_job(profile, args.sources, args.mode, max_queries=args.max_queries)
    export_result(result, args.output)
    print(json.dumps({"status": result.status, "product": profile.product_name, "records_collected": result.records_collected, "buyers_found": result.buyers_found, "sellers_filtered": result.sellers_filtered, "duplicates_removed": result.duplicates_removed, "grok_calls": result.grok_calls, "errors": result.errors, "output": str(args.output)}, ensure_ascii=False, indent=2))
    return 0 if result.status in {"COMPLETED", "PARTIAL_SUCCESS"} else 1


if __name__ == "__main__":
    sys.exit(main())
