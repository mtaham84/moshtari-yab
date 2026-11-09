"""Instagram live activity log API used by the dashboard's live monitor tab."""
import logging

from django.http import JsonResponse
from django.views.decorators.http import require_POST



from apps.billing.manage.access import manage_access_required

logger = logging.getLogger("management")


@manage_access_required
def instagram_live_logs_api(request):
    """
    AJAX endpoint returning live Instagram activity logs and worker heartbeats:
    - Is the scraper running right now? What query / post?
    - Is it watching a video / reel / story right now?
    - Recent log lines with timestamps and status indicators.
    """
    from apps.discovery.live_logger import get_live_monitoring_snapshot

    try:
        limit = min(int(request.GET.get("limit", 60)), 150)
    except (ValueError, TypeError):
        limit = 60

    try:
        since_id = int(request.GET.get("since_id", 0)) or None
    except (ValueError, TypeError):
        since_id = None

    data = get_live_monitoring_snapshot(limit=limit, since_id=since_id)
    return JsonResponse(data)


@manage_access_required
@require_POST
def instagram_live_test_pulse_api(request):
    """
    Trigger a simulated test pulse (e.g. video watch & comment inspect)
    so operators can verify live streaming immediately.
    """
    from apps.discovery.live_logger import simulate_test_pulse

    username = request.POST.get("username", "").strip().lstrip("@")
    res = simulate_test_pulse(account_username=username)
    return JsonResponse(res)


@manage_access_required
@require_POST
def instagram_live_clear_logs_api(request):
    """Clear activity logs stream on demand."""
    from apps.discovery.models import InstagramLiveActivity

    try:
        InstagramLiveActivity.objects.all().delete()
        return JsonResponse({"success": True, "message": "کنسول لاگ‌ها با موفقیت پاک‌سازی شد."})
    except Exception as exc:
        logger.warning("instagram_live_clear_logs_api failed", exc_info=True)
        return JsonResponse({"success": False, "error": str(exc)}, status=500)
