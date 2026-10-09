import csv
import json

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db.models import Q
from django.http import HttpResponse, JsonResponse
from django.utils import timezone
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from apps.core.agent import AgentUnavailable, drafts_for, rewrite_reply

from apps.businesses.models import Business
from apps.core.jalali import format_jalali_date, parse_jalali_date
from apps.products.models import Category, Product

from .models import MonitoredCommunity, Opportunity, OPPORTUNITY_STATUS_CHOICES
from .services import get_performance_analytics
from .sources import GLOBAL, PRIVATE, TG_INVITE_RE, TG_USERNAME_RE, max_private_sources, normalize_link


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


def _filter_opportunities_queryset(business, params):
    """
    Unified filter and sort engine for opportunities table and Excel export.
    Supports platform, status, catalog product filter, Jalali date range, search,
    and sorting by Jalali time or purchase intent score.
    """
    qs = Opportunity.objects.filter(business=business).select_related(
        "customer", "category", "ai_analysis"
    ).prefetch_related("product_matches__product")

    # Platform filter
    platform_filter = params.get("platform", "").strip().lower()
    if platform_filter in {"telegram", "x", "instagram", "divar", "other"}:
        qs = qs.filter(source_platform=platform_filter)

    # Status filter
    status_filter = params.get("status", "").strip().upper()
    if status_filter in dict(OPPORTUNITY_STATUS_CHOICES):
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
        qs = qs.filter(ai_analysis__product_fit_score__gte=score_val)

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
        "high_intent": all_opps.filter(ai_analysis__product_fit_score__gte=0.8).count(),
        "qualified": all_opps.filter(status="NEW").count(),
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
        "latest_opportunity_id": all_opps.order_by("-id").values_list("id", flat=True).first() or 0,
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

    matches = list(opportunity.product_matches.select_related("product").order_by("rank"))
    drafts = drafts_for(opportunity)
    analysis = getattr(opportunity, "ai_analysis", None)
    if matches and analysis and analysis.suggested_reply and not drafts.get(str(matches[0].product_id)):
        drafts[str(matches[0].product_id)] = analysis.suggested_reply
    context = {
        "opportunity": opportunity,
        "customer": opportunity.customer,
        "analysis": analysis,
        "product_matches": matches,
        "evidence_items": opportunity.evidence_items.all(),
        "drafts": {str(m.product_id): drafts.get(str(m.product_id), "") for m in matches},
        "first_draft": drafts.get(str(matches[0].product_id), "") if matches else (analysis.suggested_reply if analysis else ""),
    }
    return render(request, "discovery/opportunity_detail.html", context)


@login_required
@require_POST
def opportunity_rewrite_view(request, pk):
    """«دوباره بنویس»: a new draft for one matched product, with the seller's saved message style."""
    business = getattr(request.user, "business", None)
    opportunity = get_object_or_404(Opportunity.objects.select_related("business", "ai_analysis"), pk=pk, business=business)
    pid = (request.POST.get("product_id") or "").strip()
    matches = opportunity.product_matches.select_related("product").order_by("rank")
    match = matches.filter(product_id=pid).first() if pid.isdigit() else matches.first()
    if match is None:
        return JsonResponse({"status": "error", "message": "برای این فرصت محصولی ثبت نشده است."}, status=400)
    try:
        text = rewrite_reply(opportunity, match)
    except AgentUnavailable as e:
        return JsonResponse({"status": "error", "message": str(e)}, status=503)
    return JsonResponse({"status": "success", "product_id": match.product_id, "reply": text})


@login_required
def opportunity_new_count_view(request):
    """Polled every 20 s by the opportunities list: how many opportunities arrived after ``after`` (an id)."""
    business = getattr(request.user, "business", None)
    raw = (request.GET.get("after") or "0").strip()
    after = int(raw) if raw.isdigit() else 0
    qs = Opportunity.objects.filter(business=business, id__gt=after) if business else Opportunity.objects.none()
    return JsonResponse({"status": "success", "count": qs.count()})


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


