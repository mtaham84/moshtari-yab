"""Proxy configs and the Telegram channels that supply them, on the /manage/ dashboard."""
import logging
from typing import Any

from django.http import JsonResponse
from django.utils import timezone as dj_timezone
from django.views.decorators.http import require_POST


from apps.discovery.models import (
    ProxyConfig
)
from apps.discovery.proxies import health

from apps.billing.manage.access import manage_access_required

logger = logging.getLogger("management")


def _ping_and_evaluate_proxy_config(proxy: ProxyConfig, timeout_sec: float = 3.5) -> dict[str, Any]:
    """Live test of one ProxyConfig (health.measure_config); saves its ping_ms, status and last_checked_at."""
    is_ok, ping_ms, err_msg = health.measure_config(proxy, timeout_sec=min(2.5, timeout_sec))
    if is_ok:
        proxy.ping_ms = ping_ms
        proxy.status = "ACTIVE"
    else:
        proxy.ping_ms = -1.0
        proxy.status = "DEAD"

    proxy.last_checked_at = dj_timezone.now()
    try:
        proxy.save(update_fields=["ping_ms", "status", "last_checked_at"])
    except Exception as exc:
        logger.warning("Could not save proxy #%s test result: %s", proxy.id, exc, exc_info=True)

    return {
        "id": proxy.id,
        "tag": proxy.tag or proxy.server or f"پروکسی #{proxy.id}",
        "protocol": proxy.protocol,
        "server": f"{proxy.server}:{proxy.port}" if proxy.server else "—",
        "local_port": proxy.local_port,
        "ping_ms": proxy.ping_ms,
        "ping_display": f"{proxy.ping_ms:.0f} ms" if proxy.ping_ms >= 0 else "قطع",
        "status": proxy.status,
        "status_label": "فعال" if proxy.status == "ACTIVE" else "قطع",
        "last_checked_fa": "چند لحظه پیش",
        "error": err_msg,
    }


@manage_access_required
@require_POST
def manage_proxy_test(request, pk=None):
    """
    Live real-time test for proxy configs.
    Supports testing a single proxy (via pk or POST proxy_id) or all proxies concurrently.
    """
    from concurrent.futures import ThreadPoolExecutor

    proxy_id = pk or request.POST.get("proxy_id")
    if proxy_id:
        try:
            proxy = ProxyConfig.objects.get(pk=int(proxy_id))
        except (ProxyConfig.DoesNotExist, ValueError):
            return JsonResponse({"success": False, "message": "پروکسی مورد نظر یافت نشد."}, status=404)

        res = _ping_and_evaluate_proxy_config(proxy)
        msg = f"پروکسی {res['tag']} با موفقیت تست شد: {res['status_label']} ({res['ping_display']})"
        return JsonResponse({"success": True, "proxy": res, "message": msg})

    # Test ALL proxies concurrently
    proxies = list(ProxyConfig.objects.all().order_by("local_port"))
    if not proxies:
        return JsonResponse({
            "success": True,
            "total": 0,
            "active_count": 0,
            "dead_count": 0,
            "results": [],
            "message": "هیچ کانفیگ پروکسی برای تست در پایگاه داده ثبت نشده است."
        })

    max_workers = min(12, max(2, len(proxies)))
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        results = list(executor.map(_ping_and_evaluate_proxy_config, proxies))

    active_count = sum(1 for r in results if r["status"] == "ACTIVE")
    dead_count = len(results) - active_count

    return JsonResponse({
        "success": True,
        "total": len(results),
        "active_count": active_count,
        "dead_count": dead_count,
        "results": results,
        "message": f"تست زنده {len(results)} پروکسی به پایان رسید: {active_count} فعال، {dead_count} قطع."
    })


