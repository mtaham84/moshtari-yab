import csv
import json
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.views.decorators.csrf import csrf_exempt
from django.utils import timezone
from django.http import JsonResponse, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from apps.businesses.models import Business
from apps.products.models import Product, Category
from apps.core.jalali import parse_jalali_date, format_jalali_date, format_jalali_datetime
from .models import (
    DiscoveredLead,
    CategoryBranchMemory,
    ProcessedMessageHash,
    ProductDailyMetric,
    Customer,
    Opportunity,
    AIAnalysis,
    OpportunityProductMatch,
    Evidence,
    MonitoredCommunity
)
from .opportunity_service import OpportunityService
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


def _filter_opportunities_queryset(business, params):
    """
    Unified filter and sort engine for opportunities table and Excel export.
    Supports platform, status, catalog product filter, Jalali date range, search,
    and sorting by Jalali time or purchase intent score.
    """
    from django.db.models import Q

    qs = Opportunity.objects.filter(business=business).select_related(
        "customer", "category", "ai_analysis"
    ).prefetch_related("product_matches__product")

    # Platform filter
    platform_filter = params.get("platform", "").strip().lower()
    if platform_filter in {"telegram", "x", "instagram", "divar", "other"}:
        qs = qs.filter(source_platform=platform_filter)

    # Status filter
    status_filter = params.get("status", "").strip().upper()
    if status_filter in {"NEW", "QUALIFIED", "REVIEWED", "CONTACTED", "CONVERTED", "REJECTED"}:
        qs = qs.filter(status=status_filter)

    # Product filter (specific catalog item)
    product_id_str = params.get("product_id", "").strip()
    selected_product_id = int(product_id_str) if product_id_str.isdigit() else None
    if selected_product_id:
        qs = qs.filter(product_matches__product_id=selected_product_id)

    # Search query
    q = params.get("q", "").strip()
    if q:
        qs = qs.filter(
            Q(customer__name__icontains=q) |
            Q(customer__source_username__icontains=q) |
            Q(customer__phone_number__icontains=q) |
            Q(source_raw_message__icontains=q) |
            Q(ai_analysis__need__icontains=q) |
            Q(category_name_snapshot__icontains=q) |
            Q(product_matches__product__name__icontains=q)
        ).distinct()

    # Min score filter
    min_score = params.get("min_score", "").strip()
    if min_score.isdigit():
        score_val = float(min_score) / 100.0
        qs = qs.filter(ai_analysis__intent_score__gte=score_val)

    # Date range filters (Persian calendar)
    date_from_raw = params.get("date_from", "").strip()
    date_to_raw = params.get("date_to", "").strip()
    if date_from_raw:
        d_from = parse_jalali_date(date_from_raw)
        if d_from:
            qs = qs.filter(created_at__date__gte=d_from)
    if date_to_raw:
        d_to = parse_jalali_date(date_to_raw)
        if d_to:
            qs = qs.filter(created_at__date__lte=d_to)

    # Sorting
    # If filtering by a specific product, default sort is highest purchase intent score first!
    default_sort = "intent_desc" if selected_product_id else "newest"
    sort_order = params.get("sort", default_sort).strip()

    if sort_order == "newest":
        qs = qs.order_by("-source_message_timestamp", "-created_at")
    elif sort_order == "oldest":
        qs = qs.order_by("source_message_timestamp", "created_at")
    elif sort_order == "intent_desc":
        qs = qs.order_by("-ai_analysis__intent_score", "-created_at")
    elif sort_order == "intent_asc":
        qs = qs.order_by("ai_analysis__intent_score", "-created_at")
    elif sort_order == "fit_desc":
        qs = qs.order_by("-ai_analysis__product_fit_score", "-created_at")
    elif sort_order == "cost_desc":
        qs = qs.order_by("-ai_analysis__cost_toman", "-created_at")
    else:
        qs = qs.order_by("-created_at")

    filter_meta = {
        "platform": platform_filter,
        "status": status_filter,
        "product_id": selected_product_id,
        "min_score": min_score,
        "q": q,
        "date_from": date_from_raw,
        "date_to": date_to_raw,
        "sort": sort_order,
    }
    return qs, filter_meta


