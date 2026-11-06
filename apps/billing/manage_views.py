import json
import logging
from collections import defaultdict
import socket
from datetime import timedelta
from decimal import Decimal
from enum import Enum
from typing import Any
from urllib.parse import urlparse

try:
    from curl_cffi import requests
except ImportError:
    import requests

from django.conf import settings
from django.contrib import messages
from django.db.models import Count, Q, Sum
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone as dj_timezone
from django.views.decorators.http import require_POST, require_GET

import zoneinfo

from apps.businesses.models import Business
from apps.billing.models import WalletTransaction, BillingSettings
from apps.discovery.models import (
    InstagramEngineAccount, InstagramEngineLead, InstagramCrawlExecution,
    InstagramSourceStat, TwitterEngineAccount, InstagramCrawledPost,
    XCrawledPost, XSearchQuery, ProxyConfig
)
from apps.core.jalali import format_jalali_date, format_jalali_datetime
from apps.core.http import wants_json
from apps.core.permissions import operator_required
from apps.discovery.constants import DM_DELIVERED_STATUSES, HIGH_INTENT_THRESHOLD
from apps.discovery.channel_config_automation import get_monitored_channels
from apps.discovery.ig_accounts import canonical_tenant, default_proxy_url, tenant_key, tenant_keys_for_business
from apps.discovery.device_emulator import connect_saved_session

logger = logging.getLogger("management")


# Kept as a name because ig_accounts_views and many decorators use it.
manage_access_required = operator_required


def _test_proxy_connection(proxy_url: str, timeout: float = 4.0) -> tuple[bool, str]:
    """Tests TCP connection to proxy host and port."""
    if not proxy_url or not proxy_url.strip():
        return True, "بدون پروکسی (اتصال مستقیم)"
    try:
        parsed = urlparse(proxy_url)
        host = parsed.hostname
        port = parsed.port or (1080 if "socks" in (parsed.scheme or "") else 8080)
        if not host:
            return False, "فرمت پروکسی نامعتبر است"
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(timeout)
        result = sock.connect_ex((host, port))
        sock.close()
        if result == 0:
            return True, f"پروکسی {host}:{port} متصل و در دسترس است"
        else:
            return False, f"خطای اتصال به پروکسی {host}:{port} (کد خطا: {result})"
    except Exception as exc:
        return False, f"خطای تست پروکسی: {exc}"


