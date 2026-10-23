from __future__ import annotations

import ast
import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ENV_CALLS = {"os.getenv", "os.environ.get", "os.environ.setdefault", "os.environ.pop"}
CONFIG_HELPERS = {"_f", "_int", "_float", "_bool"}
PROCESS_PREFIXES = (
    ("apps/", "Django panel and management processes"),
    ("config/", "Django panel and management processes"),
    ("need_engine/", "need_engine"),
    ("telegram_crawler/", "Telegram crawler"),
    ("workers/x_collector/", "X collector"),
    ("workers/ig_collector/", "Instagram collector"),
    ("x_ingest/", "X ingest"),
    ("instagram_discovery/", "Instagram discovery CLI"),
    ("shared/", "collectors using shared utilities"),
    ("scripts/", "maintenance scripts"),
)
SHELL_READS = {
    "PIP_INDEX_URL": ("https://mirror-pypi.runflare.com/simple", "Docker image build", "Dockerfile:14"),
    "PIP_TRUSTED_HOST": ("mirror-pypi.runflare.com", "Docker image build", "Dockerfile:15"),
    "DJANGO_SEED_DEMO": ("false", "web entrypoint", "docker/entrypoint.sh:27"),
    "GUNICORN_WORKERS": ("3", "web entrypoint", "docker/entrypoint.sh:32"),
    "GUNICORN_TIMEOUT": ("60", "web entrypoint", "docker/entrypoint.sh:33"),
    "X_STATUS_SYNC_INTERVAL": ("30", "sync entrypoint", "docker/entrypoint.sh:38"),
    "SYNC_INTERVAL": ("10", "sync entrypoint", "docker/entrypoint.sh:39"),
    "TG_GROUPS_FILE": ("data/groups.txt", "crawler entrypoint", "docker/entrypoint.sh:50-51"),
    "WEB_PORT": ("8000", "Compose web port", "docker-compose.yml:54"),
    "XRAY_IMAGE": ("teddysun/xray", "Compose proxy service", "docker-compose.yml:121"),
    "POSTGRES_DB": ("customer_yab", "Compose and database processes", "docker-compose.yml:31"),
    "POSTGRES_USER": ("postgres", "Compose and database processes", "docker-compose.yml:32"),
    "POSTGRES_PASSWORD": ("required", "Compose and database processes", "docker-compose.yml:33"),
    "POSTGRES_HOST": ("db in Compose; 127.0.0.1 locally", "database processes", "docker-compose.yml:18"),
    "POSTGRES_PORT": ("5432", "database processes", "docker-compose.yml:19"),
    "RETENTION_REPORT_INTERVAL_HOURS": ("168", "retention report", "docker-compose.yml:114"),
}
COMPOSE_READS = {
    "POSTGRES_DB": ("customer_yab", "PostgreSQL and application services", "docker-compose.yml:31"),
    "POSTGRES_USER": ("postgres", "PostgreSQL and application services", "docker-compose.yml:32"),
    "POSTGRES_PASSWORD": ("required", "PostgreSQL and application services", "docker-compose.yml:33"),
    "POSTGRES_HOST": ("db", "application services", "docker-compose.yml:18"),
    "POSTGRES_PORT": ("5432", "application services", "docker-compose.yml:19"),
    "WEB_PORT": ("8000", "web service", "docker-compose.yml:54"),
    "RETENTION_REPORT_INTERVAL_HOURS": ("168", "retention report service", "docker-compose.yml:114"),
    "XRAY_IMAGE": ("teddysun/xray", "Xray service", "docker-compose.yml:121"),
}


def _call_name(node: ast.Call) -> str:
    parts = []
    value = node.func
    while isinstance(value, ast.Attribute):
        parts.append(value.attr)
        value = value.value
    if isinstance(value, ast.Name):
        parts.append(value.id)
    return ".".join(reversed(parts))


def _process(path: str) -> str:
    if path == "conftest.py" or "/tests/" in path or path.startswith("tests/"):
        return "pytest"
    for prefix, label in PROCESS_PREFIXES:
        if path.startswith(prefix):
            return label
    if path == "workers/state_migrate.py":
        return "collector state migration CLI"
    return "Django or CLI startup"


