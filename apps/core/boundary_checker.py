"""
Architecture boundary checker and AST import analyzer (P08.03 / R39, R40, R42).

Enforces package direction rules, frozen legacy boundaries, and relative import resolution.
"""

from __future__ import annotations

import ast
import os
from pathlib import Path
from typing import Dict, List, NamedTuple, Set, Tuple


class ImportViolation(NamedTuple):
    source_file: str
    line_number: int
    imported_module: str
    rule_broken: str


# Explicit Baseline Exceptions (Frozen legacy and known temporary cross-package edges)
# These are strictly frozen and may only be removed in subsequent tasks (P08.04-P08.20), NEVER added to.
BASELINE_EXCEPTIONS: Set[Tuple[str, str]] = {
    # File path relative to project root -> Forbidden package root imported
    ("apps/discovery/management/commands/run_instagram_scraper.py", "instagram_engine"),
    ("apps/core/llm.py", "instagram_engine"),
    ("need_engine/eval/golden.py", "apps"),
    ("need_engine/retention.py", "telegram_crawler"),
    ("need_engine/registry.py", "apps"),
    ("need_engine/gateway_adapter.py", "apps"),
    ("shared/x_filters.py", "apps"),
    ("xray/multi_proxy_manager.py", "apps"),
}

# Forbidden targets for specific packages
FORBIDDEN_PACKAGE_TARGETS: Dict[str, Set[str]] = {
    # Package root -> Set of forbidden package roots
    "xray": {"apps", "need_engine", "workers", "shared", "instagram_engine"},
    "shared": {"apps", "need_engine", "workers", "xray", "instagram_engine"},
    "need_engine": {"apps", "instagram_engine"},
    "workers": {"instagram_engine"},
    "apps": {"instagram_engine"},
}


class ImportVisitor(ast.NodeVisitor):
    def __init__(self, file_path: str, module_package: str):
        self.file_path = file_path
        self.module_package = module_package
        self.imports: List[Tuple[int, str]] = []

    def visit_Import(self, node: ast.Import) -> None:
        for alias in node.names:
            self.imports.append((node.lineno, alias.name))
        self.generic_visit(node)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        level = node.level
        module = node.module or ""
        if level > 0:
            # Resolve relative import
            pkg_parts = self.module_package.split(".") if self.module_package else []
            if level <= len(pkg_parts):
                base = ".".join(pkg_parts[: len(pkg_parts) - level + 1])
                full_module = f"{base}.{module}" if module else base
            else:
                full_module = module
        else:
            full_module = module

        self.imports.append((node.lineno, full_module))
        self.generic_visit(node)


def is_excluded_path(rel_path: str) -> bool:
    """
    Excludes tests, migrations, virtual environments, build artifacts,
    and the frozen legacy directory from application boundary scanning.
    """
    parts = Path(rel_path).parts
    if not parts:
        return True

    # Ignored directories
    ignored_dirs = {
        "tests",
        "migrations",
        ".git",
        ".venv",
        "venv",
        "env",
        "__pycache__",
        "instagram_engine",  # Frozen legacy code itself is excluded from active linting
        "build",
        "dist",
        "docs",
        "scripts",
    }
    if any(p in ignored_dirs for p in parts):
        return True

    # Test file patterns
    filename = parts[-1]
    if (
        filename.startswith("test_")
        or filename.endswith("_test.py")
        or filename == "tests.py"
        or filename == "tests_agent.py"
    ):
        return True

    return False


def check_source_file_imports(
    file_path: str,
    project_root: str,
    custom_content: str | None = None,
) -> List[ImportViolation]:
    """
    Parses a single python file and checks imports against boundary rules.
    """
    rel_path = os.path.relpath(file_path, project_root).replace("\\", "/")
    if is_excluded_path(rel_path):
        return []

    # Determine source package root
    parts = rel_path.split("/")
    source_pkg = parts[0]
    if source_pkg not in FORBIDDEN_PACKAGE_TARGETS:
        return []

    forbidden_targets = FORBIDDEN_PACKAGE_TARGETS[source_pkg]

    if custom_content is not None:
        content = custom_content
    else:
        try:
            with open(file_path, "r", encoding="utf-8") as f:
                content = f.read()
        except Exception:
            return []

    try:
        tree = ast.parse(content, filename=file_path)
    except SyntaxError:
        return []

    # Module package for relative resolution
    mod_package = ".".join(parts[:-1])
    visitor = ImportVisitor(rel_path, mod_package)
    visitor.visit(tree)

    violations = []
    for line_no, imported_mod in visitor.imports:
        if not imported_mod:
            continue
        imp_parts = imported_mod.split(".")
        imp_root = imp_parts[0]

        if imp_root in forbidden_targets:
            # Check baseline exception
            if (rel_path, imp_root) in BASELINE_EXCEPTIONS:
                continue

            rule = f"Package '{source_pkg}' is forbidden from importing '{imp_root}'"
            violations.append(
                ImportViolation(
                    source_file=rel_path,
                    line_number=line_no,
                    imported_module=imported_mod,
                    rule_broken=rule,
                )
            )

    return violations


def scan_project_boundaries(project_root: str) -> List[ImportViolation]:
    """
    Scans entire project directory for import boundary violations.
    """
    violations = []
    for root, _, files in os.walk(project_root):
        for f in files:
            if f.endswith(".py"):
                full_path = os.path.join(root, f)
                violations.extend(check_source_file_imports(full_path, project_root))
    return violations
