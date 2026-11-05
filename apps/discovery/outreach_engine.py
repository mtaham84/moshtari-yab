"""
One Instagram DM dispatcher, shared by the ig-outreach command and the Celery task.

Both runners call run_outreach_batch() on a short tick (about once a minute). A step sends at most one
message and never sleeps between DMs: pacing lives in the database instead, so a step is short, a crashed
step loses nothing and a Celery worker is never blocked for half an hour.

* One step at a time per tenant (Postgres advisory lock): no double sends, even with several workers.
* Priority 1: QUEUED operator / follow-up messages, oldest first, only through a usable sender
  (ACTIVE, not cooling down, a short human gap since its last action). Failed sends back off and give up
  after QUEUED_MAX_ATTEMPTS message-level failures.
* Priority 2: the best NEW lead (45-point score, last 5 days) through the least-recently-used ready sender.
* Per-sender pacing: after a cold DM the sender waits its tier delay or a micro-break (next_outreach_at).
* Global pacing: at most one cold DM per INSTAGRAM_OUTREACH_MIN_GAP_MINUTES (+0-50% jitter) across senders.
* Daily/hourly caps never above the warm-up tier. Errors are classified per sender (challenge, session,
  proxy + IP rotation, 1404006, feedback / rate limit, message controls), so one broken account never stops
  the others.
"""
from __future__ import annotations

import json
import logging
import random
import zlib
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone as dt_timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple
from zoneinfo import ZoneInfo

from django.conf import settings
from django.db import connection, models, transaction
from django.utils import timezone

from apps.discovery.anti_ban import InstagramAntiBanManager
from apps.discovery.device_emulator import connect_saved_session, has_device_profile
from apps.discovery.human_routine import HumanShiftPlanner
from apps.discovery.models import InstagramEngineAccount, InstagramEngineLead
from apps.discovery.outreach_service import (
    build_bulletproof_message,
    execute_organic_journey,
    get_effective_sender_limits,
    process_queued_outbox_message,
    qualify_recipient_profile,
    resolve_queued_sender,
    select_outreach_candidates,
)

logger = logging.getLogger("instagram_outreach")
TEHRAN_TZ = ZoneInfo("Asia/Tehran")

CONFIG_FILE = Path("data/sarv_outreach_config.json")
DEFAULT_BUSINESS_NAME = "مجموعه تخصصی سرو"
DEFAULT_SENDER_PITCHES = {
    "raha.rostamim": {
        "consultant_name": "رها",
        "consultant_role": "مشاور و پشتیبانی تخصصی دندان‌پزشکی",
        "template": "درود بر شما \nبنده رها هستم مشاور و پشتیبانی تخصصی دندان پزشکی ، متوجه شدم پیگیر امور زیبایی و سلامتی دندونتون هستید\nمیتونید هر سوالی دارید از بنده به عنوان متخصص بپرسید. \nاگر هم فکر میکنید موثر هست ، شماره تماستون رو بزارید من برای 10 دقیقه مشاوره تخصصی و بدون چشم داشت با شما تماس میگیرم",
    },
    "pania.mehrim": {
        "consultant_name": "پنیا",
        "consultant_role": "مشاور و پشتیبانی تخصصی دندان‌پزشکی",
        "template": "درود بر شما \nبنده پنیا هستم مشاور و پشتیبانی تخصصی دندان پزشکی ، متوجه شدم پیگیر امور زیبایی و سلامتی دندونتون هستید\nمیتونید هر سوالی دارید از بنده به عنوان متخصص بپرسید \nاگر هم فکر میکنید موثر هست ، شماره تماستون رو بزارید من برای 10 دقیقه مشاوره تخصصی و بدون چشم داشت با شما تماس میگیرم",
    },
}

# Error fingerprints (lower-cased exception text) that must take a sender out of rotation
# instead of burning the lead as FAILED.
SESSION_EXPIRED_MARKERS = (
    "login_required",
    "loginrequired",
    "user_has_logged_out",
    "unauthorized",
    "clientforbiddenerror",
)
PROXY_ERROR_MARKERS = (
    "connection refused",
    "connectionerror",
    "proxyconnectionerror",
    "proxyerror",
    "errno 111",
    "max retries exceeded",
    "timed out",
)

