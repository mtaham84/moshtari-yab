import csv
import json
import os
import re
from datetime import timedelta
from django.conf import settings

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db.models import Q
from django.http import HttpResponse, JsonResponse
from django.utils import timezone
from django.shortcuts import get_object_or_404, redirect, render

from apps.businesses.models import Business
from apps.core.jalali import format_jalali_date, parse_jalali_date
from apps.products.models import Category, Product
from need_engine.text import norm

from .models import (MonitoredCommunity, Opportunity, OPPORTUNITY_STATUS_CHOICES, XCollectorState,
                    XNegativeTerm, XSearchQuery)
from .services import get_performance_analytics


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
    hide_stale = params.get("hide_stale") == "1"
    if hide_stale:
        qs = qs.exclude(source_platform="x", source_posted_at__lt=timezone.now() - timedelta(hours=48))

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
    elif sort_order == "source_newest":
        qs = qs.order_by("-source_posted_at", "-created_at")
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
        "hide_stale": hide_stale,
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
    now = timezone.now()
    for opportunity in qs:
        opportunity.source_age_label = ""
        opportunity.source_age_title = ""
        if opportunity.source_platform == "x" and opportunity.source_posted_at:
            age = max(0, int((now - opportunity.source_posted_at).total_seconds() // 3600))
            opportunity.source_age_label = "تازه" if age < 3 else "گرم" if age < 12 else "در حال کهنگی" if age < 48 else "قدیمی"
            opportunity.source_age_title = format_jalali_date(opportunity.source_posted_at.date())

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
        "hide_stale": filter_meta["hide_stale"],
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
    if opportunity.source_platform == "x":
        from need_engine.x_replies import build_x_intent_url

        base = getattr(settings, "X_INTENT_BASE_URL", "https://x.com/intent/post")
        context["x_intent_base_url"] = base
        tweet_id = opportunity.source_message_id
        if not str(tweet_id).isdigit():
            evidence_url = next((item.source_reference for item in opportunity.evidence_items.all() if item.source_reference), "")
            match = re.search(r"/status/(\d+)", evidence_url)
            tweet_id = match.group(1) if match else None
        context["x_reply_cards"] = []
        for match in context["product_matches"]:
            variants = match.reply_variants or {}
            if not variants:
                continue
            context["x_reply_cards"].append({
                "product": match.product,
                "variants": variants,
                "public_url": build_x_intent_url(base, tweet_id, variants.get("public", "")),
                "short_url": build_x_intent_url(base, tweet_id, variants.get("short", "")),
            })
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


@login_required
def opportunity_feedback_view(request, pk):
    if request.method != "POST":
        return JsonResponse({"status": "error", "message": "متد غیرمجاز است."}, status=405)

    business = getattr(request.user, "business", None)
    opportunity = get_object_or_404(Opportunity, pk=pk, business=business)
    feedback = request.POST.get("feedback", "").strip()
    if feedback not in {"relevant", "irrelevant"}:
        return JsonResponse({"status": "error", "message": "بازخورد نامعتبر است."}, status=400)

    metadata = dict(opportunity.trace_metadata or {})
    metadata["seller_feedback"] = feedback
    if opportunity.source_platform == "x":
        opportunity.lead_feedback = "good" if feedback == "relevant" else "bad_not_buyer"
        opportunity.lead_feedback_at = timezone.now()
        opportunity.save(update_fields=["lead_feedback", "lead_feedback_at", "updated_at"])
    opportunity.trace_metadata = metadata
    opportunity.save(update_fields=["trace_metadata", "updated_at"])
    return JsonResponse({"status": "success", "feedback": feedback})


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
    if community.is_active and community.sync_status == "ERROR":
        community.sync_status, community.sync_error = "PENDING", ""
    community.save(update_fields=["is_active", "sync_status", "sync_error"])

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


@login_required
def x_management_view(request):
    if not getattr(settings, "X_PANEL_ENABLED", False):
        return HttpResponse(status=404)
    business = getattr(request.user, "business", None)
    if business is None:
        return HttpResponse(status=404)
    if request.method == "POST":
        if not request.user.is_authenticated:
            return HttpResponse(status=403)
        action = request.POST.get("action", "")
        if action == "add_query":
            text = " ".join(request.POST.get("query", "").split())
            if len(text) > 100:
                return HttpResponse("عبارت حداکثر ۱۰۰ نویسه باشد.", status=400)
            if text:
                normalized = norm(text)[:100]
                if XSearchQuery.objects.filter(business=business, kind="manual").count() >= int(os.getenv("X_MANUAL_QUERIES_MAX", "30")):
                    return HttpResponse("سقف عبارت‌های این کسب‌وکار تکمیل شده است.", status=400)
                XSearchQuery.objects.get_or_create(business=business, normalized_text=normalized,
                    defaults={"text": text, "kind": "manual", "state": "active"})
        elif action == "set_query_state":
            query = get_object_or_404(XSearchQuery, pk=request.POST.get("query_id"), business=business)
            state = request.POST.get("state")
            if state in {"active", "paused_manual"}:
                query.state = state
                query.save(update_fields=["state", "last_state_change_at"])
        elif action == "add_negative":
            term = " ".join(request.POST.get("term", "").split())
            if len(term) > 40:
                return HttpResponse("واژه حداکثر ۴۰ نویسه باشد.", status=400)
            if term:
                if XNegativeTerm.objects.filter(business=business).count() >= int(os.getenv("X_NEGATIVE_TERMS_MAX", "50")):
                    return HttpResponse("سقف واژه‌های منفی تکمیل شده است.", status=400)
                XNegativeTerm.objects.get_or_create(business=business, product=None, normalized_term=norm(term)[:40],
                    defaults={"term": term})
        elif action == "delete_negative":
            get_object_or_404(XNegativeTerm, pk=request.POST.get("term_id"), business=business).delete()
        return redirect("discovery:x_management")
    from django.core.paginator import Paginator
    queries = Paginator(XSearchQuery.objects.filter(business=business).order_by("state", "text"), 50).get_page(request.GET.get("page"))
    terms = XNegativeTerm.objects.filter(business=business).order_by("term")
    status = XCollectorState.objects.order_by("-updated_at").first()
    status_file = settings.BASE_DIR / __import__("os").getenv("X_STATUS_FILE", "data/x_collected/status.json")
    try:
        import json
        status_data = json.loads(status_file.read_text(encoding="utf-8"))
        from django.utils.dateparse import parse_datetime
        from django.utils import timezone
        updated = parse_datetime(status_data.get("updated_at", ""))
        if not updated or timezone.now() - updated > timedelta(minutes=int(os.getenv("X_STATUS_STALE_MINUTES", "30"))):
            status = None
    except Exception:
        status = None
    return render(request, "discovery/x_management.html", {"queries": queries, "terms": terms,
        "collector_state": status})