@manage_access_required
@require_POST
def manage_channel_proxy_sync(request):
    """
    Triggers on-demand sync from a single channel or all active channels.
    """
    from apps.discovery.channel_config_automation import sync_channel_configs, sync_all_active_channels

    channel = request.POST.get("channel", "").strip()
    if not channel or channel.lower() == "all":
        res = sync_all_active_channels(max_posts=20, max_add_per_channel=10)
        return JsonResponse(res)

    force_all = request.POST.get("force_all") == "1"
    try:
        res = sync_channel_configs(channel_username=channel, force_all=force_all, max_add_per_run=10)
        return JsonResponse(res)
    except Exception as exc:
        logger.warning("manage_channel_proxy_sync failed", exc_info=True)
        return JsonResponse({"success": False, "message": f"خطا در بررسی کانال: {exc}"}, status=400)


@manage_access_required
@require_POST
def manage_proxy_channel_add(request):
    """
    Adds a new Telegram channel address to the proxy monitoring pool.
    """
    from apps.discovery.channel_config_automation import add_monitored_channel, normalize_channel_username

    raw_channel = request.POST.get("channel_username", "").strip()
    title = request.POST.get("title", "").strip()

    if not raw_channel:
        return JsonResponse({"success": False, "message": "لطفاً آدرس یا نام کاربری کانال تلگرام را وارد کنید."}, status=400)

    try:
        clean_user = normalize_channel_username(raw_channel)
        channel_data = add_monitored_channel(clean_user, title=title)
        # JSON endpoint: the page shows this message as a toast; a flash
        # message here would show it a second time on the next page load.
        return JsonResponse({
            "success": True,
            "message": f"کانال @{clean_user} با موفقیت به لیست اضافه شد.",
            "channel": channel_data,
        })
    except ValueError as val_err:
        return JsonResponse({"success": False, "message": str(val_err)}, status=400)
    except Exception as exc:
        logger.exception("Error adding proxy channel: %s", exc)
        return JsonResponse({"success": False, "message": f"خطا در ثبت کانال: {exc}"}, status=500)


@manage_access_required
@require_POST
def manage_proxy_channel_delete(request, pk):
    """
    Removes a Telegram channel from the proxy monitoring pool.
    """
    from apps.discovery.channel_config_automation import delete_monitored_channel

    try:
        delete_monitored_channel(pk)
        return JsonResponse({
            "success": True,
            "message": "کانال با موفقیت از لیست پایش حذف شد.",
            "id": pk,
        })
    except Exception as exc:
        logger.warning("manage_proxy_channel_delete failed", exc_info=True)
        return JsonResponse({"success": False, "message": f"خطا در حذف کانال: {exc}"}, status=500)


@manage_access_required
@require_POST
def manage_proxy_channel_toggle(request, pk):
    """
    Toggles the active state of a monitored Telegram channel.
    """
    from apps.discovery.channel_config_automation import toggle_monitored_channel

    try:
        ch = toggle_monitored_channel(pk)
        state_str = "فعال" if ch.get("is_active") else "متوقف"
        return JsonResponse({
            "success": True,
            "message": f"وضعیت کانال به «{state_str}» تغییر یافت.",
            "is_active": ch.get("is_active"),
            "id": pk,
        })
    except Exception as exc:
        logger.warning("manage_proxy_channel_toggle failed", exc_info=True)
        return JsonResponse({"success": False, "message": f"خطا در تغییر وضعیت: {exc}"}, status=500)


@manage_access_required
@require_POST
def manage_proxy_channel_sync(request, pk):
    """
    Synchronizes newly published configs from a specific monitored channel.
    """
    from apps.discovery.channel_config_automation import sync_channel_configs, get_monitored_channels

    channels = get_monitored_channels()
    target_channel = next((c for c in channels if c.get("id") == int(pk)), None)
    if not target_channel:
        return JsonResponse({"success": False, "message": "کانال مورد نظر یافت نشد."}, status=404)

    res = sync_channel_configs(target_channel["channel_username"], force_all=False, max_add_per_run=10)
    return JsonResponse(res)


@manage_access_required
@require_POST
def manage_proxy_channels_sync_all(request):
    """
    Synchronizes newly published configs across all active monitored channels.
    """
    from apps.discovery.channel_config_automation import sync_all_active_channels

    res = sync_all_active_channels(max_posts=20, max_add_per_channel=10)
    return JsonResponse(res)