REPLY_GAP_SECONDS = 90             # a sender never acts twice within this gap (queued replies included)
MAX_LEADS_PER_STEP = 3             # leads tried in one step when the first ones are skipped by the guardrail
QUEUED_MAX_ATTEMPTS = 3            # message-level failures before a queued message is marked FAILED
QUEUED_RETRY_MINUTES = 10          # back-off per failed attempt of a queued message
UNKNOWN_ERROR_PAUSE_MINUTES = 10   # short pause for a sender after an unclassified error
LOCK_NAMESPACE = 0x4D59            # pg advisory lock namespace ("MY")


def _error_matches(exc_str: str, markers: Tuple[str, ...]) -> bool:
    return any(marker in exc_str for marker in markers)


def load_outreach_config() -> dict:
    """data/sarv_outreach_config.json merged over the built-in pitches (legacy per-username pitches; the customer's
    OutreachTenantProfile comes first, see _pitch_for)."""
    pitches = dict(DEFAULT_SENDER_PITCHES)
    cfg: dict = {}
    if CONFIG_FILE.exists():
        try:
            cfg = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
            for u, p in (cfg.get("sender_pitches") or {}).items():
                if isinstance(p, dict):
                    pitches[str(u).lower()] = p
        except Exception as exc:
            logger.warning(f"Could not load {CONFIG_FILE}: {exc}")
            cfg = {}
    return {**cfg, "sender_pitches": pitches}


# ---------------------------------------------------------------------------
# Instagram clients (one per sender and process, reused across steps and reply checks)
# ---------------------------------------------------------------------------
_CLIENTS: Dict[str, Any] = {}
_CLIENT_KEYS: Dict[str, tuple] = {}


def _session_id(account) -> str:
    return ((account.session_json or {}).get("cookies") or {}).get("sessionid") or ""


def drop_sender_client(username: str) -> None:
    _CLIENTS.pop(username, None)
    _CLIENT_KEYS.pop(username, None)


def get_sender_client(account):
    """Logged-in instagrapi client for a SENDER; rebuilt only when its sessionid or proxy changes."""
    key = (_session_id(account), account.clean_proxy)
    if account.username in _CLIENTS and _CLIENT_KEYS.get(account.username) == key:
        return _CLIENTS[account.username]
    drop_sender_client(account.username)

    from instagrapi import Client
    from requests.adapters import HTTPAdapter

    class TimeoutHTTPAdapter(HTTPAdapter):
        def send(self, request, **kwargs):
            if kwargs.get("timeout") is None:
                kwargs["timeout"] = (10, 35 if request.method == "POST" else 15)
            return super().send(request, **kwargs)

    if not key[0]:
        raise RuntimeError("login_required: no sessionid saved for this sender")
    cl = Client()
    cl.delay_range = [1.5, 3.5]
    if account.clean_proxy:
        cl.set_proxy(account.clean_proxy)
    # The saved session on the phone that created it (set_settings, no extra login request); only rows holding a
    # bare sessionid get the account's device profile and one login_by_sessionid().
    connect_saved_session(cl, account)
    cl.public_request_retries_count = 0
    cl.public_request_retries_timeout = 0
    adapter = TimeoutHTTPAdapter()  # mounted last: instagrapi re-mounts its own adapters while loading settings
    cl.private.mount("https://", adapter)
    cl.private.mount("http://", adapter)
    _CLIENTS[account.username] = cl
    _CLIENT_KEYS[account.username] = key
    return cl


def persist_sender_session(sender) -> None:
    """Keeps the cookies/tokens Instagram rotated during this session, as the app keeps them on the phone, so a
    restart does not replay stale ones. Same device and UUIDs; only full instagrapi settings are stored."""
    cl = _CLIENTS.get(sender.username)
    if cl is None:
        return
    try:
        fresh = cl.get_settings()
    except Exception:
        return
    if not has_device_profile(fresh) or fresh == sender.session_json:
        return
    sender.session_json = fresh
    sender.save(update_fields=["session_json"])
    _CLIENT_KEYS[sender.username] = (_session_id(sender), sender.clean_proxy)  # the same client stays cached


