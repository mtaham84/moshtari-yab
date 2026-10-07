import json
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.views.decorators.csrf import csrf_exempt
from django.utils import timezone
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from apps.businesses.models import Business
from apps.products.models import Product
from apps.core.jalali import parse_jalali_date, format_jalali_date
from .models import DiscoveredLead, CategoryBranchMemory, ProcessedMessageHash, ProductDailyMetric
from .services import (
    evaluate_and_discover_leads,
    send_lead_outreach,
    get_agent_discovery_feed,
    get_performance_analytics,
    check_account_message_cap,
    extract_keywords_from_product,
    generate_smart_outreach_message,
)

@login_required
def leads_list_view(request):
    business = getattr(request.user, "business", None)
    if not business:
        business = Business.objects.create(
            user=request.user,
            name=f"کسب‌وکار {request.user.first_name}",
            business_type="PHYSICAL",
            business_domain="عمومی"
        )

    leads = DiscoveredLead.objects.filter(business=business).select_related("product")

    # Filters
    channel_filter = request.GET.get("channel", "").strip()
    if channel_filter in ["TELEGRAM", "X"]:
        leads = leads.filter(channel=channel_filter)

    status_filter = request.GET.get("status", "").strip()
    if status_filter in ["NEW", "CONTACTED", "CONVERTED", "IGNORED"]:
        leads = leads.filter(status=status_filter)

    product_filter = request.GET.get("product_id", "").strip()
    if product_filter.isdigit():
        leads = leads.filter(product_id=int(product_filter))

    # All leads with >= 30% score are kept
    min_score = request.GET.get("min_score", "30").strip()
    if min_score.isdigit():
        leads = leads.filter(intent_score__gte=int(min_score))

    active_products = Product.objects.filter(business=business, is_discovery_active=True, status="ACTIVE")
    all_products = Product.objects.filter(business=business)

    stats = {
        "total": DiscoveredLead.objects.filter(business=business).count(),
        "new": DiscoveredLead.objects.filter(business=business, status="NEW").count(),
        "contacted": DiscoveredLead.objects.filter(business=business, status="CONTACTED").count(),
        "high_intent": DiscoveredLead.objects.filter(business=business, intent_score__gte=70).count(),
        "active_products_count": active_products.count(),
        "daily_limit": business.daily_discovery_limit,
    }

    return render(request, "discovery/leads_list.html", {
        "business": business,
        "leads": leads,
        "stats": stats,
        "active_products": active_products,
        "all_products": all_products,
        "channel_filter": channel_filter,
        "status_filter": status_filter,
        "product_filter": product_filter,
    })

@login_required
def trigger_scan_view(request):
    if request.method != "POST":
        return JsonResponse({"error": "روش نامعتبر است."}, status=405)

    business = getattr(request.user, "business", None)
    if not business:
        return JsonResponse({"status": "error", "message": "کسب‌وکار یافت نشد."}, status=404)

    result = evaluate_and_discover_leads(business=business)

    if request.headers.get("X-Requested-With") == "XMLHttpRequest" or request.content_type == "application/json":
        return JsonResponse(result)

    if result["status"] == "success":
        messages.success(request, result["message"])
    else:
        messages.warning(request, result["message"])

    return redirect("discovery:leads_list")

@login_required
def toggle_product_discovery_view(request, product_id):
    if request.method != "POST":
        return JsonResponse({"error": "روش نامعتبر است."}, status=405)

    business = getattr(request.user, "business", None)
    product = get_object_or_404(Product, id=product_id, business=business)

    current_active_count = Product.objects.filter(business=business, is_discovery_active=True).count()

    if not product.is_discovery_active:
        # User wants to activate it - check quota limit
        if current_active_count >= business.daily_discovery_limit:
            return JsonResponse({
                "status": "error",
                "message": f"سقف سهمیه روزانه پایش ({business.daily_discovery_limit} محصول) تکمیل است. ابتدا کالای دیگری را غیرفعال کنید یا سقف را افزایش دهید."
            }, status=400)
        product.is_discovery_active = True
    else:
        product.is_discovery_active = False

    product.save(update_fields=["is_discovery_active", "updated_at"])
    new_active_count = Product.objects.filter(business=business, is_discovery_active=True).count()

    return JsonResponse({
        "status": "success",
        "is_discovery_active": product.is_discovery_active,
        "active_count": new_active_count,
        "daily_limit": business.daily_discovery_limit,
        "message": f"وضعیت پایش محصول «{product.name}» تغییر یافت."
    })

