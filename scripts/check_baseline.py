"""Guardrail: no file may grow past 800 lines, no file may gain broad excepts, and no function may exceed complexity limits.

Existing violations are recorded in scripts/guardrail_baseline.json and may only shrink:

    python scripts/check_baseline.py            # CI / pre-commit: fail on any new or grown violation
    python scripts/check_baseline.py --update   # after a file shrank: lower its entry (never raises one)
    python scripts/check_baseline.py --update --moved-from old.py   # code moved out of old.py into new files

A move carries broad excepts and complexity with the code. --moved-from gives the new files the entries old.py no longer needs,
never more than old.py had, so the total cannot grow.

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
COMPLEXITY_RULES = "C901,PLR0915"  # cyclomatic complexity > 15, statements > 50
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


def function_complexity(root: Path) -> dict[str, int]:
    """Number of C901 (complexity > 15) and PLR0915 (statements > 50) hits per file."""
    res = subprocess.run(
        [
            "ruff", "check", ".",
            "--select", COMPLEXITY_RULES,
            "--config", "lint.mccabe.max-complexity = 15",
            "--output-format", "json",
            "--exit-zero",
            "--no-cache",
        ],
        cwd=root, check=True, capture_output=True, text=True,
    )
    counts: dict[str, int] = {}
    for item in json.loads(res.stdout or "[]"):
        filename = item["filename"]
        if "/migrations/" in filename:
            continue
        rel = Path(filename).resolve().relative_to(root.resolve()).as_posix()
        counts[rel] = counts.get(rel, 0) + 1
    return counts


def find_problems(current: dict[str, dict[str, int]], baseline: dict[str, dict[str, int]]) -> list[str]:
    problems = []
    for path, lines in sorted(current["lines"].items()):
        allowed = baseline.get("lines", {}).get(path, MAX_LINES)
        if lines > allowed:
            problems.append(f"{path}: {lines} lines, allowed {allowed}. Split it or move new code to a new module.")
    for path, count in sorted(current["broad_excepts"].items()):
        allowed = baseline.get("broad_excepts", {}).get(path, 0)
        if count > allowed:
            problems.append(f"{path}: {count} broad excepts ({EXCEPT_RULES}), allowed {allowed}. "
                            "Catch the specific exception instead.")
    for path, count in sorted(current.get("complexity", {}).items()):
        allowed = baseline.get("complexity", {}).get(path, 0)
        if count > allowed:
            problems.append(f"{path}: {count} complex function(s) ({COMPLEXITY_RULES}), allowed {allowed}. "
                            "Simplify the function or split it into smaller helpers.")
    return problems


def lowered(current: dict[str, dict[str, int]], baseline: dict[str, dict[str, int]]) -> dict[str, dict[str, int]]:
    """Baseline entries lowered to the current value; entries no longer needed are dropped, none are added."""
    out = {}
    for key, entries in baseline.items():
        if key in current:
            out[key] = {p: min(n, current[key][p]) for p, n in sorted(entries.items()) if p in current[key]}
        else:
            out[key] = dict(entries)
    return out


def _reallocate_budget(current_section: dict[str, int], baseline_section: dict[str, int],
                       sources: list[str]) -> dict[str, int]:
    section = dict(baseline_section)
    budget = 0
    for src in sources:
        budget += max(0, section.get(src, 0) - current_section.get(src, 0))
    for path, count in sorted(current_section.items()):
        need = count - section.get(path, 0)
        if need > 0 and path not in sources:
            give = min(need, budget)
            if give:
                section[path] = section.get(path, 0) + give
                budget -= give
    return section


def moved(current: dict[str, dict[str, int]], baseline: dict[str, dict[str, int]],
          sources: list[str]) -> dict[str, dict[str, int]]:
    """Baseline where broad excepts and complexity no longer needed by `sources` are handed to the files now over their entry."""
    excepts = _reallocate_budget(current.get("broad_excepts", {}), baseline.get("broad_excepts", {}), sources)
    complexity = _reallocate_budget(current.get("complexity", {}), baseline.get("complexity", {}), sources)
    return {**baseline, "broad_excepts": excepts, "complexity": complexity}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--update", action="store_true", help="lower baseline entries to the current values")
    parser.add_argument("--moved-from", nargs="+", default=[], metavar="PATH",
                        help="with --update: code moved out of PATH; its unused broad-except entries move along")
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    args = parser.parse_args(argv)

    root = args.root
    baseline_path = root / BASELINE_NAME
    current = {
        "lines": oversized_files(root),
        "broad_excepts": broad_excepts(root),
        "complexity": function_complexity(root),
    }

    if args.update:
        if baseline_path.exists():
            old = json.loads(baseline_path.read_text(encoding="utf-8"))
            if "complexity" not in old:
                old["complexity"] = current["complexity"]
            if args.moved_from:
                old = moved(current, old, args.moved_from)
            new = lowered(current, old)
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