# ---------------------------------------------------------------------------
# Senders
# ---------------------------------------------------------------------------
def refresh_sender_states(tenant_ids: List[str], log: Callable[[str], None] = logger.info) -> None:
    """Reset daily/hourly counters (Tehran time) and bring senders whose cooldown ended back to ACTIVE."""
    now = timezone.now()
    now_tehran = now.astimezone(TEHRAN_TZ)
    for acc in InstagramEngineAccount.objects.filter(role="SENDER"):
        cols = []
        if acc.last_action_at and acc.last_action_at.astimezone(TEHRAN_TZ).date() < now_tehran.date():
            acc.daily_action_count = 0
            cols.append("daily_action_count")
        if acc.last_hour_reset and acc.last_hour_reset.astimezone(TEHRAN_TZ).hour != now_tehran.hour:
            acc.hourly_action_count = 0
            acc.last_hour_reset = now
            cols += ["hourly_action_count", "last_hour_reset"]
        elif not acc.last_hour_reset:
            acc.last_hour_reset = now
            cols.append("last_hour_reset")
        if cols:
            acc.save(update_fields=cols)

    expired = InstagramEngineAccount.objects.filter(role="SENDER", status="COOLDOWN", tenant_id__in=tenant_ids).filter(
        models.Q(cooldown_until__isnull=True) | models.Q(cooldown_until__lte=now)
    )
    for acc in expired:
        acc.status = "ACTIVE"
        acc.status_reason = ""
        acc.cooldown_until = None
        acc.status_message = "پایان دوره استراحت موقت - بازگشت خودکار به مدار ارسال دایرکت"
        acc.save(update_fields=["status", "status_reason", "cooldown_until", "status_message"])
        log(f"🟢 [بازیابی خودکار اکانت]: دوره استراحت @{acc.username} به پایان رسید و به مدار ارسال بازگشت.")


def get_available_senders(tenant_ids: List[str], fallback_daily: Optional[int] = None,
                          log: Callable[[str], None] = logger.info, *, refresh: bool = True) -> List[InstagramEngineAccount]:
    """ACTIVE SENDERs (role-checked, not cooling down) under their tier-capped daily/hourly limits, LRU first."""
    if refresh:
        refresh_sender_states(tenant_ids, log)
    now = timezone.now()
    senders = InstagramEngineAccount.objects.filter(role="SENDER", status="ACTIVE", tenant_id__in=tenant_ids).filter(
        models.Q(cooldown_until__isnull=True) | models.Q(cooldown_until__lte=now)
    )
    available = []
    for s in senders:
        if not InstagramAntiBanManager.validate_account_role(s, "SENDER")[0]:
            continue
        daily, hourly = get_effective_sender_limits(s, fallback_daily=fallback_daily)
        if s.daily_action_count < daily and (s.hourly_action_count or 0) < hourly:
            available.append(s)
    available.sort(key=lambda s: s.last_action_at or datetime.min.replace(tzinfo=dt_timezone.utc))
    return available


def _sender_can_act(sender, now, *, dry_run: bool) -> bool:
    if not sender or sender.role != "SENDER" or sender.status != "ACTIVE":
        return False
    if sender.cooldown_until and sender.cooldown_until > now:
        return False
    if dry_run:
        return True
    return not sender.last_action_at or sender.last_action_at <= now - timedelta(seconds=REPLY_GAP_SECONDS)


def _cold_ready(sender, now) -> bool:
    return _sender_can_act(sender, now, dry_run=False) and (not sender.next_outreach_at or sender.next_outreach_at <= now)


def global_gap_until(tenant_ids: List[str], min_gap_minutes: int) -> Optional[datetime]:
    """Earliest time the next cold DM may go out (any sender): last cold DM + gap with stable 0-50% jitter."""
    if not min_gap_minutes:
        return None
    last = InstagramEngineLead.objects.filter(tenant_id__in=tenant_ids, initial_dm_at__isnull=False).aggregate(
        m=models.Max("initial_dm_at"))["m"]
    if not last:
        return None
    jitter = random.Random(int(last.timestamp())).uniform(1.0, 1.5)
    return last + timedelta(minutes=min_gap_minutes * jitter)


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------
def classify_send_error(error_text: str) -> str:
    s = (error_text or "").lower()
    if "challenge" in s or "checkpoint" in s or "suspended" in s:
        return "challenge"
    if _error_matches(s, SESSION_EXPIRED_MARKERS):
        return "session"
    if _error_matches(s, PROXY_ERROR_MARKERS):
        return "proxy"
    if "1404006" in s:
        return "dm_block"
    if "message controls" in s:
        return "recipient_blocked"
    if "feedback" in s or "please wait" in s or "rate limit" in s or "403" in s:
        return "rate_limit"
    return "unknown"