@manage_access_required
def manage_dashboard(request):
    """
    Main Management Dashboard at /manage/
    Displays Scrapers, Senders & Prioritized Leads Queue, and Financial Consumption.
    """
    tehran_tz = zoneinfo.ZoneInfo("Asia/Tehran")
    now_utc = dj_timezone.now()
    now_tehran = now_utc.astimezone(tehran_tz)
    one_day_ago = now_utc - timedelta(hours=24)
    thirty_days_ago = now_utc - timedelta(days=30)

    # Same operating window as the anti-ban manager (INSTAGRAM_WINDOW_START_HOUR / _END_HOUR, Tehran time)
    from apps.discovery.anti_ban import (INSTAGRAM_WINDOW_END_HOUR, INSTAGRAM_WINDOW_START_HOUR, in_operating_window,
                                         next_window_start)
    from apps.core.jalali import to_persian_digits

    is_ig_night_sleep = not in_operating_window(now_tehran.hour)
    ig_window_label = to_persian_digits(f"{INSTAGRAM_WINDOW_START_HOUR:02d}:00 تا {INSTAGRAM_WINDOW_END_HOUR:02d}:00")
    if is_ig_night_sleep:
        minutes_to_wake = max(1, int((next_window_start(now_tehran) - now_tehran).total_seconds() / 60))
        hours_to_wake = minutes_to_wake // 60
        rem_mins = minutes_to_wake % 60
        wake_label = to_persian_digits(f"{INSTAGRAM_WINDOW_START_HOUR:02d}:00")
        ig_sleep_desc = (f"{hours_to_wake} ساعت و {rem_mins} دقیقه تا {wake_label}" if hours_to_wake > 0
                         else f"{rem_mins} دقیقه تا {wake_label}")
    else:
        ig_sleep_desc = ""

    # 1. Scraper Accounts
    scrapers = list(InstagramEngineAccount.objects.filter(role="SCRAPER").order_by("-id"))
    scrapers_total = len(scrapers)
    scrapers_active = sum(1 for a in scrapers if a.status == "ACTIVE")
    scrapers_cooldown = sum(1 for a in scrapers if a.status == "COOLDOWN")
    scrapers_challenged = sum(1 for a in scrapers if a.status == "CHALLENGED")
    scrapers_banned = sum(1 for a in scrapers if a.status == "BANNED")

    for acc in scrapers:
        execs_qs = InstagramCrawlExecution.objects.filter(Q(account=acc) | Q(account_username=acc.username))
        acc_searches_total = execs_qs.count()
        acc_searches_24h = execs_qs.filter(crawled_at__gte=one_day_ago).count()
        acc_posts_total = execs_qs.aggregate(s=Sum("posts_inspected"))["s"] or 0
        acc_posts_24h = execs_qs.filter(crawled_at__gte=one_day_ago).aggregate(s=Sum("posts_inspected"))["s"] or 0
        acc_leads_total = execs_qs.aggregate(s=Sum("leads_found"))["s"] or 0
        acc_leads_24h = execs_qs.filter(crawled_at__gte=one_day_ago).aggregate(s=Sum("leads_found"))["s"] or 0
        acc.searches_total = acc_searches_total
        acc.searches_24h = acc_searches_24h
        acc.posts_total = acc_posts_total
        acc.posts_24h = acc_posts_24h
        acc.leads_total = acc_leads_total
        acc.leads_24h = acc_leads_24h
        acc.conv_rate_total = round((acc_leads_total / acc_posts_total * 100.0), 2) if acc_posts_total > 0 else 0.0
        acc.conv_rate_24h = round((acc_leads_24h / acc_posts_24h * 100.0), 2) if acc_posts_24h > 0 else 0.0

        if is_ig_night_sleep:
            acc.live_status_key = "NIGHT_SLEEP"
            acc.live_status_label = f"خواب شبانه (تا {wake_label})"
            acc.live_status_badge = "night_sleep"
            acc.live_status_desc = ig_sleep_desc
        elif acc.cooldown_until and acc.cooldown_until > now_utc:
            mins_cd = max(1, int((acc.cooldown_until - now_utc).total_seconds() / 60))
            acc.live_status_key = "COOLDOWN"
            acc.live_status_label = "استراحت موقت"
            acc.live_status_badge = "cooldown"
            acc.live_status_desc = f"{mins_cd} دقیقه باقی‌مانده"
        elif acc.status == "ACTIVE":
            acc.live_status_key = "ACTIVE"
            acc.live_status_label = "فعال و در حال اسکرپ"
            acc.live_status_badge = "active"
            acc.live_status_desc = "آماده دریافت و ارزیابی پست‌ها"
        elif acc.status == "CHALLENGED":
            acc.live_status_key = "CHALLENGED"
            acc.live_status_label = "نیازمند سشن / چالش"
            acc.live_status_badge = "challenged"
            acc.live_status_desc = "نیازمند لاگین مجدد"
        elif acc.status == "BANNED":
            acc.live_status_key = "BANNED"
            acc.live_status_label = "مسدود شده"
            acc.live_status_badge = "banned"
            acc.live_status_desc = "اکانت بلاک شده است"
        else:
            acc.live_status_key = acc.status
            acc.live_status_label = acc.get_status_display()
            acc.live_status_badge = "inactive"
            acc.live_status_desc = ""

    # 2. Sender Accounts & Leads
    senders = InstagramEngineAccount.objects.filter(role="SENDER").order_by("-id")
    senders_total = senders.count()
    senders_active = senders.filter(status="ACTIVE").count()

    all_ig_accounts = list(senders) + list(scrapers)
    # Device profiles are assigned when a client is built (device_emulator.apply_device_profile_to_client) and
    # backfilled by `manage.py instagram_backfill --device-profiles`; a GET of the dashboard writes nothing.

    # Prioritized Leads: Ordered strictly by intent_score DESC, created_at ASC
    leads = InstagramEngineLead.objects.all().order_by("-intent_score", "created_at")[:60]
    total_leads = InstagramEngineLead.objects.count()
    dms_sent_total = InstagramEngineLead.objects.filter(status__in=DM_DELIVERED_STATUSES).count()
    dms_replied_total = InstagramEngineLead.objects.filter(has_replied=True).count()
    high_intent_leads = InstagramEngineLead.objects.filter(intent_score__gte=HIGH_INTENT_THRESHOLD).count()

    # 3. Settings & Exchange Rate
    billing_settings = BillingSettings.get()
    usd_rate = billing_settings.usd_to_toman
    markup_percent = billing_settings.markup_percent

    # Scraper latest activity tracking
    latest_scraper = InstagramEngineAccount.objects.filter(role="SCRAPER", last_action_at__isnull=False).order_by("-last_action_at").first()
    latest_scraper_action_at = latest_scraper.last_action_at if latest_scraper else None
    from apps.discovery.models import Opportunity
    latest_lead = Opportunity.objects.order_by("-created_at").first()
    latest_lead_at = latest_lead.created_at if latest_lead else None

    # 4. Multi-Platform Leads Matrix for all 3 Tenants
    businesses = Business.objects.select_related("wallet", "user").all().order_by("id")

    tenant_matrix = []
    matrix_totals = {
        "instagram": 0,
        "telegram": 0,
        "x": 0,
        "total": 0,
        "high_intent": 0,
        "dms_sent": 0,
    }

    # One grouped query per table instead of ~10 queries per business (ADM-03).
    opp_stats = {
        row["business_id"]: row
        for row in Opportunity.objects.order_by().values("business_id").annotate(
            instagram=Count("id", filter=Q(source_platform="instagram")),
            telegram=Count("id", filter=Q(source_platform="telegram")),
            x=Count("id", filter=Q(source_platform="x")),
            total=Count("id"),
            high_intent=Count("id", filter=Q(ai_analysis__intent_score__gte=HIGH_INTENT_THRESHOLD)),
        )
    }
    lead_stats: dict[str, dict] = defaultdict(lambda: {"leads": 0, "dms": 0})
    for row in InstagramEngineLead.objects.order_by().values("tenant_id").annotate(
            leads=Count("id"), dms=Count("id", filter=Q(status__in=DM_DELIVERED_STATUSES))):
        bucket = lead_stats[canonical_tenant(row["tenant_id"])]   # legacy aliases count for their business
        bucket["leads"] += row["leads"]
        bucket["dms"] += row["dms"]
    usage_stats = {
        row["wallet__business_id"]: row
        for row in WalletTransaction.objects.filter(kind="USAGE").order_by().values("wallet__business_id").annotate(
            spent=Sum("amount_toman"), cost_usd=Sum("cost_usd"), calls=Sum("calls"))
    }
    senders_by_tenant: dict[str, list] = defaultdict(list)
    for acc in InstagramEngineAccount.objects.filter(role="SENDER").order_by("id"):
        senders_by_tenant[canonical_tenant(acc.tenant_id)].append(acc)
    empty_opp = {"instagram": 0, "telegram": 0, "x": 0, "total": 0, "high_intent": 0}

    for b in businesses:
        o = opp_stats.get(b.id, empty_opp)
        b_dms = lead_stats.get(tenant_key(b), {}).get("dms", 0)

        tenant_matrix.append({
            "business": b,
            "instagram": o["instagram"],
            "telegram": o["telegram"],
            "x": o["x"],
            "total": o["total"],
            "high_intent": o["high_intent"],
            "dms_sent": b_dms,
        })
        matrix_totals["instagram"] += o["instagram"]
        matrix_totals["telegram"] += o["telegram"]
        matrix_totals["x"] += o["x"]
        matrix_totals["total"] += o["total"]
        matrix_totals["high_intent"] += o["high_intent"]
        matrix_totals["dms_sent"] += b_dms

    # 5. Financial & Credit Consumption per Business / Account
    financial_rows = []
    total_balance_all = Decimal("0")
    total_spent_all = Decimal("0")
    total_cost_usd_all = Decimal("0")
    total_raw_toman_all = Decimal("0")
    total_calls_all = 0

    for b in businesses:
        wallet = getattr(b, "wallet", None)
        balance = wallet.balance_toman if wallet else Decimal("0")
        total_balance_all += balance

        # Aggregated consumption from transactions
        tx_usage = usage_stats.get(b.id) or {}
        spent = abs(tx_usage.get("spent") or Decimal("0"))
        cost_usd = tx_usage.get("cost_usd") or Decimal("0")
        calls = tx_usage.get("calls") or 0

        # Raw cost at *today's* rate, for reference only. What the tenant
        # actually paid is ``spent`` (sum of the debited transactions), which
        # used the rate/markup in force at the time of each call.
        raw_toman = (cost_usd * usd_rate).quantize(Decimal("1"))

        total_spent_all += spent
        total_cost_usd_all += cost_usd
        total_raw_toman_all += raw_toman
        total_calls_all += calls

        # Business associated Instagram leads & senders
        b_leads = lead_stats.get(tenant_key(b), {"leads": 0, "dms": 0})
        b_leads_count = b_leads["leads"]
        b_dms_sent = b_leads["dms"]
        b_senders = senders_by_tenant.get(tenant_key(b), [])

        financial_rows.append({
            "business": b,
            "balance": balance,
            "spent": spent,
            "cost_usd": cost_usd,
            "raw_toman": raw_toman,
            "calls": calls,
            "leads_count": b_leads_count,
            "dms_sent": b_dms_sent,
            "senders": b_senders,
            "has_credit": balance > 0,
        })

    # 6. Twitter / X Scraper Accounts & Lead Funnel
    try:
        twitter_scrapers = list(TwitterEngineAccount.objects.all().order_by("-id"))
        twitter_scrapers_total = len(twitter_scrapers)
        twitter_scrapers_active = sum(1 for a in twitter_scrapers if a.status == "ACTIVE" and a.is_active)
        twitter_scrapers_cooldown = sum(1 for a in twitter_scrapers if a.status == "COOLDOWN")
        twitter_scrapers_challenged = sum(1 for a in twitter_scrapers if a.status == "CHALLENGED")
        twitter_scrapers_banned = sum(1 for a in twitter_scrapers if a.status == "BANNED")
    except Exception as exc:
        logger.warning("Could not query TwitterEngineAccount: %s", exc)
        twitter_scrapers = []
        twitter_scrapers_total = twitter_scrapers_active = twitter_scrapers_cooldown = twitter_scrapers_challenged = twitter_scrapers_banned = 0

    # 7. Twitter Search Runs / Crawl Logs
    twitter_crawl_runs = []
    try:
        from apps.discovery.x_activity import _crawler_schema, _exists, _all
        cs = _crawler_schema()
        if _exists(cs, "x_search_runs"):
            twitter_crawl_runs = _all(f"""
                SELECT run_id, ran_at, product_id, query, fetched, new_posts
                FROM {cs}.x_search_runs
                ORDER BY ran_at DESC
                LIMIT 60
            """)
    except Exception as exc:
        logger.warning("Could not query x_search_runs: %s", exc)

    # 8. Proxy Pool & Configs
    try:
        proxy_configs = ProxyConfig.objects.all().order_by("local_port")
        proxy_configs_total = proxy_configs.count()
        proxy_configs_active = proxy_configs.filter(status="ACTIVE").count()
    except Exception:
        proxy_configs = []
        proxy_configs_total = proxy_configs_active = 0

    # 9. Lead Conversion Funnel & Comprehensive Intelligence Metrics
    try:
        # A. Twitter / X Platform KPIs & Funnel
        x_posts_total = XCrawledPost.objects.count()
        x_posts_24h = XCrawledPost.objects.filter(crawled_at__gte=one_day_ago).count()
        x_opps_qs = Opportunity.objects.filter(source_platform="x")
        x_leads_total = x_opps_qs.exclude(status="REJECTED").count()
        x_leads_24h = x_opps_qs.filter(created_at__gte=one_day_ago).exclude(status="REJECTED").count()
        x_searches_total = sum(acc.total_action_count for acc in twitter_scrapers) if twitter_scrapers else 0
        x_searches_24h = sum(acc.daily_action_count for acc in twitter_scrapers) if twitter_scrapers else 0
        x_conversion_rate_total = round((x_leads_total / x_posts_total * 100.0), 2) if x_posts_total > 0 else 0.0
        x_conversion_rate_24h = round((x_leads_24h / x_posts_24h * 100.0), 2) if x_posts_24h > 0 else 0.0
        x_tweets_inspected = x_posts_total
        x_tweets_approved = x_leads_total
        x_leads_count = x_leads_total
        x_tweets_rejected = max(0, x_tweets_inspected - x_tweets_approved)

        # Twitter Scraper Accounts Funnel & Live Status Enrichment
        for acc in twitter_scrapers:
            acc.searches_total = acc.total_action_count
            acc.searches_24h = acc.daily_action_count
            acc.posts_total = acc.posts_found_count
            acc.posts_24h = int(acc.posts_found_count * (acc.daily_action_count / max(1, acc.total_action_count))) if acc.total_action_count > 0 else 0
            acc.leads_total = round(x_leads_total * (acc.posts_found_count / max(1, x_posts_total))) if x_posts_total > 0 else 0
            acc.leads_24h = round(x_leads_24h * (acc.daily_action_count / max(1, x_searches_24h))) if x_searches_24h > 0 else 0
            acc.conv_rate_total = round((acc.leads_total / acc.posts_total * 100.0), 2) if acc.posts_total > 0 else 0.0
            acc.conv_rate_24h = round((acc.leads_24h / acc.posts_24h * 100.0), 2) if acc.posts_24h > 0 else 0.0
            # posts_24h / leads_* are proportional shares of the totals (X
            # results don't record which account found them) -> shown with "≈".
            acc.metrics_estimated = True

            if acc.cooldown_until and acc.cooldown_until > now_utc:
                mins_cd = max(1, int((acc.cooldown_until - now_utc).total_seconds() / 60))
                acc.live_status_key = "COOLDOWN"
                acc.live_status_label = "استراحت موقت (Rate Limit)"
                acc.live_status_badge = "cooldown"
                acc.live_status_desc = f"{mins_cd} دقیقه باقی‌مانده"
            elif not acc.is_active or acc.status == "DISABLED":
                acc.live_status_key = "DISABLED"
                acc.live_status_label = "غیرفعال دستی"
                acc.live_status_badge = "inactive"
                acc.live_status_desc = "خارج از چرخه چرخش"
            elif acc.status == "CHALLENGED":
                acc.live_status_key = "CHALLENGED"
                acc.live_status_label = "نیازمند سشن معتبر"
                acc.live_status_badge = "challenged"
                acc.live_status_desc = "کوکی auth_token منقضی یا نامعتبر"
            elif acc.status == "BANNED":
                acc.live_status_key = "BANNED"
                acc.live_status_label = "مسدود شده"
                acc.live_status_badge = "banned"
                acc.live_status_desc = "اکانت توسط X مسدود شده"
            elif acc.status == "ACTIVE":
                acc.live_status_key = "ACTIVE"
                acc.live_status_label = "فعال و آماده جستجو"
                acc.live_status_badge = "active"
                acc.live_status_desc = "متصل به پروکسی و در چرخه جستجو"
            else:
                acc.live_status_key = acc.status
                acc.live_status_label = acc.get_status_display()
                acc.live_status_badge = "inactive"
                acc.live_status_desc = ""

        # B. Instagram Platform KPIs & Funnel
        ig_searches_total = InstagramCrawlExecution.objects.count()
        ig_searches_24h = InstagramCrawlExecution.objects.filter(crawled_at__gte=one_day_ago).count()
        ig_posts_total = InstagramCrawledPost.objects.count()
        ig_posts_24h = InstagramCrawledPost.objects.filter(crawled_at__gte=one_day_ago).count()
        ig_opps_qs = Opportunity.objects.filter(source_platform="instagram")
        ig_leads_total = ig_opps_qs.exclude(status="REJECTED").count()
        ig_leads_24h = ig_opps_qs.filter(created_at__gte=one_day_ago).exclude(status="REJECTED").count()
        ig_conversion_rate_total = round((ig_leads_total / ig_posts_total * 100.0), 2) if ig_posts_total > 0 else 0.0
        ig_conversion_rate_24h = round((ig_leads_24h / ig_posts_24h * 100.0), 2) if ig_posts_24h > 0 else 0.0

        ig_posts_approved = InstagramCrawledPost.objects.filter(comment_count__gte=settings.INSTAGRAM_MIN_COMMENTS, posted_at__gte=thirty_days_ago).count()
        ig_posts_rejected = max(0, ig_posts_total - ig_posts_approved)
        ig_comments_total = InstagramCrawledPost.objects.aggregate(s=Sum("comment_count"))["s"] or 0
        # Saved leads (one per qualifying comment), not "approved comments":
        # comment evaluations themselves are not stored.
        ig_comments_approved = InstagramEngineLead.objects.count()
        ig_comments_rejected = max(0, ig_comments_total - ig_comments_approved)

        # Approved Instagram Posts
        approved_ig_posts = list(
            InstagramCrawledPost.objects.filter(comment_count__gte=settings.INSTAGRAM_MIN_COMMENTS, posted_at__gte=thirty_days_ago)
            .order_by("-crawled_at")[:40]
        )
        for p in approved_ig_posts:
            p.post_url = f"https://www.instagram.com/p/{p.media_code}/" if p.media_code else ""
            p.posted_jalali = format_jalali_date(p.posted_at.astimezone(tehran_tz).date(), persian_digits=True) if p.posted_at else "—"
            p.crawled_jalali = format_jalali_date(p.crawled_at.astimezone(tehran_tz).date(), persian_digits=True) if p.crawled_at else "—"

        # C. Overall System Funnel (Twitter + Instagram)
        funnel_total_searches = x_searches_total + ig_searches_total
        funnel_24h_searches = x_searches_24h + ig_searches_24h
        funnel_total_posts = x_posts_total + ig_posts_total
        funnel_24h_posts = x_posts_24h + ig_posts_24h
        funnel_total_leads = x_leads_total + ig_leads_total
        funnel_24h_leads = x_leads_24h + ig_leads_24h
        funnel_total_conv_rate = round((funnel_total_leads / funnel_total_posts * 100.0), 2) if funnel_total_posts > 0 else 0.0
        funnel_24h_conv_rate = round((funnel_24h_leads / funnel_24h_posts * 100.0), 2) if funnel_24h_posts > 0 else 0.0

        # D. Verified X Leads Dataset (Dedicated Tab)
        verified_x_leads = []
        approved_x_tweets = []
        x_opps_verified = list(
            x_opps_qs.exclude(status="REJECTED")
            .select_related("customer", "ai_analysis", "category", "business")
            .order_by("-created_at")[:100]
        )
        for opp in x_opps_verified:
            c = opp.customer
            handle = (c.source_username or "").lstrip("@") if c else ""
            profile_url = c.source_profile_url or (f"https://x.com/{handle}" if handle else "")
            if not handle and isinstance(opp.trace_metadata, dict):
                cand = opp.trace_metadata.get("candidate", {})
                handle = (cand.get("username") or "").lstrip("@")
                if not profile_url:
                    profile_url = cand.get("profile_url") or (f"https://x.com/{handle}" if handle else "")

            msg_id = opp.source_message_id
            if handle and msg_id:
                tweet_url = f"https://x.com/{handle}/status/{msg_id}"
            elif msg_id:
                tweet_url = f"https://x.com/i/web/status/{msg_id}"
            elif handle:
                tweet_url = f"https://x.com/{handle}"
            else:
                tweet_url = ""

            posted_dt = opp.source_posted_at or opp.source_message_timestamp
            posted_jalali = format_jalali_datetime(posted_dt.astimezone(tehran_tz), persian_digits=True) if posted_dt else "—"
            created_jalali = format_jalali_datetime(opp.created_at.astimezone(tehran_tz), persian_digits=True) if opp.created_at else "—"

            ai = getattr(opp, "ai_analysis", None)
            intent_score = ai.intent_score if ai else (opp.category_confidence or 0.8)
            intent_percent = int(round(intent_score * 100))
            need_desc = ai.need if ai and ai.need else (opp.category_name_snapshot or "")

            cat_name = opp.category_name_snapshot or (opp.category.name if opp.category else "عمومی")

            lead_dict = {
                "id": opp.id,
                "author_handle": handle,
                "author_name": (c.name if c else "") or handle,
                "profile_url": profile_url,
                "tweet_id": msg_id,
                "tweet_url": tweet_url,
                "raw_text": opp.source_raw_message,
                "business_name": opp.business.name if opp.business else "عمومی",
                "category_name": cat_name,
                "source_query": opp.source_query or "—",
                "intent_score": intent_score,
                "intent_percent": intent_percent,
                "need_desc": need_desc,
                "posted_jalali": posted_jalali,
                "created_jalali": created_jalali,
                "status": opp.get_status_display(),
            }
            verified_x_leads.append(lead_dict)

            # Compatibility for legacy audit view
            opp.created_jalali = created_jalali
            opp.author_handle_clean = handle
            opp.tweet_url = tweet_url
            approved_x_tweets.append(opp)

        # E. Query Intelligence (Bayesian Thompson Sampling) & Instagram Queries List
        all_stats_qs = InstagramSourceStat.objects.select_related("business").all()
        query_items = []
        ig_queries_list = []
        for s in all_stats_qs:
            total_ev = s.alpha + s.beta
            score = round((s.alpha / total_ev) * 100.0, 1) if total_ev > 0 else 50.0
            delta = round(score - 50.0, 1)
            type_title = (
                "کوئری X (توییتر)" if s.source_type == "x_query"
                else "هشتگ اینستاگرام" if s.source_type == "hashtag"
                else "کلیدواژه اینستاگرام" if s.source_type == "keyword"
                else "صفحه هدف اینستاگرام" if s.source_type == "page"
                else s.source_type
            )
            platform_name = "X (توییتر)" if s.source_type == "x_query" else "اینستاگرام"
            last_run_jalali = format_jalali_date(s.last_run_at.astimezone(tehran_tz).date(), persian_digits=True) if s.last_run_at else "—"
            q_info = {
                "id": s.id,
                "label": s.label,
                "business_name": s.business.name if s.business else "عمومی",
                "source_type": s.source_type,
                "type_title": type_title,
                "platform": platform_name,
                "alpha": round(s.alpha, 2),
                "beta": round(s.beta, 2),
                "requests": s.requests,
                "leads": s.leads,
                "score": score,
                "delta": delta,
                "last_run_jalali": last_run_jalali,
            }
            query_items.append(q_info)
            if s.source_type in ("hashtag", "keyword", "page"):
                ig_queries_list.append(q_info)

        queries_top_rated = sorted(query_items, key=lambda x: (-x["score"], -x["leads"]))
        queries_top_gainers = sorted([q for q in query_items if q["delta"] > 0], key=lambda x: -x["delta"])
        queries_top_losers = sorted([q for q in query_items if q["delta"] < 0], key=lambda x: x["delta"])
        ig_queries_list = sorted(ig_queries_list, key=lambda x: (-x["score"], -x["leads"]))

        x_active_queries_count = XSearchQuery.objects.filter(state="active").count()
        ig_active_queries_count = len(ig_queries_list)

    except Exception as exc:
        logger.warning("Could not calculate crawler audit and funnel metrics: %s", exc)
        x_posts_total = x_posts_24h = x_leads_total = x_leads_24h = 0
        x_searches_total = x_searches_24h = 0
        x_conversion_rate_total = x_conversion_rate_24h = 0.0
        x_tweets_inspected = x_tweets_approved = x_leads_count = x_tweets_rejected = 0
        ig_searches_total = ig_searches_24h = ig_posts_total = ig_posts_24h = 0
        ig_leads_total = ig_leads_24h = 0
        ig_conversion_rate_total = ig_conversion_rate_24h = 0.0
        funnel_total_searches = funnel_24h_searches = funnel_total_posts = funnel_24h_posts = 0
        funnel_total_leads = funnel_24h_leads = 0
        funnel_total_conv_rate = funnel_24h_conv_rate = 0.0
        ig_posts_approved = ig_posts_rejected = ig_comments_total = ig_comments_approved = ig_comments_rejected = 0
        approved_ig_posts = []
        approved_x_tweets = []
        verified_x_leads = []
        ig_queries_list = []
        queries_top_rated = queries_top_gainers = queries_top_losers = []
        x_active_queries_count = 0
        ig_active_queries_count = 0

    # Business-specific tenant filtering for outreach tab
    business_id_filter = request.GET.get("outreach_biz", "").strip()
    businesses_for_outreach = Business.objects.select_related("user").order_by("id")
    selected_outreach_biz = None
    if business_id_filter and business_id_filter.isdigit():
        selected_outreach_biz = Business.objects.filter(id=int(business_id_filter)).first()
        if selected_outreach_biz:
            b_tenants = tenant_keys_for_business(selected_outreach_biz)
            senders = senders.filter(tenant_id__in=b_tenants)
            leads = InstagramEngineLead.objects.filter(tenant_id__in=b_tenants).order_by("-intent_score", "created_at")[:60]
            total_leads = InstagramEngineLead.objects.filter(tenant_id__in=b_tenants).count()
            dms_sent_total = InstagramEngineLead.objects.filter(tenant_id__in=b_tenants, status__in=DM_DELIVERED_STATUSES).count()
            senders_total = senders.count()
            senders_active = senders.filter(status="ACTIVE").count()

    # One consultant name / pitch per customer (tenant), as the outreach engine resolves it
    from apps.discovery.ig_accounts import tenant_pitch
    pitch_by_tenant = {}
    accounts_with_pitches = []
    for s in senders:
        if s.tenant_id not in pitch_by_tenant:
            pitch_by_tenant[s.tenant_id] = tenant_pitch(s.tenant_id)
        p = pitch_by_tenant[s.tenant_id]
        accounts_with_pitches.append({
            "obj": s,
            "consultant_name": p["consultant_name"] or s.username,
            "template": p["pitch_template"],
        })

    default_pitch = ""
    default_consultant = ""
    if accounts_with_pitches:
        default_pitch = accounts_with_pitches[0]["template"]
        default_consultant = accounts_with_pitches[0]["consultant_name"]
    # No hard-coded industry pitch here: per-tenant pitches (and the old
    # tenant_4 fallback) come from ig_accounts.tenant_pitch().

    outreach_replied_qs = InstagramEngineLead.objects.filter(has_replied=True)
    if selected_outreach_biz:
        b_tenants = tenant_keys_for_business(selected_outreach_biz)
        outreach_replied_qs = outreach_replied_qs.filter(tenant_id__in=b_tenants)
    outreach_replied_leads = outreach_replied_qs.order_by("-reply_at")[:50]
    outreach_replied_count = outreach_replied_qs.count()

    context = {
        "nav": "manage",
        "all_ig_accounts": all_ig_accounts,
        "ig_accounts_total": len(all_ig_accounts),
        "proxy_configs": proxy_configs,
        "proxy_configs_total": proxy_configs_total,
        "proxy_configs_active": proxy_configs_active,
        "proxy_channels": get_monitored_channels(),
        "scrapers": scrapers,
        "scrapers_total": scrapers_total,
        "scrapers_active": scrapers_active,
        "scrapers_cooldown": scrapers_cooldown,
        "scrapers_challenged": scrapers_challenged,
        "scrapers_banned": scrapers_banned,
        "senders": senders,
        "senders_total": senders_total,
        "senders_active": senders_active,
        "businesses_for_outreach": businesses_for_outreach,
        "selected_outreach_biz": selected_outreach_biz,
        "selected_business_id": business_id_filter,
        "accounts_with_pitches": accounts_with_pitches,
        "default_pitch": default_pitch,
        "default_consultant": default_consultant,
        "outreach_replied_leads": outreach_replied_leads,
        "outreach_replied_count": outreach_replied_count,
        "twitter_scrapers": twitter_scrapers,
        "twitter_scrapers_total": twitter_scrapers_total,
        "twitter_scrapers_active": twitter_scrapers_active,
        "twitter_scrapers_cooldown": twitter_scrapers_cooldown,
        "twitter_scrapers_challenged": twitter_scrapers_challenged,
        "twitter_scrapers_banned": twitter_scrapers_banned,
        "twitter_crawl_runs": twitter_crawl_runs,
        "leads": leads,
        "total_leads": total_leads,
        "dms_sent_total": dms_sent_total,
        "dms_replied_total": dms_replied_total,
        "high_intent_leads": high_intent_leads,
        "billing_settings": billing_settings,
        "usd_rate": usd_rate,
        "markup_percent": markup_percent,
        "latest_scraper_action_at": latest_scraper_action_at,
        "latest_lead_at": latest_lead_at,
        "tenant_matrix": tenant_matrix,
        "matrix_totals": matrix_totals,
        "financial_rows": financial_rows,
        "total_balance_all": total_balance_all,
        "total_spent_all": total_spent_all,
        "total_cost_usd_all": total_cost_usd_all,
        "total_raw_toman_all": total_raw_toman_all,
        "total_calls_all": total_calls_all,
        "businesses": businesses,
        "crawl_executions": InstagramCrawlExecution.objects.all().select_related("business", "account").order_by("-crawled_at")[:60],
        "instagram_source_stats": InstagramSourceStat.objects.select_related("business").order_by("-last_run_at", "business_id")[:150],
        "ig_posts_total": ig_posts_total,
        "ig_posts_approved": ig_posts_approved,
        "ig_posts_rejected": ig_posts_rejected,
        "ig_comments_total": ig_comments_total,
        "ig_comments_approved": ig_comments_approved,
        "ig_comments_rejected": ig_comments_rejected,
        "approved_ig_posts": approved_ig_posts,
        "x_tweets_inspected": x_tweets_inspected,
        "x_tweets_approved": x_tweets_approved,
        "x_leads_count": x_leads_count,
        "x_tweets_rejected": x_tweets_rejected,
        "approved_x_tweets": approved_x_tweets,
        "queries_top_rated": queries_top_rated,
        "queries_top_gainers": queries_top_gainers,
        "queries_top_losers": queries_top_losers,
        "verified_x_leads": verified_x_leads,
        "ig_queries_list": ig_queries_list,
        "x_active_queries_count": x_active_queries_count,
        "ig_min_comments": settings.INSTAGRAM_MIN_COMMENTS,
        "ig_active_queries_count": ig_active_queries_count,
        "is_ig_night_sleep": is_ig_night_sleep,
        "ig_window_label": ig_window_label,
        "ig_sleep_desc": ig_sleep_desc,
        "funnel_total_searches": funnel_total_searches,
        "funnel_24h_searches": funnel_24h_searches,
        "funnel_total_posts": funnel_total_posts,
        "funnel_24h_posts": funnel_24h_posts,
        "funnel_total_leads": funnel_total_leads,
        "funnel_24h_leads": funnel_24h_leads,
        "funnel_total_conv_rate": funnel_total_conv_rate,
        "funnel_24h_conv_rate": funnel_24h_conv_rate,
        "x_searches_total": x_searches_total,
        "x_searches_24h": x_searches_24h,
        "x_posts_total": x_posts_total,
        "x_posts_24h": x_posts_24h,
        "x_leads_total": x_leads_total,
        "x_leads_24h": x_leads_24h,
        "x_conversion_rate_total": x_conversion_rate_total,
        "x_conversion_rate_24h": x_conversion_rate_24h,
        "ig_searches_total": ig_searches_total,
        "ig_searches_24h": ig_searches_24h,
        "ig_posts_24h": ig_posts_24h,
        "ig_leads_total": ig_leads_total,
        "ig_leads_24h": ig_leads_24h,
        "ig_conversion_rate_total": ig_conversion_rate_total,
        "ig_conversion_rate_24h": ig_conversion_rate_24h,
        "outreach_dms_list": InstagramEngineLead.objects.filter(status__in=DM_DELIVERED_STATUSES).order_by("-updated_at")[:60],
        "outreach_pending_count": InstagramEngineLead.objects.filter(status="NEW").count(),
        "outreach_failed_count": InstagramEngineLead.objects.filter(status="FAILED").count(),
        "now": dj_timezone.now(),
    }
    return render(request, "ops/manage_dashboard.html", context)


