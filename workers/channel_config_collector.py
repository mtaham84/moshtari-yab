#!/usr/bin/env python3
"""
Standalone Daemon Worker for Telegram Channel Proxy Config Automation.
Strictly collects and tests configs from https://t.me/AR14N24B
"""
from __future__ import annotations

import logging
import os
import sys
import time
from pathlib import Path

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)]
)
log = logging.getLogger("workers.channel_config_collector")

# Setup Django Environment
ROOT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT_DIR))

for env_candidate in [ROOT_DIR / ".env", Path("/root/moshtari-yab/.env")]:
    if env_candidate.exists():
        try:
            with open(env_candidate, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if line and not line.startswith("#") and "=" in line:
                        k, v = line.split("=", 1)
                        os.environ.setdefault(k.strip(), v.strip())
        except Exception:
            pass

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")

try:
    import django
    django.setup()
except Exception as exc:
    log.warning("Django setup note: %s", exc)

from apps.discovery.channel_config_automation import sync_channel_configs, AUTHORIZED_CHANNELS


def main():
    target_channel = os.environ.get("CONFIG_CHANNEL", "AR14N24B").strip().lstrip("@")
    interval = int(os.environ.get("CONFIG_CHANNEL_INTERVAL", "180"))
    max_add = int(os.environ.get("CONFIG_MAX_ADD_PER_CYCLE", "10"))

    if target_channel.upper() not in [c.upper() for c in AUTHORIZED_CHANNELS]:
        sys.exit(f"Target channel '{target_channel}' is unauthorized. Allowed: {AUTHORIZED_CHANNELS}")

    log.info(
        "🚀 Starting Channel Config Collector for @%s (Interval: %ds, Max additions: %d)",
        target_channel, interval, max_add
    )

    cycle = 1
    while True:
        log.info("--- Cycle #%d: Checking @%s for new configs ---", cycle, target_channel)
        try:
            res = sync_channel_configs(
                channel_username=target_channel,
                max_posts=15,
                max_add_per_run=max_add,
            )
            if res.get("success"):
                log.info(
                    "Cycle #%d completed: %d added, %d checked, %d tested.",
                    cycle, res.get("added_count", 0), res.get("posts_checked", 0), res.get("configs_tested", 0)
                )
            else:
                log.warning("Cycle #%d returned error: %s", cycle, res.get("error"))
        except Exception as exc:
            log.exception("Unexpected error during cycle #%d: %s", cycle, exc)

        cycle += 1
        time.sleep(interval)


if __name__ == "__main__":
    main()
