"""Export messages for manual labelling and validate the labelled file.

One row per message. Labellers fill:
  label_product_ids   comma-separated product ids that are a real opportunity ("" = none)
  label_need_type     explicit | implicit | none
  label_user_level    free text (optional)
  label_notes         free text (optional)

The ``split`` column (dev/test, ~70/30) is deterministic per uid. Tune prompts on
dev only; evaluate once on test at the end (milestone 3, analysis/evaluate.py).

    python -m analysis.labeling export --out data/labeling.csv [--with-predictions]
    python -m analysis.labeling validate data/labeling.csv
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import sys
from collections import Counter
from pathlib import Path

from analysis.catalog import load_catalog
from analysis.config import get_settings
from analysis.store import AnalysisStore

COLUMNS = ["uid", "split", "source", "channel", "author", "text", "context",
           "label_product_ids", "label_need_type", "label_user_level", "label_notes"]
PRED_COLUMNS = ["pred_product_ids", "pred_fit_scores", "pred_stage"]
NEED_TYPES = {"explicit", "implicit", "none", ""}


def split_for(uid: str, test_share: float = 0.3) -> str:
    bucket = int(hashlib.sha1(uid.encode()).hexdigest()[:8], 16) % 100
    return "test" if bucket < test_share * 100 else "dev"


def _context_text(m) -> str:
    lines = []
    for c in m.context:
        tag = {"parent": "↑", "previous": "·", "reply": "↓"}[c.relation]
        lines.append(f"{tag} {c.author_name or 'کاربر'}: {c.text[:200]}")
    return "\n".join(lines)


def export(store: AnalysisStore, out: Path, with_predictions: bool = False) -> int:
    preds: dict[str, list] = {}
    if with_predictions:
        for _, v in store.list_verdicts():
            preds.setdefault(v.message_uid, []).append(v)
    cols = COLUMNS + (PRED_COLUMNS if with_predictions else [])
    out.parent.mkdir(parents=True, exist_ok=True)
    n = 0
    with out.open("w", encoding="utf-8-sig", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=cols)
        w.writeheader()
        for m, _status in store.iter_messages():
            row = {
                "uid": m.uid, "split": split_for(m.uid), "source": m.source, "channel": m.channel_title or m.channel_id or "",
                "author": m.author.display_name or m.author.handle or "", "text": m.text, "context": _context_text(m),
                "label_product_ids": "", "label_need_type": "", "label_user_level": "", "label_notes": "",
            }
            if with_predictions:
                vs = preds.get(m.uid, [])
                row["pred_product_ids"] = ",".join(str(v.product_id) for v in vs if v.final_decision == "respond")
                row["pred_fit_scores"] = ",".join(f"{v.product_id}:{v.analysis.fit_score}" for v in vs if v.analysis)
                row["pred_stage"] = max((v.reached_stage for v in vs), key=["prefilter", "triage", "deep"].index, default="")
            w.writerow(row)
            n += 1
    return n


def validate(path: Path, product_ids: set[int]) -> tuple[list[str], Counter]:
    errors, counts = [], Counter()
    with path.open(encoding="utf-8-sig", newline="") as fh:
        for i, row in enumerate(csv.DictReader(fh), 2):
            raw_ids = (row.get("label_product_ids") or "").replace("،", ",")
            ids = [x.strip() for x in raw_ids.split(",") if x.strip()]
            for x in ids:
                if not x.isdigit() or int(x) not in product_ids:
                    errors.append(f"line {i}: unknown product id {x!r}")
            need = (row.get("label_need_type") or "").strip().lower()
            if need not in NEED_TYPES:
                errors.append(f"line {i}: label_need_type must be explicit/implicit/none, got {need!r}")
            if ids and need in {"", "none"}:
                errors.append(f"line {i}: has products but need_type is {need or 'empty'}")
            counts["rows"] += 1
            counts[f"split_{row.get('split') or '?'}"] += 1
            counts["positive" if ids else "negative"] += 1
            if need:
                counts[f"need_{need}"] += 1
            else:
                counts["unlabelled_need_type"] += 1
    return errors, counts


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="python -m analysis.labeling")
    p.add_argument("--db-path", default=None)
    sub = p.add_subparsers(dest="cmd", required=True)
    e = sub.add_parser("export")
    e.add_argument("--out", default="data/labeling.csv")
    e.add_argument("--with-predictions", action="store_true", help="Add model predictions (can bias labellers!)")
    v = sub.add_parser("validate")
    v.add_argument("path")
    v.add_argument("--catalog", default=None)
    args = p.parse_args(argv)
    settings = get_settings()
    if args.cmd == "export":
        n = export(AnalysisStore(args.db_path or settings.db_path), Path(args.out), args.with_predictions)
        print(f"Exported {n} messages to {args.out}")
        return 0
    ids = {pr.product_id for pr in load_catalog(settings, args.catalog)}
    errors, counts = validate(Path(args.path), ids)
    print(", ".join(f"{k}={v}" for k, v in sorted(counts.items())))
    for err in errors[:50]:
        print("  ✗", err)
    if len(errors) > 50:
        print(f"  ... and {len(errors) - 50} more")
    print("OK" if not errors else f"{len(errors)} problem(s)")
    return 0 if not errors else 1


if __name__ == "__main__":
    sys.exit(main())
