"""Data for the /manage/ dashboard, one function per area. Each returns the template context entries it owns."""
import logging
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta, tzinfo
from decimal import Decimal

from django.db.models import Count, Q, Sum

import zoneinfo

from apps.billing.models import WalletTransaction
from apps.discovery.constants import DM_DELIVERED_STATUSES, HIGH_INTENT_THRESHOLD
from apps.discovery.models import (InstagramCrawlExecution, InstagramEngineAccount, InstagramEngineLead,
                                   ProxyConfig, TwitterEngineAccount)
from apps.discovery.tenants import canonical_tenant, tenant_key

logger = logging.getLogger("management")


@dataclass(frozen=True)
class Clock:
    tehran_tz: tzinfo
    now_utc: datetime
    now_tehran: datetime
    one_day_ago: datetime
    thirty_days_ago: datetime

    @classmethod
    def at(cls, now_utc: datetime) -> "Clock":
        tehran_tz = zoneinfo.ZoneInfo("Asia/Tehran")
        return cls(tehran_tz, now_utc, now_utc.astimezone(tehran_tz), now_utc - timedelta(hours=24),
                   now_utc - timedelta(days=30))


def ig_window(clock: Clock) -> dict:
    """Same operating window as the anti-ban manager (INSTAGRAM_WINDOW_START_HOUR / _END_HOUR, Tehran time)."""
    from apps.discovery.anti_ban import (INSTAGRAM_WINDOW_END_HOUR, INSTAGRAM_WINDOW_START_HOUR, in_operating_window,
                                         next_window_start)
    from apps.core.jalali import to_persian_digits

    now_tehran = clock.now_tehran
    is_ig_night_sleep = not in_operating_window(now_tehran.hour)
    ig_window_label = to_persian_digits(f"{INSTAGRAM_WINDOW_START_HOUR:02d}:00 تا {INSTAGRAM_WINDOW_END_HOUR:02d}:00")
    wake_label = ""
    if is_ig_night_sleep:
        minutes_to_wake = max(1, int((next_window_start(now_tehran) - now_tehran).total_seconds() / 60))
        hours_to_wake = minutes_to_wake // 60
        rem_mins = minutes_to_wake % 60
        wake_label = to_persian_digits(f"{INSTAGRAM_WINDOW_START_HOUR:02d}:00")
        ig_sleep_desc = (f"{hours_to_wake} ساعت و {rem_mins} دقیقه تا {wake_label}" if hours_to_wake > 0
                         else f"{rem_mins} دقیقه تا {wake_label}")
    else:
        ig_sleep_desc = ""
    return {"is_ig_night_sleep": is_ig_night_sleep, "ig_window_label": ig_window_label,
            "ig_sleep_desc": ig_sleep_desc, "wake_label": wake_label}


def scraper_accounts(clock: Clock, window: dict) -> dict:
    """Instagram scraper accounts with their crawl totals and a live status badge."""
    now_utc, one_day_ago = clock.now_utc, clock.one_day_ago
    is_ig_night_sleep, wake_label, ig_sleep_desc = window["is_ig_night_sleep"], window["wake_label"], window["ig_sleep_desc"]
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
    return {"scrapers": scrapers, "scrapers_total": scrapers_total, "scrapers_active": scrapers_active,
            "scrapers_cooldown": scrapers_cooldown, "scrapers_challenged": scrapers_challenged,
            "scrapers_banned": scrapers_banned}


def tenant_matrix_and_finance(businesses, usd_rate) -> dict:
    """Leads per platform and credit consumption per business (one grouped query per table, ADM-03)."""
    from apps.discovery.models import Opportunity

    tenant_matrix = []
    matrix_totals = {
        "instagram": 0,
        "telegram": 0,
        "x": 0,
        "total": 0,
        "high_intent": 0,
        "dms_sent": 0,
    }

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

    # Financial & Credit Consumption per Business / Account
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
    return {"tenant_matrix": tenant_matrix, "matrix_totals": matrix_totals, "financial_rows": financial_rows,
            "total_balance_all": total_balance_all, "total_spent_all": total_spent_all,
            "total_cost_usd_all": total_cost_usd_all, "total_raw_toman_all": total_raw_toman_all,
            "total_calls_all": total_calls_all}


def twitter_accounts() -> dict:
    """X scraper accounts and their status counts."""
    try:
        twitter_scrapers = list(TwitterEngineAccount.objects.all().order_by("-id"))
        twitter_scrapers_total = len(twitter_scrapers)
        twitter_scrapers_active = sum(1 for a in twitter_scrapers if a.status == "ACTIVE" and a.is_active)
        twitter_scrapers_cooldown = sum(1 for a in twitter_scrapers if a.status == "COOLDOWN")
        twitter_scrapers_challenged = sum(1 for a in twitter_scrapers if a.status == "CHALLENGED")
        twitter_scrapers_banned = sum(1 for a in twitter_scrapers if a.status == "BANNED")
    except Exception as exc:
        logger.warning("Could not query TwitterEngineAccount: %s", exc, exc_info=True)
        twitter_scrapers = []
        twitter_scrapers_total = twitter_scrapers_active = twitter_scrapers_cooldown = twitter_scrapers_challenged = twitter_scrapers_banned = 0
    return {"twitter_scrapers": twitter_scrapers, "twitter_scrapers_total": twitter_scrapers_total,
            "twitter_scrapers_active": twitter_scrapers_active, "twitter_scrapers_cooldown": twitter_scrapers_cooldown,
            "twitter_scrapers_challenged": twitter_scrapers_challenged,
            "twitter_scrapers_banned": twitter_scrapers_banned}


def twitter_crawl_runs() -> list:
    """Latest X search runs from the crawler schema."""
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
        logger.warning("Could not query x_search_runs: %s", exc, exc_info=True)
    return twitter_crawl_runs


def proxy_pool() -> dict:
    try:
        proxy_configs = ProxyConfig.objects.all().order_by("local_port")
        proxy_configs_total = proxy_configs.count()
        proxy_configs_active = proxy_configs.filter(status="ACTIVE").count()
    except Exception:
        logger.warning("Could not load the proxy configs", exc_info=True)
        proxy_configs = []
        proxy_configs_total = proxy_configs_active = 0
    return {"proxy_configs": proxy_configs, "proxy_configs_total": proxy_configs_total,
            "proxy_configs_active": proxy_configs_active}
