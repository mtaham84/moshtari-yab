import json
import logging
import os
import secrets
from decimal import Decimal, InvalidOperation
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.db.models import F, Q
from django.http import Http404, HttpResponseForbidden, HttpResponseRedirect, JsonResponse
from django.views.decorators.http import require_POST
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from apps.businesses.models import Business
from apps.discovery.models import Opportunity, ProductDailyMetric
from .models import Category, Product, ProductImage
from . import agent_card
from .services import suggest_category_for_product

logger = logging.getLogger("products.views")


ALLOWED_IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".gif"}

def _is_safe_image_file(f):
    from apps.core.image_service import is_valid_image
    return is_valid_image(f)


from apps.core.form_helpers import parse_bounded_int, parse_form_list

# Service layer re-exports (P08.17 - R39)
from apps.products.services import (
    VALID_PRODUCT_TYPES,
    VALID_STATUSES,
    categories_for_business,
    categories_for_business as _categories_for,
    parse_and_validate_price,
    parse_and_validate_price as _parse_and_validate_price,
    parse_dynamic_attributes,
    resolve_product_save_error,
    sanitize_and_validate_images,
    save_product,
    validate_product_input,
    validate_product_input as _validate_product_input,
)

_form_list = parse_form_list
_form_list_int = parse_bounded_int


@login_required
def product_list_view(request):
    business = getattr(request.user, "business", None)
    if not business:
        business = Business.objects.create(
            user=request.user,
            name=f"کسب‌وکار {request.user.first_name}",
            business_type="PHYSICAL",
            business_domain="عمومی"
        )

    products = Product.objects.filter(business=business).select_related("category")

    query = request.GET.get("q", "").strip()
    if query:
        products = products.filter(name__icontains=query)

    type_filter = request.GET.get("type", "").strip()
    if type_filter in ["PHYSICAL", "SERVICE"]:
        products = products.filter(product_type=type_filter)

    stats = {
        "total": products.count(),
        "active": products.filter(status="ACTIVE").count(),
        "physical": products.filter(product_type="PHYSICAL").count(),
        "service": products.filter(product_type="SERVICE").count(),
    }

    return render(request, "products/product_list.html", {
        "business": business,
        "products": products,
        "stats": stats,
        "query": query,
        "type_filter": type_filter,
    })

@login_required
def product_add_view(request):
    business = getattr(request.user, "business", None)
    if not business:
        business = Business.objects.create(
            user=request.user,
            name=f"کسب‌وکار {request.user.first_name}",
            business_type="PHYSICAL",
            business_domain="عمومی",
        )

    categories = categories_for_business(business)
    category_attributes_map = {str(c.id): (c.suggested_attributes or []) for c in categories}

    if request.method == "POST":
        cleaned, errors = validate_product_input(request.POST, categories)
        attributes = parse_dynamic_attributes(request.POST)

        images = request.FILES.getlist("images")
        if len(images) > 10:
            errors.append("حداکثر ۱۰ تصویر برای هر محصول مجاز است.")

        if errors:
            for err in errors:
                messages.error(request, err)
            return render(request, "products/product_form.html", {
                "categories": categories,
                "category_attributes_map": category_attributes_map,
                "is_edit": False,
                "product_type": cleaned.get("product_type", "PHYSICAL"),
            })

        main_index_raw = request.POST.get("selected_main_index", "0")
        try:
            main_img_index = int(main_index_raw)
        except ValueError:
            main_img_index = 0

        valid_images, image_warnings = sanitize_and_validate_images(images, max_allowed=10)
        for warn in image_warnings:
            messages.warning(request, warn)

        try:
            product, organizing = save_product(
                business,
                cleaned,
                attributes,
                valid_images,
                main_img_index=main_img_index,
            )
        except Exception as exc:
            from apps.core.typed_errors import log_sanitized

            log_sanitized(
                logger,
                logging.ERROR,
                "product_create_failed",
                tenant_id=business.id,
                error=exc,
            )
            messages.error(request, resolve_product_save_error(exc))
            return render(request, "products/product_form.html", {
                "categories": categories,
                "category_attributes_map": category_attributes_map,
                "is_edit": False,
                "product_type": cleaned.get("product_type", "PHYSICAL"),
            })

        messages.success(
            request,
            f"محصول «{product.name}» با موفقیت ثبت و ساختاردهی شد."
            + (" هوش مصنوعی در حال دسته‌بندی کالاهای شما و ساخت خانواده‌های جستجوست." if organizing else ""),
        )
        return redirect("products:detail", pk=product.pk)

    return render(request, "products/product_form.html", {
        "categories": categories,
        "category_attributes_map": category_attributes_map,
        "is_edit": False,
    })


