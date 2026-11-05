"""
Celery Task Definitions for Moshtari-Yab Distributed Asynchronous Engine.
Celery ticks of the single Instagram DM dispatcher (apps.discovery.outreach_engine),
fast reply synchronization, and automated proxy self-healing.
"""
from __future__ import annotations

import logging
import random
from typing import Any, Dict, List, Optional
from zoneinfo import ZoneInfo

from celery import shared_task
from django.conf import settings
from django.utils import timezone as django_tz

from apps.discovery.models.instagram import InstagramEngineAccount
from apps.discovery.models.proxy import ProxyConfig
from apps.discovery.human_routine import HumanShiftPlanner  # noqa: F401  (tests patch tasks.HumanShiftPlanner)
from apps.discovery.outreach_engine import get_available_senders, reply_sync_allowed, run_outreach_batch
from apps.discovery.outreach_service import check_and_sync_lead_replies

logger = logging.getLogger("discovery.tasks")
TEHRAN_TZ = ZoneInfo("Asia/Tehran")
REPLY_SYNC_SKIP_CHANCE = 0.3  # share of 5-minute beat ticks that do not check the inbox (irregular 5-15 min gaps)


def get_available_senders_atomic(tenant_ids: List[str]) -> List[InstagramEngineAccount]:
    """Active senders with remaining tier-capped quota (shared with the ig-outreach command)."""
    return get_available_senders(tenant_ids)


@shared_task(
    bind=True,
    name="apps.discovery.tasks.task_dispatch_instagram_outreach",
    max_retries=2,
    default_retry_delay=120,
)
def task_dispatch_instagram_outreach(
    self,
    tenant: str = "tenant_4",
    batch_size: int = 2,
    min_intent: float = 0.65,
    ignore_shift: bool = False,
) -> Dict[str, Any]:
    """
    Celery tick of the single DM dispatcher (apps.discovery.outreach_engine), same logic as ig-outreach:
    queued messages first, then the best 45-point lead; per-sender and global pacing live in the DB,
    a Postgres advisory lock keeps one step per tenant, so this task never sleeps between DMs.
    `min_intent` is accepted for old beat entries only; ranking uses the 45-point outreach score.
    """
    tenant_ids = ["tenant_4", "sarv"] if tenant in ("sarv", "tenant_4") else [tenant]

    # Single-dispatcher guard: never send in parallel with the ig-outreach command loop
    dispatcher = getattr(settings, "INSTAGRAM_OUTREACH_DISPATCHER", "command")
    if dispatcher != "celery":
        logger.info("⏭️ [Celery Outreach] Skipped: INSTAGRAM_OUTREACH_DISPATCHER=%s (the ig-outreach command is the active dispatcher).", dispatcher)
        return {"success": True, "skipped": True, "reason": "dispatcher_disabled", "sent_count": 0}

    batch = run_outreach_batch(tenant_ids, max_sends=batch_size, ignore_shift=ignore_shift, log=logger.info)
    last = batch["last"]
    if last.get("action") == "paused":
        logger.info("💤 [Celery Shift Pause] Task skipped: %s (Resuming in %dm)", last.get("reason"), (last.get("wait_seconds") or 0) // 60)
        return {
            "success": True,
            "paused": True,
            "reason": last.get("reason"),
            "wait_seconds": last.get("wait_seconds"),
            "sent_count": 0,
        }
    return {
        "success": True,
        "sent_count": batch["sent"],
        "last_action": last.get("action"),
        "tenant": tenant,
        "timestamp": django_tz.now().isoformat(),
    }


@shared_task(
    name="apps.discovery.tasks.task_sync_lead_replies",
    max_retries=1,
)
def task_sync_lead_replies(tenant_ids: Optional[List[str]] = None) -> Dict[str, Any]:
    """
    Polling task for incoming Instagram lead replies (Celery dispatcher only).
    Beat ticks every 5 minutes; outside shift hours nothing is checked, and REPLY_SYNC_SKIP_CHANCE of the ticks are
    skipped so the checks are not evenly spaced.
    """
    if not tenant_ids:
        tenant_ids = ["tenant_4", "sarv"]
    allowed, reason = reply_sync_allowed()
    if not allowed:
        return {"success": True, "skipped": True, "reason": reason, "replies_found": 0}
    if random.random() < REPLY_SYNC_SKIP_CHANCE:
        return {"success": True, "skipped": True, "reason": "random_skip", "replies_found": 0}
    try:
        res = check_and_sync_lead_replies(tenant_ids)
        replies_count = res.get("replies_found", 0)
        if replies_count > 0:
            logger.info("🎉 [Celery Fast Replies] %d new customer replies synced!", replies_count)
        return res
    except Exception as exc:
        logger.warning("Error syncing lead replies in Celery task: %s", exc)
        return {"success": False, "error": str(exc), "replies_found": 0}


@shared_task(
    name="apps.discovery.tasks.task_sync_telegram_channel_configs",
    max_retries=2,
)
def task_sync_telegram_channel_configs(max_posts: int = 15, max_add: int = 10) -> Dict[str, Any]:
    """
    Asynchronous task crawling all active monitored Telegram proxy channels.
    """
    from apps.discovery.channel_config_automation import sync_all_active_channels
    try:
        res = sync_all_active_channels(max_posts=max_posts, max_add_per_channel=max_add)
        logger.info("📡 [Celery Channels] %s", res.get("message"))
        return res
    except Exception as exc:
        logger.error("Error syncing Telegram channels in Celery: %s", exc)
        return {"success": False, "error": str(exc)}


@shared_task(
    name="apps.discovery.tasks.task_healthcheck_and_heal_proxies",
)
def task_healthcheck_and_heal_proxies() -> Dict[str, Any]:
    """
    Automatic proxy healthcheck & auto-healing:
    Probes all sender accounts' assigned proxies. If an account's proxy is dead,
    automatically swaps it with a healthy active proxy from the pool!
    """
    from apps.discovery.proxy_service import clean_proxy_url
    import socket

    senders = InstagramEngineAccount.objects.filter(role="SENDER")
    healed = []

    for s in senders:
        proxy_str = clean_proxy_url(s.proxy_url)
        is_healthy = False

        if proxy_str and ("socks5://" in proxy_str or "socks5h://" in proxy_str):
            try:
                # Extract host and port
                parts = proxy_str.split("://")[-1].split(":")
                host = parts[0]
                port = int(parts[1].split("/")[0])
                sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                sock.settimeout(4.0)
                if sock.connect_ex((host, port)) == 0:
                    is_healthy = True
                sock.close()
            except Exception:
                is_healthy = False

        # If dead or disconnected, auto-heal with an active spare proxy
        if not is_healthy:
            spare = ProxyConfig.objects.filter(
                status="ACTIVE",
                local_port__isnull=False,
            ).exclude(
                local_port__in=[acc.proxy_url for acc in senders if acc.proxy_url]
            ).order_by("ping_ms").first()

            if spare:
                new_proxy_url = f"socks5h://xray:{spare.local_port}"
                old_proxy = s.proxy_url
                s.proxy_url = new_proxy_url
                s.status_message = f"پروکسی به صورت خودکار ترمیم شد (پورت {spare.local_port})"
                s.save(update_fields=["proxy_url", "status_message"])
                logger.info(
                    "🔧 [Auto-Heal] Replaced dead proxy (%s) for @%s with healthy port %d (%s ms)",
                    old_proxy, s.username, spare.local_port, spare.ping_ms
                )
                healed.append({"username": s.username, "new_port": spare.local_port, "ping": spare.ping_ms})

    return {"success": True, "healed_count": len(healed), "details": healed}
