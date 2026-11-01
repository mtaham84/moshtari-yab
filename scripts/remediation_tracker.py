#!/usr/bin/env python3
import json
import datetime
import argparse
from pathlib import Path

REGISTRY_PATH = Path("docs/remediation/task_registry.json")
PROGRESS_MD_PATH = Path("docs/remediation/PROGRESS.md")

def load_registry():
    with open(REGISTRY_PATH, "r", encoding="utf-8") as f:
        return json.load(f)

def save_registry(data):
    data["updated_at"] = datetime.datetime.now().isoformat()
    # update summary counts
    counts = {"PLANNED": 0, "IN_PROGRESS": 0, "DONE": 0, "BLOCKED": 0, "DEFERRED": 0, "N/A": 0}
    for t in data["tasks"]:
        st = t["status"]["overall"]
        counts[st] = counts.get(st, 0) + 1
    data["summary"] = counts
    
    with open(REGISTRY_PATH, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    generate_progress_md(data)

def generate_progress_md(data):
    lines = []
    lines.append("# پرونده پیشرفت پایدار اصلاح پروژه moshtari-yab (۱۱ فاز و ۹۹ تسک)")
    lines.append("")
    lines.append(f"- **آخرین به‌روزرسانی:** `{data['updated_at']}`")
    lines.append(f"- **مجموع تسک‌ها:** {data['total_tasks']}")
    lines.append("")
    lines.append("## خلاصه وضعیت کلی")
    lines.append("")
    lines.append("| وضعیت | تعداد |")
    lines.append("|---|---|")
    for k, v in data["summary"].items():
        lines.append(f"| **{k}** | {v} |")

    lines.append("")
    lines.append("## ماتریس وضعیت تمام ۹۹ تسک")
    lines.append("")
    lines.append("| شناسه | فاز | عنوان | پیش‌نیاز | برنامه‌ریزی | پیاده‌سازی | تست | فعال‌سازی | وضعیت کلی | گزارش / سند |")
    lines.append("|---|---|---|---|---|---|---|---|---|---|")

    for t in data["tasks"]:
        t_id = t["id"]
        phase = t["phase"]
        title = t["title"]
        prereq = t["prerequisites"]
        st = t["status"]
        report_link = f"[{t_id}](tasks/{t_id}.md)" if Path(t["report_path"]).exists() else f"`{t_id}`"
        lines.append(
            f"| `{t_id}` | {phase} | {title} | {prereq} | {st['planning']} | {st['implementation']} | {st['testing']} | {st['activation']} | **{st['overall']}** | {report_link} |"
        )

    with open(PROGRESS_MD_PATH, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")

def update_task_status(task_id, planning=None, implementation=None, testing=None, activation=None, overall=None, evidence=None):
    data = load_registry()
    found = False
    for t in data["tasks"]:
        if t["id"] == task_id:
            found = True
            if planning: t["status"]["planning"] = planning
            if implementation: t["status"]["implementation"] = implementation
            if testing: t["status"]["testing"] = testing
            if activation: t["status"]["activation"] = activation
            if overall: t["status"]["overall"] = overall
            if evidence: t["evidence"] = evidence
            break
    if not found:
        raise ValueError(f"Task {task_id} not found.")
    save_registry(data)
    print(f"Updated task {task_id} -> overall: {t['status']['overall']}, planning: {t['status']['planning']}, implementation: {t['status']['implementation']}, testing: {t['status']['testing']}, activation: {t['status']['activation']}")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Remediation Tracker Helper")
    subparsers = parser.add_subparsers(dest="subcommand")

    update_parser = subparsers.add_parser("update")
    update_parser.add_argument("task_id", help="Task ID, e.g. P00.05")
    update_parser.add_argument("--planning", choices=["PLANNED", "IN_PROGRESS", "DONE", "BLOCKED", "DEFERRED", "N/A"])
    update_parser.add_argument("--implementation", choices=["PLANNED", "IN_PROGRESS", "DONE", "BLOCKED", "DEFERRED", "N/A"])
    update_parser.add_argument("--testing", choices=["PLANNED", "IN_PROGRESS", "DONE", "BLOCKED", "DEFERRED", "N/A"])
    update_parser.add_argument("--activation", choices=["PLANNED", "IN_PROGRESS", "DONE", "BLOCKED", "DEFERRED", "N/A"])
    update_parser.add_argument("--overall", choices=["PLANNED", "IN_PROGRESS", "DONE", "BLOCKED", "DEFERRED", "N/A"])
    update_parser.add_argument("--evidence", help="Evidence path or notes")

    args = parser.parse_args()
    if args.subcommand == "update":
        update_task_status(
            args.task_id,
            planning=args.planning,
            implementation=args.implementation,
            testing=args.testing,
            activation=args.activation,
            overall=args.overall or "IN_PROGRESS",
            evidence=args.evidence,
        )
    else:
        data = load_registry()
        generate_progress_md(data)
        print("Progress markdown regenerated.")