@login_required
def opportunity_list_view(request):
    business = getattr(request.user, "business", None)
    if not business:
        business = Business.objects.create(
            user=request.user,
            name=f"کسب‌وکار {request.user.first_name}",
            business_type="PHYSICAL",
            business_domain="عمومی"
        )

    qs, filter_meta = _filter_opportunities_queryset(business, request.GET)

    # View layout mode (table vs cards)
    view_mode = request.GET.get("view", "table").strip().lower()
    if view_mode not in {"table", "cards"}:
        view_mode = "table"

    # Seller's products for catalog filter dropdown
    seller_products = Product.objects.filter(business=business).order_by("name")

    # Global summary stats
    all_opps = Opportunity.objects.filter(business=business)
    stats = {
        "total": all_opps.count(),
        "filtered_count": qs.count(),
        "high_intent": all_opps.filter(ai_analysis__intent_score__gte=0.8).count(),
        "qualified": all_opps.filter(status="QUALIFIED").count(),
        "converted": all_opps.filter(status="CONVERTED").count(),
        "telegram_count": all_opps.filter(source_platform="telegram").count(),
        "x_count": all_opps.filter(source_platform="x").count(),
        "instagram_count": all_opps.filter(source_platform="instagram").count(),
        "divar_count": all_opps.filter(source_platform="divar").count(),
    }

    # Query string without page or view for export link
    export_query = request.GET.copy()
    if "view" in export_query:
        del export_query["view"]

    context = {
        "opportunities": qs,
        "stats": stats,
        "seller_products": seller_products,
        "selected_platform": filter_meta["platform"],
        "selected_status": filter_meta["status"],
        "selected_product_id": filter_meta["product_id"],
        "selected_min_score": filter_meta["min_score"],
        "selected_sort": filter_meta["sort"],
        "date_from": filter_meta["date_from"],
        "date_to": filter_meta["date_to"],
        "search_query": filter_meta["q"],
        "view_mode": view_mode,
        "export_query_string": export_query.urlencode(),
    }
    return render(request, "discovery/opportunities_list.html", context)


@login_required
def opportunity_export_view(request):
    """
    Exports filtered opportunities into an Excel-compatible CSV file (UTF-8 BOM).
    Preserves all active search, product, date, and score filters.
    """
    business = getattr(request.user, "business", None)
    if not business:
        return HttpResponse("کسب‌وکار یافت نشد.", status=404)

    qs, _ = _filter_opportunities_queryset(business, request.GET)

    today_str = format_jalali_date(timezone.now().date(), persian_digits=False).replace("/", "-")
    filename = f"peyda_customers_{today_str}.csv"

    response = HttpResponse(content_type="text/csv; charset=utf-8-sig")
    response["Content-Disposition"] = f'attachment; filename="{filename}"'

    # UTF-8 BOM for seamless Persian rendering in MS Excel
    response.write("\ufeff")

    writer = csv.writer(response)
    writer.writerow([
        "ردیف",
        "شناسه فرصت",
        "پلتفرم",
        "نام خریدار",
        "نام کاربری / هندل",
        "شماره تماس",
        "پیام خریدار در شبکه اجتماعی",
        "نیاز یا مسئله استخراج‌شده",
        "کالای پیشنهادی کاتالوگ",
        "شاخه دسته‌بندی کالا",
        "درصد احتمال خرید (نیت)",
        "درصد تطابق با کالا",
        "وضعیت",
        "دلیل انتخاب هوش مصنوعی",
        "پیش‌نویس پاسخ آماده",
        "تاریخ و زمان ثبت (شمسی)",
        "هزینه پردازش (تومان)",
        "توکن‌های مصرفی",
    ])

    for idx, opp in enumerate(qs, start=1):
        primary_match = opp.primary_product_match
        prod_name = primary_match.product.name if primary_match else "-"
        analysis = getattr(opp, "ai_analysis", None)

        need_text = analysis.need if analysis else "-"
        why_selected = analysis.why_selected if analysis else "-"
        suggested_reply = analysis.suggested_reply if analysis else "-"
        cost_toman = analysis.cost_toman if analysis else 0
        tokens_used = analysis.tokens_used if analysis else 0
        intent_pct = f"{opp.intent_score_percentage}٪"
        fit_pct = f"{opp.product_fit_score_percentage}٪"
        phone = opp.customer.phone_number or "-"
        username = f"@{opp.customer.source_username}" if opp.customer.source_username else "-"

        platform_label = "تلگرام" if opp.source_platform == "telegram" else (
            "ایکس (توییتر)" if opp.source_platform == "x" else (
                "اینستاگرام" if opp.source_platform == "instagram" else (
                    "دیوار" if opp.source_platform == "divar" else opp.source_platform
                )
            )
        )

        writer.writerow([
            idx,
            opp.id,
            platform_label,
            opp.customer.name or "ناشناس",
            username,
            phone,
            opp.source_raw_message.replace("\n", " ").strip(),
            need_text.replace("\n", " ").strip(),
            prod_name,
            opp.category_name_snapshot or (opp.category.name if opp.category else "-"),
            intent_pct,
            fit_pct,
            opp.get_status_display(),
            why_selected.replace("\n", " ").strip(),
            suggested_reply.replace("\n", " ").strip(),
            opp.created_at_jalali or "-",
            cost_toman,
            tokens_used,
        ])

    return response


