"""Tests for scripts/find_unreachable.py against a small fake project tree."""
from __future__ import annotations

from pathlib import Path

from scripts.find_unreachable import unreachable


def make(root: Path, files: dict[str, str]) -> Path:
    for rel, text in files.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    return root


BASE = {
    "config/__init__.py": "",
    "config/settings.py": 'ROOT_URLCONF = "config.urls"\nTEST = "apps.shop.helpers"\n',
    "config/urls.py": "",
    "apps/__init__.py": "",
    "apps/shop/__init__.py": "",
    "apps/shop/views.py": "",
    "apps/shop/helpers.py": "",
    "apps/shop/management/__init__.py": "",
    "apps/shop/management/commands/__init__.py": "",
    "apps/shop/management/commands/run_shop.py": "from apps.shop import services\n",
    "apps/shop/management/commands/old_backfill.py": "",
    "apps/shop/services.py": "def go():\n    from apps.shop.lazy import x\n",
    "apps/shop/lazy.py": "x = 1\n",
    "apps/shop/orphan.py": "",
    "apps/shop/tests.py": "from apps.shop import orphan\n",
    "worker/__init__.py": "",
    "docker-compose.yml": 'services:\n  shop:\n    command: ["python", "manage.py", "run_shop"]\n',
    "docker/entrypoint.sh": "",
}


def test_entry_commands_imports_and_dotted_strings_are_followed(tmp_path):
    dead = unreachable(make(tmp_path, BASE), manual_roots={})
    assert "apps.shop.services" not in dead   # imported by a command named in docker-compose
    assert "apps.shop.lazy" not in dead       # import inside a function
    assert "apps.shop.helpers" not in dead    # dotted string in settings
    assert "apps.shop.management.commands.old_backfill" in dead   # command nothing starts
    assert "apps.shop.orphan" in dead         # only a test imports it
    assert "apps.shop.views" in dead


def test_prompt_file_references_and_python_m_entry_points(tmp_path):
    files = dict(BASE)
    files["apps/shop/models.py"] = 'PROMPTS = {"k": {"module_path": "apps/shop/views.py:SYSTEM"}}\n'
    files["need_engine/__init__.py"] = ""
    files["need_engine/__main__.py"] = "from need_engine import engine\n"
    files["need_engine/engine.py"] = ""
    files["docker/entrypoint.sh"] = "exec python -m need_engine run\n"
    dead = unreachable(make(tmp_path, files), manual_roots={})
    assert "apps.shop.views" not in dead      # models.py is auto-loaded and names views.py:SYSTEM
    assert "need_engine.engine" not in dead


def test_manual_roots_keep_tools_run_outside_compose(tmp_path):
    files = dict(BASE)
    files["workers/bot.py"] = "from apps.shop import orphan\n"
    root = make(tmp_path, files)
    assert "workers.bot" in unreachable(root, manual_roots={})
    dead = unreachable(root, manual_roots={"workers.bot": "systemd"})
    assert "workers.bot" not in dead and "apps.shop.orphan" not in dead
