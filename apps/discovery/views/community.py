from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.shortcuts import get_object_or_404, redirect, render

from apps.discovery.models import MonitoredCommunity
from apps.discovery.sources import GLOBAL, PRIVATE, TG_INVITE_RE, TG_USERNAME_RE, max_private_sources, normalize_link


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
