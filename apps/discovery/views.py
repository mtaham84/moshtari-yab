import csv
import io
import json
import os
import re
from datetime import timedelta
from django.conf import settings

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db.models import Q
from django.http import HttpResponse, JsonResponse
from django.core.paginator import Paginator
from django.utils import timezone
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from apps.core.agent import AgentUnavailable, drafts_for, rewrite_reply

from apps.businesses.models import Business
from apps.core.jalali import format_jalali_date, parse_jalali_date
from apps.products.models import Category, Product
from need_engine.text import norm

from .models import (MonitoredCommunity, Opportunity, OPPORTUNITY_STATUS_CHOICES, SELLER_FEEDBACK_REASON_CHOICES, XCollectorState,
                    XNegativeTerm, XSearchQuery)
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

    paginator = Paginator(qs, 25)
    page_obj = paginator.get_page(request.GET.get("page"))
    opportunities = page_obj.object_list

    # Global summary stats
    all_opps = Opportunity.objects.filter(business=business)
    stats = {
        "total": all_opps.count(),
        "filtered_count": paginator.count,
        "high_intent": all_opps.filter(ai_analysis__product_fit_score__gte=0.8).count(),
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
    pagination_query = request.GET.copy()
    pagination_query.pop("page", None)
    pagination_query_string = pagination_query.urlencode()
    pagination_base_url = f"?{pagination_query_string}&page=" if pagination_query_string else "?page="

    context = {
        "opportunities": opportunities,
        "page_obj": page_obj,
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
        "pagination_base_url": pagination_base_url,
        "pagination_range": paginator.get_elided_page_range(page_obj.number, on_each_side=2, on_ends=1),
        "latest_opportunity_id": all_opps.order_by("-id").values_list("id", flat=True).first() or 0,
    }
    return render(request, "discovery/opportunities_list.html", context)


@login_required
def opportunity_export_view(request):
    """
    Exports filtered opportunities into an Excel-compatible CSV file (UTF-8 BOM).
    Columns:
    1. آیدی مشتری
    2. شبکه اجتماعی مشتری
    3. لینک پروفایل کامل مشتری
    4. پیام مشتری
    5. کالا های پیشنهاد شده برای مشتری
    6. پیام ساجست شده برای مشتری
    """
    business = getattr(request.user, "business", None)
    if not business:
        return HttpResponse("کسب‌وکار یافت نشد.", status=404)

    qs, _ = _filter_opportunities_queryset(business, request.GET)

    today_str = format_jalali_date(timezone.now().date(), persian_digits=False).replace("/", "-")
    filename = f"customers_{today_str}.csv"

    buffer = io.StringIO()
    writer = csv.writer(buffer, dialect="excel", quoting=csv.QUOTE_ALL, lineterminator="\r\n")

    headers = [
        "آیدی مشتری",
        "شبکه اجتماعی مشتری",
        "لینک پروفایل کامل مشتری",
        "پیام مشتری",
        "کالا های پیشنهاد شده برای مشتری",
        "پیام ساجست شده برای مشتری",
    ]
    writer.writerow(headers)

    platform_map = {
        "telegram": "تلگرام",
        "x": "ایکس (توییتر)",
        "twitter": "ایکس (توییتر)",
        "instagram": "اینستاگرام",
        "divar": "دیوار",
        "bale": "بله",
        "eitaa": "ایتا",
    }

    for opp in qs.select_related("customer", "ai_analysis").prefetch_related("product_matches__product", "evidence_items"):
        c = opp.customer

        # 1. آیدی مشتری (Clean English Identifier)
        customer_id = "-"
        if c:
            if c.source_username and c.source_username.strip():
                u_str = c.source_username.strip().lstrip("@")
                customer_id = f"@{u_str}" if u_str else "-"
            elif c.external_user_id and c.external_user_id.strip():
                raw_id = c.external_user_id.strip()
                for pfx in ("telegram_", "x_", "instagram_", "divar_"):
                    if raw_id.startswith(pfx):
                        raw_id = raw_id[len(pfx):]
                        break
                customer_id = raw_id or "-"
            elif c.phone_number and c.phone_number.strip():
                customer_id = c.phone_number.strip()
            elif c.name and c.name.strip():
                customer_id = c.name.strip()

        # 2. شبکه اجتماعی مشتری
        social_network = platform_map.get(opp.source_platform.lower(), opp.source_platform or "نامشخص")

        # 3. لینک پروفایل کامل مشتری (Complete English URL)
        profile_link = ""
        if c:
            if c.source_profile_url and c.source_profile_url.startswith("http"):
                profile_link = c.source_profile_url.strip()
            elif c.source_username and c.source_username.strip():
                clean_un = c.source_username.strip().lstrip("@")
                if clean_un:
                    if opp.source_platform == "telegram":
                        profile_link = f"https://t.me/{clean_un}"
                    elif opp.source_platform in ("x", "twitter"):
                        profile_link = f"https://x.com/{clean_un}"
                    elif opp.source_platform == "instagram":
                        profile_link = f"https://instagram.com/{clean_un}"
            elif c.external_user_id and c.external_user_id.strip():
                raw_id = c.external_user_id.strip()
                for pfx in ("telegram_", "x_", "instagram_", "divar_"):
                    if raw_id.startswith(pfx):
                        raw_id = raw_id[len(pfx):]
                        break
                if raw_id.isdigit():
                    if opp.source_platform == "telegram":
                        profile_link = f"https://t.me/c/{raw_id}"
                    elif opp.source_platform in ("x", "twitter"):
                        profile_link = f"https://x.com/i/user/{raw_id}"
        if not profile_link:
            profile_link = "-"

        # 4. پیام مشتری (Persian Message - Newlines normalized to keep single row)
        raw_msg = (opp.source_raw_message or "").strip()
        if not raw_msg:
            cust_evs = [e.content.strip() for e in opp.evidence_items.all() if e.evidence_type == "customer_message" and e.content]
            if cust_evs:
                raw_msg = " | ".join(cust_evs)
            else:
                need_evs = [e.content.strip() for e in opp.evidence_items.all() if e.evidence_type == "need_signal" and e.content]
                if need_evs:
                    raw_msg = " | ".join(need_evs)
                elif hasattr(opp, "ai_analysis") and opp.ai_analysis:
                    raw_msg = (opp.ai_analysis.need or "").strip()

        customer_message = " ".join(raw_msg.split()) if raw_msg else "-"

        # 5. کالا های پیشنهاد شده برای مشتری (Comma-separated product names)
        matches = list(opp.product_matches.all())
        if matches:
            prods = [m.product.name.strip() for m in matches if m.product and m.product.name]
            recommended_products = " ، ".join(prods) if prods else "-"
        elif opp.primary_product_match and opp.primary_product_match.product:
            recommended_products = opp.primary_product_match.product.name.strip()
        else:
            recommended_products = "-"

        # 6. پیام ساجست شده برای مشتری
        suggested_reply = ""
        if hasattr(opp, "ai_analysis") and opp.ai_analysis:
            suggested_reply = (opp.ai_analysis.suggested_reply or "").strip()

        if not suggested_reply:
            try:
                drafts = drafts_for(opp)
                if drafts:
                    suggested_reply = next((v for v in drafts.values() if v), "").strip()
            except Exception:
                pass

        if not suggested_reply:
            first_prod = matches[0].product.name.strip() if (matches and matches[0].product) else ""
            need_str = (opp.ai_analysis.need.strip() if hasattr(opp, "ai_analysis") and opp.ai_analysis and opp.ai_analysis.need else "").strip()
            if first_prod and need_str:
                suggested_reply = f"سلام وقت بخیر؛ در رابطه با {need_str}، پیشنهاد می‌کنم «{first_prod}» رو بررسی بفرمایید."
            elif first_prod:
                suggested_reply = f"سلام وقت بخیر؛ در رابطه با موضوعی که مطرح کردید، پیشنهاد می‌کنم «{first_prod}» رو بررسی بفرمایید."
            else:
                suggested_reply = "-"
        else:
            suggested_reply = " ".join(suggested_reply.split())

        writer.writerow([
            customer_id,
            social_network,
            profile_link,
            customer_message,
            recommended_products,
            suggested_reply,
        ])

    csv_bytes = b"\xef\xbb\xbf" + buffer.getvalue().encode("utf-8")
    response = HttpResponse(csv_bytes, content_type="text/csv; charset=utf-8")
    response["Content-Disposition"] = f'attachment; filename="{filename}"'
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
    """→ (normalized_link, "") or ("", Persian error). Rules: valid Telegram link, not already one of this seller's
    sources, at most TG_MAX_PRIVATE_SOURCES per seller. A global source may be added too (the seller then pays its
    whole analysis, see need_engine/access.py ``owners``); the group is still crawled and analysed once."""
    key = normalize_link(raw_link)
    if not key:
        return "", "لطفاً لینک یا آیدی گروه تلگرامی را وارد کنید."
    if not (TG_USERNAME_RE.match(key) or TG_INVITE_RE.match(key)):
        return "", "لینک واردشده معتبر نیست. نمونه‌ی درست: @group_name یا https://t.me/group_name یا لینک دعوت https://t.me/+…"
    own = MonitoredCommunity.objects.filter(scope=PRIVATE, business=business)
    if own.filter(normalized_link=key).exists():
        return "", "این گروه قبلاً در منابع اختصاصی شما ثبت شده است."
    limit = max_private_sources()
    if own.count() >= limit:
        return "", f"حداکثر {limit} منبع اختصاصی می‌توانید داشته باشید. برای افزودن، یکی از منابع فعلی را حذف کنید."
    return key, ""


@login_required
def opportunity_feedback_view(request, pk):
    if request.method != "POST":
        return JsonResponse({"status": "error", "message": "متد غیرمجاز است."}, status=405)

    business = getattr(request.user, "business", None)
    opportunity = get_object_or_404(Opportunity, pk=pk, business=business)
    feedback = request.POST.get("feedback", "").strip()
    if feedback not in {"relevant", "irrelevant"}:
        return JsonResponse({"status": "error", "message": "بازخورد نامعتبر است."}, status=400)
    reason = request.POST.get("reason", "").strip()
    if reason and reason not in dict(SELLER_FEEDBACK_REASON_CHOICES):
        return JsonResponse({"status": "error", "message": "دلیل بازخورد نامعتبر است."}, status=400)

    metadata = dict(opportunity.trace_metadata or {})
    metadata["seller_feedback"] = feedback
    opportunity.feedback = feedback
    opportunity.feedback_at = timezone.now()
    opportunity.feedback_reason = reason
    metadata["seller_feedback_at"] = opportunity.feedback_at.isoformat()
    metadata["seller_feedback_reason"] = reason
    opportunity.trace_metadata = metadata
    update_fields = ["feedback", "feedback_at", "feedback_reason", "trace_metadata", "updated_at"]
    if opportunity.source_platform == "x":
        opportunity.lead_feedback = "good" if feedback == "relevant" else "bad_not_buyer"
        opportunity.lead_feedback_at = opportunity.feedback_at
        update_fields.extend(["lead_feedback", "lead_feedback_at"])
    opportunity.save(update_fields=update_fields)
    return JsonResponse({"status": "success", "feedback": feedback, "reason": reason})


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
            if MonitoredCommunity.objects.filter(scope=GLOBAL, normalized_link=key, is_active=True).exists():
                messages.info(request, "این گروه جزو منابع عمومی هم هست و برای همه پایش می‌شود؛ با افزودن آن به منابع "
                                       "اختصاصی، هزینه‌ی تحلیل پیام‌هایش از کیف پول شما کسر می‌شود.")
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
    from apps.products.models import Product

    from . import x_activity

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
    from apps.products.models import Product

    from . import x_activity

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