@login_required
def send_outreach_view(request, lead_id):
    if request.method != "POST":
        return JsonResponse({"error": "روش نامعتبر است."}, status=405)

    business = getattr(request.user, "business", None)
    result = send_lead_outreach(lead_id, business)

    if request.headers.get("X-Requested-With") == "XMLHttpRequest" or request.content_type == "application/json":
        return JsonResponse(result)

    if result["status"] == "success":
        messages.success(request, result["message"])
    else:
        messages.error(request, result["message"])

    return redirect("discovery:leads_list")

@login_required
def update_lead_status_view(request, lead_id):
    if request.method != "POST":
        return JsonResponse({"error": "روش نامعتبر است."}, status=405)

    business = getattr(request.user, "business", None)
    lead = get_object_or_404(DiscoveredLead, id=lead_id, business=business)

    try:
        data = json.loads(request.body) if request.body else request.POST
    except Exception:
        data = request.POST

    new_status = data.get("status")
    if new_status in ["NEW", "CONTACTED", "CONVERTED", "IGNORED"]:
        lead.status = new_status
        lead.save(update_fields=["status", "updated_at"])
        return JsonResponse({"status": "success", "new_status": new_status})

    return JsonResponse({"status": "error", "message": "وضعیت نامعتبر است."}, status=400)


@login_required
def api_agent_discovery_feed_view(request):
    """
    Dedicated REST API endpoint for AI Agents and Scrapers to fetch clean, prioritized
    catalog feed, keywords, categories, and outreach settings.
    Query params:
      - min_priority: int (1..5)
      - limit: int
      - channel: 'TELEGRAM' | 'X'
    """
    business = getattr(request.user, "business", None)
    if not business:
        return JsonResponse({"status": "error", "message": "کسب‌وکار معتبری یافت نشد."}, status=404)

    min_priority = 1
    raw_min_p = request.GET.get("min_priority")
    if raw_min_p and raw_min_p.isdigit():
        min_priority = int(raw_min_p)

    limit = None
    raw_limit = request.GET.get("limit")
    if raw_limit and raw_limit.isdigit():
        limit = int(raw_limit)

    channel = request.GET.get("channel", "").upper()
    if channel not in ["TELEGRAM", "X"]:
        channel = None

    feed = get_agent_discovery_feed(
        business=business,
        min_priority=min_priority,
        limit=limit,
        channel=channel
    )
    return JsonResponse(feed, json_dumps_params={"ensure_ascii": False, "indent": 2})


@login_required
def api_analytics_report_view(request):
    """
    Returns aggregated performance analytics for seller's catalog (clicks, views, sales, conversion rate)
    with support for Persian/Shamsi start_date and end_date filtering.
    """
    business = getattr(request.user, "business", None)
    if not business:
        return JsonResponse({"status": "error", "message": "کسب‌وکار معتبری یافت نشد."}, status=404)

    start_raw = request.GET.get("start_date", "").strip()
    end_raw = request.GET.get("end_date", "").strip()

    start_date = parse_jalali_date(start_raw) if start_raw else None
    end_date = parse_jalali_date(end_raw) if end_raw else None

    data = get_performance_analytics(business=business, start_date=start_date, end_date=end_date)
    return JsonResponse({"status": "success", "data": data}, json_dumps_params={"ensure_ascii": False, "indent": 2})


