"""List project modules that no production entry point can reach (dead code candidates).

    python scripts/find_unreachable.py            # summary per package
    python scripts/find_unreachable.py --modules  # one unreachable module per line

Roots (where production code starts):
- every management command and ``python -m`` module named in docker-compose.yml and docker/entrypoint.sh
- what Django and Celery load by themselves: settings, urls, wsgi/asgi, celery, and per app apps/models/admin/urls/
  signals/tasks/context_processors/middleware/templatetags
- MANUAL_ROOTS below: tools started outside docker-compose (systemd, by hand)

From the roots, imports are followed recursively: ``import``/``from`` statements (also inside functions), dotted
strings naming a project module (settings, ``include("...")``, Celery task names) and ``"path/to/file.py:NAME"``
strings (apps/core/prompts.py imports those at runtime). Tests and migrations are ignored. A module listed here may
still be used dynamically or by hand: follow the removing-dead-code skill before deleting anything.
"""
from __future__ import annotations

import argparse
import ast
import re
import sys
from pathlib import Path

PACKAGES = ("apps", "config", "need_engine", "telegram_crawler", "workers", "x_ingest", "instagram_engine",
            "instagram_discovery", "xray", "shared", "scripts")
ENTRY_FILES = ("docker-compose.yml", "docker/entrypoint.sh")
DJANGO_AUTOLOADED = {"apps", "models", "admin", "urls", "signals", "tasks", "context_processors", "middleware"}
FRAMEWORK_ROOTS = {"config.settings", "config.urls", "config.wsgi", "config.asgi", "config.celery"}

# Started outside docker-compose. Keep this list short and explain every entry.
MANUAL_ROOTS = {
    "workers.telegram_config_bot": "ops Telegram bot, run by systemd on the server",
    "workers.channel_config_collector": "Telegram channel config daemon, run by systemd on the server",
    "apps.billing.management.commands.charge_usage": "manual billing run (README)",
    "apps.billing.management.commands.rotate_provider_keys": "manual key rotation (README)",
    "apps.discovery.management.commands.seed_global_sources": "manual seeding (.env.example)",
    "scripts.check_proxy_leaks": "manual check (docs/SECURITY_CLEANUP.md)",
    "scripts.check_baseline": "CI and pre-commit",
    "scripts.check_commits": "CI",
    "scripts.find_unreachable": "this tool",
    "scripts.import_graph": "developer tool",
    "scripts.config_inventory": "developer tool",
}

_MANAGE_RE = re.compile(r"manage\.py[\"',\s]+([a-z_]+)")
_MODULE_RE = re.compile(r"-m[\"',\s]+([a-z_][a-z0-9_.]*)")
_TOKEN_RE = re.compile(r"[\"']([a-z_]+)[\"']")
_DOTTED_RE = re.compile(r"[a-z_][a-z0-9_]*(\.[A-Za-z0-9_]+)+")
_FILE_REF_RE = re.compile(r"([a-z_][a-z0-9_/]*)\.py:[A-Za-z_][A-Za-z0-9_]*")


def _is_test_or_migration(rel: str) -> bool:
    parts = rel.split("/")
    name = parts[-1]
    return ("tests" in parts or "migrations" in parts or name.startswith(("test_", "tests_"))
            or name in ("tests.py", "conftest.py"))


def project_modules(root: Path) -> dict[str, Path]:
    modules = {}
    for package in PACKAGES:
        for path in (root / package).rglob("*.py"):
            rel = path.relative_to(root).as_posix()
            if _is_test_or_migration(rel):
                continue
            name = rel[:-3].replace("/", ".")
            modules[name.removesuffix(".__init__")] = path
    return modules


def _resolve(name: str, modules: dict[str, Path]) -> str | None:
    """The deepest project module that `name` (a module or module.attribute) refers to."""
    while name:
        if name in modules:
            return name
        name = name.rpartition(".")[0]
    return None


def imports_of(name: str, modules: dict[str, Path]) -> set[str]:
    path = modules[name]
    package = name if path.name == "__init__.py" else name.rpartition(".")[0]
    referenced = set()
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"), filename=str(path))):
        if isinstance(node, ast.Import):
            referenced.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                parts = package.split(".")
                base = ".".join(parts[: len(parts) - node.level + 1] + ([node.module] if node.module else []))
            else:
                base = node.module or ""
            referenced.add(base)
            referenced.update(f"{base}.{alias.name}" for alias in node.names)
        elif isinstance(node, ast.Constant) and isinstance(node.value, str):
            if _DOTTED_RE.fullmatch(node.value) and node.value.split(".")[0] in PACKAGES:
                referenced.add(node.value)
            file_ref = _FILE_REF_RE.fullmatch(node.value)
            if file_ref and file_ref.group(1).split("/")[0] in PACKAGES:
                referenced.add(file_ref.group(1).replace("/", "."))
    parts = name.split(".")
    referenced.update(".".join(parts[:i]) for i in range(1, len(parts)))   # importing a.b.c imports a and a.b
    return {r for r in (_resolve(ref, modules) for ref in referenced) if r and r != name}


def entry_roots(root: Path, modules: dict[str, Path]) -> set[str]:
    text = "\n".join((root / f).read_text(encoding="utf-8") for f in ENTRY_FILES if (root / f).exists())
    commands = {m.rsplit(".", 1)[-1]: m for m in modules if ".management.commands." in m}
    roots = set()
    for command in set(_MANAGE_RE.findall(text)) | set(_TOKEN_RE.findall(text)):   # `entrypoint: manage.py` + token
        if command in commands:
            roots.add(commands[command])
    for module in _MODULE_RE.findall(text):
        for candidate in (f"{module}.__main__", module):
            if candidate in modules:
                roots.add(candidate)
    return roots


def framework_roots(modules: dict[str, Path]) -> set[str]:
    roots = {m for m in FRAMEWORK_ROOTS if m in modules}
    for m in modules:
        if m.startswith("apps.") and (m.rsplit(".", 1)[-1] in DJANGO_AUTOLOADED or ".templatetags." in m
                                      or ".models." in m):
            roots.add(m)
    return roots


def unreachable(root: Path, manual_roots: dict[str, str] | None = None) -> list[str]:
    modules = project_modules(root)
    manual = MANUAL_ROOTS if manual_roots is None else manual_roots
    stack = list(entry_roots(root, modules) | framework_roots(modules) | {m for m in manual if m in modules})
    seen: set[str] = set()
    while stack:
        name = stack.pop()
        if name not in seen:
            seen.add(name)
            stack.extend(imports_of(name, modules) - seen)
    return sorted(set(modules) - seen)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--modules", action="store_true", help="one module per line")
    args = parser.parse_args(argv)
    modules = project_modules(args.root)
    dead = unreachable(args.root)
    if args.modules:
        print("\n".join(dead))
        return 0
    lines = {m: len(modules[m].read_text(encoding="utf-8").splitlines()) for m in dead}
    groups: dict[str, list[str]] = {}
    for m in dead:
        groups.setdefault(".".join(m.split(".")[:2]), []).append(m)
    print(f"{len(modules)} modules, {len(modules) - len(dead)} reachable, {len(dead)} unreachable "
          f"({sum(lines.values())} lines)")
    for group, names in sorted(groups.items(), key=lambda kv: -sum(lines[m] for m in kv[1])):
        rest = ", ".join(m[len(group) + 1:] or "(module)" for m in names)
        print(f"{sum(lines[m] for m in names):6d}  {group}: {rest}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