def collect_reads() -> tuple[dict[str, list[tuple[str, int, str]]], set[str]]:
    files = set(subprocess.run(["git", "ls-files", "*.py"], cwd=ROOT, capture_output=True,
                               text=True, check=True).stdout.splitlines())
    skipped = {".git", ".worktrees", "__pycache__", "tests", "migrations"}
    for folder in ("apps", "config", "need_engine", "telegram_crawler", "workers", "x_ingest",
                   "instagram_discovery", "shared", "xray", "scripts"):
        for path in (ROOT / folder).rglob("*.py"):
            if not any(part in skipped for part in path.parts):
                files.add(path.relative_to(ROOT).as_posix())

    reads: dict[str, list[tuple[str, int, str]]] = {}
    config_keys = set()
    for name in sorted(files):
        path = ROOT / name
        if not path.is_file():
            continue
        try:
            tree = ast.parse(path.read_text(encoding="utf-8-sig"), filename=name)
        except (SyntaxError, UnicodeError):
            continue
        dynamic_helpers = {}
        for definition in tree.body:
            if not isinstance(definition, (ast.FunctionDef, ast.AsyncFunctionDef)) or not definition.args.args:
                continue
            first_argument = definition.args.args[0].arg
            reads_argument = any(
                isinstance(call, ast.Call) and _call_name(call) in {"os.getenv", "os.environ.get"}
                and call.args and isinstance(call.args[0], ast.Name) and call.args[0].id == first_argument
                for call in ast.walk(definition)
            )
            if reads_argument:
                dynamic_helpers[definition.name] = definition
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            call_name = _call_name(node)
            key = default = None
            if call_name in ENV_CALLS and node.args and isinstance(node.args[0], ast.Constant) and isinstance(node.args[0].value, str):
                key = node.args[0].value
                default = ast.unparse(node.args[1]) if len(node.args) > 1 else "required"
            elif isinstance(node.func, ast.Name) and node.func.id in CONFIG_HELPERS:
                if node.args and isinstance(node.args[0], ast.Constant) and isinstance(node.args[0].value, str):
                    key = node.args[0].value
                    default = ast.unparse(node.args[1]) if len(node.args) > 1 else "0" if node.func.id == "_int" else "required"
            if key:
                reads.setdefault(key, []).append((name, node.lineno, default or "dynamic"))
            if isinstance(node.func, ast.Name) and node.func.id in dynamic_helpers and node.args:
                helper = dynamic_helpers[node.func.id]
                if isinstance(node.args[0], ast.Constant) and isinstance(node.args[0].value, str):
                    helper_default = ast.unparse(node.args[1]) if len(node.args) > 1 else "helper default"
                    if helper.args.defaults:
                        missing_defaults = len(helper.args.args) - len(helper.args.defaults)
                        if len(node.args) < 2 and missing_defaults + 1 < len(helper.args.args):
                            helper_default = ast.unparse(helper.args.defaults[-1])
                    reads.setdefault(node.args[0].value, []).append((name, node.lineno, helper_default))
            if (call_name == "os.getenv" and node.args and isinstance(node.args[0], ast.Name)):
                helper_key = node.args[0].id
                for candidate in ast.walk(tree):
                    if (isinstance(candidate, ast.Call) and isinstance(candidate.func, ast.Name)
                            and candidate.func.id in CONFIG_HELPERS and candidate.args
                            and isinstance(candidate.args[0], ast.Constant)
                            and candidate.args[0].value == helper_key):
                        value = ast.unparse(candidate.args[1]) if len(candidate.args) > 1 else "helper default"
                        reads.setdefault(helper_key, []).append((name, node.lineno, value))
            if (isinstance(node, ast.Subscript) and isinstance(node.value, ast.Attribute)
                    and isinstance(node.value.value, ast.Name) and node.value.value.id == "os"
                    and node.value.attr == "environ" and isinstance(node.slice, ast.Constant)
                    and isinstance(node.slice.value, str)):
                reads.setdefault(node.slice.value, []).append((name, node.lineno, "required"))
    config_path = ROOT / "need_engine" / "config.py"
    config_tree = ast.parse(config_path.read_text(encoding="utf-8"), filename=str(config_path))
    for node in ast.walk(config_tree):
        if (isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "_f"
                and node.args and isinstance(node.args[0], ast.Constant) and isinstance(node.args[0].value, str)):
            config_keys.add(node.args[0].value)
    return reads, config_keys


def main() -> None:
    reads, config_keys = collect_reads()
    documented = set()
    for line in (ROOT / ".env.example").read_text(encoding="utf-8-sig").splitlines():
        documented.update(re.findall(r"\b([A-Z][A-Z0-9_]*)\s*=", line))

    output = [
        "# Environment variables", "",
        "Generated by python scripts/config_inventory.py. Python readers are found with AST; Docker Compose and the entrypoint are listed explicitly. Status checks variable names present in .env.example, including commented examples. A documented-but-unused variable has no detected Python, Compose, or shell reader; an external dependency may still consume it.", "",
        "| Variable | Default | Process | Status | Evidence |", "|---|---|---|---|---|",
    ]
    for key in sorted(set(reads) | documented | set(SHELL_READS) | set(COMPOSE_READS) | config_keys):
        entries = reads.get(key, [])
        if key in SHELL_READS and key in COMPOSE_READS:
            shell_default, shell_process, shell_proof = SHELL_READS[key]
            compose_default, compose_process, compose_proof = COMPOSE_READS[key]
            defaults = {shell_default, compose_default}
            processes = {shell_process, compose_process}
            proofs = {shell_proof, compose_proof}
        elif key in SHELL_READS:
            default, process, proof = SHELL_READS[key]
            defaults, processes, proofs = {default}, {process}, {proof}
        elif key in COMPOSE_READS:
            default, process, proof = COMPOSE_READS[key]
            defaults, processes, proofs = {default}, {process}, {proof}
        else:
            defaults = {entry[2] for entry in entries}
            processes = {_process(entry[0]) for entry in entries}
            proofs = {f"{entry[0]}:{entry[1]}" for entry in entries}
        if key in config_keys:
            processes.add("need_engine")
        status = ("documented" if key in documented else "used-but-undocumented") if entries or key in SHELL_READS or key in COMPOSE_READS or key in config_keys else "documented-but-unused"
        output.append(f"| {key} | {', '.join(sorted(defaults)) or '—'} | {', '.join(sorted(processes)) or 'not read'} | {status} | {', '.join(sorted(proofs)) or '—'} |")
    output.extend(["", "Some values are chosen by helper functions, runtime settings, or configuration files rather than direct environment reads. Evidence points to the helper or call site.", ""])
    target = ROOT / "docs/architecture/CONFIG.md"
    target.write_text("\n".join(output), encoding="utf-8")


if __name__ == "__main__":
    main()