@csrf_exempt
def api_submit_lead_view(request):
    """
    Dedicated REST API endpoint for external AI Crawlers, Telegram Listeners, and X Bots
    to ingest discovered leads into the database with full deduplication, token/cost tracking,
    and daily quota enforcement.
    Endpoint: POST /discovery/api/leads/submit/
    """
    if request.method != "POST":
        return JsonResponse({"status": "error", "message": "فقط متد POST مجاز است."}, status=405)

    try:
        data = json.loads(request.body) if request.body else request.POST
    except Exception as e:
        return JsonResponse({"status": "error", "message": f"خطا در پردازش JSON: {str(e)}"}, status=400)

    # Resolve business
    business = None
    if request.user.is_authenticated:
        business = getattr(request.user, "business", None)
    
    business_id = data.get("business_id")
    if not business and business_id:
        business = Business.objects.filter(id=business_id).first()

    if not business:
        business = Business.objects.first()

    if not business:
        return JsonResponse({"status": "error", "message": "کسب‌وکار معتبری یافت نشد."}, status=404)

    channel = data.get("channel", "X").upper()
    if channel not in ["TELEGRAM", "X"]:
        channel = "X"

    lead_handle = data.get("lead_handle", "").strip()
    text = data.get("content_snippet", "").strip()
    if not lead_handle or not text:
        return JsonResponse({"status": "error", "message": "ارسال lead_handle و content_snippet الزامی است."}, status=400)

    # 1. Deduplication Hash check
    fingerprint = ProcessedMessageHash.calculate_hash(channel, lead_handle, text)
    if ProcessedMessageHash.objects.filter(fingerprint=fingerprint).exists():
        return JsonResponse({
            "status": "duplicate",
            "message": "این پیام قبلاً پردازش و ثبت شده است (Deduplicated).",
            "fingerprint": fingerprint
        }, status=200)

    # 2. Product match
    product_id = data.get("product_id")
    product = None
    if product_id:
        product = Product.objects.filter(id=product_id, business=business).first()

    if not product:
        # Match text against business active products using keyword scoring
        active_products = list(
            Product.objects.filter(business=business, is_discovery_active=True).select_related("category")
        )
        best_score = 0
        text_lower = text.lower()
        for cand in active_products:
            kw_list, _ = extract_keywords_from_product(cand)
            score_count = sum(1 for kw in kw_list if kw in text_lower)
            if score_count > best_score:
                best_score = score_count
                product = cand

    if not product:
        product = Product.objects.filter(business=business, is_discovery_active=True).first() or Product.objects.filter(business=business).first()

    if not product:
        return JsonResponse({"status": "error", "message": "هیچ محصولی برای تطبیق یافت نشد."}, status=404)

    # 3. Intent score & reasoning
    score = int(data.get("intent_score", 50))
    reasoning = data.get("intent_reasoning", "کشف هوشمند از طریق ایجنت هوش مصنوعی تلگرام")
    matched_branch = data.get("matched_branch", product.category.get_full_path() if product.category else "")

    outreach_mode = data.get("outreach_mode", "COMMENT" if channel == "X" else "DIRECT")
    outreach_msg = data.get("outreach_message", "")
    direct_link = data.get("direct_link_sent", product.url or f"https://customerweb.ir/p/{product.id}")

    if not outreach_msg and product:
        outreach_msg, direct_link = generate_smart_outreach_message(
            business,
            product,
            {
                "lead_handle": lead_handle,
                "lead_display_name": data.get("lead_display_name", ""),
                "channel": channel,
                "text": text,
            },
            mode=outreach_mode,
        )

    tokens_used = int(data.get("tokens_used", 0))
    cost_usd = float(data.get("cost_usd", 0.0))
    cost_toman = int(data.get("cost_toman", 0))

    # Daily account cap check (10 messages per day per account)
    today = timezone.now().date()
    existing_lead = DiscoveredLead.objects.filter(business=business, channel=channel, lead_handle=lead_handle).first()
    msg_count = 1
    if existing_lead:
        if existing_lead.last_message_date == today:
            msg_count = existing_lead.message_count + 1
        else:
            msg_count = 1

    lead = DiscoveredLead.objects.create(
        business=business,
        product=product,
        channel=channel,
        lead_handle=lead_handle,
        lead_display_name=data.get("lead_display_name", ""),
        post_url=data.get("post_url", ""),
        content_snippet=text,
        intent_score=score,
        intent_reasoning=reasoning,
        matched_branch=matched_branch,
        outreach_mode=outreach_mode,
        outreach_message=outreach_msg,
        outreach_status=data.get("outreach_status", "SENT"),
        sent_from_handle=data.get("sent_from_handle", business.telegram_account_handle if channel == "TELEGRAM" else business.x_account_handle or "@peyda_bot"),
        bot_agent_name=data.get("bot_agent_name", "بات پیدا (@peyda_bot)" if channel == "X" else "ایجنت هوشمند فروش"),
        customer_reply=data.get("customer_reply", ""),
        customer_reply_at=timezone.now() if data.get("customer_reply") else None,
        message_count=msg_count,
        last_message_date=today,
        is_conversation_capped=(msg_count >= 10),
        tokens_used=tokens_used,
        cost_usd=cost_usd,
        cost_toman=cost_toman,
        guardrail_status=data.get("guardrail_status", "SAFE_IN_DOMAIN"),
        direct_link_sent=direct_link,
        status=data.get("status", "CONTACTED" if outreach_msg else "NEW")
    )

    # Save hash
    ProcessedMessageHash.objects.create(fingerprint=fingerprint, channel=channel)

    # Update metric
    metric, _ = ProductDailyMetric.objects.get_or_create(product=product, date=today)
    metric.outreach_sent_count += 1
    metric.save(update_fields=["outreach_sent_count"])

    return JsonResponse({
        "status": "success",
        "lead_id": lead.id,
        "product_id": product.id,
        "message_count_today": msg_count,
        "is_conversation_capped": lead.is_conversation_capped,
        "cost_toman": cost_toman,
        "tokens_used": tokens_used,
        "message": "سرنخ کشف‌شده با موفقیت در پایگاه داده ثبت و پیام به مشتری ارسال شد."
    }, status=201)


