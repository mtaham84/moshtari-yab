"""The /manage/ dashboard page and the billing settings form on it."""
import logging
from decimal import Decimal

from django.conf import settings
from django.contrib import messages
from django.shortcuts import redirect, render
from django.utils import timezone as dj_timezone
from django.views.decorators.http import require_POST


from apps.businesses.models import Business
from apps.billing.models import BillingSettings
from apps.discovery.models import (
    InstagramEngineAccount, InstagramEngineLead, InstagramCrawlExecution,
    InstagramSourceStat
)
from apps.discovery.constants import DM_DELIVERED_STATUSES, HIGH_INTENT_THRESHOLD
from apps.discovery.channel_config_automation import get_monitored_channels
from apps.discovery.tenants import tenant_keys_for_business

from apps.billing.manage import dashboard_data as data, dashboard_funnel
from apps.billing.manage.access import manage_access_required

logger = logging.getLogger("management")


@manage_access_required
def manage_dashboard(request):
    """
    Main Management Dashboard at /manage/
    Displays Scrapers, Senders & Prioritized Leads Queue, and Financial Consumption.
    """
    from apps.discovery.models import Opportunity

    clock = data.Clock.at(dj_timezone.now())
    window = data.ig_window(clock)
    scraper_ctx = data.scraper_accounts(clock, window)
    scrapers = scraper_ctx["scrapers"]

    # Sender Accounts & Leads
    senders = InstagramEngineAccount.objects.filter(role="SENDER").order_by("-id")
    senders_total = senders.count()
    senders_active = senders.filter(status="ACTIVE").count()

    all_ig_accounts = list(senders) + list(scrapers)
    # Device profiles are assigned when a client is built (device_emulator.apply_device_profile_to_client);
    # a GET of the dashboard writes nothing.

    # Prioritized Leads: Ordered strictly by intent_score DESC, created_at ASC
    leads = InstagramEngineLead.objects.all().order_by("-intent_score", "created_at")[:60]
    total_leads = InstagramEngineLead.objects.count()
    dms_sent_total = InstagramEngineLead.objects.filter(status__in=DM_DELIVERED_STATUSES).count()
    dms_replied_total = InstagramEngineLead.objects.filter(has_replied=True).count()
    high_intent_leads = InstagramEngineLead.objects.filter(intent_score__gte=HIGH_INTENT_THRESHOLD).count()

    # Settings & Exchange Rate
    billing_settings = BillingSettings.get()
    usd_rate = billing_settings.usd_to_toman
    markup_percent = billing_settings.markup_percent

    # Scraper latest activity tracking
    latest_scraper = InstagramEngineAccount.objects.filter(role="SCRAPER", last_action_at__isnull=False).order_by("-last_action_at").first()
    latest_scraper_action_at = latest_scraper.last_action_at if latest_scraper else None
    latest_lead = Opportunity.objects.order_by("-created_at").first()
    latest_lead_at = latest_lead.created_at if latest_lead else None

    businesses = Business.objects.select_related("wallet", "user").all().order_by("id")
    matrix_ctx = data.tenant_matrix_and_finance(businesses, usd_rate)
    twitter_ctx = data.twitter_accounts()
    twitter_crawl_runs = data.twitter_crawl_runs()
    proxy_ctx = data.proxy_pool()
    funnel_ctx = dashboard_funnel.funnel_and_audit(clock, twitter_ctx["twitter_scrapers"])

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
    from apps.discovery.tenants import tenant_pitch
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
    # tenant_4 fallback) come from tenants.tenant_pitch().

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
        **proxy_ctx,
        "proxy_channels": get_monitored_channels(),
        **scraper_ctx,
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
        **twitter_ctx,
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
        **matrix_ctx,
        "businesses": businesses,
        "crawl_executions": InstagramCrawlExecution.objects.all().select_related("business", "account").order_by("-crawled_at")[:60],
        "instagram_source_stats": InstagramSourceStat.objects.select_related("business").order_by("-last_run_at", "business_id")[:150],
        **funnel_ctx,
        "ig_min_comments": settings.INSTAGRAM_MIN_COMMENTS,
        "is_ig_night_sleep": window["is_ig_night_sleep"],
        "ig_window_label": window["ig_window_label"],
        "ig_sleep_desc": window["ig_sleep_desc"],
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