def apply_sender_error(sender, kind: str, error_text: str, log: Callable[[str], None] = logger.info) -> None:
    """Take a failing sender out of rotation for the right amount of time; the other senders keep working."""
    now = timezone.now()
    short = (error_text or "")[:50]
    from apps.discovery.ig_accounts import log_event

    if kind in ("challenge", "session"):
        sender.status = "CHALLENGED"
        sender.status_reason = "needs_code" if kind == "challenge" else "session_expired"
        sender.status_message = (
            "نیازمند حل چالش امنیتی اینستاگرام" if kind == "challenge"
            else "سشن اینستاگرام منقضی شده است (نیاز به لاگین مجدد یا ثبت کوکی sessionid جدید)"
        )
        sender.save(update_fields=["status", "status_reason", "status_message"])
        log_event(sender, "challenge" if kind == "challenge" else "session_expired", sender.status_message,
                  {"error": short}, "outreach")
        log(f"   ⚠️ اکانت @{sender.username} ({kind}) از مدار ارسال خارج شد.")
    elif kind == "proxy":
        if getattr(sender, "proxy_rotation_url", None):
            try:
                from apps.discovery.proxy_service import rotate_account_proxy_ip
                log("   🔄 چرخش خودکار IP پروکسی موبایل جهت رفع خطای اتصال...")
                rotate_account_proxy_ip(sender)
            except Exception as rot_err:
                log(f"   ⚠️ چرخش IP ناموفق بود: {rot_err}")
        sender.cooldown_until = now + timedelta(minutes=15)
        sender.status = "COOLDOWN"
        sender.status_reason = "proxy_down"
        sender.status_message = f"خطای اتصال پروکسی ({short}) - استراحت ۱۵ دقیقه‌ای"
        sender.save(update_fields=["cooldown_until", "status", "status_reason", "status_message"])
        log_event(sender, "proxy_down", sender.status_message, {"error": short}, "outreach")
        log(f"   ⚠️ خطای پروکسی یا اتصال برای @{sender.username}. ۱۵ دقیقه استراحت.")
    elif kind in ("dm_block", "rate_limit"):
        sender.cooldown_until = now + timedelta(hours=3)
        sender.status = "COOLDOWN"
        sender.status_reason = "rate_limited"
        sender.status_message = (
            "محدودیت موقت دایرکت (1404006) - استراحت ۳ ساعته" if kind == "dm_block"
            else "محدودیت موقت اینستاگرام (استراحت ۳ ساعته)"
        )
        sender.save(update_fields=["cooldown_until", "status", "status_reason", "status_message"])
        log_event(sender, "rate_limited", sender.status_message, {"error": short}, "outreach")
        log(f"   ⚠️ اکانت @{sender.username} به خاطر محدودیت اینستاگرام ({kind}) ۳ ساعت استراحت می‌کند.")
    elif kind == "unknown":
        pause_until = now + timedelta(minutes=UNKNOWN_ERROR_PAUSE_MINUTES)
        if not sender.next_outreach_at or sender.next_outreach_at < pause_until:
            sender.next_outreach_at = pause_until
        sender.status_message = f"خطای ارسال ({short}) - مکث {UNKNOWN_ERROR_PAUSE_MINUTES} دقیقه‌ای"
        sender.save(update_fields=["next_outreach_at", "status_message"])
    if kind != "recipient_blocked":
        drop_sender_client(sender.username)


# ---------------------------------------------------------------------------
# Dispatcher lock
# ---------------------------------------------------------------------------
def dispatch_lock_key(tenant_ids: List[str]) -> int:
    return zlib.crc32(("ig-outreach:" + ",".join(sorted(tenant_ids))).encode()) & 0x7FFFFFFF


