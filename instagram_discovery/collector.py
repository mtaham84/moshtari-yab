"""Read Instagram discovery results from the Agent Reach/OpenCLI CLI."""
from __future__ import annotations

import json
import os
import shutil
import subprocess
from dataclasses import dataclass
from typing import Any
import re


class CollectorError(RuntimeError):
    pass


@dataclass(frozen=True)
class Source:
    kind: str
    value: str


class OpenCLICollector:
    """Thin, read-only adapter around OpenCLI's Instagram commands.

    Agent Reach routes Instagram to OpenCLI. This adapter intentionally invokes
    upstream read commands without implementing its own Instagram scraper.
    """

    def __init__(self, executable: str | None = None, timeout: int = 120):
        self.executable = executable or os.environ.get("OPENCLI_BIN", "opencli")
        self.timeout = max(5, int(timeout))

    def check_available(self) -> None:
        if not shutil.which(self.executable):
            raise CollectorError(
                f"OpenCLI not found ({self.executable}). Install Agent Reach/OpenCLI and set OPENCLI_BIN if needed."
            )

    def fetch(self, source: Source, limit: int) -> list[dict[str, Any]]:
        self.check_available()
        limit = max(1, min(int(limit), 100))
        if source.kind == "user":
            if not re.fullmatch(r"[A-Za-z0-9._]{1,30}", source.value):
                raise CollectorError("Instagram usernames may contain only letters, numbers, dots, and underscores (up to 30 characters).")
            args = ["instagram", "user", source.value, "--limit", str(limit)]
        elif source.kind == "explore":
            args = ["instagram", "explore", "--limit", str(limit)]
        else:
            raise CollectorError(f"Unsupported Instagram source kind: {source.kind}")

        try:
            result = subprocess.run(
                [self.executable, *args, "-f", "json"],
                capture_output=True, text=True, encoding="utf-8", errors="replace",
                timeout=self.timeout, check=False,
            )
        except subprocess.TimeoutExpired as exc:
            raise CollectorError(f"OpenCLI timed out while reading Instagram {source.kind} results.") from exc
        except OSError as exc:
            raise CollectorError(f"Could not start OpenCLI: {exc}") from exc

        if result.returncode != 0:
            detail = (result.stderr or result.stdout or "OpenCLI returned no details.").strip()
            raise CollectorError(f"OpenCLI failed ({result.returncode}): {detail[:1200]}")
        try:
            payload = json.loads(result.stdout)
        except json.JSONDecodeError as exc:
            raise CollectorError("OpenCLI output was not valid JSON. Run the same command with -f json to inspect it.") from exc

        for _ in range(3):
            if isinstance(payload, dict):
                nested = next(
                    (payload[key] for key in ("items", "posts", "results", "data")
                     if isinstance(payload.get(key), (list, dict))),
                    None,
                )
                if nested is not None:
                    payload = nested
                    continue
            break
        if not isinstance(payload, list):
            raise CollectorError("OpenCLI JSON should be a list of Instagram posts.")
        return [row for row in payload if isinstance(row, dict)][:limit]
