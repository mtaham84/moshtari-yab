import json
import secrets
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db.models import Q
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from apps.businesses.models import Business
from apps.discovery.models import ProductDailyMetric, ProductOrder, DiscoveredLead
from apps.core.jalali import format_jalali_date, to_persian_digits
from .models import Category, Product, ProductImage
from .services import suggest_category_for_product

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
            business_domain="عمومی"
        )

    categories = Category.objects.filter(
        Q(business__isnull=True) | Q(business=business),
        is_active=True
    ).select_related("parent")

    if request.method == "POST":
        name = request.POST.get("name", "").strip()
        product_type = request.POST.get("product_type", "PHYSICAL").strip()
        category_id = request.POST.get("category_id")
        description = request.POST.get("description", "").strip()
        price_val = request.POST.get("price", "").strip().replace(",", "").replace("،", "")
        url = request.POST.get("url", "").strip()
        target_customer = request.POST.get("target_customer", "").strip()
        status = request.POST.get("status", "ACTIVE").strip()

        # Parse dynamic attributes from form (custom key-value pairs + category suggestions)
        attributes = {}
        attr_keys = request.POST.getlist("custom_attr_key")
        attr_vals = request.POST.getlist("custom_attr_value")
        for k, v in zip(attr_keys, attr_vals):
            k_clean = k.strip()
            v_clean = v.strip()
            if k_clean and v_clean:
                attributes[k_clean] = v_clean

        for key, val in request.POST.items():
            if key.startswith("attr_") and val.strip():
                attr_name = key[5:]
                if attr_name not in attributes:
                    attributes[attr_name] = val.strip()

        # Validation
        if not name or not description:
            messages.error(request, "نام محصول و شرح آن برای شناخت ایجنت الزامی است.")
            return render(request, "products/product_form.html", {
                "categories": categories,
                "is_edit": False,
                "product_type": product_type,
            })

        # Image validation (max 10)
        images = request.FILES.getlist("images")
        if len(images) > 10:
            messages.error(request, "حداکثر ۱۰ تصویر برای هر محصول مجاز است.")
            return render(request, "products/product_form.html", {
                "categories": categories,
                "is_edit": False,
                "product_type": product_type,
            })

        category = None
        if category_id:
            category = Category.objects.filter(id=category_id).first()

        price = None
        if price_val.isdigit():
            price = int(price_val)

        is_discovery_active = request.POST.get("is_discovery_active") in ["on", "true", "1"]
        discovery_priority_val = request.POST.get("discovery_priority", "1")
        discovery_priority = int(discovery_priority_val) if discovery_priority_val.isdigit() else 1
        telegram_outreach_enabled = request.POST.get("telegram_outreach_enabled") in ["on", "true", "1"]
        x_outreach_enabled = request.POST.get("x_outreach_enabled") in ["on", "true", "1"]

        product = Product.objects.create(
            business=business,
            name=name,
            product_type=product_type,
            category=category,
            description=description,
            price=price,
            url=url,
            attributes=attributes,
            target_customer=target_customer,
            status=status,
            is_discovery_active=is_discovery_active,
            discovery_priority=discovery_priority,
            telegram_outreach_enabled=telegram_outreach_enabled,
            x_outreach_enabled=x_outreach_enabled,
        )

        # Save uploaded images (up to 10)
        main_index_raw = request.POST.get("selected_main_index", "0")
        try:
            main_img_index = int(main_index_raw)
        except ValueError:
            main_img_index = 0

        for idx, img_file in enumerate(images[:10]):
            ProductImage.objects.create(
                product=product,
                image=img_file,
                is_main=(idx == main_img_index or (main_img_index >= len(images) and idx == 0)),
                order=idx,
            )

        messages.success(request, f"محصول «{product.name}» با موفقیت ثبت و ساختاردهی شد.")
        return redirect("products:detail", pk=product.pk)

    return render(request, "products/product_form.html", {
        "categories": categories,
        "is_edit": False,
    })