@manage_access_required
@require_POST
def manage_exchange_rate_update(request):
    """Updates live USD to Toman exchange rate and tenant markup percentage."""
    usd_val = request.POST.get("usd_to_toman", "").strip()
    markup_val = request.POST.get("markup_percent", "").strip()

    s = BillingSettings.get()
    try:
        if usd_val:
            s.usd_to_toman = Decimal(usd_val.replace(",", "").replace("،", ""))
        if markup_val:
            s.markup_percent = Decimal(markup_val.replace("%", "").strip())
        s.save()
        messages.success(request, f"نرخ دلار ({s.usd_to_toman:,.0f} تومان) و کارمزد مستأجرین ({s.markup_percent}٪) با موفقیت به‌روزرسانی شد.")
    except Exception as exc:
        messages.error(request, f"خطا در به‌روزرسانی تنظیمات مالی: {exc}")

    return redirect("manage:dashboard")


# ---------------------------------------------------------------------------
# Instagram accounts: the old dashboard endpoints, now thin wrappers over apps.discovery.ig_accounts (the same
# service as /manage/instagram/accounts/), keeping their JSON replies for the dashboard scripts.
# ---------------------------------------------------------------------------
def _ig_actor(request) -> str:
    return request.user.get_username()


def _ig_proxy_choice(raw):
    """(mode, url, config) of a proxy typed in the old forms: empty or xray's shared ports = the system's choice,
    another xray port = that system config, anything else = the account's own proxy."""
    from apps.discovery import ig_accounts as svc

    url = svc.normalize_proxy_url(raw) if (raw or "").strip() else ""
    if not url:
        return "system", "", None
    port = svc.xray_port(url)
    if port is None:
        return "custom", url, None
    cfg = None if port in svc.RESERVED_PORTS else ProxyConfig.objects.filter(local_port=port).first()
    return ("config", "", cfg) if cfg is not None else ("system", "", None)


