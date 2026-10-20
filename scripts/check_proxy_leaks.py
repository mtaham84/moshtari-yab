import argparse
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "xray/new_configs.txt"


def _source_bytes(path: Path | None) -> bytes:
    if path is not None:
        return path.read_bytes()
    if SOURCE.exists():
        return SOURCE.read_bytes()
    added = subprocess.run(
        ["git", "log", "--all", "--diff-filter=A", "--format=%H", "--", "xray/new_configs.txt"],
        cwd=ROOT, capture_output=True, text=True, check=True,
    ).stdout.splitlines()
    if not added:
        raise FileNotFoundError("provide a private backup with --source after the history rewrite")
    return subprocess.run(
        ["git", "show", f"{added[0]}:xray/new_configs.txt"], cwd=ROOT,
        capture_output=True, check=True,
    ).stdout


def main() -> int:
    parser = argparse.ArgumentParser(description="Find copies of proxy config lines without printing them.")
    parser.add_argument("--source", type=Path, help="private backup of the old config after history is rewritten")
    args = parser.parse_args()
    needles = [line.strip() for line in _source_bytes(args.source).splitlines()
               if line.strip() and not line.lstrip().startswith(b"#")]
    matches = []
    for root in (ROOT / ".worktrees", ROOT / "docs"):
        if not root.exists():
            continue
        for path in root.rglob("*"):
            if not path.is_file() or path == SOURCE:
                continue
            try:
                content = path.read_bytes()
            except OSError:
                continue
            if any(needle in content for needle in needles):
                matches.append(path.relative_to(ROOT))
    print(f"matching files: {len(matches)}")
    for path in matches:
        print(path)
    return int(bool(matches))


if __name__ == "__main__":
    raise SystemExit(main())
