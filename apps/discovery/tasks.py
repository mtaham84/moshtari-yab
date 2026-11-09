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
    tenant: Optional[str] = None,
    batch_size: int = 2,
    min_intent: float = 0.65,
    ignore_shift: bool = False,
) -> Dict[str, Any]:
    """
    Celery tick of the single DM dispatcher (apps.discovery.outreach_engine), same logic as ig-outreach:
    queued messages first, then the best 45-point lead; per-sender and global pacing live in the DB,
    a Postgres advisory lock keeps one step per tenant, so this task never sleeps between DMs.
    `tenant` limits the tick to one tenant; by default every tenant of `outreach_tenants()` is served.
    `min_intent` is accepted for old beat entries only; ranking uses the 45-point outreach score.
    """
    from apps.discovery.ig_accounts import outreach_tenants, tenant_ids as ig_tenant_ids

    # Single-dispatcher guard: never send in parallel with the ig-outreach command loop
    dispatcher = getattr(settings, "INSTAGRAM_OUTREACH_DISPATCHER", "command")
    if dispatcher != "celery":
        logger.info("⏭️ [Celery Outreach] Skipped: INSTAGRAM_OUTREACH_DISPATCHER=%s (the ig-outreach command is the active dispatcher).", dispatcher)
        return {"success": True, "skipped": True, "reason": "dispatcher_disabled", "sent_count": 0}

    tenants = [tenant] if tenant else outreach_tenants()
    sent, last, paused = 0, {}, []
    for t in tenants:
        batch = run_outreach_batch(ig_tenant_ids(t), max_sends=batch_size, ignore_shift=ignore_shift, log=logger.info)
        sent += batch["sent"]
        last = batch["last"]
        if last.get("action") == "paused":
            paused.append(last)
    if tenants and len(paused) == len(tenants):
        last = paused[0]
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
        "sent_count": sent,
        "last_action": last.get("action"),
        "tenant": tenant or ",".join(tenants),
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
        from apps.discovery.ig_accounts import outreach_tenant_ids

        tenant_ids = outreach_tenant_ids()
        if not tenant_ids:
            return {"success": True, "skipped": True, "reason": "no_outreach_tenants", "replies_found": 0}
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
    Proxy healthcheck & healing for every Instagram account without its own proxy (ig_accounts.ensure_system_configs):
    a config that is still ACTIVE and listening stays with its account (same IP); a dead one is replaced by a free
    config (else the shared pool) and the move is logged on the account's timeline. Accounts with their own
    (custom) proxy are never touched; nothing moves while xray itself is down.
    """
    from apps.discovery.ig_accounts import ensure_system_configs
    from apps.discovery.proxies import health
    from apps.discovery.proxies.urls import POOL_PORT, XRAY_HOST

    if not health.port_open(XRAY_HOST, POOL_PORT):
        return {"success": False, "skipped": "xray_down", "healed_count": 0, "details": []}

    result = ensure_system_configs(actor="system")
    for d in result.get("details", []):
        logger.info("[Auto-Heal] @%s: config %s -> %s (port %s)", d["username"], d.get("from"), d.get("to"),
                    d.get("new_port"))
    return result


@shared_task(
    name="apps.discovery.tasks.task_warmup_sender_account",
    max_retries=1,
)
def task_warmup_sender_account(username: Optional[str] = None, tenant_ids: Optional[List[str]] = None) -> Dict[str, Any]:
    """
    Independent Celery warmup task: simulates natural browsing (feed scroll, story views, occasional likes)
    for active sender accounts without blocking or slowing down the direct message send path.
    """
    from apps.discovery.outreach_engine import get_available_senders, get_sender_client
    from apps.discovery.outreach_service import execute_background_organic_warmup
    from apps.discovery.ig_accounts import outreach_tenant_ids

    tenants = tenant_ids or outreach_tenant_ids()
    senders = get_available_senders(tenants)
    if username:
        senders = [s for s in senders if s.username == username]
    if not senders:
        return {"success": True, "skipped": True, "reason": "no_available_senders"}

    sender = senders[0]
    try:
        cl = get_sender_client(sender)
        actions = execute_background_organic_warmup(cl, target_username=sender.username)
        return {"success": True, "sender": sender.username, "actions": actions}
    except Exception as exc:
        logger.warning("Error running background warmup for @%s: %s", sender.username, exc)
        return {"success": False, "sender": sender.username, "error": str(exc)}