@manage_access_required
@require_POST
def manage_account_add(request):
    """Adds an Instagram account (SCRAPER or SENDER) and logs it in.

    When Instagram asks for a code the login keeps waiting for it: the reply says requires_code and the code goes
    to the same login (account page /manage/instagram/accounts/<id>/settings/ or accounts/<id>/code/).
    """
    from apps.discovery import ig_accounts as svc

    p = request.POST
    mode, url, cfg = _ig_proxy_choice(p.get("proxy_url"))
    acc, res = svc.add_account(
        username=p.get("username", ""), password=p.get("password", ""), sessionid=p.get("sessionid", ""),
        role=(p.get("role") or "SCRAPER"), tenant_id=(p.get("tenant_id") or "tenant_default").strip(),
        account_tier=p.get("account_tier") or "NEW", daily_limit=p.get("daily_limit"), proxy=mode, proxy_url=url,
        config=cfg, actor=_ig_actor(request), login=True, login_wait=25)
    if acc is not None and acc.role == "SENDER" and (p.get("consultant_name") or "").strip():
        svc.save_tenant_profile(acc.tenant_id, consultant_name=p["consultant_name"], only_empty=True)
    out = res.as_dict()
    out.update(account_id=acc.pk if acc else None,
               username=acc.username if acc else svc.normalize_username(p.get("username")))
    if wants_json(request):
        return JsonResponse(out)
    if acc is not None and res.state in svc.LIVE_STATES:
        messages.warning(request, res.message)
        return redirect(reverse("manage:ig_account_detail", args=[acc.pk]) + "#login")
    (messages.success if res.ok else messages.error)(request, res.message)
    return redirect("manage:dashboard")


@manage_access_required
@require_POST
def manage_account_delete(request, pk):
    """Deletes an Instagram account (its system config becomes free for another account)."""
    from apps.discovery import ig_accounts as svc

    account = get_object_or_404(InstagramEngineAccount, pk=pk)
    username = svc.delete_account(account, actor=_ig_actor(request))
    messages.success(request, f"اکانت @{username} با موفقیت از سیستم حذف گردید.")
    return redirect("manage:dashboard")


@manage_access_required
@require_POST
def manage_account_set_session(request, pk):
    """Connects the account with a sessionid copied from a browser that is logged in (no password, no code)."""
    from apps.discovery import ig_accounts as svc

    account = get_object_or_404(InstagramEngineAccount, pk=pk)
    if not (request.POST.get("sessionid") or "").strip():
        return JsonResponse({"success": False, "message": "لطفاً مقدار کوکی sessionid را وارد نمایید."})
    out = svc.login_with_sessionid(account, request.POST["sessionid"], actor=_ig_actor(request)).as_dict()
    out.setdefault("deleted", False)
    return JsonResponse(out)


def ig_edit_sender(account, data, actor: str = "") -> dict:
    """The old sender editor's fields (username, customer, password, sessionid, consultant name, tier, daily limit,
    proxy, status, clear_cooldown) applied through apps.discovery.ig_accounts; the dashboard's JSON reply."""
    from apps.discovery import ig_accounts as svc

    ok, notes, session_updated = True, [], False
    res = svc.update_settings(account, actor=actor, **{k: data.get(k, "") for k in (
        "username", "tenant_id", "account_tier", "daily_limit", "password")})
    if not res.ok:
        ok = False
        notes.append(res.message)
    elif res.extra.get("changed"):
        notes.append(res.message)

    consultant = (data.get("consultant_name") or "").strip()
    if consultant and consultant != svc.tenant_pitch(account.tenant_id).get("consultant_name"):
        svc.save_tenant_profile(account.tenant_id, consultant_name=consultant)
        notes.append(f"نام مشاور مشتری «{svc.tenant_label(account.tenant_id)}»: {consultant}")

    if "proxy_url" in data and (data.get("proxy_url") or "").strip() != (account.proxy_url or "").strip():
        mode, url, cfg = _ig_proxy_choice(data.get("proxy_url"))
        res = svc.set_proxy(account, mode, url=url, config=cfg, actor=actor)
        ok = ok and res.ok
        if not res.ok or res.extra.get("changed"):
            notes.append(res.message)

    sessionid = (data.get("sessionid") or "").strip()
    if sessionid:
        res = svc.login_with_sessionid(account, sessionid, actor=actor)
        session_updated = res.ok
        ok = ok and res.ok
        notes.append(res.message)

    status = (data.get("status") or "").strip().upper()
    if status and status != account.status and status in dict(InstagramEngineAccount.STATUS_CHOICES) and not sessionid:
        if status == "DISABLED":
            res = svc.pause(account, actor=actor)
        elif status == "ACTIVE":
            res = svc.resume(account, actor=actor)
        else:
            svc.set_state(account, status, "", "وضعیت به‌صورت دستی تغییر کرد", force=True)
            svc.log_event(account, "status_changed", f"وضعیت دستی: {account.get_status_display()}", {"status": status},
                          actor)
            res = svc.Result(True, f"وضعیت: {account.get_status_display()}")
        notes.append(res.message)
    if data.get("clear_cooldown") in ("1", "true", "True", "on"):
        notes.append(svc.clear_cooldown(account, actor=actor).message)

    account.refresh_from_db()
    return {"success": ok, "message": "؛ ".join(n for n in notes if n) or f"اکانت @{account.username} تغییری نکرد.",
            "status": account.status, "session_updated": session_updated}


@manage_access_required
@require_POST
def manage_sender_account_edit(request, pk):
    """The old sender editor of the dashboard (see ig_edit_sender)."""
    account = get_object_or_404(InstagramEngineAccount.objects.select_related("proxy_config"), pk=pk)
    out = ig_edit_sender(account, request.POST, _ig_actor(request))
    if wants_json(request):
        return JsonResponse(out)
    (messages.success if out["success"] else messages.error)(request, out["message"])
    return redirect("/manage/?tab=outreach")


@manage_access_required
@require_POST
def manage_sender_clear_cooldown(request, pk):
    """Ends a protective rest early (an account that needs a code or a login stays as it is)."""
    from apps.discovery import ig_accounts as svc

    account = get_object_or_404(InstagramEngineAccount, pk=pk)
    res = svc.clear_cooldown(account, actor=_ig_actor(request))
    if wants_json(request):
        return JsonResponse({"success": True, "message": res.message, "status": account.status})
    messages.success(request, res.message)
    return redirect("/manage/?tab=outreach")


@manage_access_required
@require_POST
def manage_account_test(request, pk):
    """Connection test on the account's own phone, session and connection (what the workers use). An expired session
    with a saved password starts a login; if Instagram asks for a code, it is typed on the account page."""
    from apps.discovery import ig_accounts as svc

    account = get_object_or_404(InstagramEngineAccount.objects.select_related("proxy_config"), pk=pk)
    actor = _ig_actor(request)
    res = svc.check(account, actor=actor)
    account.refresh_from_db()
    if (not res.ok and account.status == "CHALLENGED" and account.status_reason == "session_expired"
            and account.password and account.password != "session_only"):
        res = svc.start_login(account, actor=actor, wait=25)
    elif res.state in svc.LIVE_STATES:
        res = svc.wait_for_login(account, timeout=25)
    account.refresh_from_db()
    out = res.as_dict()
    out.update(success=res.ok and account.status != "CHALLENGED", status=account.status,
               challenge_required=account.status == "CHALLENGED", challenge_hint=res.hint)
    return JsonResponse(out)


@manage_access_required
@require_POST
def manage_account_submit_code(request, pk):
    """Hands Instagram's code to the login that is waiting for it (the same login that asked for it)."""
    from apps.discovery import ig_accounts as svc

    account = get_object_or_404(InstagramEngineAccount, pk=pk)
    raw_code = ""
    if request.content_type == "application/json":
        try:
            body = json.loads(request.body.decode("utf-8") or "{}")
            raw_code = str(body.get("code", "")).strip() if isinstance(body, dict) else ""
        except ValueError:
            pass
    if not raw_code:
        raw_code = (request.POST.get("code") or "").strip()
    if not raw_code:
        return JsonResponse({"success": False, "message": "لطفاً کد تایید را وارد کنید."})
    res = svc.submit_code(account, raw_code, actor=_ig_actor(request), wait=25)
    account.refresh_from_db()
    out = res.as_dict()
    out["status"] = account.status
    return JsonResponse(out)


@manage_access_required
@require_GET
def manage_leads_export_csv(request):
    """
    Exports all extracted leads as a UTF-8 BOM CSV dataset (streamed, formula-safe).
    """
    from django.db.models import F
    from django.db.models.functions import Coalesce
    from django.http import StreamingHttpResponse

    from apps.discovery.exports import instagram_lead_row, stream_csv_rows

    leads = InstagramEngineLead.objects.all().order_by(
        F("outreach_score").desc(nulls_last=True),
        F("intent_score").desc(nulls_last=True),
        Coalesce("comment_created_at", "created_at").desc(nulls_last=True),
        "-id"
    )
    header = [
        "شناسه عددی کاربر (IG PK)",
        "نام کاربری (Username)",
        "نام کامل (Full Name)",
        "لینک پروفایل (Profile URL)",
        "متن کامنت خریدار (Comment)",
        "لینک پست مبدا (Source Post URL)",
        "خلاصه کپشن پست (Post Caption)",
        "کال‌تو‌اکشن منطبق (Trigger / CTA)",
        "ضریب احتمال خرید (Intent Score)",
        "وضعیت (Status)",
        "مستأجر / فروشگاه (Tenant ID)",
        "زمان کشف (Created At)",
    ]

    def rows():
        for lead in leads.iterator(chunk_size=500):
            r = instagram_lead_row(lead)
            yield [
                r["ig_user_pk"], r["username"], r["full_name"], r["profile_url"], r["comment_text"],
                r["source_post_url"], r["post_caption_snippet"], r["matched_cta"], f"{r['intent_score']:.2f}",
                r["status_display"], r["tenant_id"],
                r["created_at"].strftime("%Y-%m-%d %H:%M:%S") if r["created_at"] else "",
            ]

    response = StreamingHttpResponse(stream_csv_rows(header, rows()), content_type="text/csv; charset=utf-8")
    response["Content-Disposition"] = 'attachment; filename="instagram_leads_dataset.csv"'
    return response


