"""Guardrail: every commit of a PR follows Conventional Commits and carries no AI attribution.

    python scripts/check_commits.py origin/main..HEAD

Merge commits are exempt from the subject format, not from the attribution check.
"""
from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

TYPES = ("feat", "fix", "refactor", "perf", "test", "docs", "build", "ci", "chore")
MAX_SUBJECT = 72
SUBJECT_RE = re.compile(rf"^({'|'.join(TYPES)})(\([a-z0-9._/-]+\))?!?: \S")
_AI = r"(claude|anthropic|antigravity|gemini|copilot|cursor|codex|openai|chatgpt|gpt-?\d|devin|windsurf|aider)"
ATTRIBUTION_RE = re.compile(
    rf"(co-authored-by:[^\n]*{_AI})|(generated (with|by)[^\n]*{_AI})|(\U0001F916)", re.IGNORECASE
)


def commits(rev_range: str, root: Path) -> list[tuple[str, bool, str, str]]:
    """(sha, is_merge, subject, body) for every commit in rev_range."""
    out = subprocess.run(["git", "log", "--format=%H%x00%P%x00%s%x00%b%x1e", rev_range],
                         cwd=root, check=True, capture_output=True, text=True).stdout
    result = []
    for record in out.split("\x1e"):
        record = record.strip("\n")
        if not record:
            continue
        sha, parents, subject, body = record.split("\x00", 3)
        result.append((sha, len(parents.split()) > 1, subject, body))
    return result


def problems_for(sha: str, is_merge: bool, subject: str, body: str) -> list[str]:
    short = sha[:7]
    problems = []
    if not is_merge:
        if not SUBJECT_RE.match(subject):
            problems.append(f"{short} '{subject}': not Conventional Commits (<type>(<scope>): <summary>, "
                            f"type one of {', '.join(TYPES)})")
        if len(subject) > MAX_SUBJECT:
            problems.append(f"{short} '{subject}': subject is {len(subject)} characters, max {MAX_SUBJECT}")
    if ATTRIBUTION_RE.search(f"{subject}\n{body}"):
        problems.append(f"{short} '{subject}': contains AI attribution; commits must carry only the author's identity")
    return problems


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if len(argv) not in (1, 2):
        print(__doc__)
        return 2
    root = Path(argv[1]) if len(argv) == 2 else Path.cwd()
    problems = [p for c in commits(argv[0], root) for p in problems_for(*c)]
    for p in problems:
        print(p)
    if problems:
        print(f"\n{len(problems)} commit message problem(s). Fix with git rebase -i / git commit --amend.")
        return 1
    print("commit messages: OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
