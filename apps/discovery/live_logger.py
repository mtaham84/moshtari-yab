"""
Live Activity Logger and Real-time Heartbeat Engine for Instagram Operations.

Tracks real-time operations across Instagram scrapers, outreach workers, and cockpits:
- Active scraping (queries, targets, posts, inspection progress)
- Active video / reel / story watching simulation with durations
- Comment reading and buyer lead detection
- Direct message sending and organic warmup actions
"""

from __future__ import annotations

import logging
import random
import time
from datetime import timedelta
from typing import Any, Dict, Optional

from django.utils import timezone

logger = logging.getLogger("instagram_live")

# Cache / rate limiter for DB pruning so we don't prune on every single log write
_LAST_PRUNE_TIME: float = 0.0
PRUNE_INTERVAL_SEC: float = 300.0  # prune once every 5 minutes
MAX_KEPT_LOGS: int = 800


def report_live_activity(
    account_username: str,
    action_type: str,
    title: str,
    description: str = "",
    media_type: str = "NONE",
    media_code: str = "",
    media_url: str = "",
    duration_seconds: float = 0.0,
    level: str = "INFO",
    metadata: Optional[Dict[str, Any]] = None,
    update_worker: str = "",
    worker_active: bool = True,
) -> Optional[int]:
    """
    Safely records a live Instagram activity item and updates worker heartbeat.
    Exception-safe: Will never crash or disrupt worker operation.
    """
    global _LAST_PRUNE_TIME
    try:
        from apps.discovery.models import InstagramLiveActivity, InstagramLiveWorkerState

        meta = metadata or {}

        # 1. Update Worker State if requested
        if update_worker in ("scraper", "outreach", "cockpit"):
            try:
                InstagramLiveWorkerState.objects.update_or_create(
                    worker_key=update_worker,
                    defaults={
                        "is_active": worker_active,
                        "current_account": account_username or "",
                        "action_type": action_type,
                        "details": title or description or "",
                        "media_type": media_type or "NONE",
                        "media_code": media_code or "",
                        "media_url": media_url or "",
                        "duration_seconds": float(duration_seconds or 0.0),
                        "metadata": meta,
                    },
                )
            except Exception as w_exc:
                logger.debug("Failed updating live worker state: %s", w_exc)

        # 2. Create Activity Log Entry
        entry = InstagramLiveActivity.objects.create(
            account_username=account_username or "system",
            action_type=action_type,
            title=title[:255],
            description=description or "",
            media_type=media_type or "NONE",
            media_code=media_code or "",
            media_url=media_url or "",
            duration_seconds=float(duration_seconds or 0.0),
            level=level,
            metadata=meta,
        )

        # 3. Log to console / stdout
        log_msg = f"[IG-LIVE-{action_type}] @{account_username}: {title}"
        if duration_seconds > 0:
            log_msg += f" ({duration_seconds}s)"
        if media_url:
            log_msg += f" -> {media_url}"
        if level == "ERROR":
            logger.error(log_msg)
        elif level == "WARNING":
            logger.warning(log_msg)
        else:
            logger.info(log_msg)

        # 4. Periodic prune to keep table lightweight
        now_ts = time.time()
        if now_ts - _LAST_PRUNE_TIME > PRUNE_INTERVAL_SEC:
            _LAST_PRUNE_TIME = now_ts
            try:
                cutoff_id = (
                    InstagramLiveActivity.objects.order_by("-id")
                    .values_list("id", flat=True)[MAX_KEPT_LOGS : MAX_KEPT_LOGS + 1]
                )
                if cutoff_id:
                    InstagramLiveActivity.objects.filter(id__lt=cutoff_id[0]).delete()
            except Exception:
                pass

        return entry.id
    except Exception as exc:
        logger.debug("report_live_activity skipped due to error: %s", exc)
        return None


