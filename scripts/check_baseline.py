"""Guardrail: no file may grow past 800 lines, and no file may gain broad excepts.

Existing violations are recorded in scripts/guardrail_baseline.json and may only shrink:

    python scripts/check_baseline.py            # CI / pre-commit: fail on any new or grown violation
    python scripts/check_baseline.py --update   # after a file shrank: lower its entry (never raises one)

Without a baseline file, --update records the current state.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

MAX_LINES = 800
SIZE_SUFFIXES = (".py", ".html", ".css")
EXCEPT_RULES = "BLE001,S110"   # blind `except Exception`, `try/except/pass`
BASELINE_NAME = "scripts/guardrail_baseline.json"


def _tracked_files(root: Path) -> list[str]:
    out = subprocess.run(["git", "ls-files", "-z"], cwd=root, check=True, capture_output=True, text=True).stdout
    return [p for p in out.split("\0") if p]


def oversized_files(root: Path) -> dict[str, int]:
    """Line count of every tracked .py/.html/.css file over MAX_LINES (migrations excluded)."""
    sizes = {}
    for rel in _tracked_files(root):
        if not rel.endswith(SIZE_SUFFIXES) or "/migrations/" in f"/{rel}":
            continue
        path = root / rel
        if not path.is_file():   # deleted in the working tree
            continue
        lines = len(path.read_text(encoding="utf-8", errors="replace").splitlines())
        if lines > MAX_LINES:
            sizes[rel] = lines
    return sizes


def broad_excepts(root: Path) -> dict[str, int]:
    """Number of BLE001/S110 hits per file, using the project's ruff configuration."""
    res = subprocess.run(
        ["ruff", "check", ".", "--select", EXCEPT_RULES, "--output-format", "json", "--exit-zero", "--no-cache"],
        cwd=root, check=True, capture_output=True, text=True,
    )
    counts: dict[str, int] = {}
    for item in json.loads(res.stdout or "[]"):
        rel = Path(item["filename"]).resolve().relative_to(root.resolve()).as_posix()
        counts[rel] = counts.get(rel, 0) + 1
    return counts


def find_problems(current: dict[str, dict[str, int]], baseline: dict[str, dict[str, int]]) -> list[str]:
    problems = []
    for path, lines in sorted(current["lines"].items()):
        allowed = baseline["lines"].get(path, MAX_LINES)
        if lines > allowed:
            problems.append(f"{path}: {lines} lines, allowed {allowed}. Split it or move new code to a new module.")
    for path, count in sorted(current["broad_excepts"].items()):
        allowed = baseline["broad_excepts"].get(path, 0)
        if count > allowed:
            problems.append(f"{path}: {count} broad excepts ({EXCEPT_RULES}), allowed {allowed}. "
                            "Catch the specific exception instead.")
    return problems


def lowered(current: dict[str, dict[str, int]], baseline: dict[str, dict[str, int]]) -> dict[str, dict[str, int]]:
    """Baseline entries lowered to the current value; entries no longer needed are dropped, none are added."""
    out = {}
    for key, entries in baseline.items():
        out[key] = {p: min(n, current[key][p]) for p, n in sorted(entries.items()) if p in current[key]}
    return out


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--update", action="store_true", help="lower baseline entries to the current values")
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    args = parser.parse_args(argv)

    root = args.root
    baseline_path = root / BASELINE_NAME
    current = {"lines": oversized_files(root), "broad_excepts": broad_excepts(root)}

    if args.update:
        if baseline_path.exists():
            new = lowered(current, json.loads(baseline_path.read_text(encoding="utf-8")))
        else:
            new = current
        baseline_path.write_text(json.dumps(new, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        print(f"wrote {BASELINE_NAME}")
        return 0

    baseline = json.loads(baseline_path.read_text(encoding="utf-8"))
    problems = find_problems(current, baseline)
    for p in problems:
        print(p)
    if problems:
        print(f"\n{len(problems)} guardrail violation(s). Existing ones are listed in {BASELINE_NAME}; "
              "they may only shrink (python scripts/check_baseline.py --update after a file shrank).")
        return 1
    print("guardrail baseline: OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
