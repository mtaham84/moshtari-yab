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


def complex_func(branches: int = 16) -> str:
    """Generate a function exceeding McCabe complexity threshold (15)."""
    body = "def f(x):\n"
    for i in range(branches):
        body += f"    if x == {i}:\n        return {i}\n"
    body += "    return -1\n"
    return body


def many_statements_func(n: int = 55) -> str:
    """Generate a function exceeding statement limit (50)."""
    body = "def g():\n"
    for i in range(n):
        body += f"    y{i} = {i}\n"
    body += "    return y0\n"
    return body


def baseline_of(root: Path) -> dict:
    return json.loads((root / check_baseline.BASELINE_NAME).read_text(encoding="utf-8"))


class TestCheckBaseline:
    def test_existing_violations_pass_and_growth_fails(self, repo, write, capsys):
        write(repo, "big.py", lines(900))
        write(repo, "app.py", BROAD)
        assert check_baseline.main(["--root", str(repo), "--update"]) == 0
        assert baseline_of(repo) == {"broad_excepts": {"app.py": 1}, "complexity": {}, "lines": {"big.py": 900}}
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
        assert baseline_of(repo) == {"broad_excepts": {"app.py": 1}, "complexity": {}, "lines": {"big.py": 850}}
        assert check_baseline.main(["--root", str(repo)]) == 1

    def test_moved_code_takes_its_entries_but_never_more(self, repo, write):
        write(repo, "old.py", BROAD * 3)
        check_baseline.main(["--root", str(repo), "--update"])

        write(repo, "old.py", lines(1))
        write(repo, "a.py", BROAD * 2)
        write(repo, "b.py", BROAD * 2)        # one more than old.py gave up
        check_baseline.main(["--root", str(repo), "--update", "--moved-from", "old.py"])
        assert baseline_of(repo) == {"broad_excepts": {"a.py": 2, "b.py": 1}, "complexity": {}, "lines": {}}
        assert check_baseline.main(["--root", str(repo)]) == 1

    def test_update_without_moved_from_never_adds_entries(self, repo, write):
        write(repo, "old.py", BROAD)
        check_baseline.main(["--root", str(repo), "--update"])
        write(repo, "old.py", lines(1))
        write(repo, "a.py", BROAD)
        check_baseline.main(["--root", str(repo), "--update"])
        assert baseline_of(repo) == {"broad_excepts": {}, "complexity": {}, "lines": {}}

    def test_complexity_existing_passes_and_growth_fails(self, repo, write, capsys):
        write(repo, "comp.py", complex_func(16))
        assert check_baseline.main(["--root", str(repo), "--update"]) == 0
        assert baseline_of(repo)["complexity"] == {"comp.py": 1}
        assert check_baseline.main(["--root", str(repo)]) == 0

        write(repo, "comp.py", complex_func(16) + "\n" + many_statements_func(55))
        assert check_baseline.main(["--root", str(repo)]) == 1
        out = capsys.readouterr().out
        assert "comp.py: 2 complex function(s)" in out
        assert "allowed 1" in out

    def test_new_complex_function_fails(self, repo, write, capsys):
        write(repo, "clean.py", "def simple():\n    return 1\n")
        check_baseline.main(["--root", str(repo), "--update"])
        write(repo, "comp.py", complex_func(16))
        assert check_baseline.main(["--root", str(repo)]) == 1
        out = capsys.readouterr().out
        assert "comp.py: 1 complex function(s)" in out
        assert "allowed 0" in out

    def test_complexity_update_lowers_and_drops(self, repo, write):
        write(repo, "comp1.py", complex_func(16) + "\n" + many_statements_func(55))
        write(repo, "comp2.py", complex_func(16))
        check_baseline.main(["--root", str(repo), "--update"])
        assert baseline_of(repo)["complexity"] == {"comp1.py": 2, "comp2.py": 1}

        write(repo, "comp1.py", complex_func(16))
        write(repo, "comp2.py", "def simple():\n    return 1\n")
        write(repo, "other.py", complex_func(16))
        check_baseline.main(["--root", str(repo), "--update"])
        assert baseline_of(repo)["complexity"] == {"comp1.py": 1}
        assert check_baseline.main(["--root", str(repo)]) == 1

    def test_complexity_moved_from_transfers_budget(self, repo, write):
        write(repo, "old.py", complex_func(16) + "\n" + many_statements_func(55))
        check_baseline.main(["--root", str(repo), "--update"])
        assert baseline_of(repo)["complexity"] == {"old.py": 2}

        write(repo, "old.py", "def simple():\n    return 1\n")
        write(repo, "part1.py", complex_func(16))
        write(repo, "part2.py", many_statements_func(55))
        check_baseline.main(["--root", str(repo), "--update", "--moved-from", "old.py"])
        assert baseline_of(repo)["complexity"] == {"part1.py": 1, "part2.py": 1}
        assert check_baseline.main(["--root", str(repo)]) == 0

    def test_complexity_ignores_migrations(self, repo, write):
        check_baseline.main(["--root", str(repo), "--update"])
        write(repo, "apps/example/migrations/0001_initial.py", complex_func(20))
        assert check_baseline.main(["--root", str(repo)]) == 0

