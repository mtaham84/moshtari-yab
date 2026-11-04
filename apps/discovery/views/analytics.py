from django.contrib.auth.decorators import login_required
from django.http import JsonResponse

from apps.businesses.models import Business
from apps.core.jalali import parse_jalali_date
from apps.discovery.services import get_performance_analytics, get_leaf_category_dashboard_stats


@login_required
def api_analytics_report_view(request):
    """
    Returns aggregated performance analytics for seller's catalog (clicks, views, sales, conversion rate)
    with support for Persian/Shamsi start_date and end_date filtering.
    """
    business = getattr(request.user, "business", None)
    if (request.user.is_staff or request.user.is_superuser) and request.GET.get("business_id", "").strip().isdigit():
        b_found = Business.objects.filter(id=int(request.GET.get("business_id"))).first()
        if b_found:
            business = b_found
    if not business:
        return JsonResponse({"status": "error", "message": "کسب‌وکار معتبری یافت نشد."}, status=404)

    start_raw = request.GET.get("start_date", "").strip()
    end_raw = request.GET.get("end_date", "").strip()

    start_date = parse_jalali_date(start_raw) if start_raw else None
    end_date = parse_jalali_date(end_raw) if end_raw else None

    data = get_performance_analytics(business=business, start_date=start_date, end_date=end_date)
    return JsonResponse({"status": "success", "data": data}, json_dumps_params={"ensure_ascii": False, "indent": 2})


@login_required
def api_leaf_category_stats_view(request):
    """
    Returns JSON dashboard metrics for all active leaf categories and platforms.
    Accepts ?platform=x, ?platform=instagram, or returns both.
    """
    business = getattr(request.user, "business", None)
    if (request.user.is_staff or request.user.is_superuser) and request.GET.get("business_id", "").strip().isdigit():
        b_found = Business.objects.filter(id=int(request.GET.get("business_id"))).first()
        if b_found:
            business = b_found

    if not business:
        return JsonResponse({"status": "error", "message": "کسب‌وکار معتبری یافت نشد."}, status=400)

    platform_param = request.GET.get("platform")
    data = get_leaf_category_dashboard_stats(business, platform=platform_param)
    return JsonResponse({
        "status": "success",
        "business_id": business.id,
        "business_name": business.name,
        "leaf_categories_count": len(data),
        "results": data,
    })
