"""Authenticated, CSRF-protected manual Telegram sending."""
from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.http import Http404, JsonResponse
from django.shortcuts import get_object_or_404
from django.views.decorators.http import require_http_methods

from .models import Opportunity, TelegramOutboundMessage
from .telegram_outbox import OutboxError, enqueue, serialize, validate_opportunity


@login_required
@require_http_methods(["GET", "POST"])
def telegram_message_view(request, pk):
    business = getattr(request.user, "business", None)
    if business is None:
        raise Http404
    opportunity = get_object_or_404(Opportunity.objects.select_related("customer"), pk=pk, business=business)
    if request.method == "GET":
        job = TelegramOutboundMessage.objects.filter(opportunity=opportunity).first()
        if job is None and not settings.TELEGRAM_OUTREACH_ENABLED:
            return JsonResponse({
                "status": "success", "state": "DISABLED",
                "label": "ارسال غیرفعال", "message": "ارسال تلگرام روی سرور غیرفعال است.",
            })
        if job is None:
            try:
                validate_opportunity(opportunity)
            except OutboxError as exc:
                return JsonResponse({
                    "status": "success", "state": "UNAVAILABLE",
                    "label": "ارسال ممکن نیست", "message": str(exc),
                })
        return JsonResponse({"status": "success", **serialize(job)})
    if not settings.TELEGRAM_OUTREACH_ENABLED:
        return JsonResponse({"status": "error", "message": "ارسال تلگرام روی سرور غیرفعال است."}, status=403)
    try:
        job, created = enqueue(pk, business, request.user, request.POST.get("text", ""))
    except Opportunity.DoesNotExist:
        raise Http404
    except OutboxError as exc:
        return JsonResponse({"status": "error", "message": str(exc)}, status=400)
    return JsonResponse({"status": "success", **serialize(job)}, status=202 if created else 200)