@login_required
def product_edit_view(request, pk):
    business = get_object_or_404(Business, user=request.user)
    product = get_object_or_404(Product, pk=pk, business=business)
    categories = categories_for_business(business)
    category_attributes_map = {str(c.id): (c.suggested_attributes or []) for c in categories}

    if request.method == "POST":
        cleaned, errors = validate_product_input(request.POST, categories)
        new_images = request.FILES.getlist("images")
        current_images_count = product.images.count()
        if current_images_count + len(new_images) > 10:
            errors.append(
                f"حداکثر ۱۰ تصویر برای هر محصول مجاز است. شما در حال حاضر {current_images_count} تصویر دارید و تنها مجاز به افزودن {max(0, 10 - current_images_count)} تصویر دیگر هستید."
            )

        if errors:
            for err in errors:
                messages.error(request, err)
            return render(request, "products/product_form.html", {
                "product": product,
                "categories": categories,
                "category_attributes_map": category_attributes_map,
                "is_edit": True,
            })

        attributes = parse_dynamic_attributes(request.POST)

        remaining_slots = max(0, 10 - current_images_count)
        valid_new_images, image_warnings = sanitize_and_validate_images(new_images, max_allowed=remaining_slots)
        for warn in image_warnings:
            messages.warning(request, warn)

        try:
            product, _ = save_product(
                business,
                cleaned,
                attributes,
                valid_new_images,
                product=product,
            )
        except Exception as exc:
            from apps.core.typed_errors import log_sanitized

            log_sanitized(
                logger,
                logging.ERROR,
                "product_edit_failed",
                tenant_id=business.id,
                error=exc,
            )
            messages.error(request, resolve_product_save_error(exc))
            return render(request, "products/product_form.html", {
                "product": product,
                "categories": categories,
                "category_attributes_map": category_attributes_map,
                "is_edit": True,
            })

        messages.success(request, f"مشخصات محصول «{product.name}» به‌روزرسانی شد.")
        return redirect("products:detail", pk=product.pk)

    return render(request, "products/product_form.html", {
        "product": product,
        "categories": categories,
        "category_attributes_map": category_attributes_map,
        "is_edit": True,
    })

@login_required
def product_detail_view(request, pk):
    business = get_object_or_404(Business, user=request.user)
    product = get_object_or_404(Product, pk=pk, business=business)
    return render(request, "products/product_detail.html", {
        "product": product,
        "business": business,
        "agent_card": agent_card.card_state(product),
    })


@login_required
@require_POST
def product_agent_card_view(request, pk):
    """Save the seller's version of the agent's card, or go back to the agent's own version."""
    business = get_object_or_404(Business, user=request.user)
    product = get_object_or_404(Product, pk=pk, business=business)
    if request.POST.get("action") == "revert":
        product.agent_card_override = None
        product.save(update_fields=["agent_card_override", "updated_at"])
        messages.success(request, "برداشت ایجنت به نسخه‌ی خودش برگشت؛ چند ثانیه‌ی دیگر دوباره ساخته می‌شود.")
    else:
        current = agent_card.card_state(product)["card"]
        card = agent_card.from_form(request.POST, current)
        if not card["what_it_is"]:
            messages.error(request, "بخش «این محصول چیست» نمی‌تواند خالی باشد.")
        else:
            product.agent_card_override = card
            product.save(update_fields=["agent_card_override", "updated_at"])
            messages.success(request, "برداشت ویرایش‌شده ذخیره شد و از این به بعد مستقیم در تطبیق استفاده می‌شود.")
    return redirect("products:detail", pk=product.pk)


@login_required
@require_POST
def api_autofill_from_url(request):
    """Product page URL → suggested form values (the form fills only empty fields)."""
    from apps.core.agent import AgentUnavailable

    from .autofill import FetchError, suggest, validate_url

    business = getattr(request.user, "business", None)
    url = (request.POST.get("url") or "").strip()
    if not url:
        return JsonResponse({"status": "error", "message": "لینک صفحه‌ی محصول را وارد کنید."}, status=400)
    try:
        url = validate_url(url)
        _, data = suggest(url, business=business)
    except (FetchError, AgentUnavailable) as e:
        return JsonResponse({"status": "error", "message": str(e)}, status=400)
    return JsonResponse({"status": "success", "url": url, "data": data})


