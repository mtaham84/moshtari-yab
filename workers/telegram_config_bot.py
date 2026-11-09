#!/usr/bin/env python3
"""
Telegram Config Management Bot (@config_peyda_bot)
- Listens for incoming proxy links (vless://, vmess://, trojan://, ss://)
- Validates config formats strictly
- Tests live latency (ping_ms) via Xray
- Adds healthy configs to database and assigns dedicated Xray ports per account
- Automatically deletes/prunes dead configs (ping == -1) from server & DB
- Reports live status and account-proxy assignments
- Connects through local proxy (socks5/http) since Telegram API is filtered

This file is what systemd runs: it loads .env and sets up Django. The bot itself lives in apps/core/ops_bot/.
"""
from __future__ import annotations

import logging
import os
import sys

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)]
)
log = logging.getLogger("telegram_config_bot")

# Setup Django Environment & Load .env
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))
for env_candidate in [REPO_ROOT / ".env"]:
    if env_candidate.exists():
        with open(env_candidate, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, v = line.split("=", 1)
                    os.environ.setdefault(k.strip(), v.strip())

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")

# The bot token lives only in .env (CONFIG_BOT_TOKEN); it is never stored in the repository.
BOT_TOKEN = os.environ.get("CONFIG_BOT_TOKEN", "").strip()
if not BOT_TOKEN:
    sys.exit("CONFIG_BOT_TOKEN is not set (add it to .env)")

# When running on host, PostgreSQL is bound to localhost 127.0.0.1
if not Path("/.dockerenv").exists():
    if os.environ.get("POSTGRES_HOST") in (None, "db", "172.18.0.3"):
        os.environ["POSTGRES_HOST"] = "127.0.0.1"

try:
    import django
    django.setup()
except Exception as exc:
    log.warning("Django setup warning: %s", exc)

from apps.core.ops_bot.worker import run_bot  # noqa: E402  (needs django.setup() above)


if __name__ == "__main__":
    run_bot()
