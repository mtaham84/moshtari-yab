import json
import logging
import socket
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from enum import Enum
from functools import wraps
from typing import Any
from urllib.parse import urlparse

try:
    from curl_cffi import requests
except ImportError:
    import requests

from django.contrib import messages
from django.contrib.auth.views import redirect_to_login
from django.core.exceptions import PermissionDenied
from django.db.models import Count, Q, Sum
from django.http import JsonResponse, HttpResponseBadRequest
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone as dj_timezone
from django.views.decorators.http import require_POST, require_GET

import zoneinfo

from apps.businesses.models import Business
from apps.billing.models import Wallet, WalletTransaction, BillingSettings
from apps.discovery.models import (
    InstagramEngineAccount, InstagramEngineLead, InstagramCrawlExecution,
    InstagramSourceStat, TwitterEngineAccount, InstagramCrawledPost,
    XCrawledPost, Opportunity, ProxyConfig
)
from apps.core.jalali import format_jalali_date

logger = logging.getLogger("management")


def manage_access_required(view_func):
    """Decorator ensuring user is authenticated and is staff or superuser."""
    @wraps(view_func)
    def _wrapped(request, *args, **kwargs):
        if not request.user.is_authenticated:
            return redirect_to_login(request.get_full_path())
        if not (request.user.is_staff or request.user.is_superuser):
            raise PermissionDenied("دسترسی به بخش مدیریت برای حساب کاربری شما مجاز نمی‌باشد.")
        return view_func(request, *args, **kwargs)
    return _wrapped


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
    # 1. Scraper Accounts
    scrapers = InstagramEngineAccount.objects.filter(role="SCRAPER").order_by("-id")
    scrapers_total = scrapers.count()
    scrapers_active = scrapers.filter(status="ACTIVE").count()
    scrapers_cooldown = scrapers.filter(status="COOLDOWN").count()
    scrapers_challenged = scrapers.filter(status="CHALLENGED").count()
    scrapers_banned = scrapers.filter(status="BANNED").count()

    # 2. Sender Accounts & Leads
    senders = InstagramEngineAccount.objects.filter(role="SENDER").order_by("-id")
    senders_total = senders.count()
    senders_active = senders.filter(status="ACTIVE").count()

    from apps.discovery.device_emulator import ensure_account_device_profile
    for acc in list(scrapers) + list(senders):
        if not acc.device_settings or not isinstance(acc.device_settings, dict) or not acc.device_settings.get("device_settings"):
            try:
                ensure_account_device_profile(acc)
            except Exception:
                pass

    # Prioritized Leads: Ordered strictly by intent_score DESC, created_at ASC
    leads = InstagramEngineLead.objects.all().order_by("-intent_score", "created_at")[:60]
    total_leads = InstagramEngineLead.objects.count()
    dms_sent_total = InstagramEngineLead.objects.filter(status__in=["DM_SENT", "REPLIED", "FOLLOWUP_SENT"]).count()
    dms_replied_total = InstagramEngineLead.objects.filter(has_replied=True).count()
    high_intent_leads = InstagramEngineLead.objects.filter(intent_score__gte=0.85).count()

    # 3. Settings & Exchange Rate
    billing_settings = BillingSettings.get()
    usd_rate = billing_settings.usd_to_toman or Decimal("268000")
    markup_percent = billing_settings.markup_percent or Decimal("150.0")

    # Scraper latest activity tracking
    latest_scraper = scrapers.filter(last_action_at__isnull=False).order_by("-last_action_at").first()
    latest_scraper_action_at = latest_scraper.last_action_at if latest_scraper else None
    latest_lead = InstagramEngineLead.objects.order_by("-created_at").first()
    latest_lead_at = latest_lead.created_at if latest_lead else None

    # 4. Multi-Platform Leads Matrix for all 3 Tenants
    from apps.discovery.models import Opportunity
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

    for b in businesses:
        ig_count = Opportunity.objects.filter(business=b, source_platform="instagram").count()
        tg_count = Opportunity.objects.filter(business=b, source_platform="telegram").count()
        x_count = Opportunity.objects.filter(business=b, source_platform="x").count()
        b_total = Opportunity.objects.filter(business=b).count()
        b_high_intent = Opportunity.objects.filter(business=b, ai_analysis__intent_score__gte=0.80).count()
        b_dms = InstagramEngineLead.objects.filter(tenant_id=f"tenant_{b.id}", status="DM_SENT").count()

        tenant_matrix.append({
            "business": b,
            "instagram": ig_count,
            "telegram": tg_count,
            "x": x_count,
            "total": b_total,
            "high_intent": b_high_intent,
            "dms_sent": b_dms,
        })
        matrix_totals["instagram"] += ig_count
        matrix_totals["telegram"] += tg_count
        matrix_totals["x"] += x_count
        matrix_totals["total"] += b_total
        matrix_totals["high_intent"] += b_high_intent
        matrix_totals["dms_sent"] += b_dms

    # 5. Financial & Credit Consumption per Business / Account
    financial_rows = []
    total_balance_all = Decimal("0")
    total_spent_all = Decimal("0")
    total_cost_usd_all = Decimal("0")
    total_raw_toman_all = Decimal("0")
    total_payable_all = Decimal("0")
    total_calls_all = 0

    for b in businesses:
        wallet = getattr(b, "wallet", None)
        balance = wallet.balance_toman if wallet else Decimal("0")
        total_balance_all += balance

        # Aggregated consumption from transactions
        if wallet:
            tx_usage = wallet.transactions.filter(kind="USAGE").aggregate(
                spent=Sum("amount_toman"),
                cost_usd=Sum("cost_usd"),
                calls=Sum("calls"),
            )
            spent = abs(tx_usage["spent"] or Decimal("0"))
            cost_usd = tx_usage["cost_usd"] or Decimal("0")
            calls = tx_usage["calls"] or 0
        else:
            spent = Decimal("0")
            cost_usd = Decimal("0")
            calls = 0

        raw_toman = (cost_usd * usd_rate).quantize(Decimal("1"))
        markup_toman = (raw_toman * (markup_percent / Decimal("100"))).quantize(Decimal("1"))
        total_payable = raw_toman + markup_toman

        total_spent_all += spent
        total_cost_usd_all += cost_usd
        total_raw_toman_all += raw_toman
        total_payable_all += total_payable
        total_calls_all += calls

        # Business associated Instagram leads & senders
        tenant_key = f"tenant_{b.id}"
        b_leads_count = InstagramEngineLead.objects.filter(tenant_id=tenant_key).count()
        b_dms_sent = InstagramEngineLead.objects.filter(tenant_id=tenant_key, status="DM_SENT").count()
        b_senders = InstagramEngineAccount.objects.filter(tenant_id=tenant_key, role="SENDER")

        financial_rows.append({
            "business": b,
            "balance": balance,
            "spent": spent,
            "cost_usd": cost_usd,
            "raw_toman": raw_toman,
            "markup_percent": markup_percent,
            "markup_toman": markup_toman,
            "total_payable": total_payable,
            "calls": calls,
            "leads_count": b_leads_count,
            "dms_sent": b_dms_sent,
            "senders": b_senders,
            "has_credit": balance > 0,
        })

    # 6. Twitter / X Scraper Accounts
    try:
        twitter_scrapers = TwitterEngineAccount.objects.all().order_by("-id")
        twitter_scrapers_total = twitter_scrapers.count()
        twitter_scrapers_active = twitter_scrapers.filter(status="ACTIVE", is_active=True).count()
        twitter_scrapers_cooldown = twitter_scrapers.filter(status="COOLDOWN").count()
        twitter_scrapers_challenged = twitter_scrapers.filter(status="CHALLENGED").count()
        twitter_scrapers_banned = twitter_scrapers.filter(status="BANNED").count()
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

    # 9. Comprehensive Crawler & Query Intelligence Metrics
    try:
        now_dt = dj_timezone.now()
        thirty_days_ago = now_dt - timedelta(days=30)
        tehran_tz = zoneinfo.ZoneInfo("Asia/Tehran")

        # 1. Instagram Crawl & Quality KPIs
        ig_posts_total = InstagramCrawledPost.objects.count()
        ig_posts_approved = InstagramCrawledPost.objects.filter(comment_count__gte=10, posted_at__gte=thirty_days_ago).count()
        ig_posts_rejected = max(0, ig_posts_total - ig_posts_approved)
        ig_comments_total = InstagramCrawledPost.objects.aggregate(s=Sum("comment_count"))["s"] or 0
        ig_comments_approved = InstagramEngineLead.objects.count()
        ig_comments_rejected = max(0, ig_comments_total - ig_comments_approved)

        # Approved Instagram Posts
        approved_ig_posts = list(
            InstagramCrawledPost.objects.filter(comment_count__gte=10, posted_at__gte=thirty_days_ago)
            .order_by("-crawled_at")[:40]
        )
        for p in approved_ig_posts:
            p.post_url = f"https://www.instagram.com/p/{p.media_code}/" if p.media_code else ""
            p.posted_jalali = format_jalali_date(p.posted_at.astimezone(tehran_tz).date(), persian_digits=True) if p.posted_at else "—"
            p.crawled_jalali = format_jalali_date(p.crawled_at.astimezone(tehran_tz).date(), persian_digits=True) if p.crawled_at else "—"

        # 2. Twitter / X Crawl & Quality KPIs
        x_tweets_inspected = XCrawledPost.objects.count()
        x_opps_qs = Opportunity.objects.filter(source_platform="x")
        x_leads_count = x_opps_qs.count()
        x_tweets_approved = x_opps_qs.exclude(status="REJECTED").count()
        x_tweets_rejected = max(0, x_tweets_inspected - x_tweets_approved)

        # Approved Twitter Tweets
        approved_x_tweets = list(
            x_opps_qs.exclude(status="REJECTED")
            .select_related("customer", "ai_analysis", "category", "business")
            .order_by("-created_at")[:40]
        )
        for t in approved_x_tweets:
            t.created_jalali = format_jalali_date(t.created_at.astimezone(tehran_tz).date(), persian_digits=True) if t.created_at else "—"
            handle = t.customer.source_username.lstrip("@") if (t.customer and t.customer.source_username) else ""
            t.author_handle_clean = handle
            if handle and t.source_message_id:
                t.tweet_url = f"https://x.com/{handle}/status/{t.source_message_id}"
            elif t.source_message_id:
                t.tweet_url = f"https://x.com/i/web/status/{t.source_message_id}"
            elif handle:
                t.tweet_url = f"https://x.com/{handle}"
            else:
                t.tweet_url = ""

        # 3. Query Intelligence (Bayesian Thompson Sampling)
        all_stats_qs = InstagramSourceStat.objects.select_related("business").all()
        query_items = []
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
            query_items.append({
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
            })

        queries_top_rated = sorted(query_items, key=lambda x: (-x["score"], -x["leads"]))
        queries_top_gainers = sorted([q for q in query_items if q["delta"] > 0], key=lambda x: -x["delta"])
        queries_top_losers = sorted([q for q in query_items if q["delta"] < 0], key=lambda x: x["delta"])

    except Exception as exc:
        logger.warning("Could not calculate crawler audit metrics: %s", exc)
        ig_posts_total = ig_posts_approved = ig_posts_rejected = 0
        ig_comments_total = ig_comments_approved = ig_comments_rejected = 0
        approved_ig_posts = []
        x_tweets_inspected = x_tweets_approved = x_leads_count = x_tweets_rejected = 0
        approved_x_tweets = []
        queries_top_rated = queries_top_gainers = queries_top_losers = []

    # Load outreach pitch config
    from pathlib import Path
    import json
    config_path = Path("data/sarv_outreach_config.json")
    sender_pitches = {}
    if config_path.exists():
        try:
            cfg = json.loads(config_path.read_text(encoding="utf-8"))
            sender_pitches = cfg.get("sender_pitches", {})
        except Exception:
            pass

    accounts_with_pitches = []
    for s in senders:
        p = sender_pitches.get(s.username) or sender_pitches.get(s.username.lower(), {})
        accounts_with_pitches.append({
            "obj": s,
            "consultant_name": p.get("consultant_name", s.username),
            "template": p.get("template", ""),
        })

    default_pitch = ""
    default_consultant = ""
    if accounts_with_pitches:
        default_pitch = accounts_with_pitches[0]["template"]
        default_consultant = accounts_with_pitches[0]["consultant_name"]
    if not default_pitch:
        default_pitch = (
            "درود بر شما \n"
            "بنده مشاور و پشتیبانی تخصصی دندان پزشکی هستم، متوجه شدم پیگیر امور زیبایی و سلامتی دندونتون هستید\n"
            "میتونید هر سوالی دارید از بنده بپرسید. \n"
            "اگر هم فکر میکنید موثر هست ، شماره تماستون رو بزارید برای ۱۰ دقیقه مشاوره تخصصی رایگان با شما تماس میگیرم"
        )
        default_consultant = "مشاور دندان‌پزشکی"

    outreach_replied_leads = InstagramEngineLead.objects.filter(has_replied=True).order_by("-reply_at")[:50]
    outreach_replied_count = InstagramEngineLead.objects.filter(has_replied=True).count()

    context = {
        "nav": "manage",
        "proxy_configs": proxy_configs,
        "proxy_configs_total": proxy_configs_total,
        "proxy_configs_active": proxy_configs_active,
        "scrapers": scrapers,
        "scrapers_total": scrapers_total,
        "scrapers_active": scrapers_active,
        "scrapers_cooldown": scrapers_cooldown,
        "scrapers_challenged": scrapers_challenged,
        "scrapers_banned": scrapers_banned,
        "senders": senders,
        "senders_total": senders_total,
        "senders_active": senders_active,
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
        "total_payable_all": total_payable_all,
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
        "outreach_dms_list": InstagramEngineLead.objects.filter(status__in=["DM_SENT", "REPLIED", "FOLLOWUP_SENT"]).order_by("-updated_at")[:60],
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


def _normalize_digits(s: str) -> str:
    """Converts Persian and Arabic digits to ASCII digits."""
    if not s:
        return ""
    trans = str.maketrans("۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩", "01234567890123456789")
    return s.translate(trans).strip()


@manage_access_required
@require_POST
def manage_account_add(request):
    """
    Adds a new Instagram account (either SCRAPER or SENDER) and attempts immediate live authentication.
    If Instagram triggers an email/SMS verification code or 2FA challenge, captures the challenge state,
    sets status to CHALLENGED, and returns `requires_code: True` for the modal to prompt for the code.
    """
    is_ajax = (
        request.headers.get("x-requested-with") == "XMLHttpRequest"
        or request.content_type == "application/json"
        or "application/json" in request.headers.get("Accept", "")
    )

    username = request.POST.get("username", "").strip().lstrip("@")
    password = request.POST.get("password", "").strip()
    sessionid = (request.POST.get("sessionid") or "").strip()
    role = request.POST.get("role", "SCRAPER").strip().upper()
    proxy_url = request.POST.get("proxy_url", "").strip() or None
    tenant_id = request.POST.get("tenant_id", "tenant_default").strip()

    if not username or (not password and not sessionid):
        err_msg = "نام کاربری و (کلمه عبور یا کوکی sessionid) اینستاگرام الزامی است."
        if is_ajax:
            return JsonResponse({"success": False, "requires_code": False, "message": err_msg})
        messages.error(request, err_msg)
        return redirect("manage:dashboard")

    if role not in ("SCRAPER", "SENDER"):
        role = "SCRAPER"

    # Check if account already exists
    existing = InstagramEngineAccount.objects.filter(username__iexact=username).first()
    if existing:
        if existing.status == "ACTIVE" and existing.session_json:
            err_msg = f"اکانت @{username} قبلاً در سیستم ثبت شده و فعال است."
            if is_ajax:
                return JsonResponse({"success": False, "requires_code": False, "message": err_msg})
            messages.warning(request, err_msg)
            return redirect("manage:dashboard")
        # Update credentials on existing account to allow re-authentication
        account = existing
        if password:
            account.password = password
        account.role = role
        account.tenant_id = tenant_id
        if proxy_url:
            account.proxy_url = proxy_url
        account.status = "CHALLENGED"
        account.save()
    else:
        account = InstagramEngineAccount.objects.create(
            tenant_id=tenant_id,
            username=username,
            password=password or "session_only",
            role=role,
            proxy_url=proxy_url,
            status="CHALLENGED",
        )

    # 1. Test Proxy Reachability if specified
    proxy_ok = True
    proxy_msg = ""
    if account.proxy_url:
        proxy_ok, proxy_msg = _test_proxy_connection(account.proxy_url)
        if not proxy_ok:
            account.status = "BANNED"
            account.save(update_fields=["status"])
            err_msg = f"خطای پروکسی: {proxy_msg}"
            if is_ajax:
                return JsonResponse({
                    "success": False,
                    "requires_code": False,
                    "account_id": account.id,
                    "username": account.username,
                    "message": err_msg,
                })
            messages.error(request, err_msg)
            return redirect("manage:dashboard")

    # 2. Attempt real-time Instagram authentication
    try:
        from instagrapi import Client
        from instagrapi.exceptions import (
            ChallengeRequired,
            TwoFactorRequired,
            BadPassword,
            FeedbackRequired,
            PleaseWaitFewMinutes,
        )

        cl = Client()

        # Apply unique realistic device profile (Pixel 8 Pro, S24 Ultra, Xiaomi 14, etc.)
        try:
            from apps.discovery.device_emulator import apply_device_profile_to_client, ensure_account_device_profile
            dev_profile = ensure_account_device_profile(account)
            apply_device_profile_to_client(cl, account)
        except Exception as dev_err:
            dev_profile = {}
            logger.warning(f"Could not apply device profile for @{account.username}: {dev_err}")

        if account.proxy_url:
            cl.set_proxy(account.proxy_url)

        def _challenge_detector(u, choice=None):
            raise ChallengeRequired(f"کد تایید به روش {choice or 'ایمیل/پیامک'} ارسال شد.")
        cl.challenge_code_handler = _challenge_detector

        sessionid = (request.POST.get("sessionid") or "").strip()
        if sessionid:
            logger.info(f"Attempting direct sessionid login for @{account.username}...")
            try:
                cl.login_by_sessionid(sessionid)
                settings = cl.get_settings()
                account.session_json = settings
                account.device_settings = dev_profile or {"settings": settings}
                account.status = "ACTIVE"
                account.save(update_fields=["session_json", "device_settings", "status"])
                role_fa = "اسکرپر" if role == "SCRAPER" else "فرستنده / پاسخگو"
                succ_msg = f"اکانت @{account.username} با موفقیت توسط کوکی sessionid متصل و فعال شد!"
                if is_ajax:
                    return JsonResponse({
                        "success": True,
                        "requires_code": False,
                        "status": "ACTIVE",
                        "account_id": account.id,
                        "username": account.username,
                        "message": succ_msg,
                    })
                messages.success(request, succ_msg)
                return redirect("manage:dashboard")
            except Exception as se:
                logger.warning(f"sessionid login failed: {se}")

        logger.info(f"Attempting live Instagram login for newly added account @{account.username}...")
        try:
            cl.login(account.username, account.password)
            # Verified and logged in successfully
            settings = cl.get_settings()
            account.session_json = settings
            account.device_settings = dev_profile or {"settings": settings}
            account.status = "ACTIVE"
            account.save(update_fields=["session_json", "device_settings", "status"])

            role_fa = "اسکرپر" if role == "SCRAPER" else "فرستنده / پاسخگو"
            succ_msg = f"اکانت @{account.username} با نقش «{role_fa}» با موفقیت متصل، احراز هویت و فعال شد!"
            if is_ajax:
                return JsonResponse({
                    "success": True,
                    "requires_code": False,
                    "status": "ACTIVE",
                    "account_id": account.id,
                    "username": account.username,
                    "message": succ_msg,
                })
            messages.success(request, succ_msg)
            return redirect("manage:dashboard")

        except (ChallengeRequired, TwoFactorRequired) as ce:
            account.status = "CHALLENGED"
            account.device_settings = {
                "settings": cl.get_settings(),
                "last_json": getattr(cl, "last_json", None),
                "challenge_hint": str(ce),
            }
            account.save(update_fields=["status", "device_settings"])
            warn_msg = f"اینستاگرام کد تایید امنیتی به ایمیل یا پیامک اکانت ارسال کرد. لطفاً کد را وارد کنید."
            if is_ajax:
                return JsonResponse({
                    "success": False,
                    "requires_code": True,
                    "account_id": account.id,
                    "username": account.username,
                    "message": "کد تایید امنیتی اینستاگرام به ایمیل یا شماره موبایل اکانت ارسال شد. لطفاً کد ۶ رقمی را وارد نمایید:",
                })
            messages.warning(request, warn_msg)
            return redirect("manage:dashboard")

        except (FeedbackRequired, PleaseWaitFewMinutes) as fe:
            account.status = "COOLDOWN"
            account.save(update_fields=["status"])
            err_msg = f"اینستاگرام محدودیت موقت (Cooldown) اعمال کرده است: {fe}"
            if is_ajax:
                return JsonResponse({
                    "success": False,
                    "requires_code": False,
                    "account_id": account.id,
                    "username": account.username,
                    "message": err_msg,
                })
            messages.error(request, err_msg)
            return redirect("manage:dashboard")

        except BadPassword as bpe:
            account.status = "BANNED"
            account.save(update_fields=["status"])
            err_msg = f"نام کاربری یا کلمه عبور اینستاگرام اشتباه است: {bpe}"
            if is_ajax:
                return JsonResponse({
                    "success": False,
                    "requires_code": False,
                    "account_id": account.id,
                    "username": account.username,
                    "message": err_msg,
                })
            messages.error(request, err_msg)
            return redirect("manage:dashboard")

        except Exception as le:
            le_str = str(le).lower()
            if any(k in le_str for k in ["challenge", "checkpoint", "two_step", "two_factor", "two-factor", "verification", "sms", "code"]):
                account.status = "CHALLENGED"
                account.device_settings = {
                    "settings": cl.get_settings(),
                    "last_json": getattr(cl, "last_json", None),
                    "challenge_hint": str(le),
                }
                account.save(update_fields=["status", "device_settings"])
                if is_ajax:
                    return JsonResponse({
                        "success": False,
                        "requires_code": True,
                        "account_id": account.id,
                        "username": account.username,
                        "message": "اینستاگرام درخواست کد تایید هویت (ایمیل/پیامک) داده است. لطفاً کد را وارد کنید:",
                    })
                messages.warning(request, "اینستاگرام درخواست کد تایید هویت داده است.")
                return redirect("manage:dashboard")
            else:
                account.status = "BANNED"
                account.save(update_fields=["status"])
                err_msg = f"خطای ورود به اینستاگرام: {le}"
                if is_ajax:
                    return JsonResponse({
                        "success": False,
                        "requires_code": False,
                        "account_id": account.id,
                        "username": account.username,
                        "message": err_msg,
                    })
                messages.error(request, err_msg)
                return redirect("manage:dashboard")

    except ImportError:
        account.status = "ACTIVE"
        account.save(update_fields=["status"])
        role_fa = "اسکرپر" if role == "SCRAPER" else "فرستنده / پاسخگو"
        succ_msg = f"اکانت @{username} با نقش «{role_fa}» ثبت شد (محیط بدون instagrapi)."
        if is_ajax:
            return JsonResponse({
                "success": True,
                "requires_code": False,
                "status": "ACTIVE",
                "account_id": account.id,
                "username": account.username,
                "message": succ_msg,
            })
        messages.success(request, succ_msg)
        return redirect("manage:dashboard")

    except Exception as exc:
        err_msg = f"خطای غیرمنتظره در ثبت اکانت: {exc}"
        if is_ajax:
            return JsonResponse({
                "success": False,
                "requires_code": False,
                "account_id": account.id,
                "username": account.username,
                "message": err_msg,
            })
        messages.error(request, err_msg)
        return redirect("manage:dashboard")


@manage_access_required
@require_POST
def manage_account_delete(request, pk):
    """Deletes an Instagram account."""
    account = get_object_or_404(InstagramEngineAccount, pk=pk)
    username = account.username
    account.delete()
    messages.success(request, f"اکانت @{username} با موفقیت از سیستم حذف گردید.")
    return redirect("manage:dashboard")


@manage_access_required
@require_POST
def manage_account_set_session(request, pk):
    """
    Directly updates or injects a valid Instagram sessionid cookie from a web browser.
    This bypasses username/password authentication hurdles and security challenges.
    """
    account = get_object_or_404(InstagramEngineAccount, pk=pk)
    raw_sessionid = (request.POST.get("sessionid") or "").strip()
    if not raw_sessionid:
        return JsonResponse({"success": False, "message": "لطفاً مقدار کوکی sessionid را وارد نمایید."})

    # Clean sessionid in case full cookie header or key=value was pasted
    sessionid = raw_sessionid
    if "sessionid=" in sessionid:
        import re
        m = re.search(r"sessionid=([^;\s]+)", sessionid)
        if m:
            sessionid = m.group(1).strip()
    sessionid = sessionid.strip('"').strip("'").strip(";").strip()

    from instagrapi import Client
    cl = Client()
    if account.proxy_url:
        cl.set_proxy(account.proxy_url)

    from apps.discovery.device_emulator import apply_device_profile_to_client, ensure_account_device_profile
    dev_profile = ensure_account_device_profile(account)
    try:
        apply_device_profile_to_client(cl, account)
    except Exception as dev_err:
        logger.warning(f"Could not apply device profile for @{account.username}: {dev_err}")

    # Note: Do NOT call cl.set_settings(account.session_json) here!
    # Because old session_json contains expired Bearer authorization tokens and mismatched ds_user_id cookies
    # which cause Instagram to reject the new sessionid with 401 Unauthorized or login_required!

    try:
        cl.login_by_sessionid(sessionid)
        settings = cl.get_settings()
        account.session_json = settings
        account.device_settings = dev_profile or {"settings": settings}
        account.status = "ACTIVE"
        account.status_message = ""
        account.cooldown_until = None
        account.save(update_fields=["session_json", "device_settings", "status", "status_message", "cooldown_until"])
        return JsonResponse({
            "success": True,
            "status": "ACTIVE",
            "message": f"سشن اکانت @{account.username} با موفقیت ثبت شد و اکانت فعال گردید (بدون چالش پسورد).",
        })
    except Exception as exc:
        from apps.discovery.ig_session import redact
        uname = account.username
        account.status = "CHALLENGED"
        account.status_message = f"سشن نامعتبر یا منقضی است: {redact(exc, 160)}"
        account.save(update_fields=["status", "status_message"])
        return JsonResponse({
            "success": False,
            "deleted": False,
            "status": "CHALLENGED",
            "message": f"سشن واردشده برای @{uname} معتبر نبود ({redact(exc, 160)}). لطفاً مطمئن شوید کوکی sessionid را از مرورگری که در همان لحظه در اکانت لاگین است کپی کرده‌اید و در صورت داشتن پروکسی، اتصال پروکسی برقرار است.",
        })


@manage_access_required
@require_POST
def manage_account_test(request, pk):
    """
    Performs a real-time connectivity and status health check on an account.
    Returns JSON response for instant asynchronous UI update.
    """
    account = get_object_or_404(InstagramEngineAccount, pk=pk)

    # 1. Test Proxy Reachability
    proxy_ok = True
    proxy_msg = ""
    if account.proxy_url:
        proxy_ok, proxy_msg = _test_proxy_connection(account.proxy_url)
        if not proxy_ok:
            return JsonResponse({
                "success": False,
                "status": account.status,
                "message": f"خطای پروکسی: {proxy_msg}",
            })

    # 2. Check Cooldown
    now = dj_timezone.now()
    if account.cooldown_until and account.cooldown_until > now:
        rem_seconds = int((account.cooldown_until - now).total_seconds())
        rem_min = rem_seconds // 60
        return JsonResponse({
            "success": True,
            "status": "COOLDOWN",
            "message": f"اکانت در دوره استراحت است (حدود {rem_min} دقیقه باقی‌مانده)",
        })

    # 3. Live Instagram Login / Session Test
    live_status = account.status
    msg = "اتصال و وضعیت اکانت با موفقیت تایید شد."
    challenge_hint = ""
    try:
        from instagrapi import Client
        from instagrapi.exceptions import (ChallengeRequired, FeedbackRequired, BadPassword, TwoFactorRequired,
                                           PleaseWaitFewMinutes)
        cl = Client()
        if account.proxy_url:
            cl.set_proxy(account.proxy_url)

        def _non_interactive_challenge(username, choice=None):
            raise ChallengeRequired(f"کد تایید امنیتی به روش {choice} ارسال شد.")
        cl.challenge_code_handler = _non_interactive_challenge

        if account.device_settings and isinstance(account.device_settings, dict) and "model" in account.device_settings:
            try:
                cl.set_device(account.device_settings, reset=False)
            except Exception:
                pass

        if account.session_json:
            cookies = account.session_json.get("cookies", {})
            sess_id = cookies.get("sessionid")
            if sess_id:
                try:
                    cl.login_by_sessionid(sess_id)
                    live_status = "ACTIVE"
                    account.status = "ACTIVE"
                    account.session_json = cl.get_settings()
                    account.save(update_fields=["status", "session_json"])
                    msg = f"سشن اینستاگرام کاملاً معتبر است و ارتباط با موفقیت برقرار شد. {proxy_msg}"
                except Exception as se:
                    se_str = str(se).lower()
                    if "pleasewait" in se_str or "429" in se_str:
                        live_status = "COOLDOWN"
                        account.status = "COOLDOWN"
                        account.cooldown_until = dj_timezone.now() + timedelta(minutes=15)
                        account.save(update_fields=["status", "cooldown_until"])
                        msg = f"محدودیت موقت اینستاگرام (PleaseWaitFewMinutes). اکانت برای ۱۵ دقیقه در حالت استراحت قرار گرفت."
                    else:
                        live_status = "CHALLENGED"
                        account.status = "CHALLENGED"
                        account.save(update_fields=["status"])
                        msg = f"سشن اینستاگرام منقضی یا نامعتبر شده است. لطفاً sessionid جدید را ثبت نمایید: {se}"
            else:
                cl.set_settings(account.session_json)
                try:
                    cl.get_timeline_feed()
                    live_status = "ACTIVE"
                    account.status = "ACTIVE"
                    account.save(update_fields=["status"])
                    msg = f"سشن اینستاگرام معتبر است و ارتباط با موفقیت برقرار شد. {proxy_msg}"
                except Exception as se:
                    live_status = "CHALLENGED"
                    account.status = "CHALLENGED"
                    account.save(update_fields=["status"])
                    msg = f"سشن منقضی شده است: {se}"
        else:
            # First-time login test
            try:
                cl.login(account.username, account.password)
                new_settings = cl.get_settings()
                account.session_json = new_settings
                account.device_settings = {"settings": new_settings}
                account.status = "ACTIVE"
                account.save(update_fields=["session_json", "device_settings", "status"])
                msg = f"ورود اولیه با موفقیت انجام و سشن ذخیره گردید. {proxy_msg}"
            except (ChallengeRequired, TwoFactorRequired) as ce:
                live_status = "CHALLENGED"
                account.status = "CHALLENGED"
                account.device_settings = {
                    "settings": cl.get_settings(),
                    "last_json": getattr(cl, "last_json", None),
                    "challenge_hint": str(ce),
                }
                account.save(update_fields=["status", "device_settings"])
                challenge_hint = str(ce)
                msg = f"اینستاگرام کد تایید امنیتی (ایمیل/پیامک) درخواست کرده است."
            except (FeedbackRequired, PleaseWaitFewMinutes) as fe:
                live_status = "COOLDOWN"
                account.status = "COOLDOWN"
                account.cooldown_until = dj_timezone.now() + timedelta(minutes=15)
                account.save(update_fields=["status", "cooldown_until"])
                msg = f"اینستاگرام محدودیت موقت چند دقیقه‌ای اعمال کرده است (Cooldown): {fe}"
            except Exception as le:
                le_str = str(le).lower()
                if "pleasewaitfewminutes" in le_str or "429" in le_str or "feedback" in le_str:
                    live_status = "COOLDOWN"
                    account.cooldown_until = dj_timezone.now() + timedelta(minutes=15)
                    msg = "اینستاگرام محدودیت موقت اعمال کرده است (PleaseWaitFewMinutes)."
                elif "suspended" in le_str:
                    live_status = "CHALLENGED"
                    msg = "اکانت توسط اینستاگرام به صفحه تعلیق (Suspended) منتقل شده است."
                elif "challenge" in le_str or "checkpoint" in le_str or "two_step" in le_str:
                    live_status = "CHALLENGED"
                    account.device_settings = {
                        "settings": cl.get_settings(),
                        "last_json": getattr(cl, "last_json", None),
                        "challenge_hint": str(le),
                    }
                    msg = "اینستاگرام چالش امنیتی (کد تایید) درخواست کرده است."
                elif "bad_password" in le_str or "password" in le_str:
                    live_status = "BANNED"
                    msg = f"کلمه عبور اشتباه است: {le}"
                else:
                    live_status = "COOLDOWN"
                    msg = f"خطای ورود اولیه (استراحت موقت): {le}"
                account.status = live_status
                account.save(update_fields=["status", "device_settings", "cooldown_until"] if account.cooldown_until else ["status", "device_settings"])

    except ImportError:
        if proxy_ok:
            live_status = account.status
            msg = f"پروکسی و مشخصات ذخیره‌شده بررسی شد (پروکسی متصل است). {proxy_msg}"
        else:
            live_status = "BANNED"
            msg = f"پروکسی قطع می‌باشد: {proxy_msg}"

    except Exception as exc:
        msg = f"خطای غیرمنتظره در بررسی وضعیت: {exc}"

    return JsonResponse({
        "success": live_status == "ACTIVE",
        "status": account.status,
        "challenge_required": account.status == "CHALLENGED",
        "challenge_hint": challenge_hint,
        "message": msg,
    })


@manage_access_required
@require_POST
def manage_account_submit_code(request, pk):
    """
    Submits a 2FA or email/SMS verification code for an account in CHALLENGED status.
    Resolves the challenge, verifies session with timeline feed, saves valid session_json,
    and updates status to ACTIVE.
    """
    account = get_object_or_404(InstagramEngineAccount, pk=pk)
    
    # Accept JSON or form POST
    raw_code = ""
    if request.content_type == "application/json":
        try:
            body = json.loads(request.body.decode("utf-8"))
            raw_code = str(body.get("code", "")).strip()
        except Exception:
            pass
    if not raw_code:
        raw_code = request.POST.get("code", "").strip()

    code = _normalize_digits(raw_code).replace(" ", "").replace("-", "")

    if not code:
        return JsonResponse({"success": False, "message": "لطفاً کد تایید را وارد کنید."})

    try:
        from instagrapi import Client
        cl = Client()
        if account.proxy_url:
            cl.set_proxy(account.proxy_url)

        saved_data = account.device_settings or {}
        saved_settings = (
            saved_data.get("settings")
            if isinstance(saved_data, dict) and "settings" in saved_data
            else saved_data
        )
        last_json = saved_data.get("last_json") if isinstance(saved_data, dict) else None

        if saved_settings and isinstance(saved_settings, dict):
            try:
                cl.set_settings(saved_settings)
            except Exception as se:
                logger.warning(f"Could not restore settings for @{account.username}: {se}")

        # Set handler to return user-entered code
        cl.challenge_code_handler = lambda u, c=None: code

        success = False
        last_error = None

        # 1. First attempt: If last_json was saved with a pending contact challenge
        if last_json and isinstance(last_json, dict) and "challenge" in last_json:
            try:
                logger.info(f"Attempting challenge_resolve from saved last_json for @{account.username} with code {code}...")
                cl.last_json = last_json
                if cl.challenge_resolve(last_json):
                    success = True
                    logger.info(f"challenge_resolve succeeded for @{account.username}")
            except Exception as ce1:
                last_error = ce1
                logger.warning(f"challenge_resolve using saved last_json failed: {ce1}")

        # 2. Second attempt: Direct login with verification_code (handles Bloks 2FA / TOTP / SMS 2FA)
        if not success:
            try:
                logger.info(f"Attempting cl.login(verification_code='{code}') for @{account.username}...")
                cl.login(account.username, account.password, verification_code=code)
                success = True
                logger.info(f"cl.login with verification_code succeeded for @{account.username}")
            except Exception as le:
                last_error = le
                logger.warning(f"cl.login with verification_code failed: {le}")

                # 3. Third attempt: If login triggered challenge_required and cl.last_json has challenge
                cl_last = getattr(cl, "last_json", None)
                if isinstance(cl_last, dict) and "challenge" in cl_last:
                    try:
                        logger.info(f"Attempting challenge_resolve with newly received challenge for @{account.username}...")
                        if cl.challenge_resolve(cl_last):
                            success = True
                            logger.info(f"challenge_resolve with new challenge succeeded for @{account.username}")
                    except Exception as ce2:
                        last_error = ce2
                        logger.warning(f"challenge_resolve retry failed: {ce2}")

        if not success:
            err_str = str(last_error or "کد تایید وارد شده نامعتبر یا منقضی است.")
            account.status_message = f"کد رد شد: {err_str}"[:250]
            account.save(update_fields=["status_message"])
            return JsonResponse({
                "success": False,
                "status": account.status,
                "message": f"خطا در تایید کد امنیتی: {err_str}",
            })

        # Successful verification -> verify feed and save session
        try:
            cl.get_timeline_feed()
        except Exception as fe:
            logger.debug(f"Feed test after verification: {fe}")

        new_settings = cl.get_settings()
        account.session_json = new_settings
        account.device_settings = {"settings": new_settings}
        account.status = "ACTIVE"
        account.status_message = ""
        account.cooldown_until = None
        account.save(update_fields=["session_json", "device_settings", "status", "status_message", "cooldown_until"])

        return JsonResponse({
            "success": True,
            "status": "ACTIVE",
            "message": f"اکانت @{account.username} با موفقیت تایید و سشن دائمی آن فعال شد!",
        })
    except Exception as exc:
        logger.warning(f"Failed verification for @{account.username}: {exc}")
        return JsonResponse({
            "success": False,
            "status": account.status,
            "message": f"خطا در ثبت کد تایید: {exc}",
        })


@manage_access_required
@require_GET
def manage_leads_export_csv(request):
    """
    Exports all extracted leads as a UTF-8 BOM CSV dataset.
    """
    import csv
    from django.http import HttpResponse

    leads = InstagramEngineLead.objects.all().order_by("-intent_score", "-created_at")

    response = HttpResponse(content_type="text/csv; charset=utf-8-sig")
    response["Content-Disposition"] = 'attachment; filename="instagram_leads_dataset.csv"'
    response.write('\ufeff')

    writer = csv.writer(response)
    writer.writerow([
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
    ])

    for l in leads:
        writer.writerow([
            l.ig_user_pk,
            l.username,
            l.full_name or "",
            l.profile_url or f"https://www.instagram.com/{l.username}/",
            l.comment_text,
            l.source_post_url or "",
            l.post_caption_snippet or "",
            l.matched_cta or "",
            f"{l.intent_score:.2f}",
            l.get_status_display(),
            l.tenant_id,
            l.created_at.strftime("%Y-%m-%d %H:%M:%S") if l.created_at else "",
        ])

    return response


@manage_access_required
@require_GET
def manage_leads_export_json(request):
    """
    Exports all extracted leads as a JSON dataset.
    """
    leads = InstagramEngineLead.objects.all().order_by("-intent_score", "-created_at")
    data = []
    for l in leads:
        data.append({
            "ig_user_pk": l.ig_user_pk,
            "username": l.username,
            "full_name": l.full_name,
            "profile_url": l.profile_url or f"https://www.instagram.com/{l.username}/",
            "comment_text": l.comment_text,
            "source_post_url": l.source_post_url,
            "post_caption_snippet": l.post_caption_snippet,
            "matched_cta": l.matched_cta,
            "intent_score": l.intent_score,
            "status": l.status,
            "tenant_id": l.tenant_id,
            "created_at": l.created_at.isoformat() if l.created_at else None,
        })

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

    executable = shutil.which("twitter") or shutil.which("twitter-cli") or ("/usr/local/bin/twitter" if os.path.exists("/usr/local/bin/twitter") else None)
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
                    return True, "COOLDOWN", "اکانت با محدودیت موقت ریت‌لیمیت توییتر مواجه شد (۱۵ دقیقه استراحت)."
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
            pass

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
    is_ajax = (
        request.headers.get("x-requested-with") == "XMLHttpRequest"
        or request.content_type == "application/json"
        or "application/json" in request.headers.get("Accept", "")
    )
    username = request.POST.get("username", "").strip().lstrip("@")
    password = request.POST.get("password", "").strip()
    auth_token = request.POST.get("auth_token", "").strip()
    ct0 = request.POST.get("ct0", "").strip()
    proxy_url = request.POST.get("proxy_url", "").strip() or "socks5h://xray:1080"

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



