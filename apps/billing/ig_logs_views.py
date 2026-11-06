"""
Instagram Live Logs & Activity Radar (/manage/instagram/logs/)
Real-time operational dashboard for monitoring Instagram scraper events:
- Latest discovered leads with scoring and intent
- Full details of the latest scraper execution (query, comments, target post, status)
- Stream of recently crawled posts with exact crawl timestamps
- Per-account 24-hour telemetry: searches, latest query, latest post, latest comments
"""
from __future__ import annotations

from datetime import timedelta
from typing import Any
import zoneinfo

from django.db.models import Q, Sum
from django.http import JsonResponse
from django.shortcuts import render
from django.utils import timezone as dj_timezone

from apps.billing.manage_views import manage_access_required
from apps.core.http import wants_json
from apps.core.jalali import format_jalali_date, to_persian_digits
from apps.discovery.models import (
    InstagramCrawledPost,
    InstagramCrawlExecution,
    InstagramEngineAccount,
    InstagramEngineLead,
)

TEHRAN_TZ = zoneinfo.ZoneInfo("Asia/Tehran")


def _format_relative_time_fa(dt: Any, now: Any | None = None) -> str:
    """Returns concise Persian human-readable relative time (e.g. 'همین الان', '۲ دقیقه پیش')."""
    if not dt:
        return "—"
    now = now or dj_timezone.now()
    diff = (now - dt).total_seconds()
    if diff < 0:
        return "در آینده"
    if diff < 60:
        return "چند ثانیه پیش"
    mins = int(diff / 60)
    if mins < 60:
        return f"{to_persian_digits(str(mins))} دقیقه پیش"
    hours = int(mins / 60)
    if hours < 24:
        return f"{to_persian_digits(str(hours))} ساعت پیش"
    days = int(hours / 24)
    if days == 1:
        return "دیروز"
    if days < 30:
        return f"{to_persian_digits(str(days))} روز پیش"
    months = int(days / 30)
    return f"{to_persian_digits(str(months))} ماه پیش"


def _format_exact_jalali_tehran(dt: Any) -> str:
    """Formats exact datetime in Tehran timezone with Jalali date and HH:MM:SS."""
    if not dt:
        return "—"
    dt_tehran = dt.astimezone(TEHRAN_TZ)
    date_part = format_jalali_date(dt_tehran.date(), persian_digits=True)
    time_part = to_persian_digits(f"{dt_tehran.hour:02d}:{dt_tehran.minute:02d}:{dt_tehran.second:02d}")
    return f"{date_part} ساعت {time_part}"