@login_required
def product_edit_view(request, pk):
    business = get_object_or_404(Business, user=request.user)
    product = get_object_or_404(Product, pk=pk, business=business)
    categories = Category.objects.filter(
        Q(business__isnull=True) | Q(business=business),
        is_active=True
    ).select_related("parent")

    if request.method == "POST":
        new_images = request.FILES.getlist("images")
        current_images_count = product.images.count()
        if current_images_count + len(new_images) > 10:
            messages.error(
                request,
                f"حداکثر ۱۰ تصویر برای هر محصول مجاز است. شما در حال حاضر {current_images_count} تصویر دارید و تنها مجاز به افزودن {max(0, 10 - current_images_count)} تصویر دیگر هستید."
            )
            return render(request, "products/product_form.html", {
                "product": product,
                "categories": categories,
                "is_edit": True,
            })

        product.name = request.POST.get("name", "").strip()
        product.product_type = request.POST.get("product_type", "PHYSICAL").strip()
        category_id = request.POST.get("category_id")
        product.description = request.POST.get("description", "").strip()
        price_val = request.POST.get("price", "").strip().replace(",", "").replace("،", "")
        product.url = request.POST.get("url", "").strip()
        product.target_customer = request.POST.get("target_customer", "").strip()
        product.status = request.POST.get("status", "ACTIVE").strip()
        product.is_discovery_active = request.POST.get("is_discovery_active") in ["on", "true", "1"]
        discovery_priority_val = request.POST.get("discovery_priority", "1")
        product.discovery_priority = int(discovery_priority_val) if discovery_priority_val.isdigit() else 1
        product.telegram_outreach_enabled = request.POST.get("telegram_outreach_enabled") in ["on", "true", "1"]
        product.x_outreach_enabled = request.POST.get("x_outreach_enabled") in ["on", "true", "1"]

        # Parse dynamic attributes from form (custom key-value pairs + category suggestions)
        attributes = {}
        attr_keys = request.POST.getlist("custom_attr_key")
        attr_vals = request.POST.getlist("custom_attr_value")
        for k, v in zip(attr_keys, attr_vals):
            k_clean = k.strip()
            v_clean = v.strip()
            if k_clean and v_clean:
                attributes[k_clean] = v_clean

        for key, val in request.POST.items():
            if key.startswith("attr_") and val.strip():
                attr_name = key[5:]
                if attr_name not in attributes:
                    attributes[attr_name] = val.strip()
        product.attributes = attributes

        if category_id:
            product.category = Category.objects.filter(id=category_id).first()
        else:
            product.category = None

        if price_val.isdigit():
            product.price = int(price_val)
        else:
            product.price = None

        product.save()

        # Save new images up to remaining slots
        has_main = product.images.filter(is_main=True).exists()
        for idx, img_file in enumerate(new_images):
            if current_images_count + idx >= 10:
                break
            ProductImage.objects.create(
                product=product,
                image=img_file,
                is_main=(not has_main and idx == 0),
                order=current_images_count + idx,
            )

        messages.success(request, f"مشخصات محصول «{product.name}» به‌روزرسانی شد.")
        return redirect("products:detail", pk=product.pk)

    return render(request, "products/product_form.html", {
        "product": product,
        "categories": categories,
        "is_edit": True,
    })

@login_required
def product_detail_view(request, pk):
    business = get_object_or_404(Business, user=request.user)
    product = get_object_or_404(Product, pk=pk, business=business)
    return render(request, "products/product_detail.html", {
        "product": product,
        "business": business,
    })

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
        product.images.update(is_main=False)
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
        image.delete()
        messages.success(request, "تصویر با موفقیت حذف گردید.")

    referer = request.META.get("HTTP_REFERER")
    if referer and ("edit" in referer or "detail" in referer):
        return redirect(referer)
    return redirect("products:edit", pk=product.pk)

def api_suggest_category(request):
    name = request.GET.get("name", "").strip()
    description = request.GET.get("description", "").strip()
    product_type = request.GET.get("product_type", "").strip() or None

    if not name:
        return JsonResponse({"status": "empty", "suggestion": None})

    suggestion = suggest_category_for_product(name=name, description=description, product_type=product_type)
    if suggestion:
        return JsonResponse({"status": "success", "suggestion": suggestion})
    return JsonResponse({"status": "no_match", "suggestion": None})

