from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from buyer_engine.core import ProductProfile, export_result
from buyer_engine.engine import make_queries, run_job
from buyer_engine.evaluation import evaluate_persian_rules
from buyer_engine.jobs import JobStore


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="Standalone product-specific buyer discovery and ranking")
    parser.add_argument("--product")
    parser.add_argument("--category", default="")
    parser.add_argument("--city", default=None)
    parser.add_argument("--quantity", type=int, default=None)
    parser.add_argument("--transaction", default="unknown", choices=["unknown", "retail", "wholesale"])
    parser.add_argument("--sources", nargs="+", choices=["x", "divar"], default=["x", "divar"])
    parser.add_argument("--mode", choices=["mock", "live", "dry-run"], default="mock")
    parser.add_argument("--output", type=Path, default=Path("output"))
    parser.add_argument("--max-queries", type=int, default=int(os.getenv("MAX_X_QUERIES", "8")))
    parser.add_argument("--evaluate-persian", action="store_true", help="Print local intent-classifier quality metrics and exit")
    actions = parser.add_mutually_exclusive_group()
    actions.add_argument("--status", metavar="JOB_ID", help="Show a persisted job status")
    actions.add_argument("--stop", metavar="JOB_ID", help="Request a running job to pause")
    actions.add_argument("--resume", metavar="JOB_ID", help="Resume a paused or partial job")
    actions.add_argument("--list-jobs", action="store_true", help="List recent persistent jobs")
    args = parser.parse_args()
    job_store = JobStore()
    if args.list_jobs:
        print(json.dumps(job_store.list(), ensure_ascii=False, indent=2))
        return 0
    if args.status:
        job = job_store.get(args.status)
        if not job:
            print(json.dumps({"error": "job not found", "job_id": args.status}))
            return 1
        fields = ("job_id", "status", "current_phase", "created_at", "updated_at", "stop_requested", "profile", "records_collected", "buyers_found", "sellers_filtered", "duplicates_removed", "grok_calls", "grok_prompt_tokens", "grok_completion_tokens", "grok_estimated_cost_usd", "errors")
        print(json.dumps({key: job[key] for key in fields if key in job}, ensure_ascii=False, indent=2))
        return 0
    if args.stop:
        stopped = job_store.request_stop(args.stop)
        job = job_store.get(args.stop)
        print(json.dumps({"job_id": args.stop, "status": "STOP_REQUESTED" if stopped else (job["status"] if job else "NOT_FOUND")}))
        return 0 if stopped else 1
    if args.resume:
        job = job_store.get(args.resume)
        if not job:
            print(json.dumps({"error": "job not found", "job_id": args.resume}))
            return 1
        if job["status"] not in {"PAUSED", "PARTIAL_SUCCESS", "RUNNING", "STOP_REQUESTED"}:
            print(json.dumps({"error": "job is not resumable", "job_id": args.resume, "status": job["status"]}))
            return 1
        profile = ProductProfile(**job["profile"])
        result = run_job(profile, job["sources"], job["mode"], job["max_queries"], job_id=args.resume, job_store=job_store)
        export_result(result, args.output)
        print(json.dumps({"job_id": result.job_id, "status": result.status, "records_collected": result.records_collected, "buyers_found": result.buyers_found, "errors": result.errors, "output": str(args.output)}, ensure_ascii=False, indent=2))
        return 0 if result.status in {"COMPLETED", "PARTIAL_SUCCESS", "PAUSED"} else 1
    if args.evaluate_persian:
        print(json.dumps(evaluate_persian_rules(), ensure_ascii=False, indent=2))
        return 0
    if not args.product:
        parser.error("--product is required unless using a job-management command or --evaluate-persian")
    profile = ProductProfile(args.product, args.category, city=args.city, quantity=args.quantity, transaction_type=args.transaction)
    if args.mode == "dry-run":
        print(json.dumps({"product": profile.__dict__, "sources": args.sources, "queries": make_queries(profile, [], args.max_queries), "mode": "dry-run"}, ensure_ascii=False, indent=2))
        return 0
    result = run_job(profile, args.sources, args.mode, max_queries=args.max_queries, job_store=job_store)
    export_result(result, args.output)
    print(json.dumps({"job_id": result.job_id, "status": result.status, "product": profile.product_name, "records_collected": result.records_collected, "buyers_found": result.buyers_found, "sellers_filtered": result.sellers_filtered, "duplicates_removed": result.duplicates_removed, "grok_calls": result.grok_calls, "grok_prompt_tokens": result.grok_prompt_tokens, "grok_completion_tokens": result.grok_completion_tokens, "grok_estimated_cost_usd": result.grok_estimated_cost_usd, "cache_hits": result.cache_hits, "errors": result.errors, "output": str(args.output)}, ensure_ascii=False, indent=2))
    return 0 if result.status in {"COMPLETED", "PARTIAL_SUCCESS"} else 1


if __name__ == "__main__":
    sys.exit(main())
