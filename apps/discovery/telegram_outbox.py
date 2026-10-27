"""Seller-approved Telegram DM outbox. No network calls in web requests."""
from datetime import timedelta
import re

from django.db import close_old_connections, transaction
from django.utils import timezone

from .models import Opportunity, TelegramOutboundMessage


class OutboxError(ValueError):
    """Safe, localized validation error for the seller."""


def telegram_user_id(customer):
    match = re.fullmatch(r"(?:telegram_)?([1-9][0-9]{0,17})", customer.external_user_id or "")
    if customer.source_platform != "telegram" or not match:
        raise OutboxError("شناسهٔ معتبر کاربر تلگرام برای این فرصت موجود نیست.")
    return int(match[1])


def validate_opportunity(opportunity):
    if opportunity.source_platform != "telegram":
        raise OutboxError("ارسال مستقیم فقط برای فرصت‌های تلگرام فعال است.")
    if opportunity.status in {"EXPIRED", "RESOLVED", "REJECTED", "CONVERTED"}:
        raise OutboxError("این فرصت بسته یا منقضی شده است.")
    if opportunity.expires_at and opportunity.expires_at <= timezone.now():
        raise OutboxError("این فرصت منقضی شده است.")
    if opportunity.trace_metadata.get("retention_scrubbed"):
        raise OutboxError("داده‌های این فرصت پاک‌سازی شده‌اند.")
    if opportunity.customer.business_id != opportunity.business_id:
        raise OutboxError("مخاطب این فرصت متعلق به کسب‌وکار شما نیست.")
    return telegram_user_id(opportunity.customer)


@transaction.atomic
def enqueue(opportunity_id, business, user, body):
    # Serialize concurrent clicks; one outbox record per opportunity is also DB-enforced.
    opportunity = (Opportunity.objects.select_for_update()
                   .select_related("customer").get(pk=opportunity_id, business=business))
    existing = TelegramOutboundMessage.objects.filter(opportunity=opportunity).first()
    if existing:
        return existing, False
    recipient = validate_opportunity(opportunity)
    body = body.strip()
    if not body or len(body.encode("utf-16-le")) // 2 > 4096:
        raise OutboxError("متن پیام باید بین ۱ تا ۴۰۹۶ واحد متنی تلگرام باشد.")
    return TelegramOutboundMessage.objects.create(
        opportunity=opportunity, requested_by=user, body=body,
        recipient_user_id=recipient,
        recipient_username=opportunity.customer.source_username.lstrip("@"),
    ), True


def claim_next():
    now = timezone.now()
    # A crash after a network send has an ambiguous outcome. Never blindly resend it.
    TelegramOutboundMessage.objects.filter(
        status="SENDING", updated_at__lt=now - timedelta(minutes=10),
    ).update(status="UNKNOWN", error_code="INTERRUPTED", updated_at=now)
    with transaction.atomic():
        job = (TelegramOutboundMessage.objects.select_for_update(skip_locked=True)
               .filter(status="PENDING", next_attempt_at__lte=now).order_by("created_at").first())
        if job is None:
            return None
        opportunity = Opportunity.objects.select_related("customer").get(pk=job.opportunity_id)
        try:
            if validate_opportunity(opportunity) != job.recipient_user_id:
                raise OutboxError("مخاطب تغییر کرده است.")
        except OutboxError:
            job.status, job.error_code = "FAILED", "STALE_OPPORTUNITY"
            job.save(update_fields=["status", "error_code", "updated_at"])
            return None
        job.status = "SENDING"
        job.save(update_fields=["status", "updated_at"])
        return {
            "id": job.id, "body": job.body, "recipient_user_id": job.recipient_user_id,
            "recipient_username": job.recipient_username, "random_id": job.random_id,
        }


def finish(job_id, status, error_code="", message_id=None, wait=0):
    now = timezone.now()
    with transaction.atomic():
        job = TelegramOutboundMessage.objects.select_for_update().get(pk=job_id)
        job.status, job.error_code = status, error_code
        job.telegram_message_id = message_id
        job.sent_at = now if status == "SENT" else None
        job.next_attempt_at = now + timedelta(seconds=wait)
        job.save()
        if status == "SENT":
            Opportunity.objects.filter(
                pk=job.opportunity_id, status__in=["NEW", "REVIEWED"],
            ).update(status="CONTACTED", updated_at=now)


def worker_db_call(function, *args, **kwargs):
    """Manage ORM connection lifetime only at the background worker boundary."""
    close_old_connections()
    try:
        return function(*args, **kwargs)
    finally:
        close_old_connections()


ERROR_MESSAGES = {
    "PRIVACY": "به‌دلیل تنظیمات حریم خصوصی یا مسدودبودن، ارسال ممکن نیست.",
    "PEER_UNAVAILABLE": "حساب متصل به شناسهٔ این مخاطب دسترسی ندارد؛ ارسال انجام نشد.",
    "RATE_LIMIT": "تلگرام زمان انتظار تعیین کرده؛ پیام در صف باقی مانده است.",
    "ACCOUNT_LIMITED": "تلگرام حساب فرستنده را محدود کرده؛ ارسال خودکار تکرار نمی‌شود.",
    "REJECTED": "تلگرام درخواست ارسال را رد کرد؛ ارسال خودکار تکرار نمی‌شود.",
    "STALE_OPPORTUNITY": "فرصت بسته، پاک‌سازی یا مخاطب آن تغییر کرده؛ ارسال لغو شد.",
    "INTERRUPTED": "نتیجهٔ ارسال نامشخص است؛ پیش از هر اقدام، تلگرام را بررسی کنید.",
    "NETWORK_UNKNOWN": "نتیجهٔ ارسال نامشخص است؛ برای جلوگیری از تکرار، تلاش مجدد خودکار متوقف شد.",
}


def serialize(job):
    if job is None:
        return {"state": "READY", "label": "ارسال پیام", "message": ""}
    return {
        "state": job.status, "label": job.get_status_display(),
        "message": ERROR_MESSAGES.get(job.error_code, ""),
        "sent_at": job.sent_at.isoformat() if job.sent_at else None,
    }