def get_live_monitoring_snapshot(
    limit: int = 60,
    since_id: Optional[int] = None,
) -> Dict[str, Any]:
    """
    Returns full current snapshot for the dashboard live monitor:
    - scraper state (is_active, current_account, action, media_url, etc.)
    - outreach state (is_active, current_account, action, etc.)
    - recent logs (newest first or filtered by since_id)
    """
    try:
        from apps.discovery.models import InstagramLiveActivity, InstagramLiveWorkerState

        now = timezone.now()
        active_threshold = now - timedelta(seconds=90)  # Considered active if pinged in last 90s

        # 1. Fetch Worker States
        worker_states = {w.worker_key: w for w in InstagramLiveWorkerState.objects.all()}

        def _format_worker_state(key: str, default_label: str) -> Dict[str, Any]:
            w = worker_states.get(key)
            if not w:
                return {
                    "is_active": False,
                    "account": "—",
                    "action_type": "IDLE",
                    "action_label": "آماده‌باش / متوقف",
                    "details": "ورکر هنوز پیامی ارسال نکرده است.",
                    "media_type": "NONE",
                    "media_code": "",
                    "media_url": "",
                    "duration_seconds": 0.0,
                    "updated_at": "",
                    "seconds_ago": None,
                }

            is_recently_updated = w.updated_at >= active_threshold
            effective_active = bool(w.is_active and is_recently_updated)
            seconds_ago = int((now - w.updated_at).total_seconds()) if w.updated_at else 999

            # Action label in Persian
            labels = {
                "SCRAPING": "در حال اسکرپ و جستجوی پست‌ها",
                "SCRAPE": "در حال جستجو و استخراج",
                "WATCHING_VIDEO": "در حال تماشای ویدیو / ریلز",
                "WATCH_VIDEO": "در حال تماشای ریلز",
                "WATCHING_STORY": "در حال مشاهده استوری مخاطب",
                "WATCH_STORY": "در حال مشاهده استوری",
                "FETCHING_COMMENTS": "در حال واکشی و تحلیل کامنت‌ها",
                "COMMENTS": "آنالیز کامنت‌ها",
                "SENDING_DM": "در حال ارسال پیام دایرکت",
                "SEND_DM": "ارسال دایرکت",
                "WARMUP": "گشت‌وگذار طبیعی در فید و اکسپلور",
                "TYPING": "شبیه‌سازی تایپ طبیعی پیام",
                "COOLDOWN": "وقفه محافظتی و استراحت (Anti-ban)",
                "IDLE": "در انتظار چرخه بعدی",
            }
            action_label = labels.get(w.action_type, w.action_type or default_label)

            return {
                "is_active": effective_active,
                "account": w.current_account or "—",
                "action_type": w.action_type,
                "action_label": action_label,
                "details": w.details or "",
                "media_type": w.media_type or "NONE",
                "media_code": w.media_code or "",
                "media_url": w.media_url or "",
                "duration_seconds": round(w.duration_seconds, 1),
                "updated_at": w.updated_at.strftime("%H:%M:%S") if w.updated_at else "",
                "seconds_ago": seconds_ago,
            }

        scraper_info = _format_worker_state("scraper", "آماده‌باش اسکرپر")
        outreach_info = _format_worker_state("outreach", "آماده‌باش پیام‌رسان")

        # 2. Fetch Recent Activities
        qs = InstagramLiveActivity.objects.all()
        if since_id:
            qs = qs.filter(id__gt=since_id)

        raw_logs = list(qs.order_by("-id")[:limit])

        formatted_logs = []
        for item in raw_logs:
            # Color and icon config
            badge_map = {
                "WATCH_VIDEO": {"color": "#a855f7", "bg": "rgba(168, 85, 247, 0.15)", "icon": "🎬", "label": "تماشای ویدیو/ریلز"},
                "WATCH_STORY": {"color": "#06b6d4", "bg": "rgba(6, 182, 212, 0.15)", "icon": "👁️", "label": "تماشای استوری"},
                "SCRAPE": {"color": "#3b82f6", "bg": "rgba(59, 130, 246, 0.15)", "icon": "🔍", "label": "اسکرپ هشتگ"},
                "COMMENTS": {"color": "#eab308", "bg": "rgba(234, 179, 8, 0.15)", "icon": "💬", "label": "کامنت‌ها"},
                "LEAD_FOUND": {"color": "#10b981", "bg": "rgba(16, 185, 129, 0.2)", "icon": "🎯", "label": "کشف خریدار"},
                "SEND_DM": {"color": "#6366f1", "bg": "rgba(99, 102, 241, 0.15)", "icon": "✉️", "label": "ارسال دایرکت"},
                "WARMUP": {"color": "#ec4899", "bg": "rgba(236, 72, 153, 0.15)", "icon": "☕", "label": "گشت‌وگذار ارگانیک"},
                "COOLDOWN": {"color": "#94a3b8", "bg": "rgba(148, 163, 184, 0.15)", "icon": "⏳", "label": "وقفه ضدبن"},
                "ERROR": {"color": "#ef4444", "bg": "rgba(239, 68, 68, 0.2)", "icon": "❌", "label": "خطا"},
                "SYSTEM": {"color": "#64748b", "bg": "rgba(100, 116, 139, 0.15)", "icon": "⚙️", "label": "سیستمی"},
            }
            badge = badge_map.get(item.action_type, {
                "color": "#94a3b8", "bg": "rgba(148, 163, 184, 0.15)", "icon": "📌", "label": item.get_action_type_display()
            })

            formatted_logs.append({
                "id": item.id,
                "time": item.time_fa,
                "account": item.account_username,
                "action_type": item.action_type,
                "badge": badge,
                "title": item.title,
                "description": item.description,
                "media_type": item.media_type,
                "media_code": item.media_code,
                "media_url": item.media_url,
                "duration_seconds": item.duration_seconds,
                "level": item.level,
            })

        return {
            "success": True,
            "scraper_state": scraper_info,
            "outreach_state": outreach_info,
            "logs": formatted_logs,
            "latest_id": formatted_logs[0]["id"] if formatted_logs else (since_id or 0),
            "server_time": now.astimezone().strftime("%H:%M:%S"),
        }
    except Exception as exc:
        logger.exception("Error preparing live monitoring snapshot: %s", exc)
        return {
            "success": False,
            "error": str(exc),
            "scraper_state": {"is_active": False, "action_label": "خطا در اتصال"},
            "outreach_state": {"is_active": False, "action_label": "خطا در اتصال"},
            "logs": [],
        }