@manage_access_required
@require_GET
def manage_leads_export_json(request):
    """
    Exports all extracted leads as a JSON dataset.
    """
    from apps.discovery.exports import instagram_lead_row

    data = []
    for lead in InstagramEngineLead.objects.all().order_by("-intent_score", "-created_at").iterator(chunk_size=500):
        r = instagram_lead_row(lead)
        r.pop("status_display")
        r["created_at"] = r["created_at"].isoformat() if r["created_at"] else None
        data.append(r)

    response = JsonResponse(data, safe=False, json_dumps_params={"ensure_ascii": False, "indent": 2})
    response["Content-Disposition"] = 'attachment; filename="instagram_leads_dataset.json"'
    return response


# ---------------------------------------------------------------------------
# Twitter / X Scraper Account Management
# ---------------------------------------------------------------------------

TWITTER_SETTINGS_ENDPOINT = "https://x.com/i/api/1.1/account/settings.json"


class XSessionValidationResult(str, Enum):
    VALID = "VALID"
    INVALID = "INVALID"
    FORBIDDEN_UNKNOWN = "FORBIDDEN_UNKNOWN"
    ENDPOINT_ERROR = "ENDPOINT_ERROR"
    RATE_LIMITED = "RATE_LIMITED"
    NETWORK_ERROR = "NETWORK_ERROR"


VALID = XSessionValidationResult.VALID
INVALID = XSessionValidationResult.INVALID
FORBIDDEN_UNKNOWN = XSessionValidationResult.FORBIDDEN_UNKNOWN
ENDPOINT_ERROR = XSessionValidationResult.ENDPOINT_ERROR
RATE_LIMITED = XSessionValidationResult.RATE_LIMITED
NETWORK_ERROR = XSessionValidationResult.NETWORK_ERROR


def _get_or_generate_ct0(ct0: str | None) -> tuple[str, bool]:
    """
    Returns (ct0, was_generated). If ct0 is missing or empty, generates
    fallback ct0 ("a" * 32) matching original Twitter session behavior.
    """
    cleaned = (ct0 or "").strip()
    if cleaned:
        return cleaned, False
    return "a" * 32, True


def _extract_response_ct0(response: Any) -> str | None:
    """Extracts new ct0 cookie from response if set by server."""
    if response is None:
        return None
    cookies = getattr(response, "cookies", None)
    if cookies is not None:
        try:
            if hasattr(cookies, "get"):
                c = cookies.get("ct0")
                if isinstance(c, str) and c.strip():
                    return c.strip()
            if hasattr(cookies, "items"):
                for k, v in cookies.items():
                    if k == "ct0" and isinstance(v, str) and v.strip():
                        return v.strip()
        except Exception:
            pass
    headers = getattr(response, "headers", None)
    if headers and hasattr(headers, "get"):
        sc = headers.get("set-cookie") or headers.get("Set-Cookie") or ""
        if isinstance(sc, str) and sc.strip():
            import re
            m = re.search(r"ct0=([^;,\s]+)", sc)
            if m:
                return m.group(1).strip()
    return None


def _call_twitter_settings_endpoint(
    auth_token: str,
    ct0: str,
    proxy_url: str | None = None,
    timeout: float = 12.0,
) -> tuple[Any, Exception | None]:
    """
    Performs the HTTP GET call to Twitter settings endpoint.
    Returns (response_object_or_None, exception_or_None).
    """
    headers = {
        "Authorization": "Bearer AAAAAAAAAAAAAAAAAAAAANRILgAAAAAAnNwIzUejRCOuH5E6I8xnZz4puTs%3D1Zv7ttfk8LF81IUq16cHjhLTvJu4FA33AGWWjCpTnA",
        "Cookie": f"auth_token={auth_token}; ct0={ct0};",
        "x-csrf-token": ct0,
        "X-Csrf-Token": ct0,
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    }
    proxies = {"http": proxy_url, "https": proxy_url} if proxy_url else None
    try:
        try:
            r = requests.get(
                TWITTER_SETTINGS_ENDPOINT,
                headers=headers,
                proxies=proxies,
                timeout=timeout,
                impersonate="chrome120",
            )
        except TypeError:
            r = requests.get(
                TWITTER_SETTINGS_ENDPOINT,
                headers=headers,
                proxies=proxies,
                timeout=timeout,
            )
        return r, None
    except Exception as exc:
        return None, exc


def _interpret_twitter_response(
    response: Any = None,
    error: Exception | None = None,
    username: str = "",
    *,
    status_code: int | None = None,
    response_text: str = "",
    response_data: dict | None = None,
    was_generated: bool = False,
) -> tuple[XSessionValidationResult, str, str]:
    """
    Interprets HTTP response or exception from Twitter settings endpoint.
    Returns (result_enum, message, confirmed_username).

    Status code mapping:
      200 -> VALID
      401 -> INVALID
      403 -> FORBIDDEN_UNKNOWN (logs snippet; neither confirms nor rejects)
      404 -> ENDPOINT_ERROR
      429 -> RATE_LIMITED
      SSL/Timeout/ConnectionError -> NETWORK_ERROR
      Any other -> ENDPOINT_ERROR (unknown status, keeps previous state)
    """
    if error is not None:
        err_msg = str(error)
        logger.warning("Twitter settings endpoint network error for user %s: %s", username, err_msg)
        return (
            XSessionValidationResult.NETWORK_ERROR,
            f"خطای ارتباط شبکه یا تایم‌اوت ({err_msg})",
            username,
        )

    code = status_code
    text = response_text
    data = response_data

    if response is not None:
        if code is None:
            code = getattr(response, "status_code", None)
        if not text:
            text = getattr(response, "text", "") or ""
        if data is None and hasattr(response, "json"):
            try:
                data = response.json()
            except Exception:
                data = None

    if code == 200:
        uname = username
        if isinstance(data, dict):
            uname = data.get("screen_name") or username
        return (
            XSessionValidationResult.VALID,
            f"اتصال به اکانت @{uname} تایید شد.",
            uname,
        )

    if code == 401 or (text and "Could not authenticate" in text):
        return (
            XSessionValidationResult.INVALID,
            "سشن نامعتبر یا منقضی شده است (HTTP 401). لطفاً سشن جدید را وارد نمایید.",
            username,
        )

    if code == 403:
        snippet = (text or "")[:200].replace("\r", " ").replace("\n", " ").strip()
        if was_generated:
            logger.warning(
                "Twitter settings endpoint returned 403 Forbidden with generated ct0 fallback. User: %s, Body snippet: %s",
                username,
                snippet,
            )
        else:
            logger.warning(
                "Twitter settings endpoint returned 403 Forbidden (unknown cause). User: %s, Body snippet: %s",
                username,
                snippet,
            )
        return (
            XSessionValidationResult.FORBIDDEN_UNKNOWN,
            f"دسترسی مسدود شد (HTTP 403؛ علت نامشخص). سشن نه تایید و نه رد شد. پاسخ سرور: {snippet[:80]}",
            username,
        )

    if code == 404:
        return (
            XSessionValidationResult.ENDPOINT_ERROR,
            "مسیر یا اندپوینت نامعتبر است (HTTP 404).",
            username,
        )

    if code == 429:
        return (
            XSessionValidationResult.RATE_LIMITED,
            "محدودیت موقت نرخ درخواست توییتر (HTTP 429).",
            username,
        )

    other_snippet = (text or "")[:100].replace("\r", " ").replace("\n", " ").strip()
    logger.info("Twitter settings endpoint returned unexpected status HTTP %s: %s", code, other_snippet)
    return (
        XSessionValidationResult.ENDPOINT_ERROR,
        f"پاسخ ناشناخته از توییتر (HTTP {code})؛ نتیجه اعتبارسنجی نامشخص است.",
        username,
    )


def _test_twitter_account_connection(account: TwitterEngineAccount) -> tuple[bool, str, str]:
    """Tests Twitter account connectivity and session validity."""
    auth_token = (account.auth_token or "").strip()
    if not auth_token:
        account.status = "CHALLENGED"
        account.status_message = "کوکی auth_token تنظیم نشده است"
        account.save(update_fields=["status", "status_message"])
        account._validation_result = XSessionValidationResult.INVALID
        return False, "CHALLENGED", "کوکی auth_token تنظیم نشده است و بدون آن سشن نامعتبر است."

    if account.proxy_url:
        proxy_ok, proxy_msg = _test_proxy_connection(account.proxy_url)
        if not proxy_ok:
            account._validation_result = XSessionValidationResult.NETWORK_ERROR
            return False, account.status, f"خطای پروکسی: {proxy_msg}"

    now = dj_timezone.now()
    if account.cooldown_until and account.cooldown_until > now:
        rem_min = int((account.cooldown_until - now).total_seconds()) // 60
        account._validation_result = XSessionValidationResult.RATE_LIMITED
        return False, account.status, f"اکانت در دوره استراحت است ({rem_min} دقیقه باقی‌مانده)"

    ct0, was_generated = _get_or_generate_ct0(account.ct0)

    import os
    import shutil
    import subprocess

    executable = shutil.which("twitter") or shutil.which("twitter-cli")
    if executable:
        sub_env = os.environ.copy()
        sub_env["TWITTER_AUTH_TOKEN"] = auth_token
        sub_env["TWITTER_CT0"] = ct0
        if account.proxy_url:
            sub_env["TWITTER_PROXY"] = account.proxy_url
        try:
            completed = subprocess.run(
                [executable, "search", "iran", "-t", "Latest", "--exclude", "retweets", "--max", "1", "--json"],
                capture_output=True, text=True, encoding="utf-8", timeout=25, check=False, env=sub_env
            )
            try:
                payload = json.loads(completed.stdout) if completed.stdout.strip() else {}
            except Exception:
                payload = {}

            if completed.returncode == 0 and payload.get("ok") is True:
                account.status = "ACTIVE"
                account.status_message = "متصل و فعال"
                # Rule 4: Only persist a ct0 to the database when the result is VALID
                account.ct0 = ct0
                account.save(update_fields=["status", "status_message", "ct0"])
                account._validation_result = XSessionValidationResult.VALID
                return True, "ACTIVE", f"اتصال به اکانت @{account.username} تایید شد."
            elif payload.get("ok") is False:
                err = payload.get("error") if isinstance(payload.get("error"), dict) else {}
                code = str(err.get("code") or "")
                if code == "rate_limited":
                    account.status = "COOLDOWN"
                    account.cooldown_until = dj_timezone.now() + timedelta(minutes=15)
                    account.status_message = "محدودیت موقت نرخ درخواست (429)"
                    account.save(update_fields=["status", "status_message", "cooldown_until"])
                    account._validation_result = XSessionValidationResult.RATE_LIMITED
                    # A rate-limited session is not a confirmed session: report
                    # failure so callers don't show "connected" for it.
                    return False, "COOLDOWN", "اکانت با محدودیت موقت ریت‌لیمیت توییتر مواجه شد (۱۵ دقیقه استراحت)."
                elif code in ("not_authenticated", "auth_failed"):
                    account.status = "CHALLENGED"
                    account.status_message = "سشن منقضی یا نامعتبر است (HTTP 401) — auth_token/ct0 جدید ثبت کنید"
                    account.save(update_fields=["status", "status_message"])
                    account._validation_result = XSessionValidationResult.INVALID
                    return False, "CHALLENGED", "سشن منقضی یا نامعتبر است. لطفاً auth_token و ct0 جدید را وارد نمایید."
                else:
                    msg = err.get("message") or completed.stderr or "خطای اجرای ابزار توییتر"
                    account._validation_result = XSessionValidationResult.ENDPOINT_ERROR
                    return False, account.status, f"خطای تست توییتر: {msg}"
        except subprocess.TimeoutExpired:
            account._validation_result = XSessionValidationResult.NETWORK_ERROR
            return False, account.status, "زمان پاسخ‌گویی موتور توییتر به پایان رسید (تایم‌اوت)."
        except Exception:
            # Fall back to the HTTP check below, but leave a trace of why the
            # CLI path failed instead of swallowing it silently.
            logger.warning("twitter-cli session check failed for @%s; falling back to HTTP", account.username, exc_info=True)

    response, exc = _call_twitter_settings_endpoint(auth_token, ct0, proxy_url=account.proxy_url)
    resp_ct0 = _extract_response_ct0(response)
    effective_ct0 = resp_ct0 or ct0

    result, msg, confirmed_uname = _interpret_twitter_response(
        response=response,
        error=exc,
        username=account.username,
        was_generated=was_generated,
    )
    account._validation_result = result

    if result == XSessionValidationResult.VALID:
        account.status = "ACTIVE"
        account.status_message = msg
        # Rule 4: Only persist a ct0 to the database when the result is VALID
        account.ct0 = effective_ct0
        account.save(update_fields=["status", "status_message", "ct0"])
        return True, "ACTIVE", msg
    elif result == XSessionValidationResult.INVALID:
        account.status = "CHALLENGED"
        account.status_message = msg
        account.save(update_fields=["status", "status_message"])
        return False, "CHALLENGED", msg
    else:
        # FORBIDDEN_UNKNOWN, ENDPOINT_ERROR, RATE_LIMITED, NETWORK_ERROR:
        # Keep previous state! Do NOT persist generated ct0!
        inconclusive_msg = f"نتیجه اعتبارسنجی نامشخص است ({msg})؛ وضعیت قبلی اکانت حفظ شد."
        account.status_message = inconclusive_msg
        account.save(update_fields=["status_message"])
        return False, account.status, inconclusive_msg


