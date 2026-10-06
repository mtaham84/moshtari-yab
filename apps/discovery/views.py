import json
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from apps.businesses.models import Business
from apps.products.models import Product
from .models import DiscoveredLead, CategoryBranchMemory
from .services import evaluate_and_discover_leads, send_lead_outreach, get_agent_discovery_feed

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
