import json

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db.models import Q
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render

from apps.businesses.models import Business
from apps.core.jalali import parse_jalali_date
from apps.products.models import Category

from .models import MonitoredCommunity, Opportunity, OPPORTUNITY_STATUS_CHOICES
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

    qs = Opportunity.objects.filter(business=business).select_related(
        "customer", "category", "ai_analysis"
    ).prefetch_related("product_matches__product")

    # Platform filter
    platform_filter = request.GET.get("platform", "").strip().lower()
    if platform_filter in {"telegram", "x", "instagram", "divar", "other"}:
        qs = qs.filter(source_platform=platform_filter)

    # Status filter
    status_filter = request.GET.get("status", "").strip().upper()
    if status_filter in dict(OPPORTUNITY_STATUS_CHOICES):
        qs = qs.filter(status=status_filter)

    # Search filter
    q = request.GET.get("q", "").strip()
    if q:
        qs = qs.filter(
            Q(customer__name__icontains=q) |
            Q(customer__source_username__icontains=q) |
            Q(customer__phone_number__icontains=q) |
            Q(source_raw_message__icontains=q) |
            Q(ai_analysis__need__icontains=q) |
            Q(category_name_snapshot__icontains=q)
        )

    # Min score filter
    min_score = request.GET.get("min_score", "").strip()
    if min_score.isdigit():
        score_val = float(min_score) / 100.0
        qs = qs.filter(ai_analysis__product_fit_score__gte=score_val)

    # Order
    qs = qs.order_by("-created_at")

    all_opps = Opportunity.objects.filter(business=business)
    stats = {
        "total": all_opps.count(),
        "high_intent": all_opps.filter(ai_analysis__product_fit_score__gte=0.8).count(),
        "qualified": all_opps.filter(status="NEW").count(),
        "converted": all_opps.filter(status="CONVERTED").count(),
        "telegram_count": all_opps.filter(source_platform="telegram").count(),
        "x_count": all_opps.filter(source_platform="x").count(),
        "instagram_count": all_opps.filter(source_platform="instagram").count(),
        "divar_count": all_opps.filter(source_platform="divar").count(),
    }

    context = {
        "opportunities": qs,
        "stats": stats,
        "selected_platform": platform_filter,
        "selected_status": status_filter,
        "selected_min_score": min_score,
        "search_query": q,
    }
    return render(request, "discovery/opportunities_list.html", context)


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