@manage_access_required
@require_POST
def manage_twitter_account_add(request):
    is_ajax = wants_json(request)
    username = request.POST.get("username", "").strip().lstrip("@")
    password = request.POST.get("password", "").strip()
    auth_token = request.POST.get("auth_token", "").strip()
    ct0 = request.POST.get("ct0", "").strip()
    proxy_url = request.POST.get("proxy_url", "").strip() or default_proxy_url() or "socks5h://xray:1080"

    if not username:
        err = "نام کاربری توییتر الزامی است."
        if is_ajax:
            return JsonResponse({"success": False, "message": err})
        messages.error(request, err)
        return redirect("manage:dashboard")

    if not auth_token:
        account, created = TwitterEngineAccount.objects.update_or_create(
            username=username,
            defaults={
                "password": password,
                "auth_token": "",
                "ct0": ct0,
                "proxy_url": proxy_url,
                "status": "CHALLENGED",
                "status_message": "نیازمند سشن (auth_token)",
                "is_active": True,
            }
        )
        msg = "اکانت توییتر ثبت شد ولی سشن (auth_token) وارد نشده است؛ وضعیت روی چالش قرار گرفت."
        if is_ajax:
            return JsonResponse({"success": False, "account_id": account.id, "username": username, "status": "CHALLENGED", "message": msg})
        messages.warning(request, msg)
        return redirect("manage:dashboard")

    account, created = TwitterEngineAccount.objects.update_or_create(
        username=username,
        defaults={
            "password": password,
            "auth_token": auth_token,
            "ct0": ct0,
            "proxy_url": proxy_url,
            "status": "CHALLENGED",
            "status_message": "در انتظار اعتبارسنجی",
            "is_active": True,
        }
    )

    ok, st, msg = _test_twitter_account_connection(account)
    if not ok:
        warn_msg = f"اکانت توییتر @{username} ثبت شد ولی اعتبارسنجی سشن تایید نشد ({msg})."
        if is_ajax:
            return JsonResponse({"success": False, "deleted": False, "account_id": account.id, "username": username, "status": st, "message": warn_msg})
        messages.warning(request, warn_msg)
        return redirect("manage:dashboard")

    succ_msg = f"اکانت توییتر @{username} با موفقیت ثبت شد. ({msg})"
    if is_ajax:
        return JsonResponse({"success": ok, "account_id": account.id, "username": username, "status": st, "message": succ_msg})
    messages.success(request, succ_msg)
    return redirect("manage:dashboard")


@manage_access_required
@require_POST
def manage_twitter_account_delete(request, pk):
    account = get_object_or_404(TwitterEngineAccount, pk=pk)
    uname = account.username
    account.delete()
    messages.success(request, f"اکانت توییتر @{uname} با موفقیت حذف گردید.")
    return redirect("manage:dashboard")


@manage_access_required
@require_POST
def manage_twitter_account_test(request, pk):
    account = get_object_or_404(TwitterEngineAccount, pk=pk)
    ok, status, message = _test_twitter_account_connection(account)
    return JsonResponse({
        "success": ok,
        "status": status,
        "status_display": account.get_status_display(),
        "message": message,
        "last_action_at": account.last_action_at.isoformat() if account.last_action_at else None,
    })


@manage_access_required
@require_POST
def manage_twitter_account_set_session(request, pk):
    account = get_object_or_404(TwitterEngineAccount, pk=pk)
    auth_token = request.POST.get("auth_token", "").strip()
    ct0 = request.POST.get("ct0", "").strip()
    password = request.POST.get("password", "").strip()

    # Rule 2: If auth_token is missing, return INVALID without making an HTTP request
    if not auth_token:
        return JsonResponse({
            "success": False,
            "deleted": False,
            "status": account.status,
            "status_display": account.get_status_display(),
            "message": "کوکی auth_token الزامی است و سشن بدون آن نامعتبر است.",
        })

    old_auth_token = account.auth_token
    old_ct0 = account.ct0
    old_password = account.password
    old_status = account.status

    if password:
        account.password = password
    account.auth_token = auth_token
    account.ct0 = ct0

    ok, status, message = _test_twitter_account_connection(account)
    validation_result = getattr(account, "_validation_result", None)
    if validation_result is None:
        validation_result = XSessionValidationResult.VALID if ok else XSessionValidationResult.INVALID

    if validation_result == XSessionValidationResult.VALID:
        account.status = "ACTIVE"
        account.status_message = message
        # account.ct0 was updated to effective_ct0 by _test_twitter_account_connection
        account.save()
        return JsonResponse({
            "success": True,
            "status": "ACTIVE",
            "status_display": account.get_status_display(),
            "message": message,
        })
    elif validation_result == XSessionValidationResult.INVALID:
        account.status = "CHALLENGED"
        account.status_message = message
        # Rule 4: a generated ct0 is not saved unless the result is VALID
        if not ct0:
            account.ct0 = old_ct0
        account.save()
        return JsonResponse({
            "success": False,
            "deleted": False,
            "status": "CHALLENGED",
            "status_display": account.get_status_display(),
            "message": f"{message} (سشن رد شد و اکانت در وضعیت چالش قرار گرفت).",
        })
    else:
        # Inconclusive (FORBIDDEN_UNKNOWN, ENDPOINT_ERROR, RATE_LIMITED, NETWORK_ERROR):
        # Keep previous state and credentials
        account.auth_token = old_auth_token
        account.ct0 = old_ct0
        account.password = old_password
        account.status = old_status
        account.status_message = message
        account.save()
        inconclusive_msg = f"اعتبارسنجی سشن نامشخص بود ({message}). وضعیت اکانت تغییر نکرد و سشن قبلی حفظ شد."
        return JsonResponse({
            "success": False,
            "deleted": False,
            "inconclusive": True,
            "status": old_status,
            "status_display": account.get_status_display(),
            "message": inconclusive_msg,
        })


@manage_access_required
@require_POST
def manage_twitter_account_toggle(request, pk):
    account = get_object_or_404(TwitterEngineAccount, pk=pk)
    account.is_active = not account.is_active
    account.save(update_fields=["is_active"])
    return JsonResponse({
        "success": True,
        "is_active": account.is_active,
        "message": f"وضعیت اکانت @{account.username} به {'فعال' if account.is_active else 'غیرفعال'} تغییر یافت.",
    })


def _ping_and_evaluate_proxy_config(proxy: ProxyConfig, timeout_sec: float = 3.5) -> dict[str, Any]:
    """
    Live ping test for a single ProxyConfig instance.
    Evaluates reachability and latency via:
    1. SOCKS5 inbound probe through xray container or localhost (if local_port is set)
    2. test_outbound_ping via multi_proxy_manager (if Xray binary and raw_url available)
    3. Direct TCP handshake to proxy.server:proxy.port
    Updates the proxy's ping_ms, status ('ACTIVE' or 'DEAD'), and last_checked_at.
    """
    import time
    is_ok = False
    ping_ms = -1.0
    err_msg = ""

    # Strategy 1: If proxy has a local_port, probe the local socks5 listener
    if proxy.local_port:
        for host in ["xray", "127.0.0.1", "localhost"]:
            try:
                probe_t0 = time.perf_counter()
                sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                sock.settimeout(min(1.5, timeout_sec))
                res = sock.connect_ex((host, int(proxy.local_port)))
                sock.close()
                if res == 0:
                    latency = (time.perf_counter() - probe_t0) * 1000.0
                    is_ok = True
                    ping_ms = round(latency, 1)
                    break
            except Exception:
                continue

    # Strategy 2: Ephemeral Xray probe if multi_proxy_manager is available
    if not is_ok and proxy.raw_url:
        try:
            from xray.multi_proxy_manager import parse_link_to_outbound, test_outbound_ping, XRAY_BIN
            if XRAY_BIN and XRAY_BIN.exists():
                ob = parse_link_to_outbound(proxy.raw_url)
                ok, p_ms, p_err = test_outbound_ping(ob, timeout_sec=min(2.5, timeout_sec))
                if ok and 0 < p_ms <= 3000:
                    is_ok = True
                    ping_ms = round(p_ms, 1)
                elif p_err:
                    err_msg = p_err
        except Exception as exc:
            logger.debug("Ephemeral Xray probe error: %s", exc)

    # Strategy 3: Direct TCP handshake to remote server & port
    if not is_ok and proxy.server:
        try:
            prt = int(proxy.port or 443)
            tcp_t0 = time.perf_counter()
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(timeout_sec)
            res = sock.connect_ex((proxy.server, prt))
            sock.close()
            tcp_elapsed = (time.perf_counter() - tcp_t0) * 1000.0
            if res == 0:
                is_ok = True
                ping_ms = round(tcp_elapsed, 1)
            else:
                err_msg = f"خطای اتصال TCP به {proxy.server}:{prt} (کد {res})"
        except Exception as exc:
            err_msg = f"خطای تست سرور {proxy.server}: {exc}"

    # If server was missing from fields, try parsing from raw_url
    if not is_ok and not proxy.server and proxy.raw_url:
        try:
            from xray.multi_proxy_manager import parse_link_to_outbound
            ob = parse_link_to_outbound(proxy.raw_url)
            servers = ob.get("settings", {}).get("servers") or ob.get("settings", {}).get("vnext")
            addr = None
            prt = None
            if servers and len(servers) > 0:
                s0 = servers[0]
                addr = s0.get("address")
                prt = s0.get("port")
            if addr and prt:
                sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                sock.settimeout(timeout_sec)
                t_p = time.perf_counter()
                r = sock.connect_ex((addr, int(prt)))
                sock.close()
                if r == 0:
                    is_ok = True
                    ping_ms = round((time.perf_counter() - t_p) * 1000.0, 1)
        except Exception:
            pass

    # Save evaluated status to database
    if is_ok and 0 < ping_ms <= 3000:
        proxy.ping_ms = ping_ms
        proxy.status = "ACTIVE"
    else:
        proxy.ping_ms = -1.0
        proxy.status = "DEAD"

    proxy.last_checked_at = dj_timezone.now()
    try:
        proxy.save(update_fields=["ping_ms", "status", "last_checked_at"])
    except Exception as exc:
        logger.warning("Could not save proxy #%s test result: %s", proxy.id, exc)

    return {
        "id": proxy.id,
        "tag": proxy.tag or proxy.server or f"پروکسی #{proxy.id}",
        "protocol": proxy.protocol,
        "server": f"{proxy.server}:{proxy.port}" if proxy.server else "—",
        "local_port": proxy.local_port,
        "ping_ms": proxy.ping_ms,
        "ping_display": f"{proxy.ping_ms:.0f} ms" if proxy.ping_ms >= 0 else "قطع",
        "status": proxy.status,
        "status_label": "فعال" if proxy.status == "ACTIVE" else "قطع",
        "last_checked_fa": "چند لحظه پیش",
        "error": err_msg,
    }