@manage_access_required
def instagram_logs_view(request):
    """
    Dedicated view for Instagram real-time logs, scraper executions,
    crawled posts, and per-account telemetry.
    """
    now_utc = dj_timezone.now()
    one_day_ago = now_utc - timedelta(hours=24)

    # ---------------------------------------------------------
    # 1. Latest Scraping Run (Crawl Execution)
    # ---------------------------------------------------------
    latest_exec_obj = InstagramCrawlExecution.objects.all().order_by("-crawled_at").first()
    latest_execution: dict[str, Any] | None = None

    if latest_exec_obj:
        # Determine associated post details
        top_code = latest_exec_obj.top_post_code or ""
        top_url = latest_exec_obj.top_post_url or ""
        post_obj = None

        if top_code:
            post_obj = InstagramCrawledPost.objects.filter(media_code=top_code).first()
        elif top_url and "/p/" in top_url:
            code_candidate = top_url.split("/p/")[-1].strip("/").split("/")[0]
            if code_candidate:
                post_obj = InstagramCrawledPost.objects.filter(media_code=code_candidate).first()

        # If not linked directly, find latest crawled post by this account around that execution
        if not post_obj and latest_exec_obj.account_username:
            post_obj = (
                InstagramCrawledPost.objects.filter(account_username=latest_exec_obj.account_username)
                .order_by("-crawled_at")
                .first()
            )

        if post_obj and not top_code:
            top_code = post_obj.media_code
            top_url = post_obj.post_url

        comments_count = latest_exec_obj.comments_found
        if comments_count == 0 and post_obj:
            comments_count = post_obj.comment_count or len(post_obj.comments or [])

        latest_execution = {
            "id": latest_exec_obj.id,
            "query_text": latest_exec_obj.query_text,
            "target_type": latest_exec_obj.target_type,
            "target_type_display": latest_exec_obj.get_target_type_display(),
            "status": latest_exec_obj.status,
            "status_display": latest_exec_obj.get_status_display(),
            "account_username": latest_exec_obj.account_username,
            "crawled_at_exact": _format_exact_jalali_tehran(latest_exec_obj.crawled_at),
            "crawled_at_relative": _format_relative_time_fa(latest_exec_obj.crawled_at, now_utc),
            "posts_inspected": latest_exec_obj.posts_inspected,
            "qualifying_posts": latest_exec_obj.qualifying_posts,
            "leads_found": latest_exec_obj.leads_found,
            "comments_found": comments_count,
            "top_post_code": top_code,
            "top_post_url": top_url,
            "top_post_caption": (post_obj.caption[:250] + "...") if (post_obj and len(post_obj.caption or "") > 250) else (post_obj.caption if post_obj else ""),
            "top_post_taken_at": _format_exact_jalali_tehran(post_obj.posted_at) if (post_obj and post_obj.posted_at) else "—",
            "message": latest_exec_obj.message,
        }

    # ---------------------------------------------------------
    # 2. Per-Account Scraper Telemetry
    # ---------------------------------------------------------
    scraper_accounts = list(InstagramEngineAccount.objects.filter(role="SCRAPER").order_by("-id"))
    scrapers_telemetry = []

    for acc in scraper_accounts:
        exec_qs = InstagramCrawlExecution.objects.filter(
            Q(account=acc) | Q(account_username=acc.username)
        )
        searches_24h = exec_qs.filter(crawled_at__gte=one_day_ago).count()
        searches_total = exec_qs.count()

        last_exec = exec_qs.order_by("-crawled_at").first()
        last_post = (
            InstagramCrawledPost.objects.filter(account_username=acc.username)
            .order_by("-crawled_at")
            .first()
        )

        last_post_code = last_post.media_code if last_post else (last_exec.top_post_code if last_exec else "")
        last_post_url = last_post.post_url if last_post else (last_exec.top_post_url if last_exec else "")
        last_comments_found = (
            last_post.comment_count
            if last_post and last_post.comment_count > 0
            else (last_exec.comments_found if last_exec else 0)
        )

        leads_24h = (
            exec_qs.filter(crawled_at__gte=one_day_ago).aggregate(s=Sum("leads_found"))["s"] or 0
        )
        leads_total = exec_qs.aggregate(s=Sum("leads_found"))["s"] or 0

        scrapers_telemetry.append({
            "id": acc.id,
            "username": acc.username,
            "status": acc.status,
            "status_display": acc.get_status_display(),
            "proxy_url": acc.clean_proxy or "اتصال مستقیم (بدون پروکسی)",
            "proxy_port": acc.proxy_config.local_port if acc.proxy_config else "—",
            "searches_24h": searches_24h,
            "searches_total": searches_total,
            "last_crawl_exact": _format_exact_jalali_tehran(last_exec.crawled_at) if last_exec else "—",
            "last_crawl_relative": _format_relative_time_fa(last_exec.crawled_at, now_utc) if last_exec else "—",
            "last_query": last_exec.query_text if last_exec else "—",
            "last_query_type": last_exec.get_target_type_display() if last_exec else "—",
            "last_post_code": last_post_code,
            "last_post_url": last_post_url,
            "last_comments_count": last_comments_found,
            "last_crawl_leads": last_exec.leads_found if last_exec else 0,
            "leads_24h": leads_24h,
            "leads_total": leads_total,
        })

    # ---------------------------------------------------------
    # 3. Stream of Recently Crawled Posts
    # ---------------------------------------------------------
    crawled_posts_qs = InstagramCrawledPost.objects.all().order_by("-crawled_at")[:30]
    recent_posts = []

    for p in crawled_posts_qs:
        recent_posts.append({
            "id": p.id,
            "media_code": p.media_code or p.media_id,
            "post_url": p.post_url,
            "account_username": p.account_username,
            "comment_count": p.comment_count or len(p.comments or []),
            "comments_cached": p.comments_cached,
            "crawled_at_exact": _format_exact_jalali_tehran(p.crawled_at),
            "crawled_at_relative": _format_relative_time_fa(p.crawled_at, now_utc),
            "posted_at_exact": _format_exact_jalali_tehran(p.posted_at) if p.posted_at else "—",
            "caption_snippet": (p.caption[:180] + "...") if len(p.caption or "") > 180 else (p.caption or "—"),
        })

    # ---------------------------------------------------------
    # 4. Stream of Recently Found Leads
    # ---------------------------------------------------------
    leads_qs = InstagramEngineLead.objects.all().order_by("-created_at")[:50]
    recent_leads = []

    for l in leads_qs:
        recent_leads.append({
            "id": l.id,
            "username": l.username,
            "full_name": l.full_name or "—",
            "profile_url": l.profile_url or f"https://www.instagram.com/{l.username}/",
            "comment_text": l.comment_text,
            "comment_intent_type": l.comment_intent_type,
            "intent_label": l.get_comment_intent_type_display(),
            "outreach_score": l.outreach_score,
            "success_probability": l.success_probability,
            "status": l.status,
            "status_display": l.get_status_display(),
            "is_private": l.is_private,
            "source_post_url": l.source_post_url or "—",
            "source_page_username": l.source_page_username or "—",
            "comment_created_at_exact": _format_exact_jalali_tehran(l.comment_created_at) if l.comment_created_at else "—",
            "created_at_exact": _format_exact_jalali_tehran(l.created_at),
            "created_at_relative": _format_relative_time_fa(l.created_at, now_utc),
        })

    # ---------------------------------------------------------
    # Overall Telemetry Summary
    # ---------------------------------------------------------
    summary = {
        "searches_24h": InstagramCrawlExecution.objects.filter(crawled_at__gte=one_day_ago).count(),
        "leads_24h": InstagramEngineLead.objects.filter(created_at__gte=one_day_ago).count(),
        "leads_total": InstagramEngineLead.objects.count(),
        "posts_crawled_24h": InstagramCrawledPost.objects.filter(crawled_at__gte=one_day_ago).count(),
        "posts_crawled_total": InstagramCrawledPost.objects.count(),
        "scrapers_total": len(scraper_accounts),
        "scrapers_active": sum(1 for a in scraper_accounts if a.status == "ACTIVE"),
        "server_time_jalali": _format_exact_jalali_tehran(now_utc),
    }

    # JSON response support for live polling / AJAX updates
    if wants_json(request) or request.GET.get("format") == "json":
        return JsonResponse({
            "success": True,
            "summary": summary,
            "latest_execution": latest_execution,
            "scrapers": scrapers_telemetry,
            "recent_posts": recent_posts,
            "recent_leads": recent_leads,
        })

    context = {
        "nav": "instagram_logs",
        "summary": summary,
        "latest_execution": latest_execution,
        "scrapers": scrapers_telemetry,
        "recent_posts": recent_posts,
        "recent_leads": recent_leads,
    }
    return render(request, "ops/instagram_logs.html", context)
