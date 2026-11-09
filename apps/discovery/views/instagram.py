import logging
import os
import re

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.cache import cache
from django.db import IntegrityError, transaction
from django.db.models import Q
from django.shortcuts import get_object_or_404, redirect, render

from apps.discovery.models import (
    InstagramCategoryTarget,
    InstagramCrawlExecution,
    InstagramPage,
    Opportunity,
)
from apps.products.models import Category, Product
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
    page_context["crawl_executions"] = InstagramCrawlExecution.objects.filter(business=business).order_by("-crawled_at")[:30]
    if request.method == "POST":
        action = request.POST.get("action", "add")
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