@manage_access_required
@require_POST
def manage_proxy_test(request, pk=None):
    """
    Live real-time test for proxy configs.
    Supports testing a single proxy (via pk or POST proxy_id) or all proxies concurrently.
    """
    from concurrent.futures import ThreadPoolExecutor

    proxy_id = pk or request.POST.get("proxy_id")
    if proxy_id:
        try:
            proxy = ProxyConfig.objects.get(pk=int(proxy_id))
        except (ProxyConfig.DoesNotExist, ValueError):
            return JsonResponse({"success": False, "message": "پروکسی مورد نظر یافت نشد."}, status=404)

        res = _ping_and_evaluate_proxy_config(proxy)
        msg = f"پروکسی {res['tag']} با موفقیت تست شد: {res['status_label']} ({res['ping_display']})"
        return JsonResponse({"success": True, "proxy": res, "message": msg})

    # Test ALL proxies concurrently
    proxies = list(ProxyConfig.objects.all().order_by("local_port"))
    if not proxies:
        return JsonResponse({
            "success": True,
            "total": 0,
            "active_count": 0,
            "dead_count": 0,
            "results": [],
            "message": "هیچ کانفیگ پروکسی برای تست در پایگاه داده ثبت نشده است."
        })

    max_workers = min(12, max(2, len(proxies)))
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        results = list(executor.map(_ping_and_evaluate_proxy_config, proxies))

    active_count = sum(1 for r in results if r["status"] == "ACTIVE")
    dead_count = len(results) - active_count

    return JsonResponse({
        "success": True,
        "total": len(results),
        "active_count": active_count,
        "dead_count": dead_count,
        "results": results,
        "message": f"تست زنده {len(results)} پروکسی به پایان رسید: {active_count} فعال، {dead_count} قطع."
    })


# ==============================================================================
# INSTAGRAM UNIFIED COCKPIT & SIMULATOR ENGINE (Web-based Instagram Client)
# ==============================================================================

from django.core.serializers.json import DjangoJSONEncoder


class SafeCockpitJSONEncoder(DjangoJSONEncoder):
    """Custom JSON encoder converting Pydantic HttpUrl and other rich objects to plain strings."""
    def default(self, obj):
        if hasattr(obj, "__str__"):
            return str(obj)
        return super().default(obj)


_cockpit_client_cache: dict[int, tuple[float, Any]] = {}


def _get_cockpit_client(account: InstagramEngineAccount, force_fresh: bool = False):
    """
    Returns an authenticated instagrapi.Client with device emulation and proxy applied.
    Caches client instance in memory for 12 minutes to provide instant UI responsiveness without relogging.
    """
    import time
    from instagrapi import Client

    now_ts = time.time()
    if not force_fresh and account.id in _cockpit_client_cache:
        cached_ts, cached_cl = _cockpit_client_cache[account.id]
        if now_ts - cached_ts < 720:  # 12 minutes
            return cached_cl, None

    cookies = (account.session_json or {}).get("cookies", {})
    sessionid = cookies.get("sessionid")
    if not sessionid:
        return None, f"اکانت @{account.username} کوکی سشن معتبر ندارد."

    cl = Client()
    cl.delay_range = [0.3, 0.8]
    if account.clean_proxy:   # its own proxy / system config, else the shared pool (never the server's IP)
        cl.set_proxy(account.clean_proxy)

    try:
        connect_saved_session(cl, account)  # same phone + session as the workers, no fresh login per page view
        _cockpit_client_cache[account.id] = (now_ts, cl)
        return cl, None
    except Exception as exc:
        logger.warning(f"Failed to initialize cockpit client for @{account.username}: {exc}")
        return None, str(exc)


@manage_access_required
def instagram_account_cockpit(request, pk: int):
    """
    Dedicated cockpit page for a single Instagram account.
    Provides direct chat, timeline browsing, organic search, and simulator controls.
    """
    from django.shortcuts import get_object_or_404
    from apps.discovery.device_emulator import ensure_account_device_profile
    from apps.businesses.models import Business

    account = get_object_or_404(InstagramEngineAccount, pk=pk)
    ensure_account_device_profile(account)

    business = None
    if account.tenant_id and str(account.tenant_id).startswith("tenant_"):
        b_id = str(account.tenant_id).replace("tenant_", "")
        business = Business.objects.filter(id=b_id).first()

    device_settings = account.device_settings or {}
    dev_specs = device_settings.get("device_settings", {})

    return render(request, "ops/instagram_account_cockpit.html", {
        "account": account,
        "business": business,
        "device_settings": device_settings,
        "dev_specs": dev_specs,
        "status_display": account.get_status_display(),
    })


@manage_access_required
def instagram_cockpit_threads(request, pk: int):
    """AJAX endpoint: Fetch recent Direct Message threads for the account."""
    from django.shortcuts import get_object_or_404
    account = get_object_or_404(InstagramEngineAccount, pk=pk)

    cl, err = _get_cockpit_client(account)
    if not cl:
        return JsonResponse({"success": False, "error": err or "اتصال برقرار نشد."}, status=400)

    try:
        threads, _ = cl.direct_threads_chunk(thread_message_limit=5)
        formatted_threads = []
        for th in threads[:25]:
            users = [
                {
                    "pk": str(getattr(u, "pk", "") or ""),
                    "username": str(getattr(u, "username", "") or ""),
                    "full_name": str(getattr(u, "full_name", "") or ""),
                    "profile_pic_url": str(getattr(u, "profile_pic_url", "") or ""),
                }
                for u in getattr(th, "users", [])
            ]
            last_msg = ""
            last_msg_time = ""
            is_outgoing = False
            msgs = getattr(th, "messages", [])
            if msgs:
                m = msgs[0]
                last_msg = getattr(m, "text", "") or ""
                if hasattr(m, "timestamp") and m.timestamp:
                    try:
                        last_msg_time = m.timestamp.strftime("%H:%M")
                    except Exception:
                        last_msg_time = str(m.timestamp)[:5]
                is_outgoing = str(getattr(m, "user_id", "")) == str(cl.user_id)

            thread_title = getattr(th, "thread_title", "") or (users[0]["username"] if users else f"گفتگو {th.id}")
            formatted_threads.append({
                "id": str(th.id),
                "title": str(thread_title),
                "users": users,
                "last_message": str(last_msg),
                "last_message_time": str(last_msg_time),
                "is_outgoing": is_outgoing,
                "unread": bool(getattr(th, "read_state", 1) == 1),
            })

        return JsonResponse({"success": True, "threads": formatted_threads}, encoder=SafeCockpitJSONEncoder)
    except Exception as exc:
        logger.exception("Error loading direct threads for cockpit @%s: %s", account.username, exc)
        return JsonResponse({"success": False, "error": str(exc)}, status=500)


@manage_access_required
def instagram_cockpit_thread_detail(request, pk: int, thread_id: str):
    """AJAX endpoint: Fetch detailed messages in a specific Direct thread."""
    from django.shortcuts import get_object_or_404
    account = get_object_or_404(InstagramEngineAccount, pk=pk)

    cl, err = _get_cockpit_client(account)
    if not cl:
        return JsonResponse({"success": False, "error": err or "اتصال برقرار نشد."}, status=400)

    try:
        th = cl.direct_thread(thread_id, amount=25)
        raw_msgs = getattr(th, "messages", []) or []
        msgs = []
        current_uid = str(cl.user_id)
        for m in raw_msgs:
            m_text = getattr(m, "text", "") or ""
            t_str = ""
            if hasattr(m, "timestamp") and m.timestamp:
                try:
                    t_str = m.timestamp.strftime("%Y-%m-%d %H:%M")
                except Exception:
                    t_str = str(m.timestamp)

            msgs.append({
                "id": str(getattr(m, "id", "")),
                "user_id": str(getattr(m, "user_id", "")),
                "is_outgoing": str(getattr(m, "user_id", "")) == current_uid,
                "text": m_text,
                "timestamp": t_str,
            })

        # Oldest first so chat flows downward naturally
        msgs.reverse()
        return JsonResponse({"success": True, "thread_id": thread_id, "messages": msgs})
    except Exception as exc:
        logger.exception("Error loading thread detail %s for cockpit @%s: %s", thread_id, account.username, exc)
        return JsonResponse({"success": False, "error": str(exc)}, status=500)


@manage_access_required
@require_POST
def instagram_cockpit_send_direct(request, pk: int):
    """AJAX endpoint: Send direct message from cockpit (same limits as automated outreach)."""
    from django.shortcuts import get_object_or_404
    from apps.discovery.outreach_service import manual_dm_block_reason, record_manual_dm
    account = get_object_or_404(InstagramEngineAccount, pk=pk)

    thread_id = request.POST.get("thread_id", "").strip()
    user_id = request.POST.get("user_id", "").strip()
    text = request.POST.get("text", "").strip()

    if not text:
        return JsonResponse({"success": False, "error": "متن پیام نمی‌تواند خالی باشد."}, status=400)
    if user_id and not user_id.isdigit():
        return JsonResponse({"success": False, "error": "شناسهٔ کاربر نامعتبر است."}, status=400)
    if not thread_id and not user_id:
        return JsonResponse({"success": False, "error": "مخاطب پیام مشخص نیست."}, status=400)

    blocked = manual_dm_block_reason(account)
    if blocked:
        return JsonResponse({"success": False, "error": blocked, "blocked": True}, status=429)

    cl, err = _get_cockpit_client(account)
    if not cl:
        return JsonResponse({"success": False, "error": err or "اتصال برقرار نشد."}, status=400)

    try:
        if thread_id:
            cl.direct_send(text, thread_ids=[thread_id])
        else:
            cl.direct_send(text, user_ids=[int(user_id)])

        record_manual_dm(account, user_pk=user_id or None, text=text, actor=_ig_actor(request))

        return JsonResponse({
            "success": True,
            "message": "پیام با موفقیت ارسال شد.",
            "daily_count": account.daily_action_count,
        })
    except Exception as exc:
        logger.exception("Error sending direct from cockpit @%s: %s", account.username, exc)
        return JsonResponse({"success": False, "error": str(exc)}, status=500)


