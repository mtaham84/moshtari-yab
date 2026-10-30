"""Public storefront («غرفه») and its editor."""
from __future__ import annotations

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db.models import Prefetch
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.utils.text import slugify

from apps.businesses.models import Business
from apps.products.models import Category, Product, ProductImage


def shop_view(request, slug):
    business = Business.objects.filter(slug=slug).first()
    if business is None or not business.onboarding_done:
        raise Http404("غرفه پیدا نشد.")
    products = (Product.objects.filter(business=business, status="ACTIVE")
                .select_related("category")
                .prefetch_related(Prefetch("images", queryset=ProductImage.objects.order_by("-is_main", "order")))
                .order_by("-created_at"))
    cat_id = request.GET.get("c")
    roots = list(Category.objects.filter(business=business, is_active=True, parent__isnull=True).order_by("name"))
    active = None
    if cat_id and cat_id.isdigit():
        active = Category.objects.filter(business=business, pk=int(cat_id)).first()
        if active:
            ids, stack = [], [active]
            while stack:
                c = stack.pop()
                ids.append(c.pk)
                stack.extend(c.children.all())
            products = products.filter(category_id__in=ids)
    items = []
    for p in products[:120]:
        imgs = list(p.images.all())
        items.append({"p": p, "image": imgs[0].image.url if imgs and imgs[0].image else ""})
    return render(request, "accounts/shop.html", {
        "shop": business, "items": items, "roots": roots, "active": active,
        "types": business.business_types.all(), "count": Product.objects.filter(business=business, status="ACTIVE").count(),
        "is_owner": request.user.is_authenticated and getattr(request.user, "business", None) == business,
    })


@login_required
def shop_edit_view(request):
    business = getattr(request.user, "business", None)
    if business is None:
        return redirect("accounts:onboarding")
    if request.method == "POST":
        business.name = request.POST.get("name", business.name).strip()[:255] or business.name
        business.tagline = request.POST.get("tagline", "").strip()[:140]
        business.location = request.POST.get("location", "").strip()[:150]
        business.description = request.POST.get("description", "").strip()[:2000]
        business.search_notes = request.POST.get("search_notes", "").strip()[:1000]
        slug = slugify(request.POST.get("slug", "").strip(), allow_unicode=True)[:60]
        if slug and slug != business.slug:
            if Business.objects.filter(slug=slug).exclude(pk=business.pk).exists():
                messages.error(request, "این آدرس قبلاً استفاده شده است.")
                return render(request, "accounts/shop_edit.html", {"business": business})
            business.slug = slug
        if request.FILES.get("logo"):
            business.logo = request.FILES["logo"]
        if request.FILES.get("cover"):
            business.cover = request.FILES["cover"]
        if request.POST.get("remove_logo"):
            business.logo = None
        if request.POST.get("remove_cover"):
            business.cover = None
        business.save()
        messages.success(request, "غرفه ذخیره شد.")
        return redirect("accounts:shop_edit")
    return render(request, "accounts/shop_edit.html", {"business": business})