def product_click_view(request, pk):
    """/r/<id>/?ref=<opportunity>: the link in reply drafts. Counts the click, then goes to the seller's own page."""
    product = get_object_or_404(Product.objects.select_related("business"), pk=pk)
    ref = (request.GET.get("ref") or "").strip()[:64]
    ProductDailyMetric.record_click(product=product, event_id=ref or None)
    target = (product.url or "").strip()
    if target.lower().startswith(("http://", "https://")):
        return HttpResponseRedirect(target)
    # B2C Card (/p/<id>/) is removed; redirect to the seller's shop showcase or home
    if product.business and product.business.slug and product.business.onboarding_done:
        return redirect("public_shop", slug=product.business.slug)
    return redirect("/")

@login_required
def product_delete_view(request, pk):
    business = get_object_or_404(Business, user=request.user)
    product = get_object_or_404(Product, pk=pk, business=business)
    if request.method == "POST":
        name = product.name
        product.delete()
        messages.info(request, f"محصول «{name}» از لیست پایگاه دانش حذف شد.")
        return redirect("products:list")
    return redirect("products:detail", pk=pk)

@login_required
def set_main_image_view(request, pk, image_pk):
    business = get_object_or_404(Business, user=request.user)
    product = get_object_or_404(Product, pk=pk, business=business)
    image = get_object_or_404(ProductImage, pk=image_pk, product=product)

    if request.method == "POST":
        with transaction.atomic():
            Product.objects.select_for_update().get(pk=product.pk)
            product.images.exclude(pk=image.pk).update(is_main=False)
            image.is_main = True
            image.save(update_fields=["is_main"])
        messages.success(request, "تصویر مورد نظر به عنوان تصویر اصلی محصول انتخاب شد.")

    # Redirect to where the request came from, or edit page
    referer = request.META.get("HTTP_REFERER")
    if referer and ("edit" in referer or "detail" in referer):
        return redirect(referer)
    return redirect("products:edit", pk=product.pk)

@login_required
def delete_image_view(request, pk, image_pk):
    business = get_object_or_404(Business, user=request.user)
    product = get_object_or_404(Product, pk=pk, business=business)
    image = get_object_or_404(ProductImage, pk=image_pk, product=product)

    if request.method == "POST":
        with transaction.atomic():
            Product.objects.select_for_update().get(pk=product.pk)
            image.delete()
        messages.success(request, "تصویر با موفقیت حذف گردید.")

    referer = request.META.get("HTTP_REFERER")
    if referer and ("edit" in referer or "detail" in referer):
        return redirect(referer)
    return redirect("products:edit", pk=product.pk)

def api_suggest_category(request):
    # Containment Gate (R07): Prevent anonymous callers from invoking paid LLM
    if not request.user.is_authenticated:
        return JsonResponse({"status": "error", "message": "احراز هویت الزامی است."}, status=401)
    business = getattr(request.user, "business", None)
    if not business:
        return JsonResponse({"status": "error", "message": "کسب‌وکار فعال یافت نشد."}, status=403)

    if request.method == "POST":
        # POST: Authenticated path with CSRF protection and input constraints
        name = request.POST.get("name", "").strip()
        description = request.POST.get("description", "").strip()
        product_type = request.POST.get("product_type", "").strip() or None
        request_ai = request.POST.get("allow_ai", "").lower() in ("1", "true", "yes")
    elif request.method == "GET":
        name = request.GET.get("name", "").strip()
        description = request.GET.get("description", "").strip()
        product_type = request.GET.get("product_type", "").strip() or None
        request_ai = False  # GET is strictly read-only heuristic; never triggers paid LLM
    else:
        return JsonResponse({"status": "error", "message": "روش نامعتبر است."}, status=405)

    if len(name) > 255:
        return JsonResponse({"status": "error", "message": "طول نام کالا حداکثر ۲۵۵ کاراکتر است."}, status=400)
    if len(description) > 2000:
        return JsonResponse({"status": "error", "message": "طول توضیحات کالا حداکثر ۲۰۰۰ کاراکتر است."}, status=400)

    if not name:
        return JsonResponse({"status": "empty", "suggestion": None})

    # Step 3 of P02.01: Paid AI execution remains queued until financial gate is wired in Phase 6
    if request_ai:
        return JsonResponse({
            "status": "queued",
            "message": "درخواست طبقه‌بندی هوش مصنوعی در صف اعتبارسنجی مالی فاز ۶ قرار گرفت.",
            "job_id": None,
            "suggestion": None,
        })

    # Free, local heuristic matching
    suggestion = suggest_category_for_product(
        name=name,
        description=description,
        product_type=product_type,
        business=business,
        allow_ai=False,
    )
    if suggestion:
        return JsonResponse({"status": "success", "suggestion": suggestion})
    return JsonResponse({"status": "no_match", "suggestion": None})

