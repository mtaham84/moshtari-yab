"""Tests for scripts/check_baseline.py against real temporary git repositories."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts import check_baseline

pytestmark = pytest.mark.usefixtures("ruff")


@pytest.fixture
def write(git):
    """write(root, rel, text): create the file and stage it (check_baseline only sees tracked files)."""
    def _write(root: Path, rel: str, text: str) -> None:
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        git(root, "add", rel)
    return _write


def lines(n: int) -> str:
    return "".join(f"x{i} = {i}\n" for i in range(n))


BROAD = "try:\n    pass\nexcept Exception:\n    print(1)\n"


def baseline_of(root: Path) -> dict:
    return json.loads((root / check_baseline.BASELINE_NAME).read_text(encoding="utf-8"))


class TestCheckBaseline:
    def test_existing_violations_pass_and_growth_fails(self, repo, write, capsys):
        write(repo, "big.py", lines(900))
        write(repo, "app.py", BROAD)
        assert check_baseline.main(["--root", str(repo), "--update"]) == 0
        assert baseline_of(repo) == {"lines": {"big.py": 900}, "broad_excepts": {"app.py": 1}}
        assert check_baseline.main(["--root", str(repo)]) == 0

        write(repo, "big.py", lines(901))
        assert check_baseline.main(["--root", str(repo)]) == 1
        assert "big.py: 901 lines, allowed 900" in capsys.readouterr().out

    def test_new_big_file_and_new_broad_except_fail(self, repo, write, capsys):
        write(repo, "small.py", lines(10))
        check_baseline.main(["--root", str(repo), "--update"])
        write(repo, "new_big.html", "<p></p>\n" * 801)
        write(repo, "small.py", lines(10) + BROAD)
        assert check_baseline.main(["--root", str(repo)]) == 1
        out = capsys.readouterr().out
        assert "new_big.html: 801 lines, allowed 800" in out
        assert "small.py: 1 broad excepts" in out

    def test_migrations_and_untracked_files_are_ignored(self, repo, write):
        check_baseline.main(["--root", str(repo), "--update"])
        write(repo, "app/migrations/0001_initial.py", lines(2000))
        (repo / "untracked.css").write_text("a{}\n" * 900, encoding="utf-8")
        assert check_baseline.main(["--root", str(repo)]) == 0

    def test_update_only_lowers_and_drops_entries(self, repo, write):
        write(repo, "big.py", lines(900))
        write(repo, "fixed.py", lines(900))
        write(repo, "app.py", BROAD + BROAD)
        check_baseline.main(["--root", str(repo), "--update"])

        write(repo, "big.py", lines(850))     # shrank
        write(repo, "fixed.py", lines(100))   # no longer over the limit
        write(repo, "app.py", BROAD)          # one except fixed
        write(repo, "other.py", BROAD)        # new violation: must not be added by --update
        check_baseline.main(["--root", str(repo), "--update"])
        assert baseline_of(repo) == {"lines": {"big.py": 850}, "broad_excepts": {"app.py": 1}}
        assert check_baseline.main(["--root", str(repo)]) == 1
