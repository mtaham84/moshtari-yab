"""Crawler audit and lead funnel of the /manage/ dashboard: X and Instagram KPIs, verified X leads, query scores."""
import logging

from django.conf import settings
from django.db.models import Sum

from apps.core.jalali import format_jalali_date, format_jalali_datetime
from apps.discovery.models import (InstagramCrawledPost, InstagramCrawlExecution, InstagramEngineLead,
                                   InstagramSourceStat, XCrawledPost, XSearchQuery)

logger = logging.getLogger("management")

# What the dashboard shows when the funnel cannot be computed.
EMPTY_FUNNEL = {
    "x_posts_total": 0, "x_posts_24h": 0, "x_leads_total": 0, "x_leads_24h": 0,
    "x_searches_total": 0, "x_searches_24h": 0,
    "x_conversion_rate_total": 0.0, "x_conversion_rate_24h": 0.0,
    "x_tweets_inspected": 0, "x_tweets_approved": 0, "x_leads_count": 0, "x_tweets_rejected": 0,
    "ig_searches_total": 0, "ig_searches_24h": 0, "ig_posts_total": 0, "ig_posts_24h": 0,
    "ig_leads_total": 0, "ig_leads_24h": 0,
    "ig_conversion_rate_total": 0.0, "ig_conversion_rate_24h": 0.0,
    "funnel_total_searches": 0, "funnel_24h_searches": 0, "funnel_total_posts": 0, "funnel_24h_posts": 0,
    "funnel_total_leads": 0, "funnel_24h_leads": 0,
    "funnel_total_conv_rate": 0.0, "funnel_24h_conv_rate": 0.0,
    "ig_posts_approved": 0, "ig_posts_rejected": 0, "ig_comments_total": 0, "ig_comments_approved": 0,
    "ig_comments_rejected": 0,
    "approved_ig_posts": [], "approved_x_tweets": [], "verified_x_leads": [], "ig_queries_list": [],
    "queries_top_rated": [], "queries_top_gainers": [], "queries_top_losers": [],
    "x_active_queries_count": 0, "ig_active_queries_count": 0,
}


def funnel_and_audit(clock, twitter_scrapers) -> dict:
    """Lead conversion funnel & intelligence metrics. Any failure shows the empty funnel (logged)."""
    try:
        x = _x_funnel(clock, twitter_scrapers)
        ig = _ig_funnel(clock)
        out = {**x, **ig}
        out.update(
            funnel_total_searches=x["x_searches_total"] + ig["ig_searches_total"],
            funnel_24h_searches=x["x_searches_24h"] + ig["ig_searches_24h"],
            funnel_total_posts=x["x_posts_total"] + ig["ig_posts_total"],
            funnel_24h_posts=x["x_posts_24h"] + ig["ig_posts_24h"],
            funnel_total_leads=x["x_leads_total"] + ig["ig_leads_total"],
            funnel_24h_leads=x["x_leads_24h"] + ig["ig_leads_24h"],
        )
        out["funnel_total_conv_rate"] = (round((out["funnel_total_leads"] / out["funnel_total_posts"] * 100.0), 2)
                                         if out["funnel_total_posts"] > 0 else 0.0)
        out["funnel_24h_conv_rate"] = (round((out["funnel_24h_leads"] / out["funnel_24h_posts"] * 100.0), 2)
                                       if out["funnel_24h_posts"] > 0 else 0.0)
        out.update(_verified_x_leads(clock))
        out.update(_query_intelligence(clock))
        return out
    except Exception as exc:
        logger.warning("Could not calculate crawler audit and funnel metrics: %s", exc)
        return {k: (list(v) if isinstance(v, list) else v) for k, v in EMPTY_FUNNEL.items()}


def _x_funnel(clock, twitter_scrapers) -> dict:
    """Twitter / X platform KPIs, plus per-account funnel and live status on the scraper rows."""
    from apps.discovery.models import Opportunity

    now_utc, one_day_ago = clock.now_utc, clock.one_day_ago
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
    return {"x_posts_total": x_posts_total, "x_posts_24h": x_posts_24h, "x_leads_total": x_leads_total,
            "x_leads_24h": x_leads_24h, "x_searches_total": x_searches_total, "x_searches_24h": x_searches_24h,
            "x_conversion_rate_total": x_conversion_rate_total, "x_conversion_rate_24h": x_conversion_rate_24h,
            "x_tweets_inspected": x_tweets_inspected, "x_tweets_approved": x_tweets_approved,
            "x_leads_count": x_leads_count, "x_tweets_rejected": x_tweets_rejected}


def _ig_funnel(clock) -> dict:
    """Instagram platform KPIs and the latest approved posts."""
    from apps.discovery.models import Opportunity

    tehran_tz, one_day_ago, thirty_days_ago = clock.tehran_tz, clock.one_day_ago, clock.thirty_days_ago
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
    return {"ig_searches_total": ig_searches_total, "ig_searches_24h": ig_searches_24h,
            "ig_posts_total": ig_posts_total, "ig_posts_24h": ig_posts_24h, "ig_leads_total": ig_leads_total,
            "ig_leads_24h": ig_leads_24h, "ig_conversion_rate_total": ig_conversion_rate_total,
            "ig_conversion_rate_24h": ig_conversion_rate_24h, "ig_posts_approved": ig_posts_approved,
            "ig_posts_rejected": ig_posts_rejected, "ig_comments_total": ig_comments_total,
            "ig_comments_approved": ig_comments_approved, "ig_comments_rejected": ig_comments_rejected,
            "approved_ig_posts": approved_ig_posts}


def _verified_x_leads(clock) -> dict:
    """Verified X leads dataset (dedicated tab), also as model rows for the legacy audit view."""
    from apps.discovery.models import Opportunity

    tehran_tz = clock.tehran_tz
    x_opps_qs = Opportunity.objects.filter(source_platform="x")
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
    return {"verified_x_leads": verified_x_leads, "approved_x_tweets": approved_x_tweets}


def _query_intelligence(clock) -> dict:
    """Query scores (Bayesian Thompson sampling stats) and the Instagram queries list."""
    tehran_tz = clock.tehran_tz
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
    return {"queries_top_rated": queries_top_rated, "queries_top_gainers": queries_top_gainers,
            "queries_top_losers": queries_top_losers, "ig_queries_list": ig_queries_list,
            "x_active_queries_count": x_active_queries_count, "ig_active_queries_count": ig_active_queries_count}
