"""
Celery Task Definitions for Moshtari-Yab Distributed Asynchronous Engine.
Handles atomic lead reservation, organic Instagram outreach dispatching,
fast reply synchronization, and automated proxy self-healing.
"""
from __future__ import annotations

import logging
import random
import time
from datetime import datetime
from typing import Any, Dict, List, Optional
from zoneinfo import ZoneInfo

from celery import shared_task
from django.db import transaction, models
from django.utils import timezone as django_tz

from apps.discovery.models.instagram import (
    InstagramEngineAccount,
    InstagramEngineLead,
)
from apps.discovery.models.proxy import ProxyConfig
from apps.discovery.human_routine import HumanShiftPlanner
from apps.discovery.outreach_service import (
    qualify_recipient_profile,
    build_bulletproof_message,
    execute_organic_journey,
    process_queued_outbox_message,
    check_and_sync_lead_replies,
    get_tier_specs,
)
from apps.discovery.management.commands.run_instagram_outreach import (
    load_outreach_config,
    DEFAULT_SENDER_PITCHES,
)
from apps.discovery.anti_ban import InstagramAntiBanManager

logger = logging.getLogger("discovery.tasks")
TEHRAN_TZ = ZoneInfo("Asia/Tehran")


def get_available_senders_atomic(tenant_ids: List[str]) -> List[InstagramEngineAccount]:
    """
    Returns active senders with remaining quotas and auto-recovers expired cooldowns.
    """
    now = django_tz.now()
    now_tehran = datetime.now(TEHRAN_TZ)

    # 1. Reset daily/hourly counts if new day/hour has arrived
    for acc in InstagramEngineAccount.objects.filter(role="SENDER"):
        updated = False
        update_cols = []
        if acc.last_action_at and acc.last_action_at.astimezone(TEHRAN_TZ).date() < now_tehran.date():
            acc.daily_action_count = 0
            update_cols.append("daily_action_count")
            updated = True

        if acc.last_hour_reset and acc.last_hour_reset.astimezone(TEHRAN_TZ).hour != now_tehran.hour:
            acc.hourly_action_count = 0
            acc.last_hour_reset = now
            update_cols.extend(["hourly_action_count", "last_hour_reset"])
            updated = True
        elif not acc.last_hour_reset:
            acc.last_hour_reset = now
            update_cols.append("last_hour_reset")
            updated = True

        if updated:
            acc.save(update_fields=list(set(update_cols)))

    # 2. Auto-Recovery: Restore senders whose cooldown has ended
    expired_senders = InstagramEngineAccount.objects.filter(
        role="SENDER",
        status="COOLDOWN",
        tenant_id__in=tenant_ids,
    ).filter(
        models.Q(cooldown_until__isnull=True) | models.Q(cooldown_until__lte=now)
    )
    for exp_acc in expired_senders:
        exp_acc.status = "ACTIVE"
        exp_acc.cooldown_until = None
        exp_acc.status_message = "پایان استراحت - بازگشت خودکار به مدار ارسال"
        exp_acc.save(update_fields=["status", "cooldown_until", "status_message"])
        logger.info("🟢 [Auto-Recovery] SENDER @%s restored to ACTIVE.", exp_acc.username)

    senders = InstagramEngineAccount.objects.filter(
        role="SENDER",
        status="ACTIVE",
        tenant_id__in=tenant_ids,
    ).filter(
        models.Q(cooldown_until__isnull=True) | models.Q(cooldown_until__lte=now)
    )

    available = []
    for s in senders:
        is_valid_role, _ = InstagramAntiBanManager.validate_account_role(s, "SENDER")
        if not is_valid_role:
            continue

        tier_spec = get_tier_specs(s.account_tier)
        limit_daily = min(s.daily_limit or tier_spec["default_daily"], tier_spec["max_daily"])
        limit_hourly = min(s.hourly_limit or tier_spec["hourly_speed"], tier_spec["hourly_speed"])

        if s.daily_action_count < limit_daily and (s.hourly_action_count or 0) < limit_hourly:
            available.append(s)

    # Least recently active first
    available.sort(key=lambda s: s.last_action_at or datetime.min.replace(tzinfo=django_tz.utc))
    return available


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
    Asynchronous Celery task for atomic, resilient Instagram outreach:
    1. Evaluates human shift planner (sleeps during night/lunch pause unless bypassed).
    2. Atomically reserves candidate leads using `select_for_update(skip_locked=True)`
       to completely prevent double-sends and race conditions.
    3. Handles organic journey, Spintax variation, and mobile proxy verification.
    4. Triggers reply checking.
    """
    tenant_ids = ["tenant_4", "sarv"] if tenant in ("sarv", "tenant_4") else [tenant]
    now_tehran = datetime.now(TEHRAN_TZ)

    # 1. Shift Check
    if not ignore_shift:
        is_active, shift_reason, wait_sec = HumanShiftPlanner.is_shift_active(now_tehran)
        if not is_active:
            logger.info("💤 [Celery Shift Pause] Task skipped: %s (Resuming in %dm)", shift_reason, wait_sec // 60)
            return {
                "success": True,
                "paused": True,
                "reason": shift_reason,
                "wait_seconds": wait_sec,
                "sent_count": 0,
            }

    available_senders = get_available_senders_atomic(tenant_ids)
    if not available_senders:
        logger.warning("⚠️ No active SENDER accounts available with remaining quota.")
        return {"success": False, "sent_count": 0, "message": "No available senders"}

    sent_in_batch = 0
    cfg = load_outreach_config()
    sender_pitches = cfg.get("sender_pitches", {})

    # Priority 1: Outbox Queued Messages (operator approved / urgent follow-ups)
    queued_leads = list(
        InstagramEngineLead.objects.filter(
            tenant_id__in=tenant_ids,
            status="QUEUED",
        ).order_by("updated_at")[:batch_size]
    )
    for q_lead in queued_leads:
        if sent_in_batch >= batch_size:
            break
        res = process_queued_outbox_message(q_lead)
        if res.get("success"):
            sent_in_batch += 1
            logger.info("✅ Queued message sent to @%s by @%s", q_lead.username, res.get("sender"))
            delay_sec, _ = HumanShiftPlanner.calculate_next_action_delay(is_priority_reply=True)
            time.sleep(min(delay_sec, 15.0))

    if sent_in_batch >= batch_size:
        check_and_sync_lead_replies(tenant_ids)
        return {"success": True, "sent_count": sent_in_batch, "mode": "queued_priority"}

    # Priority 2: Cold Lead Direct Outreach with Atomic Lock (select_for_update)
    remaining_slots = batch_size - sent_in_batch

    with transaction.atomic():
        # Atomically lock and fetch only unlocked leads
        candidate_leads = list(
            InstagramEngineLead.objects.select_for_update(skip_locked=True)
            .filter(
                tenant_id__in=tenant_ids,
                status="NEW",
                intent_score__gte=min_intent,
            )
            .order_by("-intent_score", "created_at")[:remaining_slots * 5]
        )

        # Adaptive intent fallback if high-intent leads are empty
        if not candidate_leads and min_intent > 0.50:
            candidate_leads = list(
                InstagramEngineLead.objects.select_for_update(skip_locked=True)
                .filter(
                    tenant_id__in=tenant_ids,
                    status="NEW",
                    intent_score__gte=0.50,
                )
                .order_by("-intent_score", "created_at")[:remaining_slots * 5]
            )

        if not candidate_leads:
            check_and_sync_lead_replies(tenant_ids)
            return {"success": True, "sent_count": sent_in_batch, "message": "No new leads in queue"}

        from apps.discovery.management.commands.run_instagram_outreach import Command as OutreachCommand
        cmd = OutreachCommand()

        for lead in candidate_leads:
            if sent_in_batch >= batch_size:
                break

            chosen_sender = None
            for cand in available_senders:
                cand.refresh_from_db()
                cand_daily_limit = cand.daily_limit or 18
                cand_hourly_limit = cand.hourly_limit or 3
                if (
                    cand.status == "ACTIVE"
                    and cand.daily_action_count < cand_daily_limit
                    and (cand.hourly_action_count or 0) < cand_hourly_limit
                    and (not cand.cooldown_until or cand.cooldown_until <= django_tz.now())
                ):
                    chosen_sender = cand
                    break

            if not chosen_sender:
                logger.warning("No sender available with empty quota.")
                break

            sender = chosen_sender
            sender_user = sender.username
            pitch_info = (
                sender_pitches.get(sender_user)
                or sender_pitches.get(sender_user.lower())
                or DEFAULT_SENDER_PITCHES.get(sender_user.lower(), {})
            )
            consultant_name = pitch_info.get("consultant_name", sender_user)
            raw_template = pitch_info.get("template", "")

            biz_name = "مجموعه تخصصی سرو"
            if hasattr(lead, "business") and lead.business and getattr(lead.business, "name", None):
                biz_name = lead.business.name

            pitch_text = build_bulletproof_message(
                pitch_template=raw_template,
                lead=lead,
                consultant_name=consultant_name,
                business_name=biz_name,
            )

            try:
                # 🛡️ Step 0: Mobile Proxy Verification
                from apps.discovery.proxy_service import prepare_sender_proxy_before_dm
                prepare_sender_proxy_before_dm(sender)

                # Initialize instagrapi client
                from instagrapi import Client
                cl = Client()
                cl.delay_range = [1.5, 3.5]
                clean_proxy = getattr(sender, "clean_proxy", None) or sender.proxy_url
                if clean_proxy:
                    cl.set_proxy(clean_proxy)

                from apps.discovery.device_emulator import apply_device_profile_to_client
                apply_device_profile_to_client(cl, sender)

                session_cookies = (sender.session_json or {}).get("cookies", {})
                sess_id = session_cookies.get("sessionid")
                if sess_id:
                    cl.login_by_sessionid(sess_id)

                # 🛡️ Step 1: Pre-Flight Safety Qualification
                qualify = qualify_recipient_profile(cl, lead.username, target_pk=lead.ig_user_pk)
                if not qualify["eligible"]:
                    lead.status = "SKIPPED"
                    lead.comment_text = (lead.comment_text or "") + f"\n[گاردریل ایمنی]: {qualify['reason']}"
                    lead.save(update_fields=["status", "comment_text", "updated_at"])
                    continue

                target_pk = qualify.get("target_pk") or (int(lead.ig_user_pk) if lead.ig_user_pk and str(lead.ig_user_pk).isdigit() else None)
                if not target_pk:
                    lead.status = "SKIPPED"
                    lead.save(update_fields=["status", "updated_at"])
                    continue

                # 🛡️ Step 2: Full In-App Organic Journey
                journey_res = execute_organic_journey(cl, lead.username, target_pk=target_pk)
                final_pk = journey_res.get("target_pk") or target_pk

                # 🛡️ Step 3: Direct Send Message
                cl.direct_send(pitch_text, user_ids=[final_pk])

                now = django_tz.now()
                lead.status = "DM_SENT"
                lead.sender_account = sender_user
                lead.initial_dm_text = pitch_text
                lead.initial_dm_at = now
                lead.conversation_history = [{
                    "sender": sender_user,
                    "direction": "out",
                    "role": "initial_outreach",
                    "text": pitch_text,
                    "time": now.isoformat(),
                    "delivery_status": "SENT",
                }]
                lead.save(update_fields=[
                    "status", "sender_account", "initial_dm_text", "initial_dm_at",
                    "conversation_history", "updated_at"
                ])

                sender.daily_action_count += 1
                sender.hourly_action_count = (sender.hourly_action_count or 0) + 1
                sender.last_action_at = now
                sender.status_message = "پیام با موفقیت ارسال شد"
                sender.save(update_fields=["daily_action_count", "hourly_action_count", "last_action_at", "status_message"])

                sent_in_batch += 1
                logger.info("✅ [Celery] DM sent successfully to @%s via @%s", lead.username, sender_user)

            except Exception as exc:
                exc_str = str(exc).lower()
                logger.error("❌ Error sending DM to @%s: %s", lead.username, exc)
                if "challenge" in exc_str or "login_required" in exc_str or "unauthorized" in exc_str:
                    sender.status = "CHALLENGED"
                    sender.status_message = f"نیازمند تایید سشن یا رفع چالش ({exc_str[:50]})"
                    sender.save(update_fields=["status", "status_message"])
                elif "connection" in exc_str or "proxy" in exc_str or "111" in exc_str:
                    sender.status = "COOLDOWN"
                    sender.cooldown_until = django_tz.now() + django_tz.timedelta(minutes=5)
                    sender.status_message = "خطای موقت پروکسی - استراحت ۵ دقیقه‌ای"
                    sender.save(update_fields=["status", "cooldown_until", "status_message"])
                lead.status = "NEW"
                lead.save(update_fields=["status", "updated_at"])

    # 4. Sync lead replies
    check_and_sync_lead_replies(tenant_ids)

    return {
        "success": True,
        "sent_count": sent_in_batch,
        "tenant": tenant,
        "timestamp": django_tz.now().isoformat(),
    }


@shared_task(
    name="apps.discovery.tasks.task_sync_lead_replies",
    max_retries=1,
)
def task_sync_lead_replies(tenant_ids: Optional[List[str]] = None) -> Dict[str, Any]:
    """
    Asynchronous fast polling task for incoming Instagram lead replies.
    Runs every 2 minutes via Celery Beat.
    """
    if not tenant_ids:
        tenant_ids = ["tenant_4", "sarv"]
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
