import logging
import os
import re
from urllib.parse import urlencode

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.cache import cache
from django.db import IntegrityError, transaction
from django.db.models import Q
from django.http import HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.csrf import csrf_protect
from django.views.decorators.http import require_http_methods

from apps.discovery.models import (
    InstagramCategoryTarget,
    InstagramCrawlExecution,
    InstagramPage,
    Opportunity,
)
from apps.products.models import Category, Product
from instagram_discovery.comment_metrics import (
    build_comment_report,
    load_labels,
    pending_human_labels,
    read_jsonl,
    reporting_paths,
    save_human_label,
)
from instagram_discovery.post_label_store import PostLabelStore, create_post_label_store
from instagram_discovery.post_metrics import (
    REASON_CODES,
    build_post_report,
    post_review_paths,
)
from shared.instagram import PageRefError, parse_page_ref

logger = logging.getLogger(__name__)
INSTAGRAM_PAGE_CHANGE_LIMIT = max(1, int(os.getenv("IG_PAGE_CHANGES_PER_HOUR", "30")))


def _instagram_list(value: str, *, hashtags: bool = False) -> list[str]:
    parts = value.replace("،", ",").replace(";", ",").splitlines()
    if len(parts) == 1:
        parts = parts[0].split(",")
    output = []
    seen = set()
    for part in parts:
        item = part.strip()
        if hashtags:
            item = item.lstrip("#").strip()
        key = item.casefold()
        if item and key not in seen:
            output.append(item[:80])
            seen.add(key)
        if len(output) == 50:
            break
    return output


def _bounded_post_int(value: str | None, default: int, minimum: int, maximum: int) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        return default
    return max(minimum, min(maximum, number))


def _instagram_change_allowed(request):
    key = f"ig-page-change:{request.user.pk}"
    limit = INSTAGRAM_PAGE_CHANGE_LIMIT
    try:
        count = cache.get(key, 0)
        if count >= limit:
            return False
        if count == 0:
            cache.add(key, 1, timeout=3600)
        else:
            cache.incr(key)
        return True
    except (ValueError, TypeError):
        cache.set(key, 1, timeout=3600)
        return True


def _page_ref_error(reason):
    messages_by_reason = {
        "reserved_path": "لینک صفحه یا پست اینستاگرام پذیرفته نمی‌شود؛ آیدی خود صفحه را وارد کنید.",
        "invalid_host": "فقط لینک پروفایل از دامنه‌های رسمی اینستاگرام پذیرفته می‌شود.",
        "invalid_scheme": "لینک باید با http یا https باشد؛ می‌توانید فقط آیدی صفحه را هم وارد کنید.",
        "too_long": "لینک یا آیدی بیش از حد طولانی است.",
        "non_ascii": "آیدی صفحه باید با حروف انگلیسی، عدد، نقطه یا زیرخط نوشته شود.",
    }
    return messages_by_reason.get(reason, "لینک یا آیدی صفحه معتبر نیست.")


