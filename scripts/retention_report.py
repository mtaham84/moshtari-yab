from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path


def main() -> int:
    interval = max(1, int(float(os.getenv("RETENTION_REPORT_INTERVAL_HOURS", "168")) * 3600))
    target = Path(os.getenv("RETENTION_REPORT_FILE", "output/retention_report.json"))
    target.parent.mkdir(parents=True, exist_ok=True)
    while True:
        result = subprocess.run(
            [sys.executable, "manage.py", "purge_retained_data"],
            check=False, capture_output=True, text=True,
        )
        counts: dict[str, int] = {}
        for line in result.stdout.splitlines():
            key, separator, value = line.partition(":")
            if separator:
                try:
                    counts[key.strip()] = int(value.strip())
                except ValueError:
                    continue
        payload = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "counts": counts,
            "exit_code": result.returncode,
        }
        temporary = target.with_suffix(target.suffix + ".tmp")
        temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(temporary, target)
        if result.returncode:
            print("Retention report command failed", file=sys.stderr, flush=True)
        time.sleep(interval)


if __name__ == "__main__":
    raise SystemExit(main())