def api_category_attributes(request, category_id):
    if not request.user.is_authenticated:
        return JsonResponse({"status": "error", "message": "احراز هویت الزامی است."}, status=401)
    business = getattr(request.user, "business", None)
    if not business and not request.user.is_superuser:
        return JsonResponse({"status": "error", "message": "کسب‌وکار فعال یافت نشد."}, status=403)

    qs = Category.objects.filter(id=category_id, is_active=True)
    if not request.user.is_superuser:
        qs = qs.filter(Q(business=business) | Q(business__isnull=True))
    category = qs.first()
    if not category:
        return JsonResponse({"attributes": []}, status=404)
    return JsonResponse({"attributes": category.suggested_attributes or []})

@login_required
def api_create_category(request):
    if request.method != "POST":
        return JsonResponse({"status": "error", "message": "روش نامعتبر است."}, status=405)

    business = getattr(request.user, "business", None)
    if not business:
        business = Business.objects.create(
            user=request.user,
            name=f"کسب‌وکار {request.user.first_name}",
            business_type="PHYSICAL",
            business_domain="عمومی"
        )

    try:
        data = json.loads(request.body) if request.body else request.POST
    except Exception:
        data = request.POST

    name = data.get("name", "").strip()
    parent_id = data.get("parent_id")
    product_type = data.get("product_type", "PHYSICAL").strip()

    if not name:
        return JsonResponse({"status": "error", "message": "نام دسته‌بندی الزامی است."}, status=400)

    from apps.products import taxonomy
    try:
        category = taxonomy.add_node(business, int(parent_id) if str(parent_id or "").isdigit() else None, name)
    except taxonomy.TaxonomyError as exc:
        return JsonResponse({"status": "error", "message": str(exc)}, status=400)

    return JsonResponse({
        "status": "success",
        "category": {
            "id": category.id,
            "name": category.name,
            "full_path": category.get_full_path(),
            "parent_id": category.parent_id,
            "product_type": category.product_type,
            "is_custom": True
        }
    })

@login_required
def api_category_tree(request):
    business = getattr(request.user, "business", None)
    categories = _categories_for(business).order_by("name")

    # Map node objects
    nodes = {
        cat.id: {
            "id": cat.id,
            "name": cat.name,
            "parent_id": cat.parent_id,
            "product_type": cat.product_type,
            "full_path": cat.get_full_path(),
            "is_custom": cat.business_id is not None,
            "depth": len(cat.get_ancestors()),
            "children": [],
        }
        for cat in categories
    }

    tree = []
    for cat_id, node in nodes.items():
        if node["parent_id"] and node["parent_id"] in nodes:
            nodes[node["parent_id"]]["children"].append(node)
        else:
            tree.append(node)

    return JsonResponse({"status": "success", "tree": tree})


def public_product_card_view(request, pk):
    """
    B2C Public Card was retired per Product Directive #1 / Remediation Phase 3 (R06, R21, R22).
    Always raises Http404; no public orders, carts, or conversions can be created.
    """
    raise Http404("کارت عمومی محصول و ثبت سفارش B2C حذف شده است.")




@login_required
@require_POST
def product_toggle_x_search_view(request, pk):
    """Quick on/off of the per-product X search from the product list (seller pays for it)."""
    product = get_object_or_404(Product, pk=pk, business__user=request.user)
    state = request.POST.get("state")   # explicit on/off from a switch; absent = flip
    product.x_search_enabled = state == "on" if state in {"on", "off"} else not product.x_search_enabled
    product.save(update_fields=["x_search_enabled", "updated_at"])
    if product.x_search_enabled:
        msg = "جستجوی مشتری در X برای «%s» روشن شد؛ از دور بعدی جستجو (چند دقیقه) نتیجه‌ها اینجا می‌آید." % product.name
        if product.status != "ACTIVE" or not product.is_discovery_active:
            messages.warning(request, "«%s» برای کشف فعال نیست؛ تا فعالش نکنید در X جستجو نمی‌شود." % product.name)
        messages.success(request, msg)
    else:
        messages.success(request, "جستجوی مشتری در X برای «%s» خاموش شد؛ دیگر هزینه‌ای برای آن کسر نمی‌شود." % product.name)
    nxt = request.POST.get("next") or ""
    return redirect(nxt if nxt.startswith("/") and not nxt.startswith("//") else "products:list")