@contextmanager
def dispatch_lock(tenant_ids: List[str]):
    """Session-level Postgres advisory lock so only one dispatch step runs per tenant at a time."""
    if connection.vendor != "postgresql":
        yield True
        return
    key = dispatch_lock_key(tenant_ids)
    with connection.cursor() as cur:
        cur.execute("SELECT pg_try_advisory_lock(%s, %s)", [LOCK_NAMESPACE, key])
        acquired = bool(cur.fetchone()[0])
    try:
        yield acquired
    finally:
        if acquired:
            try:
                with connection.cursor() as cur:
                    cur.execute("SELECT pg_advisory_unlock(%s, %s)", [LOCK_NAMESPACE, key])
            except Exception as exc:  # the lock dies with the DB session anyway
                logger.warning("Could not release outreach dispatch lock: %s", exc)


# ---------------------------------------------------------------------------
# Steps
# ---------------------------------------------------------------------------
class _Ctx:
    def __init__(self, tenant_ids, *, dry_run, max_daily_dms, min_gap_minutes, log, stdout, cfg):
        self.tenant_ids = list(tenant_ids)
        self.dry_run = dry_run
        self.max_daily_dms = max_daily_dms
        self.min_gap_minutes = (
            settings.INSTAGRAM_OUTREACH_MIN_GAP_MINUTES if min_gap_minutes is None else max(0, int(min_gap_minutes))
        )
        self.log = log or logger.info
        self.stdout = stdout
        self.cfg = cfg if cfg is not None else load_outreach_config()
        self.business_name = (self.cfg.get("business_name") or "").strip() or DEFAULT_BUSINESS_NAME


def _first_queued_item(lead) -> Tuple[Optional[int], Optional[dict]]:
    for idx, item in enumerate(lead.conversation_history or []):
        if item.get("direction") == "out" and item.get("delivery_status") == "QUEUED":
            return idx, item
    return None, None


def _record_queued_failure(lead, error: str, kind: str, now) -> bool:
    """Back off a failed queued message; returns True when it was given up (marked FAILED)."""
    lead.refresh_from_db()
    history = list(lead.conversation_history or [])
    idx, item = _first_queued_item(lead)
    if item is None:
        return False
    item["last_error"] = (error or "")[:200]
    gave_up = False
    if kind in ("unknown", "recipient_blocked"):  # message-level failure (sender errors are gated by its cooldown)
        item["attempts"] = int(item.get("attempts") or 0) + 1
        if kind == "recipient_blocked" or item["attempts"] >= QUEUED_MAX_ATTEMPTS:
            item["delivery_status"] = "FAILED"
            gave_up = True
        else:
            item["retry_after"] = (now + timedelta(minutes=QUEUED_RETRY_MINUTES * item["attempts"])).isoformat()
    history[idx] = item
    lead.conversation_history = history
    fields = ["conversation_history", "updated_at"]
    if gave_up and _first_queued_item(lead)[1] is None:
        lead.status = "REPLIED" if lead.has_replied else ("DM_SENT" if lead.initial_dm_at else "FAILED")
        fields.append("status")
    lead.save(update_fields=fields)
    return gave_up


def _send_next_queued(ctx: _Ctx, now) -> Optional[Dict[str, Any]]:
    queued = InstagramEngineLead.objects.filter(tenant_id__in=ctx.tenant_ids, status="QUEUED").order_by("updated_at")[:25]
    for lead in queued:
        _, item = _first_queued_item(lead)
        if item is None:
            continue
        retry_after = item.get("retry_after")
        if retry_after and not ctx.dry_run and datetime.fromisoformat(retry_after) > now:
            continue
        sender = resolve_queued_sender(lead, item)
        if not _sender_can_act(sender, now, dry_run=ctx.dry_run):
            continue

        ctx.log(f"⚡ [اولویت ۱ - صف ارسال مستقیم]: ارسال پیام صف برای @{lead.username} توسط @{sender.username}...")
        try:
            if not ctx.dry_run:
                from apps.discovery.proxy_service import prepare_sender_proxy_before_dm
                prepare_sender_proxy_before_dm(sender, stdout=ctx.stdout)
                get_sender_client(sender)  # cached; process_queued_outbox_message reuses it
            res = process_queued_outbox_message(lead, client_cache=_CLIENTS, dry_run=ctx.dry_run, sender=sender)
        except Exception as exc:
            res = {"success": False, "error": str(exc)}

        if res.get("success"):
            if not ctx.dry_run:
                persist_sender_session(sender)
            ctx.log(f"   ✅ پیام صف با موفقیت برای @{lead.username} توسط @{sender.username} ارسال شد.")
            return {"action": "queued_sent", "sent": True, "lead": lead.username, "sender": sender.username}

        error = res.get("error") or ""
        kind = classify_send_error(error)
        ctx.log(f"   ❌ خطا در ارسال پیام صف برای @{lead.username} ({kind}): {error[:120]}")
        apply_sender_error(sender, kind, error, ctx.log)
        gave_up = _record_queued_failure(lead, error, kind, now)
        if gave_up:
            ctx.log(f"   🛑 پیام صف @{lead.username} بعد از چند تلاش ناموفق به وضعیت FAILED رفت.")
        return {"action": "queued_failed", "sent": False, "lead": lead.username, "sender": sender.username, "kind": kind}
    return None


