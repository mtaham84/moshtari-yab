import json
import logging
import re
from datetime import timedelta

from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.db.models import Q
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_POST

from apps.core.permissions import is_operator
from apps.businesses.models import Business
from apps.businesses.services import ensure_business
from apps.core.agent import AgentUnavailable, drafts_for, rewrite_reply
from apps.core.jalali import format_jalali_date, parse_jalali_date
from apps.discovery.models import (
    InstagramReplySuggestion,
    Opportunity,
    OPPORTUNITY_STATUS_CHOICES,
    SELLER_FEEDBACK_REASON_CHOICES,
)
from apps.products.models import Product

logger = logging.getLogger(__name__)
_PLATFORM_ID_PREFIXES = ("telegram_", "x_", "instagram_", "divar_")


def _strip_platform_prefix(value):
    for prefix in _PLATFORM_ID_PREFIXES:
        if value.startswith(prefix):
            return value[len(prefix):]
    return value


def _filter_opportunities_queryset(business, params, is_admin=False, view_all_tenants=False):
    """
    Unified filter and sort engine for opportunities table and Excel export.
    Supports platform, status, catalog product filter, Jalali date range, search,
    and sorting by Jalali time or purchase intent score.
    """
    if not business and not view_all_tenants:
        return Opportunity.objects.none(), {
            "platform": "", "status": "", "product_id": None, "min_score": "",
            "min_intent": "", "q": "", "date_from": "", "date_to": "", "sort": "newest", "hide_stale": False,
        }

    if view_all_tenants or not business:
        qs = Opportunity.objects.all().select_related(
            "customer", "category", "ai_analysis", "business"
        ).prefetch_related("product_matches__product")
    else:
        qs = Opportunity.objects.filter(business=business).select_related(
            "customer", "category", "ai_analysis", "business"
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
        if business:
            qs = qs.filter(
                product_matches__product_id=selected_product_id,
                product_matches__product__business=business,
            )
        else:
            qs = qs.filter(
                product_matches__product_id=selected_product_id,
            )

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

    # Min intent score filter (buying intent / golden leads >= 90%)
    min_intent = params.get("min_intent", "").strip()
    if min_intent.isdigit():
        intent_val = float(min_intent) / 100.0
        qs = qs.filter(ai_analysis__intent_score__gte=intent_val)

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
    default_sort = "intent_desc" if selected_product_id else "intent_desc"
    sort_order = params.get("sort", default_sort).strip()

    from django.db.models import F
    from django.db.models.functions import Coalesce

    if sort_order == "newest":
        qs = qs.order_by("-created_at", "-id")
    elif sort_order == "oldest":
        qs = qs.order_by("created_at", "id")
    elif sort_order == "source_newest":
        qs = qs.order_by("-source_posted_at", "-created_at", "-id")
    elif sort_order == "intent_desc":
        qs = qs.order_by(
            F("ai_analysis__intent_score").desc(nulls_last=True),
            Coalesce("source_message_timestamp", "source_posted_at", "created_at").desc(nulls_last=True),
            "-id"
        )
    elif sort_order == "intent_asc":
        qs = qs.order_by("ai_analysis__intent_score", "created_at", "id")
    elif sort_order == "fit_desc":
        qs = qs.order_by("-ai_analysis__product_fit_score", "-created_at", "-id")
    elif sort_order == "cost_desc":
        qs = qs.order_by("-ai_analysis__cost_toman", "-created_at", "-id")
    else:
        qs = qs.order_by(
            F("ai_analysis__intent_score").desc(nulls_last=True),
            Coalesce("source_message_timestamp", "source_posted_at", "created_at").desc(nulls_last=True),
            "-id"
        )

    filter_meta = {
        "platform": platform_filter,
        "status": status_filter,
        "product_id": selected_product_id,
        "min_score": min_score,
        "min_intent": min_intent,
        "q": q,
        "date_from": date_from_raw,
        "date_to": date_to_raw,
        "sort": sort_order,
        "hide_stale": hide_stale,
    }
    return qs, filter_meta


def _admin_scope(request, own_business):
    """(business, view_all_tenants) an admin is looking at; the list, its CSV export and the new-count poll share it.

    ?business_id=all → every tenant; ?business_id=<id> or ?product_id=<id> → that business; otherwise the admin's own
    business if it has opportunities, else the first business that has any.
    """
    requested_biz_id = request.GET.get("business_id", "").strip().lower()
    requested_prod_id = request.GET.get("product_id", "").strip()
    if requested_biz_id == "all":
        return None, True
    if requested_biz_id.isdigit():
        found = Business.objects.filter(id=int(requested_biz_id)).first()
        if found:
            return found, False
    elif requested_prod_id.isdigit():
        prod = Product.objects.filter(id=int(requested_prod_id)).select_related("business").first()
        if prod and prod.business:
            return prod.business, False
    if own_business is None or not own_business.opportunities.exists():
        first_with_opps = Business.objects.filter(opportunities__isnull=False).distinct().order_by("id").first()
        if first_with_opps:
            return first_with_opps, False
        if own_business is None:
            return Business.objects.order_by("id").first(), False
    return own_business, False


@login_required
def opportunity_list_view(request):
    business = getattr(request.user, "business", None)
    all_businesses = None
    is_admin = is_operator(request.user)
    view_all_tenants = False

    if is_admin:
        from django.db.models import Count
        all_businesses = Business.objects.annotate(opp_count=Count("opportunities")).order_by("id")
        business, view_all_tenants = _admin_scope(request, business)
    else:
        # Strict tenant isolation for regular sellers
        business = ensure_business(request.user)
        requested_prod_id = request.GET.get("product_id", "").strip()
        if requested_prod_id.isdigit():
            if not Product.objects.filter(id=int(requested_prod_id), business=business).exists():
                # Cross-tenant query attempt: do not leak any other tenant data!
                qs = Opportunity.objects.none()
                seller_products = Product.objects.filter(business=business).order_by("name")
                paginator = Paginator(qs, 25)
                page_obj = paginator.get_page(1)
                context = {
                    "opportunities": [],
                    "page_obj": page_obj,
                    "stats": {"total": 0, "filtered_count": 0, "high_intent": 0, "qualified": 0, "converted": 0, "telegram_count": 0, "x_count": 0, "instagram_count": 0, "divar_count": 0},
                    "all_businesses": None,
                    "selected_business": business,
                    "seller_products": seller_products,
                    "selected_platform": "",
                    "selected_status": "",
                    "selected_product_id": int(requested_prod_id),
                    "selected_min_score": "",
                    "selected_min_intent": "",
                    "selected_sort": "newest",
                    "hide_stale": False,
                    "date_from": "",
                    "date_to": "",
                    "search_query": "",
                    "view_mode": "table",
                    "export_query_string": "",
                    "pagination_base_url": "?page=",
                    "pagination_range": [1],
                    "latest_opportunity_id": 0,
                }
                return render(request, "discovery/opportunities_list.html", context)

    qs, filter_meta = _filter_opportunities_queryset(business, request.GET, is_admin=is_admin, view_all_tenants=view_all_tenants)

    # View layout mode (table vs cards)
    view_mode = request.GET.get("view", "table").strip().lower()
    if view_mode not in {"table", "cards"}:
        view_mode = "table"

    # Seller's products for catalog filter dropdown
    if view_all_tenants or not business:
        seller_products = Product.objects.all().order_by("name")[:100]
    else:
        seller_products = Product.objects.filter(business=business).order_by("name")

    paginator = Paginator(qs, 25)
    page_obj = paginator.get_page(request.GET.get("page"))
    opportunities = page_obj.object_list
    for opportunity in opportunities:
        if opportunity.source_platform == "instagram":
            opportunity.instagram_comment_detail_url = reverse(
                "discovery:instagram_comment_detail", args=[opportunity.pk]
            )
            if opportunity.customer and not opportunity.customer.source_profile_url:
                handle = (opportunity.customer.source_username or "").lstrip("@").strip()
                if handle:
                    opportunity.customer.source_profile_url = f"https://www.instagram.com/{handle}/"
        opportunity.reply_card = None
        if opportunity.source_platform in {"instagram", "x"}:
            match = next(iter(opportunity.product_matches.all()), None)
            if match:
                saved = InstagramReplySuggestion.objects.filter(opportunity=opportunity, product=match.product).first() \
                    if opportunity.source_platform == "instagram" else None
                dm_url = (f"https://ig.me/m/{opportunity.customer.source_username.lstrip('@')}"
                          if opportunity.source_platform == "instagram" and opportunity.customer.source_username else
                          f"https://x.com/messages/compose?recipient_id={opportunity.customer.external_user_id}"
                          if opportunity.source_platform == "x" and str(opportunity.customer.external_user_id).isdigit() else "")
                opportunity.reply_card = {
                    "match": match, "variants": match.reply_variants or {},
                    "suggestion_url": reverse("discovery:opportunity_reply_suggestions", args=[opportunity.pk]),
                    "sent": match.reply_sent or bool(saved and saved.sent),
                    "sent_at": match.reply_sent_at or (saved.sent_at if saved else None),
                    "profile_url": opportunity.customer_profile_url, "post_url": opportunity.source_post_url,
                    "dm_url": dm_url,
                }

    # Global summary stats
    if view_all_tenants or not business:
        all_opps = Opportunity.objects.all()
    else:
        all_opps = Opportunity.objects.filter(business=business)
    stats = {
        "total": all_opps.count(),
        "filtered_count": paginator.count,
        "high_intent": all_opps.filter(ai_analysis__intent_score__gte=0.90).count(),
        "qualified": all_opps.filter(status="NEW").count(),
        "converted": all_opps.filter(status="CONVERTED").count(),
        "telegram_count": all_opps.filter(source_platform="telegram").count(),
        "x_count": all_opps.filter(source_platform="x").count(),
        "instagram_count": all_opps.filter(source_platform="instagram").count(),
        "divar_count": all_opps.filter(source_platform="divar").count(),
    }
    now = timezone.now()
    for opportunity in opportunities:
        opportunity.source_age_label = ""
        opportunity.source_age_title = ""
        if opportunity.source_platform == "x" and opportunity.source_posted_at:
            age = max(0, int((now - opportunity.source_posted_at).total_seconds() // 3600))
            opportunity.source_age_label = "تازه" if age < 3 else "گرم" if age < 12 else "در حال کهنگی" if age < 48 else "قدیمی"
            opportunity.source_age_title = format_jalali_date(opportunity.source_posted_at.date())

    # Query string without page or view for export link
    export_query = request.GET.copy()
    for key in ("view", "page"):
        export_query.pop(key, None)
    if is_admin:
        if view_all_tenants:
            export_query["business_id"] = "all"
        elif business:
            export_query["business_id"] = business.id

    pagination_query = request.GET.copy()
    pagination_query.pop("page", None)
    if is_admin:
        if view_all_tenants:
            pagination_query["business_id"] = "all"
        elif business:
            pagination_query["business_id"] = business.id
    pagination_query_string = pagination_query.urlencode()
    pagination_base_url = f"?{pagination_query_string}&page=" if pagination_query_string else "?page="

    context = {
        "opportunities": opportunities,
        "page_obj": page_obj,
        "stats": stats,
        "all_businesses": all_businesses,
        "selected_business": business,
        "view_all_tenants": view_all_tenants,
        "seller_products": seller_products,
        "selected_platform": filter_meta["platform"],
        "selected_status": filter_meta["status"],
        "selected_product_id": filter_meta["product_id"],
        "selected_min_score": filter_meta["min_score"],
        "selected_min_intent": filter_meta["min_intent"],
        "selected_sort": filter_meta["sort"],
        "hide_stale": filter_meta["hide_stale"],
        "date_from": filter_meta["date_from"],
        "date_to": filter_meta["date_to"],
        "search_query": filter_meta["q"],
        "view_mode": view_mode,
        "export_query_string": export_query.urlencode(),
        "pagination_base_url": pagination_base_url,
        "pagination_range": paginator.get_elided_page_range(page_obj.number, on_each_side=2, on_ends=1),
        "latest_opportunity_id": all_opps.order_by("-id").values_list("id", flat=True).first() or 0,
    }
    return render(request, "discovery/opportunities_list.html", context)


@login_required
def opportunity_detail_view(request, pk):
    business = getattr(request.user, "business", None)
    qs = Opportunity.objects.select_related(
        "business", "customer", "category", "ai_analysis"
    ).prefetch_related(
        "product_matches__product",
        "evidence_items"
    )
    if is_operator(request.user):
        opportunity = get_object_or_404(qs, pk=pk)
        business = opportunity.business
    else:
        opportunity = get_object_or_404(qs, pk=pk, business=business)

    matches = list(opportunity.product_matches.select_related("product").order_by("rank"))
    try:
        analysis = opportunity.ai_analysis
    except Exception:
        analysis = None

    drafts = drafts_for(opportunity)
    customer_profile_url = opportunity.customer_profile_url or opportunity.customer.source_profile_url
    if not customer_profile_url and opportunity.customer.source_username:
        handle = opportunity.customer.source_username.lstrip("@").strip()
        if opportunity.source_platform == "instagram":
            customer_profile_url = f"https://www.instagram.com/{handle}/"
        elif opportunity.source_platform == "x":
            customer_profile_url = f"https://x.com/{handle}"
        elif opportunity.source_platform == "telegram":
            customer_profile_url = f"https://t.me/{handle}"

    if matches and analysis and analysis.suggested_reply and not drafts.get(str(matches[0].product_id)):
        drafts[str(matches[0].product_id)] = analysis.suggested_reply
    context = {
        "opportunity": opportunity,
        "customer": opportunity.customer,
        "customer_profile_url": customer_profile_url,
        "analysis": analysis,
        "product_matches": matches,
        "evidence_items": opportunity.evidence_items.all(),
        "drafts": {str(m.product_id): drafts.get(str(m.product_id), "") for m in matches},
        "first_draft": drafts.get(str(matches[0].product_id), "") if matches else (analysis.suggested_reply if analysis else ""),
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
    reply_cards = []
    for match in context["product_matches"]:
        variants = match.reply_variants or {}
        saved = InstagramReplySuggestion.objects.filter(opportunity=opportunity, product=match.product).first() \
            if opportunity.source_platform == "instagram" else None
        reply_cards.append({
            "match": match,
            "variants": variants,
            "suggestion_url": reverse("discovery:opportunity_reply_suggestions", args=[opportunity.pk]),
            "sent": match.reply_sent or bool(saved and saved.sent),
            "sent_at": match.reply_sent_at or (saved.sent_at if saved else None),
            "profile_url": opportunity.customer_profile_url,
            "post_url": opportunity.source_post_url,
            "dm_url": (f"https://ig.me/m/{opportunity.customer.source_username.lstrip('@')}"
                       if opportunity.source_platform == "instagram" and opportunity.customer.source_username else
                       f"https://x.com/messages/compose?recipient_id={opportunity.customer.external_user_id}"
                       if opportunity.source_platform == "x" and str(opportunity.customer.external_user_id).isdigit() else ""),
        })
    context["reply_cards"] = reply_cards
    context["reply_dm_url"] = (
        f"https://ig.me/m/{opportunity.customer.source_username.lstrip('@')}"
        if opportunity.source_platform == "instagram" and opportunity.customer.source_username else
        f"https://x.com/messages/compose?recipient_id={opportunity.customer.external_user_id}"
        if opportunity.source_platform == "x" and str(opportunity.customer.external_user_id).isdigit() else ""
    )
    return render(request, "discovery/opportunity_detail.html", context)


@login_required
@require_POST
def opportunity_rewrite_view(request, pk):
    """«دوباره بنویس»: a new draft for one matched product, with the seller's saved message style."""
    business = getattr(request.user, "business", None)
    opportunity = get_object_or_404(Opportunity.objects.select_related("business", "ai_analysis"), pk=pk, business=business)
    if opportunity.source_platform == "instagram":
        return JsonResponse({"status": "error", "message": "بازنویسی برای فرصت‌های اینستاگرام فعال نیست."}, status=400)
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
@require_POST
def opportunity_reply_suggestions_view(request, pk):
    """Generate/regenerate, edit or mark a human-reviewed X/Instagram draft as sent. Never sends it."""
    business = getattr(request.user, "business", None)
    if is_operator(request.user):
        opportunity = get_object_or_404(Opportunity.objects.select_related("business", "customer", "ai_analysis"), pk=pk)
        business = opportunity.business
    else:
        opportunity = get_object_or_404(Opportunity.objects.select_related("business", "customer", "ai_analysis"),
                                        pk=pk, business=business)
    raw_pid = (request.POST.get("product_id") or "").strip()
    matches = opportunity.product_matches.select_related("product").order_by("rank")
    match = matches.filter(product_id=raw_pid).first() if raw_pid.isdigit() else matches.first()
    if match is None:
        return JsonResponse({"status": "error", "message": "برای این فرصت محصولی ثبت نشده است."}, status=400)
    action = request.POST.get("action", "generate")
    if action in {"generate", "regenerate"}:
        from apps.discovery.services import generate_opportunity_reply_suggestions
        try:
            result = generate_opportunity_reply_suggestions(
                opportunity, match, instruction=request.POST.get("instruction", ""),
                force=True,
            )
        except Exception as exc:
            return JsonResponse({"status": "error", "message": str(exc)[:240]}, status=503)
        if result is None:
            return JsonResponse({"status": "error", "message": "اعتبار این کسب‌وکار برای تولید پیام کافی نیست."}, status=402)
        return JsonResponse({"status": "success", "reply_variants": result, "product_id": match.product_id},
                            json_dumps_params={"ensure_ascii": False})
    if action == "edit":
        tone = request.POST.get("tone", "")
        text = request.POST.get("text", "").strip()
        variants = dict(match.reply_variants or {})
        rows = list(variants.get("variants") or [])
        if tone not in {"short", "friendly", "consultative"} or not text or len(text) > 1000:
            return JsonResponse({"status": "error", "message": "متن یا لحن پیام معتبر نیست."}, status=400)
        found = False
        for row in rows:
            if row.get("tone") == tone:
                row["text"], found = text, True
        if not found:
            rows.append({"tone": tone, "text": text})
        variants["variants"] = rows
        match.reply_variants = variants
        match.save(update_fields=["reply_variants"])
        if opportunity.source_platform == "instagram":
            InstagramReplySuggestion.objects.update_or_create(opportunity=opportunity, product=match.product,
                                                              defaults={"reply_variants": variants})
        return JsonResponse({"status": "success", "reply_variants": variants}, json_dumps_params={"ensure_ascii": False})
    if action == "sent":
        sent = request.POST.get("sent") == "1"
        match.reply_sent = sent
        match.reply_sent_at = timezone.now() if sent else None
        match.save(update_fields=["reply_sent", "reply_sent_at"])
        if opportunity.source_platform == "instagram":
            InstagramReplySuggestion.objects.update_or_create(
                opportunity=opportunity, product=match.product,
                defaults={"reply_variants": match.reply_variants or {}, "sent": sent,
                          "sent_at": match.reply_sent_at},
            )
        return JsonResponse({"status": "success", "sent": sent})
    return JsonResponse({"status": "error", "message": "عملیات نامعتبر است."}, status=400)


@login_required
def opportunity_new_count_view(request):
    """Polled every 20 s by the opportunities list: how many opportunities arrived after ``after`` (an id)."""
    business = getattr(request.user, "business", None)
    view_all_tenants = False
    if is_operator(request.user):
        business, view_all_tenants = _admin_scope(request, business)

    raw = (request.GET.get("after") or "0").strip()
    after = int(raw) if raw.isdigit() else 0
    if view_all_tenants:
        qs = Opportunity.objects.filter(id__gt=after)
    else:
        qs = Opportunity.objects.filter(business=business, id__gt=after) if business else Opportunity.objects.none()
    return JsonResponse({"status": "success", "count": qs.count()})


@login_required
def opportunity_status_update_view(request, pk):
    if request.method != "POST":
        return JsonResponse({"status": "error", "message": "متد غیرمجاز است."}, status=405)

    business = getattr(request.user, "business", None)
    if is_operator(request.user):
        opportunity = get_object_or_404(Opportunity, pk=pk)
    else:
        opportunity = get_object_or_404(Opportunity, pk=pk, business=business)

    new_status = request.POST.get("status")
    if not new_status and request.body:
        try:
            body_data = json.loads(request.body.decode("utf-8"))
            new_status = body_data.get("status")
        except (ValueError, AttributeError):   # not JSON, or not a JSON object
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
    if is_operator(request.user):
        opportunity = get_object_or_404(Opportunity, pk=pk)
    else:
        opportunity = get_object_or_404(Opportunity, pk=pk, business=business)
    feedback = request.POST.get("feedback", "").strip()
    if feedback not in {"relevant", "irrelevant"}:
        return JsonResponse({"status": "error", "message": "بازخورد نامعتبر است."}, status=400)
    reason = request.POST.get("reason", "").strip()
    if reason and reason not in dict(SELLER_FEEDBACK_REASON_CHOICES):
        return JsonResponse({"status": "error", "message": "دلیل بازخورد نامعتبر است."}, status=400)

    # Prevent submitting feedback more than once
    if opportunity.feedback in {"relevant", "irrelevant"}:
        return JsonResponse({
            "status": "already_submitted",
            "message": "برای این پیام قبلاً بازخورد ثبت شده است و امکان ثبت مجدد وجود ندارد.",
            "feedback": opportunity.feedback,
            "new_status": opportunity.status
        }, status=400)

    metadata = dict(opportunity.trace_metadata or {})
    metadata["seller_feedback"] = feedback
    opportunity.feedback = feedback
    opportunity.feedback_at = timezone.now()
    opportunity.feedback_reason = reason
    metadata["seller_feedback_at"] = opportunity.feedback_at.isoformat()
    metadata["seller_feedback_reason"] = reason
    opportunity.trace_metadata = metadata
    update_fields = ["feedback", "feedback_at", "feedback_reason", "trace_metadata", "updated_at"]

    if feedback == "relevant":
        opportunity.status = "QUALIFIED"
        update_fields.append("status")
    elif feedback == "irrelevant":
        opportunity.status = "REJECTED"
        update_fields.append("status")

    if opportunity.source_platform == "instagram":
        ig_meta = metadata.get("instagram", {})
        s_type = ig_meta.get("source_type")
        s_label = ig_meta.get("source_label")
        if s_type and s_label:
            from apps.discovery.models import InstagramSourceStat
            stat = InstagramSourceStat.objects.filter(
                business=business,
                source_type=s_type,
                label=s_label[:100]
            ).first()
            if stat:
                if feedback == "relevant":
                    stat.alpha += 2.0
                    stat.leads += 1
                    stat.save(update_fields=["alpha", "leads"])
                elif feedback == "irrelevant":
                    stat.beta += 3.0
                    stat.save(update_fields=["beta"])
                    # Auto-retire defective query if seller repeatedly rejects leads from it
                    if stat.beta >= 8.0 and stat.alpha <= 1.5:
                        from apps.discovery.ig_scraper import context, sources
                        sources.retire_and_delete_source(
                            context.Run(), business=business,
                            source_type=s_type,
                            label=s_label
                        )
    elif opportunity.source_platform == "x":
        opportunity.lead_feedback = "good" if feedback == "relevant" else "bad_not_buyer"
        opportunity.lead_feedback_at = opportunity.feedback_at
        update_fields.extend(["lead_feedback", "lead_feedback_at"])
        # Update X query stats via Thompson Sampling
        x_query = opportunity.source_query or ""
        if x_query:
            from apps.discovery.models import InstagramSourceStat
            stat = InstagramSourceStat.objects.filter(
                business=business,
                source_type="x_query",
                label=x_query[:100]
            ).first()
            if not stat and business:
                stat = InstagramSourceStat.objects.create(
                    business=business,
                    family_id=f"x_{business.pk}",
                    source_type="x_query",
                    label=x_query[:100],
                    alpha=1.0,
                    beta=1.0
                )
            if stat:
                if feedback == "relevant":
                    stat.alpha += 2.0
                    stat.leads += 1
                    stat.save(update_fields=["alpha", "leads"])
                elif feedback == "irrelevant":
                    stat.beta += 3.0
                    stat.save(update_fields=["beta"])

    opportunity.save(update_fields=update_fields)
    return JsonResponse({
        "status": "success",
        "message": "بازخورد با موفقیت ثبت شد و در یادگیری سیستم اعمال گردید.",
        "feedback": feedback,
        "reason": reason,
        "new_status": opportunity.status
    })