@login_required
def instagram_pages_view(request):
    business = getattr(request.user, "business", None)
    if business is None:
        messages.error(request, "حساب کسب‌وکار مرتبط یافت نشد.")
        return redirect("accounts:dashboard")

    pages = InstagramPage.objects.filter(business=business).select_related("default_product")
    for page in pages:
        try:
            handle = parse_page_ref(page.handle)
        except PageRefError:
            page.profile_url = ""
        else:
            page.profile_url = f"https://www.instagram.com/{handle}/"
    status_labels = {"ok": "موفق", "queued": "در صف", "not_found": "صفحه پیدا نشد",
                     "private": "خصوصی", "rate_limited": "محدودیت موقت", "error": "خطا",
                     "candidate": "پیشنهادی", "approved": "تأییدشده", "rejected": "ردشده"}
    for page in pages:
        page.status_label = status_labels.get(page.last_status, page.last_status)
    products = Product.objects.filter(business=business, status="ACTIVE").order_by("name")
    categories = Category.objects.filter(Q(business__isnull=True) | Q(business=business), is_active=True).order_by("full_path")
    category_targets = InstagramCategoryTarget.objects.filter(business=business).select_related("category")
    targets_by_category = {target.category_id: target for target in category_targets}
    category_configs = [{"category": category, "target": targets_by_category.get(category.pk)} for category in categories]
    page_context = {
        "pages": pages, "products": products, "categories": categories,
        "category_configs": category_configs,
    }
    evaluation_path, labels_path = reporting_paths()
    evaluation_rows = read_jsonl(evaluation_path)
    human_labels = load_labels(labels_path, business_id=business.pk)
    page_context["comment_report"] = build_comment_report(
        evaluation_rows, labels=human_labels, business_id=business.pk,
    )
    page_context["pending_comment_labels"] = pending_human_labels(
        evaluation_rows, human_labels, business_id=business.pk, limit=25,
    )
    page_context["crawl_executions"] = InstagramCrawlExecution.objects.filter(business=business).order_by("-crawled_at")[:30]
    if request.method == "POST":
        action = request.POST.get("action", "add")
        if action == "label_comment":
            comment_id = request.POST.get("comment_id", "").strip()
            label = request.POST.get("label", "")
            comment = next((row for row in pending_human_labels(
                evaluation_rows, human_labels, business_id=business.pk, limit=100,
            ) if str(row.get("id") or "") == comment_id), None)
            if comment is None:
                messages.error(request, "این کامنت در صف ارزیابی کسب‌وکار شما نیست.")
            elif label not in {"approve", "reject"}:
                messages.error(request, "برچسب باید تأیید یا رد باشد.")
            else:
                save_human_label(labels_path, comment_id=comment_id, business_id=business.pk,
                                 label=label, operator_id=request.user.pk)
                messages.success(request, "برچسب انسانی ذخیره شد.")
            return redirect("discovery:instagram_pages")
        if action in {"add", "remove", "save_category_target", "approve_candidate", "deny_candidate"} and not _instagram_change_allowed(request):
            return render(request, "discovery/instagram_pages.html", {
                **page_context, "throttled": True,
            }, status=429)
        if action == "save_category_target":
            category_id = request.POST.get("category_id", "")
            category = Category.objects.filter(
                Q(business__isnull=True) | Q(business=business), is_active=True, pk=category_id
            ).first() if category_id.isdigit() else None
            if category is None:
                messages.error(request, "دسته‌بندی معتبر نیست.")
            else:
                max_posts = _bounded_post_int(request.POST.get("max_posts"), 5, 1, 100)
                window_days = _bounded_post_int(request.POST.get("window_days"), 7, 1, 90)
                InstagramCategoryTarget.objects.update_or_create(
                    business=business, category=category,
                    defaults={
                        "terms": _instagram_list(request.POST.get("terms", "")),
                        "hashtags": _instagram_list(request.POST.get("hashtags", ""), hashtags=True),
                        "max_posts": max_posts, "window_days": window_days,
                        "is_active": request.POST.get("is_active") == "on",
                    },
                )
                messages.success(request, "تنظیم دسته‌بندی اینستاگرام ذخیره شد.")
            return redirect("discovery:instagram_pages")
        if action == "add":
            try:
                handle = parse_page_ref(request.POST.get("page_ref", ""))
            except PageRefError as exc:
                messages.error(request, _page_ref_error(exc.reason))
            else:
                kind = request.POST.get("kind", "watched")
                if kind not in {"own", "watched"}:
                    kind = "watched"
                product_id = request.POST.get("default_product", "")
                if product_id and not product_id.isdigit():
                    messages.error(request, "محصول پیش‌فرض معتبر نیست.")
                    return render(request, "discovery/instagram_pages.html", page_context)
                default_product = Product.objects.filter(pk=product_id, business=business).first() if product_id else None
                if product_id and default_product is None:
                    messages.error(request, "این محصول به کسب‌وکار شما تعلق ندارد.")
                    return render(request, "discovery/instagram_pages.html", page_context)
                if InstagramPage.objects.filter(business=business, handle__iexact=handle).exists():
                    messages.error(request, "این صفحه قبلاً به فهرست شما اضافه شده است.")
                    return render(request, "discovery/instagram_pages.html", page_context)
                try:
                    with transaction.atomic():
                        InstagramPage.objects.create(business=business, handle=handle, kind=kind, default_product=default_product)
                except IntegrityError:
                    messages.error(request, "این صفحه قبلاً به فهرست شما اضافه شده است.")
                else:
                    logger.info("Instagram page added: @%s", handle)
                    messages.success(request, f"صفحهٔ @{handle} به فهرست پایش اضافه شد.")
                    return redirect("discovery:instagram_pages")
        elif action == "remove":
            page = get_object_or_404(pages, pk=request.POST.get("page_id"))
            handle = page.handle
            page.delete()
            logger.info("Instagram page removed: @%s", handle)
            messages.success(request, f"صفحهٔ @{handle} حذف شد.")
            return redirect("discovery:instagram_pages")
        elif action == "toggle":
            page = get_object_or_404(pages, pk=request.POST.get("page_id"))
            page.is_active = not page.is_active
            if page.is_active and page.discovered_family_id:
                page.last_status = "approved"
                page.save(update_fields=["is_active", "last_status"])
            else:
                page.save(update_fields=["is_active"])
            messages.success(request, "پایش صفحه فعال شد." if page.is_active else "پایش صفحه متوقف شد.")
            return redirect("discovery:instagram_pages")
        elif action == "approve_candidate":
            page = get_object_or_404(pages, pk=request.POST.get("page_id"), discovered_family_id__gt="")
            page.is_active, page.last_status = True, "approved"
            page.save(update_fields=["is_active", "last_status"])
            messages.success(request, f"صفحهٔ @{page.handle} تأیید و برای پایش فعال شد.")
            return redirect("discovery:instagram_pages")
        elif action == "deny_candidate":
            page = get_object_or_404(pages, pk=request.POST.get("page_id"), discovered_family_id__gt="")
            page.is_active, page.last_status = False, "rejected"
            page.save(update_fields=["is_active", "last_status"])
            messages.success(request, f"صفحهٔ پیشنهادی @{page.handle} رد شد.")
            return redirect("discovery:instagram_pages")
    return render(request, "discovery/instagram_pages.html", page_context)