@login_required
def opportunity_detail_view(request, pk):
    business = getattr(request.user, "business", None)
    opportunity = get_object_or_404(
        Opportunity.objects.select_related(
            "customer", "category", "ai_analysis"
        ).prefetch_related(
            "product_matches__product",
            "evidence_items"
        ),
        pk=pk,
        business=business
    )

    context = {
        "opportunity": opportunity,
        "customer": opportunity.customer,
        "analysis": getattr(opportunity, "ai_analysis", None),
        "product_matches": opportunity.product_matches.select_related("product").order_by("rank"),
        "evidence_items": opportunity.evidence_items.all(),
    }
    return render(request, "discovery/opportunity_detail.html", context)


@login_required
def opportunity_status_update_view(request, pk):
    if request.method != "POST":
        return JsonResponse({"status": "error", "message": "متد غیرمجاز است."}, status=405)

    business = getattr(request.user, "business", None)
    opportunity = get_object_or_404(Opportunity, pk=pk, business=business)

    new_status = request.POST.get("status")
    if not new_status and request.body:
        try:
            body_data = json.loads(request.body.decode("utf-8"))
            new_status = body_data.get("status")
        except Exception:
            pass

    valid_statuses = dict(Opportunity._meta.get_field("status").choices)
    if new_status not in valid_statuses:
        return JsonResponse({"status": "error", "message": "وضعیت نامعتبر است."}, status=400)

    opportunity.status = new_status
    opportunity.save(update_fields=["status", "updated_at"])

    return JsonResponse({
        "status": "success",
        "opportunity_id": opportunity.id,
        "new_status": opportunity.status,
        "status_display": opportunity.get_status_display(),
        "message": "وضعیت فرصت با موفقیت به‌روزرسانی شد."
    })


@csrf_exempt
def api_opportunity_process_view(request):
    if request.method != "POST":
        return JsonResponse({"status": "error", "message": "فقط درخواست POST مجاز است."}, status=405)

    try:
        data = json.loads(request.body.decode("utf-8"))
    except Exception as e:
        return JsonResponse({"status": "error", "message": f"فرمت داده نامعتبر است: {str(e)}"}, status=400)

    # Resolve business
    business = None
    if request.user.is_authenticated:
        business = getattr(request.user, "business", None)
    if not business:
        business_id = data.get("business_id")
        if business_id:
            business = Business.objects.filter(id=business_id).first()
        else:
            business = Business.objects.first()

    if not business:
        return JsonResponse({"status": "error", "message": "کسب‌وکار معتبری برای انتساب فرصت یافت نشد."}, status=400)

    try:
        opportunity = OpportunityService.process_candidate(
            candidate_payload=data,
            business=business
        )
    except Exception as e:
        return JsonResponse({"status": "error", "message": f"خطا در پردازش کاندیدا: {str(e)}"}, status=500)

    ai = getattr(opportunity, "ai_analysis", None)
    matches_data = [
        {
            "product_id": m.product_id,
            "product_name": m.product.name,
            "match_score": m.match_score,
            "recommendation_reason": m.recommendation_reason,
            "rank": m.rank,
        }
        for m in opportunity.product_matches.select_related("product").all()
    ]
    evidence_data = [
        {
            "evidence_type": e.evidence_type,
            "content": e.content,
            "source_reference": e.source_reference,
        }
        for e in opportunity.evidence_items.all()
    ]

    cat_data = None
    if opportunity.category:
        cat_data = {
            "id": opportunity.category.id,
            "name": opportunity.category.name,
            "path": opportunity.category_path_snapshot or opportunity.category.get_full_path(),
            "confidence": opportunity.category_confidence,
        }

    return JsonResponse({
        "status": "success",
        "pipeline_status": opportunity.status,
        "opportunity_id": opportunity.id,
        "category": cat_data,
        "products": matches_data,
        "customer": {
            "id": opportunity.customer.id,
            "name": opportunity.customer.name,
            "display_identifier": opportunity.customer.display_identifier,
            "source_platform": opportunity.customer.source_platform,
            "source_username": opportunity.customer.source_username,
        },
        "source_platform": opportunity.source_platform,
        "ai_analysis": {
            "need": ai.need if ai else "",
            "intent_score": ai.intent_score if ai else 0.0,
            "product_fit_score": ai.product_fit_score if ai else 0.0,
            "confidence": ai.confidence if ai else 0.0,
            "why_selected": ai.why_selected if ai else "",
            "suggested_reply": ai.suggested_reply if ai else "",
        },
        "product_matches": matches_data,
        "evidence_items": evidence_data,
    }, status=201)


