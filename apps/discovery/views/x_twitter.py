import json
import os
from datetime import timedelta

from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from apps.discovery import x_activity
from apps.discovery.models import XCollectorState, XNegativeTerm, XSearchQuery
from apps.products.models import Product
from need_engine.text import norm


@login_required
def x_management_view(request):
    if not (getattr(settings, "X_PANEL_ENABLED", False) or getattr(settings, "X_SELLER_UI", False)):
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
    queries = Paginator(XSearchQuery.objects.filter(business=business).order_by("state", "text"), 50).get_page(request.GET.get("page"))
    terms = XNegativeTerm.objects.filter(business=business).order_by("term")
    status = XCollectorState.objects.order_by("-updated_at").first()
    status_file = settings.BASE_DIR / os.getenv("X_STATUS_FILE", "data/x_collected/status.json")
    try:
        status_data = json.loads(status_file.read_text(encoding="utf-8"))
        updated = parse_datetime(status_data.get("updated_at", ""))
        if not updated or timezone.now() - updated > timedelta(minutes=int(os.getenv("X_STATUS_STALE_MINUTES", "30"))):
            status = None
    except Exception:
        status = None
    return render(request, "discovery/x_management.html", {"queries": queries, "terms": terms,
        "collector_state": status})


def _x_seller_business(request):
    if not getattr(settings, "X_SELLER_UI", True):
        return None
    return getattr(request.user, "business", None)


def _x_wallet_empty(business) -> bool:
    wallet = getattr(business, "wallet", None)
    return wallet is not None and wallet.balance_toman <= 0


@login_required
def x_search_view(request):
    """Seller hub «جستجوی مشتری در X»: what it does in plain words, a switch per product and what came of it."""
    business = _x_seller_business(request)
    if business is None:
        return HttpResponse(status=404)
    products = list(Product.objects.filter(business=business).order_by("-x_search_enabled", "name"))
    stats = x_activity.product_stats(business, products)
    rows = []
    for p in products:
        st = stats.get(str(p.pk), {})
        rows.append({"product": p, "st": st, "eligible": p.status == "ACTIVE" and p.is_discovery_active})
    on = [r for r in rows if r["product"].x_search_enabled]
    totals = {k: sum(r["st"].get(k, 0) for r in rows) for k in ("found", "reviewed", "customers", "rejected", "cost_toman", "seen")}
    return render(request, "discovery/x_search.html", {
        "rows": rows, "on_count": len(on), "totals": totals, "collector": x_activity.collector_line(),
        "wallet_empty": _x_wallet_empty(business), "x_mode": os.getenv("NE_X_MODE", "product").strip().lower() or "product",
    })


@login_required
def x_product_view(request, pk):
    """One product's X search: switch, numbers, the phrases searched and a log of every post found and its outcome."""
    business = _x_seller_business(request)
    if business is None:
        return HttpResponse(status=404)
    product = get_object_or_404(Product, pk=pk, business=business)
    show = request.GET.get("show", "all")
    show = show if show in dict(x_activity.FILTERS) else "all"
    st = x_activity.product_stats(business, [product]).get(str(product.pk), {})
    return render(request, "discovery/x_product.html", {
        "product": product, "st": st, "eligible": product.status == "ACTIVE" and product.is_discovery_active,
        "phrases": x_activity.search_phrases(product), "events": x_activity.timeline(product, business, show),
        "show": show, "filters": x_activity.FILTERS, "collector": x_activity.collector_line(),
        "wallet_empty": _x_wallet_empty(business),
    })
