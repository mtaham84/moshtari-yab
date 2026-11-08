"""Fixtures for tests that need a real, throw-away git repository."""
from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

_GIT_ENV = {**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.com",
            "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@example.com"}


def _git(root: Path, *args: str) -> str:
    return subprocess.run(["git", *args], cwd=root, env=_GIT_ENV, check=True, capture_output=True, text=True).stdout


def require_tool(name: str) -> None:
    """Skip locally when a command-line tool is missing (e.g. no git in the app image); fail in CI."""
    if shutil.which(name) is None:
        if os.environ.get("CI"):
            pytest.fail(f"{name} is required in CI but not installed")
        pytest.skip(f"{name} is not installed")


@pytest.fixture
def git():
    """git(root, *args) -> stdout, with a fixed author identity."""
    require_tool("git")
    return _git


@pytest.fixture
def ruff():
    """Skip (fail in CI) when ruff is not installed."""
    require_tool("ruff")


@pytest.fixture
def repo(tmp_path: Path, git) -> Path:
    """An empty git repository on branch main, with a scripts/ folder."""
    git(tmp_path, "init", "-q", "-b", "main")
    (tmp_path / "scripts").mkdir()
    return tmp_path
