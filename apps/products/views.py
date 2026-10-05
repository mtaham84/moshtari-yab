import json
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from apps.businesses.models import Business
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

    categories = Category.objects.filter(is_active=True).select_related("parent")

    if request.method == "POST":
        name = request.POST.get("name", "").strip()
        product_type = request.POST.get("product_type", "PHYSICAL").strip()
        category_id = request.POST.get("category_id")
        description = request.POST.get("description", "").strip()
        price_val = request.POST.get("price", "").strip().replace(",", "").replace("،", "")
        url = request.POST.get("url", "").strip()
        target_customer = request.POST.get("target_customer", "").strip()
        status = request.POST.get("status", "ACTIVE").strip()

        # Parse dynamic attributes from form
        attributes = {}
        for key, val in request.POST.items():
            if key.startswith("attr_") and val.strip():
                attr_name = key[5:]
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
    categories = Category.objects.filter(is_active=True).select_related("parent")

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

        attributes = {}
        for key, val in request.POST.items():
            if key.startswith("attr_") and val.strip():
                attr_name = key[5:]
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
