"""Import boundaries (AGENTS.md rule 7): shared code stays independent, workers don't import the Django panel.

Existing violations are listed in ALLOWED. The list may only shrink: a new violation fails, and so does an entry that
no longer exists (remove it from ALLOWED in the same change that removed the import).
"""
from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PROJECT_PACKAGES = {"apps", "config", "need_engine", "telegram_crawler", "workers", "x_ingest", "instagram_engine",
                    "xray", "shared"}

# package -> top-level packages its production code must not import
FORBIDDEN = {
    "shared": PROJECT_PACKAGES - {"shared"},
    "need_engine": {"apps"},
    "telegram_crawler": {"apps"},
    "workers": {"apps"},
    "x_ingest": {"apps"},
}

# (file, imported top-level package) that exist today. Only remove entries, never add.
ALLOWED = {
    ("need_engine/catalog.py", "apps"),
    ("need_engine/eval/golden.py", "apps"),
    ("need_engine/extract.py", "apps"),
    ("need_engine/post_qualification.py", "apps"),
    ("need_engine/reply.py", "apps"),
    ("need_engine/verify.py", "apps"),
    ("shared/tele_logging/formatter.py", "apps"),
    ("shared/x_filters.py", "apps"),
    ("telegram_crawler/retention.py", "apps"),
    ("workers/channel_config_collector.py", "apps"),
    ("workers/telegram_config_bot.py", "apps"),
    ("workers/x_collector/llm_terms.py", "apps"),
    ("workers/x_collector/proqe.py", "apps"),
}


def _is_test_or_migration(rel: str) -> bool:
    parts = rel.split("/")
    name = parts[-1]
    return ("tests" in parts or "migrations" in parts or name.startswith("test_") or name == "tests.py"
            or name.startswith("tests_") or name == "conftest.py")


def _imported_top_packages(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    found = set()
    for node in ast.walk(tree):   # includes imports inside functions
        if isinstance(node, ast.Import):
            found.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            found.add(node.module.split(".")[0])
    return found


def boundary_violations() -> set[tuple[str, str]]:
    violations = set()
    for package, forbidden in FORBIDDEN.items():
        for path in sorted((ROOT / package).rglob("*.py")):
            rel = path.relative_to(ROOT).as_posix()
            if _is_test_or_migration(rel):
                continue
            for top in _imported_top_packages(path) & forbidden:
                violations.add((rel, top))
    return violations


def test_no_new_import_boundary_violations():
    new = sorted(boundary_violations() - ALLOWED)
    assert not new, ("Forbidden imports (AGENTS.md rule 7). Go through the database or move the code to its owner:\n"
                     + "\n".join(f"  {rel} imports {top}" for rel, top in new))


def test_allow_list_only_contains_existing_violations():
    stale = sorted(ALLOWED - boundary_violations())
    assert not stale, ("These imports are gone; remove them from ALLOWED in tests/test_architecture.py:\n"
                       + "\n".join(f"  {rel} -> {top}" for rel, top in stale))