@manage_access_required
def instagram_cockpit_feed(request, pk: int):
    """AJAX endpoint: Fetch recent timeline posts for account."""
    from django.shortcuts import get_object_or_404
    account = get_object_or_404(InstagramEngineAccount, pk=pk)

    cl, err = _get_cockpit_client(account)
    if not cl:
        return JsonResponse({"success": False, "error": err or "اتصال برقرار نشد."}, status=400)

    try:
        timeline = cl.get_timeline_feed()
        feed_items = getattr(timeline, "feed_items", []) or (timeline.get("feed_items", []) if isinstance(timeline, dict) else [])

        items = []
        for fi in feed_items[:12]:
            m = fi
            if isinstance(fi, dict) and "media_or_ad" in fi:
                m = fi["media_or_ad"]
            elif hasattr(fi, "media_or_ad") and fi.media_or_ad:
                m = fi.media_or_ad

            caption = ""
            user_info = {}
            img_url = ""
            like_count = 0
            m_id = getattr(m, "id", "") or (m.get("id") if isinstance(m, dict) else "")

            # Caption
            cap_obj = getattr(m, "caption", None) or (m.get("caption") if isinstance(m, dict) else None)
            if cap_obj:
                caption = getattr(cap_obj, "text", "") or (cap_obj.get("text") if isinstance(cap_obj, dict) else "")

            # User
            u_obj = getattr(m, "user", None) or (m.get("user") if isinstance(m, dict) else None)
            if u_obj:
                user_info = {
                    "username": str(getattr(u_obj, "username", "") or (u_obj.get("username") if isinstance(u_obj, dict) else "")),
                    "full_name": str(getattr(u_obj, "full_name", "") or (u_obj.get("full_name") if isinstance(u_obj, dict) else "")),
                    "profile_pic_url": str(getattr(u_obj, "profile_pic_url", "") or (u_obj.get("profile_pic_url") if isinstance(u_obj, dict) else "") or ""),
                }

            # Thumbnail
            img_url = str(getattr(m, "thumbnail_url", "") or "")
            if not img_url and hasattr(m, "image_versions2"):
                cands = getattr(m.image_versions2, "candidates", []) or []
                if cands:
                    img_url = str(getattr(cands[0], "url", "") or "")
            elif isinstance(m, dict) and not img_url:
                cands = (m.get("image_versions2") or {}).get("candidates") or []
                if cands:
                    img_url = str(cands[0].get("url", "") or "")

            like_count = getattr(m, "like_count", 0) or (m.get("like_count") if isinstance(m, dict) else 0)

            items.append({
                "id": str(m_id),
                "caption": str(caption[:150] + ("..." if len(caption) > 150 else "")),
                "user": user_info,
                "image_url": str(img_url),
                "like_count": int(like_count),
            })

        return JsonResponse({"success": True, "feed": items}, encoder=SafeCockpitJSONEncoder)
    except Exception as exc:
        logger.exception("Error loading timeline feed for cockpit @%s: %s", account.username, exc)
        return JsonResponse({"success": False, "error": str(exc)}, status=500)


@manage_access_required
@require_POST
def instagram_cockpit_scroll_feed(request, pk: int):
    """AJAX endpoint: Simulate natural human scroll and warm-up browsing on timeline feed."""
    import time
    import random
    from django.shortcuts import get_object_or_404
    account = get_object_or_404(InstagramEngineAccount, pk=pk)

    cl, err = _get_cockpit_client(account)
    if not cl:
        return JsonResponse({"success": False, "error": err or "اتصال برقرار نشد."}, status=400)

    try:
        timeline = cl.get_timeline_feed()
        delay_sec = round(random.uniform(3.5, 6.5), 1)
        time.sleep(delay_sec)

        was_liked = False
        feed_items = getattr(timeline, "feed_items", []) or (timeline.get("feed_items", []) if isinstance(timeline, dict) else [])
        if feed_items and random.random() < 0.25:
            first_m = feed_items[0]
            m_id = None
            if isinstance(first_m, dict):
                m_id = (first_m.get("media_or_ad") or {}).get("id") or first_m.get("id")
            elif hasattr(first_m, "id"):
                m_id = first_m.id
            if m_id and hasattr(cl, "media_like"):
                cl.media_like(str(m_id))
                was_liked = True

        return JsonResponse({
            "success": True,
            "message": f"اسکرول فید شبیه‌سازی شد ({delay_sec} ثانیه گشت‌وگذار).",
            "liked_post": was_liked,
            "delay_sec": delay_sec,
        })
    except Exception as exc:
        logger.exception("Error during simulated scroll for cockpit @%s: %s", account.username, exc)
        return JsonResponse({"success": False, "error": str(exc)}, status=500)


@manage_access_required
def instagram_cockpit_search(request, pk: int):
    """AJAX endpoint: Search users in Instagram search bar."""
    from django.shortcuts import get_object_or_404
    account = get_object_or_404(InstagramEngineAccount, pk=pk)

    q = request.GET.get("q", "").strip().lstrip("@")
    if not q:
        return JsonResponse({"success": True, "results": []})

    cl, err = _get_cockpit_client(account)
    if not cl:
        return JsonResponse({"success": False, "error": err or "اتصال برقرار نشد."}, status=400)

    try:
        results = cl.search_users(q) or []
        items = []
        for u in results[:10]:
            items.append({
                "pk": str(getattr(u, "pk", "") or ""),
                "username": str(getattr(u, "username", "") or ""),
                "full_name": str(getattr(u, "full_name", "") or ""),
                "is_private": bool(getattr(u, "is_private", False)),
                "is_verified": bool(getattr(u, "is_verified", False)),
                "profile_pic_url": str(getattr(u, "profile_pic_url", "") or ""),
            })
        return JsonResponse({"success": True, "results": items}, encoder=SafeCockpitJSONEncoder)
    except Exception as exc:
        logger.exception("Error searching users in cockpit @%s: %s", account.username, exc)
        return JsonResponse({"success": False, "error": str(exc)}, status=500)


@manage_access_required
@require_POST
def instagram_cockpit_organic_dm(request, pk: int):
    """AJAX endpoint: Execute full organic journey to target user and dispatch DM."""
    from django.shortcuts import get_object_or_404
    from apps.discovery.outreach_service import execute_organic_journey, manual_dm_block_reason, record_manual_dm

    account = get_object_or_404(InstagramEngineAccount, pk=pk)
    target_username = request.POST.get("target_username", "").strip().lstrip("@")
    message_text = request.POST.get("message_text", "").strip()

    if not target_username or not message_text:
        return JsonResponse({"success": False, "error": "نام کاربری مقصد و متن پیام الزامی است."}, status=400)

    blocked = manual_dm_block_reason(account)
    if blocked:
        return JsonResponse({"success": False, "error": blocked, "blocked": True}, status=429)

    cl, err = _get_cockpit_client(account)
    if not cl:
        return JsonResponse({"success": False, "error": err or "اتصال برقرار نشد."}, status=400)

    try:
        journey_res = execute_organic_journey(cl, target_username)
        target_pk = journey_res.get("target_pk")
        if not target_pk:
            target_pk = int(cl.user_id_from_username(target_username))

        cl.direct_send(message_text, user_ids=[target_pk])

        record_manual_dm(account, user_pk=target_pk, text=message_text, actor=_ig_actor(request))

        return JsonResponse({
            "success": True,
            "message": f"پیام ارگانیک با موفقیت به @{target_username} ارسال شد.",
            "journey": journey_res,
            "daily_count": account.daily_action_count,
        })
    except Exception as exc:
        logger.exception("Error executing organic DM from cockpit @%s to @%s: %s", account.username, target_username, exc)
        return JsonResponse({"success": False, "error": str(exc)}, status=500)


@manage_access_required
@require_POST
def manage_channel_proxy_sync(request):
    """
    Triggers on-demand sync from a single channel or all active channels.
    """
    from apps.discovery.channel_config_automation import sync_channel_configs, sync_all_active_channels

    channel = request.POST.get("channel", "").strip()
    if not channel or channel.lower() == "all":
        res = sync_all_active_channels(max_posts=20, max_add_per_channel=10)
        return JsonResponse(res)

    force_all = request.POST.get("force_all") == "1"
    try:
        res = sync_channel_configs(channel_username=channel, force_all=force_all, max_add_per_run=10)
        return JsonResponse(res)
    except Exception as exc:
        return JsonResponse({"success": False, "message": f"خطا در بررسی کانال: {exc}"}, status=400)


@manage_access_required
@require_POST
def manage_proxy_channel_add(request):
    """
    Adds a new Telegram channel address to the proxy monitoring pool.
    """
    from apps.discovery.channel_config_automation import add_monitored_channel, normalize_channel_username

    raw_channel = request.POST.get("channel_username", "").strip()
    title = request.POST.get("title", "").strip()

    if not raw_channel:
        return JsonResponse({"success": False, "message": "لطفاً آدرس یا نام کاربری کانال تلگرام را وارد کنید."}, status=400)

    try:
        clean_user = normalize_channel_username(raw_channel)
        channel_data = add_monitored_channel(clean_user, title=title)
        # JSON endpoint: the page shows this message as a toast; a flash
        # message here would show it a second time on the next page load.
        return JsonResponse({
            "success": True,
            "message": f"کانال @{clean_user} با موفقیت به لیست اضافه شد.",
            "channel": channel_data,
        })
    except ValueError as val_err:
        return JsonResponse({"success": False, "message": str(val_err)}, status=400)
    except Exception as exc:
        logger.exception("Error adding proxy channel: %s", exc)
        return JsonResponse({"success": False, "message": f"خطا در ثبت کانال: {exc}"}, status=500)


@manage_access_required
@require_POST
def manage_proxy_channel_delete(request, pk):
    """
    Removes a Telegram channel from the proxy monitoring pool.
    """
    from apps.discovery.channel_config_automation import delete_monitored_channel

    try:
        delete_monitored_channel(pk)
        return JsonResponse({
            "success": True,
            "message": "کانال با موفقیت از لیست پایش حذف شد.",
            "id": pk,
        })
    except Exception as exc:
        return JsonResponse({"success": False, "message": f"خطا در حذف کانال: {exc}"}, status=500)


@manage_access_required
@require_POST
def manage_proxy_channel_toggle(request, pk):
    """
    Toggles the active state of a monitored Telegram channel.
    """
    from apps.discovery.channel_config_automation import toggle_monitored_channel

    try:
        ch = toggle_monitored_channel(pk)
        state_str = "فعال" if ch.get("is_active") else "متوقف"
        return JsonResponse({
            "success": True,
            "message": f"وضعیت کانال به «{state_str}» تغییر یافت.",
            "is_active": ch.get("is_active"),
            "id": pk,
        })
    except Exception as exc:
        return JsonResponse({"success": False, "message": f"خطا در تغییر وضعیت: {exc}"}, status=500)


@manage_access_required
@require_POST
def manage_proxy_channel_sync(request, pk):
    """
    Synchronizes newly published configs from a specific monitored channel.
    """
    from apps.discovery.channel_config_automation import sync_channel_configs, get_monitored_channels

    channels = get_monitored_channels()
    target_channel = next((c for c in channels if c.get("id") == int(pk)), None)
    if not target_channel:
        return JsonResponse({"success": False, "message": "کانال مورد نظر یافت نشد."}, status=404)

    res = sync_channel_configs(target_channel["channel_username"], force_all=False, max_add_per_run=10)
    return JsonResponse(res)


@manage_access_required
@require_POST
def manage_proxy_channels_sync_all(request):
    """
    Synchronizes newly published configs across all active monitored channels.
    """
    from apps.discovery.channel_config_automation import sync_all_active_channels

    res = sync_all_active_channels(max_posts=20, max_add_per_channel=10)
    return JsonResponse(res)


# ==============================================================================
# INSTAGRAM REAL-TIME LIVE LOGS & ACTIVITY RADAR ENDPOINTS
# ==============================================================================

@manage_access_required
def instagram_live_logs_api(request):
    """
    AJAX endpoint returning live Instagram activity logs and worker heartbeats:
    - Is the scraper running right now? What query / post?
    - Is it watching a video / reel / story right now?
    - Recent log lines with timestamps and status indicators.
    """
    from apps.discovery.live_logger import get_live_monitoring_snapshot

    try:
        limit = min(int(request.GET.get("limit", 60)), 150)
    except (ValueError, TypeError):
        limit = 60

    try:
        since_id = int(request.GET.get("since_id", 0)) or None
    except (ValueError, TypeError):
        since_id = None

    data = get_live_monitoring_snapshot(limit=limit, since_id=since_id)
    return JsonResponse(data)


@manage_access_required
@require_POST
def instagram_live_test_pulse_api(request):
    """
    Trigger a simulated test pulse (e.g. video watch & comment inspect)
    so operators can verify live streaming immediately.
    """
    from apps.discovery.live_logger import simulate_test_pulse

    username = request.POST.get("username", "").strip().lstrip("@")
    res = simulate_test_pulse(account_username=username)
    return JsonResponse(res)


@manage_access_required
@require_POST
def instagram_live_clear_logs_api(request):
    """Clear activity logs stream on demand."""
    from apps.discovery.models import InstagramLiveActivity

    try:
        InstagramLiveActivity.objects.all().delete()
        return JsonResponse({"success": True, "message": "کنسول لاگ‌ها با موفقیت پاک‌سازی شد."})
    except Exception as exc:
        return JsonResponse({"success": False, "error": str(exc)}, status=500)