def _pitch_for(cfg: dict, sender) -> Tuple[str, str]:
    """(consultant name, pitch template) a cold DM of `sender` speaks with.

    The sender's customer (tenant) profile first (one consultant + pitch per customer, edited by the customer on
    the outreach page); then the legacy per-username pitch of data/sarv_outreach_config.json; then the defaults.
    `sender` is the account (or, as before, its username).
    """
    sender_user = sender if isinstance(sender, str) else sender.username
    profile = {}
    tenant = None if isinstance(sender, str) else getattr(sender, "tenant_id", None)
    if tenant:
        from apps.discovery.ig_accounts import tenant_profile
        profile = tenant_profile(tenant)
    pitches = cfg.get("sender_pitches", {})
    info = (
        pitches.get(sender_user)
        or pitches.get(sender_user.lower())
        or DEFAULT_SENDER_PITCHES.get(sender_user.lower(), {})
    )
    return (profile.get("consultant_name") or info.get("consultant_name", sender_user),
            profile.get("pitch_template") or info.get("template", ""))


def _mark_cold_sent(lead, sender, text: str, *, pace: bool) -> Tuple[float, bool]:
    now = timezone.now()
    delay, is_break = 0.0, False
    with transaction.atomic():
        lead.status = "DM_SENT"
        lead.sender_account = sender.username
        lead.initial_dm_text = text
        lead.initial_dm_at = now
        lead.conversation_history = [{
            "sender": sender.username,
            "direction": "out",
            "role": "initial_outreach",
            "text": text,
            "time": now.isoformat(),
            "delivery_status": "SENT",
        }]
        lead.save(update_fields=["status", "sender_account", "initial_dm_text", "initial_dm_at",
                                 "conversation_history", "updated_at"])
        sender.daily_action_count += 1
        sender.hourly_action_count = (sender.hourly_action_count or 0) + 1
        sender.last_action_at = now
        sender.status_message = "پیام با موفقیت ارسال شد"
        fields = ["daily_action_count", "hourly_action_count", "last_action_at", "status_message"]
        if pace:
            delay, is_break = HumanShiftPlanner.calculate_next_action_delay(
                account_tier=sender.account_tier,
                consecutive_actions=sender.daily_action_count,
                is_priority_reply=False,
            )
            sender.next_outreach_at = now + timedelta(seconds=delay)
            fields.append("next_outreach_at")
        sender.save(update_fields=fields)
    return delay, is_break