@login_required
def instagram_comment_report_api_view(request):
    business = getattr(request.user, "business", None)
    if business is None:
        return JsonResponse({"status": "error", "message": "کسب‌وکار مرتبط یافت نشد."}, status=404)
    evaluation_path, labels_path = reporting_paths()
    report = build_comment_report(
        read_jsonl(evaluation_path), labels=load_labels(labels_path, business_id=business.pk),
        business_id=business.pk,
    )
    return JsonResponse({"status": "success", "data": report}, json_dumps_params={"ensure_ascii": False})


def _instagram_post_review_context(
    request, business, label_store: PostLabelStore, *, page_number: int | None = None,
):
    analyzed_path, _, _ = post_review_paths()
    categories = list(Category.objects.filter(business=business, is_active=True).order_by("full_path", "pk"))
    products = list(Product.objects.filter(business=business, status="ACTIVE").order_by("name", "pk"))
    labels = label_store.load(business_id=business.pk)
    reason_code = request.GET.get("reason", "").strip()
    if reason_code not in REASON_CODES:
        reason_code = ""
    category_filter = request.GET.get("category", "").strip()
    own_category_ids = {str(category.pk) for category in categories}
    if category_filter not in own_category_ids:
        category_filter = ""
    page = page_number if page_number is not None else _bounded_post_int(request.GET.get("page"), 1, 1, 5_000)
    page_size = 20
    offset = (page - 1) * page_size
    rows, total = label_store.pending(
        business_id=business.pk, limit=page_size, offset=offset,
        reason_code=reason_code or None, category_id=category_filter or None,
    )
    page_count = max(1, (total + page_size - 1) // page_size)
    page = min(page, page_count)
    if offset >= total and total:
        offset = (page - 1) * page_size
        rows, _ = label_store.pending(
            business_id=business.pk, limit=page_size, offset=offset,
            reason_code=reason_code or None, category_id=category_filter or None,
        )
    last_label = None
    labeled_post_id = request.GET.get("labeled_post", "")
    if labeled_post_id:
        label = labels.get((str(business.pk), labeled_post_id))
        if label:
            last_label = {
                "post_id": label["post_id"], "decision": label["decision"],
                "analyzer_snapshot": label["analyzer_snapshot"],
            }
    report = build_post_report(read_jsonl(analyzed_path), label_store, business_id=business.pk)
    return {
        "pending_posts": rows, "pending_total": total, "page": page, "page_count": page_count,
        "reason_filter": reason_code, "category_filter": category_filter,
        "review_reasons": sorted(REASON_CODES),
        "categories": categories, "products": products, "post_report": report,
        "last_post_label": last_label,
    }


@login_required
@csrf_protect
@require_http_methods(["GET", "POST"])
def instagram_post_review_view(request):
    """Business-scoped, blind-first human post review and confident-match auditing."""
    business = getattr(request.user, "business", None)
    if business is None:
        return HttpResponse(status=404)
    label_store = create_post_label_store()

    if request.method == "POST":
        action = request.POST.get("action", "")
        if action == "skip_post":
            page = _bounded_post_int(request.POST.get("page"), 1, 1, 5_000) + 1
            query = {"page": page}
            for name in ("reason", "category"):
                value = request.POST.get(name, "").strip()
                if value:
                    query[name] = value
            return redirect(f"{reverse('discovery:instagram_post_review')}?{urlencode(query)}")
        if action != "label_post":
            return HttpResponse(status=400)
        if not _instagram_change_allowed(request):
            context = _instagram_post_review_context(request, business, label_store)
            return render(request, "discovery/instagram_post_review.html", {
                **context, "throttled": True,
            }, status=429)

        post_id = request.POST.get("post_id", "").strip()
        try:
            matches, _ = label_store.pending(
                business_id=business.pk, limit=1, post_id=post_id,
            )
        except ValueError:
            return HttpResponse(status=400)
        if not matches:
            return HttpResponse(status=404)
        review_item = matches[0]
        decision = request.POST.get("decision", "")
        if decision not in {"approve", "reject"}:
            return HttpResponse(status=400)

        category_id: int | None = None
        product_id: int | None = None
        if decision == "approve":
            raw_category_id = request.POST.get("category_id", "").strip()
            category = Category.objects.filter(
                business=business, is_active=True, pk=raw_category_id,
            ).first() if raw_category_id.isdigit() else None
            if category is None:
                messages.error(request, "برای تأیید، دسته‌بندی فعال متعلق به کسب‌وکار خودتان را انتخاب کنید.")
                return render(request, "discovery/instagram_post_review.html", {
                    **_instagram_post_review_context(request, business, label_store), "invalid_label": True,
                }, status=400)
            category_id = category.pk
            raw_product_id = request.POST.get("product_id", "").strip()
            if raw_product_id:
                product = Product.objects.filter(
                    business=business, status="ACTIVE", pk=raw_product_id,
                ).first() if raw_product_id.isdigit() else None
                if product is None or (product.category_id and product.category_id != category.pk):
                    messages.error(request, "محصول باید فعال، متعلق به کسب‌وکار شما و سازگار با دسته‌بندی باشد.")
                    return render(request, "discovery/instagram_post_review.html", {
                        **_instagram_post_review_context(request, business, label_store), "invalid_label": True,
                    }, status=400)
                product_id = product.pk
        try:
            label_store.save(
                post_id=review_item["post_id"], business_id=business.pk,
                decision=decision, category_id=category_id, product_id=product_id,
                operator_id=request.user.pk, snapshot=review_item["analyzer_snapshot"],
                audit=review_item["audit"],
            )
        except ValueError:
            return HttpResponse(status=400)
        query = urlencode({"labeled_post": review_item["post_id"]})
        return redirect(f"{reverse('discovery:instagram_post_review')}?{query}")

    context = _instagram_post_review_context(request, business, label_store)
    return render(request, "discovery/instagram_post_review.html", context)


@login_required
def instagram_post_report_api_view(request):
    business = getattr(request.user, "business", None)
    if business is None:
        return JsonResponse({"status": "error", "message": "کسب‌وکار مرتبط یافت نشد."}, status=404)
    analyzed_path, _, _ = post_review_paths()
    report = build_post_report(
        read_jsonl(analyzed_path), create_post_label_store(),
        business_id=business.pk,
    )
    return JsonResponse({"status": "success", "data": report}, json_dumps_params={"ensure_ascii": False})


@login_required
def instagram_comment_detail_view(request, pk):
    business = getattr(request.user, "business", None)
    opportunity = get_object_or_404(
        Opportunity.objects.select_related("customer", "ai_analysis").prefetch_related(
            "evidence_items", "product_matches__product",
        ),
        pk=pk, business=business, source_platform="instagram",
    )
    try:
        author = parse_page_ref(opportunity.customer.source_username or "")
    except PageRefError:
        profile_url = ""
    else:
        profile_url = f"https://www.instagram.com/{author}/"
    source_id = re.fullmatch(r"ig:([A-Za-z0-9_-]{1,80}):c[A-Za-z0-9_-]{1,80}", opportunity.source_message_id)
    post_url = f"https://www.instagram.com/p/{source_id.group(1)}/" if source_id else ""
    return render(request, "discovery/instagram_comment_detail.html", {
        "opportunity": opportunity, "customer": opportunity.customer,
        "analysis": getattr(opportunity, "ai_analysis", None),
        "instagram_profile_url": profile_url, "instagram_post_url": post_url,
    })