def simulate_test_pulse(account_username: str = "") -> Dict[str, Any]:
    """
    Simulates a live watch or scrape pulse on demand so operators can
    instantly test and verify the live stream on the UI.
    """
    from apps.discovery.models import InstagramEngineAccount

    if not account_username:
        acc = InstagramEngineAccount.objects.filter(role="SCRAPER", status="ACTIVE").first()
        if not acc:
            acc = InstagramEngineAccount.objects.first()
        account_username = acc.username if acc else "demo_operator"

    sample_reels = [
        ("C9kL3x9", "https://www.instagram.com/p/C9kL3x9/", "ایمپلنت دیجیتال و لمینت دندان", 4.8),
        ("D1mN4a2", "https://www.instagram.com/p/D1mN4a2/", "جراحی لیفت و زیبایی فک", 6.2),
        ("B8vF6z1", "https://www.instagram.com/p/B8vF6z1/", "معرفی کلینیک تخصصی و رضایت مراجعین", 5.5),
        ("A5tG8q3", "https://www.instagram.com/p/A5tG8q3/", "تخفیف ویژه کامپوزیت اقساطی", 3.9),
    ]
    code, url, topic, duration = random.choice(sample_reels)

    report_live_activity(
        account_username=account_username,
        action_type="WATCH_VIDEO",
        title=f"تماشای ریلز اینستاگرام: {topic}",
        description=f"اکانت @{account_username} در حال تماشای ویدیوی ریلز پست {url} است ({duration} ثانیه شبیه‌سازی رفتار کاربر طبیعی).",
        media_type="REEL",
        media_code=code,
        media_url=url,
        duration_seconds=duration,
        level="INFO",
        update_worker="scraper",
        worker_active=True,
    )

    report_live_activity(
        account_username=account_username,
        action_type="COMMENTS",
        title=f"واکشی و بررسی کامنت‌های ریلز {code}",
        description=f"استخراج ۲۸ کامنت از پست {url} جهت ارزیابی قصد خرید و کامنت‌های متقاضی قیمت...",
        media_type="REEL",
        media_code=code,
        media_url=url,
        level="INFO",
        update_worker="scraper",
        worker_active=True,
    )

    return {
        "success": True,
        "message": f"پالس تستی شبیه‌سازی تماشای ریلز برای @{account_username} با موفقیت ثبت شد.",
        "media_code": code,
        "duration": duration,
    }