def _send_cold_dm(ctx: _Ctx, sender, lead) -> Dict[str, Any]:
    consultant_name, template = _pitch_for(ctx.cfg, sender)
    pitch_text = build_bulletproof_message(
        pitch_template=template, lead=lead, consultant_name=consultant_name, business_name=ctx.business_name,
    )
    limit_daily, _ = get_effective_sender_limits(sender, fallback_daily=ctx.max_daily_dms)
    ctx.log("-" * 65)
    ctx.log(
        f"👉 [مشتری]: @{lead.username} (PK: {lead.ig_user_pk}) | امتیاز اولویت: {lead.outreach_score}/45 "
        f"({lead.comment_intent_type}) | نیاز: {(lead.comment_text or '')[:40]}"
    )
    ctx.log(f"   📌 اکانت فرستنده: @{sender.username} (مشاور: {consultant_name}) | "
            f"کوتای امروز: {sender.daily_action_count + 1}/{limit_daily}")

    if ctx.dry_run:
        ctx.log(f"   [DRY-RUN] شبیه‌سازی ارسال به @{lead.username}: {pitch_text[:120]}...")
        _mark_cold_sent(lead, sender, pitch_text, pace=False)
        return {"action": "cold_sent", "sent": True, "dry_run": True, "lead": lead.username, "sender": sender.username}

    try:
        from apps.discovery.proxy_service import prepare_sender_proxy_before_dm
        prepare_sender_proxy_before_dm(sender, stdout=ctx.stdout)
        cl = get_sender_client(sender)

        ctx.log(f"   [۱/۳] 🔍 بررسی ایمنی اکانت مقصد @{lead.username} توسط گاردریل...")
        qualify = qualify_recipient_profile(cl, lead.username, target_pk=lead.ig_user_pk)
        if "is_private" in qualify and qualify["is_private"] != lead.is_private:
            lead.is_private = qualify["is_private"]
            lead.save(update_fields=["is_private", "updated_at"])
        if not qualify["eligible"]:
            ctx.log(f"   ⏭️ [سپر ضد بلاک]: ارسال به @{lead.username} لغو شد: {qualify['reason']}")
            lead.status = "SKIPPED"
            lead.comment_text = (lead.comment_text or "") + f"\n[گاردریل ضد بلاک]: {qualify['reason']}"
            lead.save(update_fields=["status", "comment_text", "updated_at"])
            return {"action": "skipped", "sent": False, "lead": lead.username}

        target_pk = qualify.get("target_pk") or (
            int(lead.ig_user_pk) if lead.ig_user_pk and str(lead.ig_user_pk).isdigit() else None
        )
        if not target_pk:
            ctx.log(f"   ⚠️ عدم امکان تعیین PK معتبر برای @{lead.username}. رد شد.")
            lead.status = "SKIPPED"
            lead.save(update_fields=["status", "updated_at"])
            return {"action": "skipped", "sent": False, "lead": lead.username}

        ctx.log(f"   [۲/۳] 🌐 پیمایش ارگانیک (فید، جستجو، پروفایل، استوری) برای @{lead.username}...")
        journey = execute_organic_journey(cl, lead.username, target_pk=target_pk)
        final_pk = journey.get("target_pk") or target_pk

        ctx.log(f"   [۳/۳] ✉️ ارسال دایرکت از طرف {consultant_name} (@{sender.username})...")
        cl.direct_send(pitch_text, user_ids=[final_pk])
    except Exception as exc:
        error = str(exc)
        kind = classify_send_error(error)
        ctx.log(f"   ❌ خطا در ارسال دایرکت به @{lead.username} ({kind}): {error[:120]}")
        apply_sender_error(sender, kind, error, ctx.log)
        if kind == "recipient_blocked":
            lead.status = "SKIPPED"
            lead.comment_text = (lead.comment_text or "") + "\n[محدودیت کاربر]: مسدود بودن دایرکت اکانت‌های ناشناس"
            lead.save(update_fields=["status", "comment_text", "updated_at"])
        elif kind == "unknown":
            lead.status = "FAILED"
            lead.save(update_fields=["status", "updated_at"])
        # sender-level errors: the lead stays NEW so another sender can deliver it
        return {"action": "cold_failed", "sent": False, "lead": lead.username, "sender": sender.username, "kind": kind}

    delay, is_break = _mark_cold_sent(lead, sender, pitch_text, pace=True)
    persist_sender_session(sender)
    try:
        from apps.discovery.live_logger import report_live_activity
        report_live_activity(
            account_username=sender.username,
            action_type="SEND_DM",
            title=f"ارسال دایرکت به @{lead.username}",
            description=f"دایرکت معرفی با موفقیت برای @{lead.username} توسط @{sender.username} ارسال شد.",
            level="SUCCESS",
            update_worker="outreach",
            worker_active=True,
        )
    except Exception:
        pass
    pause = "☕ استراحت طبیعی" if is_break else "⏳ وقفه ایمنی"
    ctx.log(f"   ✅ دایرکت به @{lead.username} ارسال شد. {pause}: دایرکت بعدی @{sender.username} "
            f"حداقل {delay / 60.0:.0f} دقیقه دیگر.")
    return {"action": "cold_sent", "sent": True, "lead": lead.username, "sender": sender.username,
            "next_outreach_at": sender.next_outreach_at}