# ==============================================================================
# MONITORED ONLINE COMMUNITIES (TELEGRAM CHANNELS & GROUPS)
# ==============================================================================

@login_required
def communities_list_view(request):
    """
    Manages monitored online communities (Telegram public channels & groups).
    Lists active communities, handles adding new Telegram sources, and displays stats.
    """
    business = getattr(request.user, "business", None)
    if not business:
        messages.error(request, "حساب کسب‌وکار مرتبط یافت نشد.")
        return redirect("accounts:dashboard")

    if request.method == "POST":
        name = request.POST.get("name", "").strip()
        handle_or_link = request.POST.get("handle_or_link", "").strip()
        community_type = request.POST.get("community_type", "CHANNEL")
        category_id = request.POST.get("category_id")
        description = request.POST.get("description", "").strip()
        members_str = request.POST.get("members_count", "0").strip()
        members_count = int(members_str) if members_str.isdigit() else 0

        if not name or not handle_or_link:
            messages.error(request, "لطفاً نام و آیدی یا لینک جامعه تلگرامی را وارد نمایید.")
        else:
            cat_obj = None
            if category_id:
                cat_obj = Category.objects.filter(id=category_id, business=business).first()

            # Normalize handle (ensure @ or link)
            clean_handle = handle_or_link
            if not clean_handle.startswith("@") and not clean_handle.startswith("http"):
                clean_handle = f"@{clean_handle}"

            MonitoredCommunity.objects.create(
                business=business,
                platform="telegram",
                community_type=community_type,
                name=name,
                handle_or_link=clean_handle,
                category=cat_obj,
                description=description,
                members_count=members_count,
                is_active=True
            )
            messages.success(request, f"جامعه تلگرامی «{name}» با موفقیت به فهرست پایش هوشمند اضافه شد.")
            return redirect("discovery:communities_list")

    communities = MonitoredCommunity.objects.filter(business=business).select_related("category")
    categories = Category.objects.filter(business=business, is_active=True).order_by("depth", "name")

    total_communities = communities.count()
    active_communities = communities.filter(is_active=True).count()
    total_scanned = sum(c.messages_scanned_count for c in communities)
    total_leads = sum(c.leads_discovered_count for c in communities)

    return render(request, "discovery/communities_list.html", {
        "communities": communities,
        "categories": categories,
        "business": business,
        "stats": {
            "total_communities": total_communities,
            "active_communities": active_communities,
            "total_scanned": total_scanned,
            "total_leads": total_leads,
        }
    })


@login_required
def community_toggle_status_view(request, community_id):
    """Toggles active/inactive monitoring status for a community."""
    business = getattr(request.user, "business", None)
    community = get_object_or_404(MonitoredCommunity, id=community_id, business=business)

    community.is_active = not community.is_active
    community.save(update_fields=["is_active"])

    status_str = "فعال" if community.is_active else "غیرفعال"
    messages.success(request, f"وضعیت جامعه «{community.name}» به {status_str} تغییر یافت.")
    return redirect("discovery:communities_list")


@login_required
def community_delete_view(request, community_id):
    """Removes a community from the monitoring list."""
    business = getattr(request.user, "business", None)
    community = get_object_or_404(MonitoredCommunity, id=community_id, business=business)

    name = community.name
    community.delete()
    messages.info(request, f"جامعه تلگرامی «{name}» از لیست پایش حذف شد.")
    return redirect("discovery:communities_list")




