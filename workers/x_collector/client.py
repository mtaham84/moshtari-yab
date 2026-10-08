from __future__ import annotations

import json
import os
import shutil
import subprocess
import re
from typing import Any

from . import cli_mapping as mapping


class XCliError(RuntimeError):
    def __init__(self, message: str, kind: str = "fatal"):
        super().__init__(message)
        self.kind = kind


class XCliClient:
    def __init__(self, command: str | None = None, timeout: int | None = None):
        self.command = command or mapping.CLI_COMMAND
        self.timeout = timeout or int(os.getenv("X_CLI_TIMEOUT", "45"))

    def search(self, query: str, limit: int) -> list[dict[str, Any]]:
        executable = shutil.which(self.command)
        if not executable:
            raise XCliError(f"CLI '{self.command}' was not found. Install/diagnose Agent Reach, then run 'agent-reach doctor'.")
        if not os.getenv("TWITTER_AUTH_TOKEN") or not os.getenv("TWITTER_CT0"):
            raise XCliError("X session cookies are missing. Configure TWITTER_AUTH_TOKEN and TWITTER_CT0 securely, then run 'agent-reach doctor'.")
        args = [executable, mapping.CLI_SEARCH_SUBCOMMAND, f"{mapping.CLI_QUERY_FLAG}={query}", mapping.CLI_LIMIT_FLAG, str(limit), mapping.CLI_OUTPUT_FLAG]
        try:
            completed = subprocess.run(args, capture_output=True, text=True, encoding="utf-8", timeout=self.timeout, check=False, shell=False)
        except subprocess.TimeoutExpired as exc:
            raise XCliError("X CLI search timed out", kind="transient") from exc
        except OSError as exc:
            raise XCliError(f"Could not start X CLI: {exc}") from exc
        if completed.returncode:
            detail = (completed.stderr or completed.stdout or "CLI returned an error").strip()
            for secret_name in ("TWITTER_AUTH_TOKEN", "TWITTER_CT0"):
                secret = os.getenv(secret_name, "")
                if secret:
                    detail = detail.replace(secret, "[REDACTED]")
            detail = detail[:500]
            lowered = detail.casefold()
            if re.search(r"\b(?:429|rate limit|too many requests)\b", lowered):
                raise XCliError("X CLI rate limit detected", kind="rate_limit")
            if re.search(r"\b(?:unauthorized|authentication failed|invalid credentials|session expired|login required)\b", lowered):
                raise XCliError("X CLI authentication failed; verify cookies and run 'agent-reach doctor'")
            if re.search(r"\b5\d\d\b", lowered):
                raise XCliError(f"X CLI transient server error (exit {completed.returncode})", kind="transient")
            raise XCliError(f"X CLI failed (exit {completed.returncode}): {detail}")
        try:
            payload = json.loads(completed.stdout)
        except json.JSONDecodeError as exc:
            raise XCliError("X CLI output was not valid JSON; verify the configured CLI output mode") from exc
        if isinstance(payload, list):
            items = payload
        elif isinstance(payload, dict):
            items = next((payload[key] for key in mapping.OUTPUT_ITEMS_KEYS if isinstance(payload.get(key), list)), None)
        else:
            items = None
        if items is None or any(not isinstance(item, dict) for item in items):
            raise XCliError("X CLI JSON did not contain a recognized tweet list; update cli_mapping.py from the CLI's actual output")
        return items[:limit]