def _send_next_cold(ctx: _Ctx, now) -> Dict[str, Any]:
    senders = get_available_senders(ctx.tenant_ids, fallback_daily=ctx.max_daily_dms, log=ctx.log, refresh=False)
    if not senders:
        return {"action": "no_senders", "sent": False}
    if not ctx.dry_run:
        gap_until = global_gap_until(ctx.tenant_ids, ctx.min_gap_minutes)
        if gap_until and gap_until > now:
            return {"action": "waiting", "sent": False, "reason": "global_gap", "next_at": gap_until}
        ready = [s for s in senders if _cold_ready(s, now)]
        if not ready:
            next_at = min((s.next_outreach_at for s in senders if s.next_outreach_at), default=None)
            return {"action": "waiting", "sent": False, "reason": "sender_pacing", "next_at": next_at}
    else:
        ready = senders
    sender = ready[0]

    leads = select_outreach_candidates(ctx.tenant_ids, MAX_LEADS_PER_STEP)
    if not leads:
        return {"action": "idle", "sent": False, "reason": "no_new_leads"}
    for lead in leads[:MAX_LEADS_PER_STEP]:
        lead.refresh_from_db(fields=["status"])
        if lead.status != "NEW":
            continue
        result = _send_cold_dm(ctx, sender, lead)
        if result["action"] != "skipped":
            return result
    return {"action": "idle", "sent": False, "reason": "candidates_skipped"}


def run_outreach_step(
    tenant_ids: List[str],
    *,
    dry_run: bool = False,
    ignore_shift: bool = False,
    max_daily_dms: Optional[int] = None,
    min_gap_minutes: Optional[int] = None,
    log: Optional[Callable[[str], None]] = None,
    stdout=None,
    cfg: Optional[dict] = None,
) -> Dict[str, Any]:
    """Send at most one message (queued first, then the best cold lead) and return what happened."""
    ctx = _Ctx(tenant_ids, dry_run=dry_run, max_daily_dms=max_daily_dms, min_gap_minutes=min_gap_minutes,
               log=log, stdout=stdout, cfg=cfg)
    if not ignore_shift:
        active, reason, wait_sec = HumanShiftPlanner.is_shift_active()
        if not active:
            return {"action": "paused", "sent": False, "reason": reason, "wait_seconds": wait_sec}
    with dispatch_lock(ctx.tenant_ids) as acquired:
        if not acquired:
            return {"action": "busy", "sent": False, "reason": "another dispatch step is running"}
        now = timezone.now()
        refresh_sender_states(ctx.tenant_ids, ctx.log)
        queued = _send_next_queued(ctx, now)
        if queued:
            return queued
        return _send_next_cold(ctx, now)


def reply_sync_allowed(ignore_shift: bool = False) -> Tuple[bool, str]:
    """Inbox checks follow the same shift hours as sending: a person does not refresh DMs all night."""
    if ignore_shift:
        return True, ""
    active, reason, _ = HumanShiftPlanner.is_shift_active()
    return active, reason


def next_reply_sync_delay() -> float:
    """Seconds until the next inbox check: random within INSTAGRAM_REPLY_SYNC_MIN/MAX_SECONDS (5-10 min)."""
    lo = settings.INSTAGRAM_REPLY_SYNC_MIN_SECONDS
    return random.uniform(lo, max(lo, settings.INSTAGRAM_REPLY_SYNC_MAX_SECONDS))


def run_outreach_batch(tenant_ids: List[str], *, max_sends: int = 2, **step_kwargs) -> Dict[str, Any]:
    """Run steps until max_sends messages went out or a step had nothing to send."""
    if step_kwargs.get("cfg") is None:
        step_kwargs["cfg"] = load_outreach_config()
    sent, steps = 0, []
    for _ in range(max(1, int(max_sends))):
        result = run_outreach_step(tenant_ids, **step_kwargs)
        steps.append(result)
        if not result.get("sent"):
            break
        sent += 1
    return {"sent": sent, "last": steps[-1], "steps": steps}