def _validate_private_source(business, raw_link: str) -> tuple[str, str]:
    """→ (normalized_link, "") or ("", Persian error). Rules: valid Telegram link, not already a global
    source, not already one of this seller's sources, at most TG_MAX_PRIVATE_SOURCES per seller."""
    key = normalize_link(raw_link)
    if not key:
        return "", "لطفاً لینک یا آیدی گروه تلگرامی را وارد کنید."
    if not (TG_USERNAME_RE.match(key) or TG_INVITE_RE.match(key)):
        return "", "لینک واردشده معتبر نیست. نمونه‌ی درست: @group_name یا https://t.me/group_name یا لینک دعوت https://t.me/+…"
    if MonitoredCommunity.objects.filter(scope=GLOBAL, normalized_link=key).exists():
        return "", "این گروه جزو منابع عمومی است و از قبل برای همه‌ی فروشنده‌ها (از جمله شما) پایش می‌شود."
    own = MonitoredCommunity.objects.filter(scope=PRIVATE, business=business)
    if own.filter(normalized_link=key).exists():
        return "", "این گروه قبلاً در منابع اختصاصی شما ثبت شده است."
    limit = max_private_sources()
    if own.count() >= limit:
        return "", f"حداکثر {limit} منبع اختصاصی می‌توانید داشته باشید. برای افزودن، یکی از منابع فعلی را حذف کنید."
    return key, ""


@login_required
def communities_list_view(request):
    """
    Telegram sources the crawler watches for this seller:
      • global sources (defined by the platform admin, analysed for every seller, read-only here);
      • the seller's private sources (only this seller's products are matched there), limited per seller.
    """
    business = getattr(request.user, "business", None)
    if not business:
        messages.error(request, "حساب کسب‌وکار مرتبط یافت نشد.")
        return redirect("accounts:dashboard")

    if request.method == "POST":
        link = request.POST.get("handle_or_link", "").strip()
        description = request.POST.get("description", "").strip()[:500]
        key, error = _validate_private_source(business, link)
        if error:
            messages.error(request, error)
        else:
            community = MonitoredCommunity.objects.create(
                business=business, platform="telegram", handle_or_link=link,
                description=description, is_active=True,
            )
            messages.success(request, f"گروه {community.handle_or_link} به منابع اختصاصی شما اضافه شد و در صف اتصال قرار گرفت.")
            return redirect("discovery:communities_list")

    global_sources = MonitoredCommunity.objects.filter(scope=GLOBAL, is_active=True)
    private_sources = MonitoredCommunity.objects.filter(scope=PRIVATE, business=business)
    watched = list(global_sources) + [c for c in private_sources if c.is_active]

    return render(request, "discovery/communities_list.html", {
        "global_sources": global_sources,
        "private_sources": private_sources,
        "business": business,
        "private_limit": max_private_sources(),
        "can_add_private": private_sources.count() < max_private_sources(),
        "form_link": request.POST.get("handle_or_link", "") if request.method == "POST" else "",
        "form_description": request.POST.get("description", "") if request.method == "POST" else "",
        "stats": {
            "global_count": global_sources.count(),
            "private_count": private_sources.count(),
            "total_scanned": sum(c.messages_scanned_count for c in watched),
            "total_leads": sum(c.leads_discovered_count for c in private_sources),
        },
    })


@login_required
def community_toggle_status_view(request, community_id):
    """Toggles active/inactive monitoring status for a community."""
    business = getattr(request.user, "business", None)
    if request.method != "POST":
        return redirect("discovery:communities_list")
    community = get_object_or_404(MonitoredCommunity, id=community_id, business=business, scope=PRIVATE)

    community.is_active = not community.is_active
    if community.is_active and community.sync_status == "ERROR":
        community.sync_status, community.sync_error = "PENDING", ""
    community.save(update_fields=["is_active", "sync_status", "sync_error"])

    status_str = "فعال" if community.is_active else "غیرفعال"
    messages.success(request, f"پایش «{community.display_name}» {status_str} شد.")
    return redirect("discovery:communities_list")


@login_required
def community_delete_view(request, community_id):
    """Removes a community from the monitoring list."""
    business = getattr(request.user, "business", None)
    if request.method != "POST":
        return redirect("discovery:communities_list")
    community = get_object_or_404(MonitoredCommunity, id=community_id, business=business, scope=PRIVATE)

    name = community.display_name
    community.delete()
    messages.info(request, f"«{name}» از منابع اختصاصی شما حذف شد.")
    return redirect("discovery:communities_list")
