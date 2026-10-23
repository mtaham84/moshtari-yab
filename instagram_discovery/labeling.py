from __future__ import annotations

import csv
import json
import random
from pathlib import Path
from typing import Any

from workers.ig_collector.lexicon import PersianIntentLexicon
from workers.ig_collector.policies import buyer_request, seller_post

LABEL_COLUMNS = ("id", "kind", "text", "parent caption", "flow", "label", "notes")


def _metadata(record: dict[str, Any]) -> dict[str, Any]:
    value = record.get("metadata", record.get("meta", {}))
    return value if isinstance(value, dict) else {}


def _parent_caption(record: dict[str, Any], posts: dict[str, str]) -> str:
    metadata = _metadata(record)
    parent = metadata.get("parent_post")
    if isinstance(parent, dict):
        return str(parent.get("caption") or "")
    if isinstance(parent, str):
        return parent
    parent_id = metadata.get("post_id") or metadata.get("parent_id")
    return posts.get(str(parent_id), "")


def export_labels(source: str | Path, target: str | Path, sample: int = 200) -> int:
    records = []
    posts: dict[str, str] = {}
    with Path(source).open(encoding="utf-8") as stream:
        for line in stream:
            if not line.strip():
                continue
            row = json.loads(line)
            if not isinstance(row, dict):
                continue
            kind = row.get("kind") or row.get("source_kind")
            if kind == "post" and row.get("id"):
                caption = str(row.get("text") or row.get("caption") or "")
                posts[str(row["id"])] = caption
                post_id = _metadata(row).get("post_id")
                if post_id is not None:
                    posts[str(post_id)] = caption
            if kind == "comment":
                records.append(row)
    limit = max(0, sample)
    selected = random.Random(0).sample(records, limit) if len(records) > limit else records
    target = Path(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=LABEL_COLUMNS)
        writer.writeheader()
        for record in selected:
            writer.writerow({
                "id": record.get("id", ""),
                "kind": "comment",
                "text": record.get("text", ""),
                "parent caption": _parent_caption(record, posts)[:160],
                "flow": "",
                "label": "",
                "notes": "",
            })
    return min(len(records), limit)


def _record_for_policy(
        row: dict[str, str], lexicon: PersianIntentLexicon
) -> tuple[dict[str, Any], dict[str, Any]]:
    intent = lexicon.classify(row.get("parent caption", ""))
    comment_intent = lexicon.classify(row.get("text", ""))
    parent_id = "ig:label-parent"
    return {
        "id": row.get("id", ""), "kind": "comment", "source": "instagram",
        "text": row.get("text", ""), "author_handle": "label_user", "author_id": None,
        "created_at": None, "url": None,
        "metadata": {"intent": {"score": comment_intent.score,
                                     "labels": comment_intent.labels},
                      "post_id": parent_id,
                      "parent_post": {"id": parent_id, "caption": row.get("parent caption", ""),
                                         "url": None}},
    }, {
        "id": parent_id, "kind": "post", "source": "instagram",
        "text": row.get("parent caption", ""), "author_handle": None, "author_id": None,
        "created_at": None, "url": None,
        "metadata": {"intent": {"score": intent.score, "labels": intent.labels}},
    }


def report_labels(path: str | Path) -> dict[str, Any]:
    rows = []
    with Path(path).open(encoding="utf-8-sig", newline="") as stream:
        for row in csv.DictReader(stream):
            if row.get("flow") in {"A", "B", "neither"} and row.get("label") in {"lead", "not_lead"}:
                rows.append(row)

    leads = {flow: sum(row["label"] == "lead" for row in rows if row["flow"] == flow)
             for flow in ("A", "B", "neither")}
    total_flow_leads = leads["A"] + leads["B"]
    lexicon = PersianIntentLexicon.load()
    precision: dict[str, float | None] = {}
    policies = {"A": seller_post, "B": buyer_request}
    for flow in ("A", "B"):
        evaluated = [row for row in rows if row["flow"] == flow]
        predicted: list[bool] = []
        for row in evaluated:
            record, parent = _record_for_policy(row, lexicon)
            predicted.append(policies[flow](record, parent, 1) is not None)
        positive = [row for row, keep in zip(evaluated, predicted) if keep]
        precision[flow] = (sum(row["label"] == "lead" for row in positive) / len(positive)
                           if positive else None)

    return {
        "rows": len(rows),
        "flows": {flow: sum(row["flow"] == flow for row in rows) for flow in ("A", "B", "neither")},
        "labels": {label: sum(row["label"] == label for row in rows) for label in ("lead", "not_lead")},
        "leads": leads,
        "lead_share": {flow: leads[flow] / total_flow_leads if total_flow_leads else None for flow in ("A", "B")},
        "classifier_precision": precision,
    }


def print_report(report: dict[str, Any]) -> None:
    print(f"Labelled rows: {report['rows']}")
    print(f"Flows: {report['flows']}")
    print(f"Labels: {report['labels']}")
    print(f"Leads: {report['leads']}")
    share = report["lead_share"]
    precision = report["classifier_precision"]
    print("Lead share A/B: " + ", ".join(
        f"{flow}={value:.1%}" if value is not None else f"{flow}=n/a"
        for flow, value in share.items()
    ))
    print("Rule precision: " + ", ".join(
        f"{flow}={value:.1%}" if value is not None else f"{flow}=n/a"
        for flow, value in precision.items()
    ))
