from __future__ import annotations

import argparse
import os
from pathlib import Path

from shared.collector_kit.seen import SeenStore
from shared.collector_kit.state_store import PostgresStateStore


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Copy collector state between SQLite and PostgreSQL.")
    parser.add_argument("--from", dest="source", choices=("sqlite",), required=True)
    parser.add_argument("--to", dest="target", choices=("postgres",), required=True)
    parser.add_argument("--sqlite-path", type=Path, help="Override the platform-specific SQLite path.")
    parser.add_argument("--postgres-schema", default=os.getenv("TG_DB_SCHEMA", "crawler"))
    parser.add_argument("--platform", choices=("x", "instagram"), action="append")
    args = parser.parse_args(argv)
    platforms = args.platform or ["x", "instagram"]
    for platform in platforms:
        default_path = os.getenv(
            "X_COLLECT_STATE_FILE" if platform == "x" else "IG_COLLECT_STATE_FILE",
            "output/x_collector_state.sqlite3" if platform == "x" else "output/ig_collector_state.sqlite3",
        )
        source = SeenStore(args.sqlite_path or Path(default_path), platform=platform)
        target = PostgresStateStore(platform=platform, schema=args.postgres_schema)
        counts = target.import_missing_state(source.export_state())
        print(f"{platform}: " + ", ".join(f"{name}={count}" for name, count in sorted(counts.items())))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