def api_category_attributes(request, category_id):
    category = Category.objects.filter(id=category_id).first()
    if not category:
        return JsonResponse({"attributes": []})
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

    parent = None
    if parent_id:
        parent = Category.objects.filter(id=parent_id).first()
        if parent:
            if len(parent.get_ancestors()) >= 4:
                return JsonResponse({
                    "status": "error",
                    "message": "حداکثر عمق مجاز درخت‌واره ۵ سطح است."
                }, status=400)
            product_type = parent.product_type

    category = Category.objects.create(
        business=business,
        name=name,
        parent=parent,
        product_type=product_type,
        is_active=True
    )

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
    categories = Category.objects.filter(
        Q(business__isnull=True) | Q(business=business),
        is_active=True
    ).select_related("parent").order_by("name")

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
    Digikala-style public customer product card & checkout view.
    Accessible publicly without login. Automatically tracks views, agent clicks,
    and handles instant customer order placement and conversion tracking.
    """
    product = get_object_or_404(Product.objects.select_related("business", "category"), pk=pk)
    today = timezone.now().date()
    metric, _ = ProductDailyMetric.objects.get_or_create(product=product, date=today)

    ref_lead_id = request.GET.get("lead_id") or request.GET.get("ref")
    lead_obj = None
    if ref_lead_id and str(ref_lead_id).isdigit():
        lead_obj = DiscoveredLead.objects.filter(id=int(ref_lead_id), product=product).first()

    # Track metrics
    metric.views_count += 1
    if request.GET.get("src") == "agent" or lead_obj or request.GET.get("ref"):
        metric.clicks_count += 1
    metric.save(update_fields=["views_count", "clicks_count"])

    # If POST (order submitted directly from card)
    if request.method == "POST":
        customer_name = request.POST.get("customer_name", "").strip()
        customer_phone = request.POST.get("customer_phone", "").strip()
        shipping_address = request.POST.get("shipping_address", "").strip()
        quantity_raw = request.POST.get("quantity", "1").strip()
        quantity = max(1, int(quantity_raw)) if quantity_raw.isdigit() else 1

        if not (customer_name and customer_phone):
            if request.headers.get("X-Requested-With") == "XMLHttpRequest" or request.content_type == "application/json":
                return JsonResponse({"status": "error", "message": "لطفاً نام و شماره تماس خود را وارد نمایید."}, status=400)
            messages.error(request, "لطفاً نام و شماره تماس خود را وارد نمایید.")
        else:
            unit_price = int(product.price) if product.price else 0
            total_price = unit_price * quantity
            tracking_code = f"ORD-{secrets.randbelow(900000) + 100000}"

            order = ProductOrder.objects.create(
                product=product,
                lead=lead_obj,
                customer_name=customer_name,
                customer_phone=customer_phone,
                shipping_address=shipping_address,
                quantity=quantity,
                unit_price=unit_price,
                total_price=total_price,
                status="PAID",
                tracking_code=tracking_code
            )

            # Record in daily metric
            metric.orders_count += 1
            metric.sales_amount += total_price
            metric.save(update_fields=["orders_count", "sales_amount"])

            if lead_obj:
                lead_obj.status = "CONVERTED"
                lead_obj.save(update_fields=["status", "updated_at"])

            if request.headers.get("X-Requested-With") == "XMLHttpRequest" or request.content_type == "application/json":
                return JsonResponse({
                    "status": "success",
                    "tracking_code": tracking_code,
                    "customer_name": customer_name,
                    "quantity": quantity,
                    "total_price_formatted": f"{total_price:,}".replace(",", "،"),
                    "message": "سفارش شما با موفقیت ثبت شد."
                })

            return render(request, "products/order_success.html", {
                "product": product,
                "order": order,
            })

    images = list(product.images.all())
    main_image = product.main_image
    other_images = [img for img in images if img != main_image]

    # Calculate discount / badge
    discount_percent = 15 if (product.price and product.price > 100000) else 0

    return render(request, "products/public_product_card.html", {
        "product": product,
        "business": product.business,
        "main_image": main_image,
        "images": images,
        "other_images": other_images,
        "discount_percent": discount_percent,
        "lead_id": ref_lead_id or "",
    })


