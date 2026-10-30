"""Guided setup: business type → AI category tree (cached) → own tree editing → notes → first products."""
from __future__ import annotations

import json

from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.shortcuts import redirect, render
from django.utils.text import slugify
from django.views.decorators.http import require_POST

from apps.businesses.models import Business, BusinessType
from apps.products import taxonomy
from apps.products.models import Category

MAX_TYPES = 3
NOTES_EXAMPLES = [
    "تولیدی جوراب هستم و به‌دنبال مغازه‌دارها و فروشگاه‌هایی می‌گردم که عمده جوراب می‌خرند. فروش من حداقل ۱۰۰ جفت است.",
    "دندانپزشکی در تهران هستم؛ بیشتر مشتری‌هایی را می‌خواهم که دنبال ایمپلنت و ارتودنسی هستند و ساکن غرب تهران‌اند.",
    "آموزشگاه برنامه‌نویسی آنلاین هستم و دانشجوها و افراد تازه‌کار را برای دوره‌های پروژه‌محور پایتون می‌خواهم.",
]


def _business(request) -> Business:
    business = getattr(request.user, "business", None)
    if not business:
        business = Business.objects.create(user=request.user, name=f"کسب‌وکار {request.user.first_name}".strip(),
                                           business_type="PHYSICAL", business_domain="")
    return business


def _json(request) -> dict:
    try:
        return json.loads(request.body.decode("utf-8")) if request.body else {}
    except Exception:
        return {}


@login_required
def onboarding_view(request):
    business = _business(request)
    selected = list(business.business_types.values_list("key", flat=True))
    return render(request, "accounts/onboarding.html", {
        "business": business,
        "types": BusinessType.objects.all(),
        "selected_keys": selected,
        "mode": "manage" if business.onboarding_done else "setup",
        "notes_examples": NOTES_EXAMPLES,
        "max_types": MAX_TYPES,
    })


@login_required
@require_POST
def api_select_types(request):
    business = _business(request)
    raw_keys = _json(request).get("keys", [])
    if not isinstance(raw_keys, list) or len(raw_keys) > MAX_TYPES:
        return JsonResponse({"ok": False, "message": f"حداکثر {MAX_TYPES} نوع کسب‌وکار انتخاب کنید."}, status=400)
    keys = list(dict.fromkeys(k for k in raw_keys if isinstance(k, str)))
    types = list(BusinessType.objects.filter(key__in=keys))
    if not types or len(types) != len(keys):
        return JsonResponse({"ok": False, "message": "حداقل یک نوع کسب‌وکار را انتخاب کنید."}, status=400)
    business.business_types.set(types)
    business.business_domain = "، ".join(t.name for t in types)[:150]
    business.business_type = "SERVICE" if all(t.kind == "SERVICE" for t in types) else (
        "HYBRID" if any(t.kind == "SERVICE" for t in types) else "PHYSICAL")
    business.save(update_fields=["business_domain", "business_type"])
    return JsonResponse({"ok": True, "types": [{"key": t.key, "name": t.name} for t in types]})


@login_required
def api_type_tree(request, key):
    """System tree of one type (built by the AI the first time anyone needs it, then cached)."""
    business = _business(request)
    if not business.business_types.filter(key=key).exists():
        return JsonResponse({"ok": False, "message": "ابتدا این نوع کسب‌وکار را انتخاب کنید."}, status=403)
    btype = BusinessType.objects.filter(key=key).first()
    if not btype:
        return JsonResponse({"ok": False, "message": "نوع کسب‌وکار پیدا نشد."}, status=404)
    try:
        btype = taxonomy.ensure_type_tree(btype)
    except taxonomy.TaxonomyError as exc:
        return JsonResponse({"ok": False, "message": str(exc)}, status=503)
    adopted = set(Category.objects.filter(business=business, origin__isnull=False).values_list("origin_id", flat=True))
    return JsonResponse({"ok": True, "type": {"key": btype.key, "name": btype.name, "root_id": btype.root_category_id},
                         "tree": taxonomy.system_tree(btype), "adopted": sorted(adopted)})


@login_required
@require_POST
def api_adopt(request):
    business = _business(request)
    ids = [int(i) for i in _json(request).get("node_ids", []) if str(i).isdigit()]
    roots = [t.root_category_id for t in business.business_types.exclude(root_category=None)]
    allowed = set()
    for root_id in roots:
        root = Category.objects.filter(pk=root_id).first()
        if not root:
            continue
        nodes, stack = [root], [root]
        while stack:
            parent = stack.pop()
            children = list(parent.children.filter(business__isnull=True, is_active=True))
            nodes.extend(children)
            stack.extend(children)
        allowed.update(node.pk for node in nodes)
    if not ids or not set(ids).issubset(allowed):
        return JsonResponse({"ok": False, "message": "فقط دسته‌های نوع کسب‌وکار انتخاب‌شده قابل افزودن هستند."}, status=400)
    created = taxonomy.adopt_nodes(business, ids)
    return JsonResponse({"ok": True, "created": created, "tree": taxonomy.own_tree(business)})


@login_required
def api_own_tree(request):
    return JsonResponse({"ok": True, "tree": taxonomy.own_tree(_business(request))})


@login_required
@require_POST
def api_node(request, action):
    business = _business(request)
    data = _json(request)
    try:
        if action == "add":
            node = taxonomy.add_node(business, data.get("parent_id") or None, data.get("name", ""))
        elif action == "rename":
            node = taxonomy.rename_node(business, int(data.get("id", 0)), data.get("name", ""))
        elif action == "delete":
            taxonomy.delete_node(business, int(data.get("id", 0)))
            node = None
        else:
            return JsonResponse({"ok": False, "message": "عملیات نامعتبر است."}, status=400)
    except (taxonomy.TaxonomyError, ValueError) as exc:
        return JsonResponse({"ok": False, "message": str(exc)}, status=400)
    return JsonResponse({"ok": True, "id": getattr(node, "pk", None), "tree": taxonomy.own_tree(business)})


@login_required
@require_POST
def api_finish(request):
    business = _business(request)
    data = _json(request)
    notes = str(data.get("search_notes", "")).strip()[:1000]
    tagline = str(data.get("tagline", "")).strip()[:140]
    if not business.business_types.exists():
        return JsonResponse({"ok": False, "message": "نوع کسب‌وکار انتخاب نشده است."}, status=400)
    if not Category.objects.filter(business=business, is_active=True).exists():
        return JsonResponse({"ok": False, "message": "حداقل یک دسته‌بندی برای کسب‌وکار خود انتخاب کنید."}, status=400)
    business.search_notes = notes
    if tagline:
        business.tagline = tagline
    if not business.slug:
        base = slugify(business.name, allow_unicode=True)[:40] or f"shop-{business.pk}"
        slug, n = base, 1
        while Business.objects.filter(slug=slug).exclude(pk=business.pk).exists():
            n += 1
            slug = f"{base}-{n}"
        business.slug = slug
    business.onboarding_done = True
    business.save()
    return JsonResponse({"ok": True, "next": "/products/add/"})
