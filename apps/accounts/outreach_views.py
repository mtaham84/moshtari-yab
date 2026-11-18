"""The seller/operator outreach page (/accounts/outreach/): senders, pitches, leads, replies, follow-ups, export."""
import logging
from django.contrib import messages
from django.contrib.auth import get_user_model
from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.shortcuts import redirect, render, get_object_or_404
from django.urls import reverse
from django.views.decorators.http import require_POST
from apps.core.http import wants_json
from apps.core.permissions import is_operator
from apps.discovery.constants import DM_DELIVERED_STATUSES
from apps.discovery.exports import safe_csv_row
from apps.discovery.models import InstagramEngineAccount
from apps.discovery import tenants

logger = logging.getLogger(__name__)

User = get_user_model()


def _outreach_tenant_keys(business) -> list[str]:
    """Lead/sender tenant ids that belong to a business (tenant_4 also owns legacy 'sarv' rows)."""
    from apps.discovery.tenants import tenant_keys_for_business

    return tenant_keys_for_business(business)


def _captured_phone_for_lead(lead, thread, customer_phones: dict | None = None, business=None):
    """Phone saved on the lead's Customer, else the first Iranian phone number the lead sent us in DM."""
    from apps.discovery.models import Customer
    from apps.discovery.outreach.messages import extract_iranian_phone_number

    if lead.ig_user_pk:
        if customer_phones is not None:
            phone = customer_phones.get(str(lead.ig_user_pk))
        else:
            cust_qs = Customer.objects.filter(external_user_id=str(lead.ig_user_pk)).exclude(phone_number__isnull=True)
            if business is not None:
                cust_qs = cust_qs.filter(business=business)
            phone = cust_qs.exclude(phone_number="").values_list("phone_number", flat=True).first()
        if phone:
            return phone
    for m in thread or []:
        if m.get("is_incoming") or m.get("direction") == "in":
            extracted = extract_iranian_phone_number(m.get("text"))
            if extracted:
                return extracted
    return None


def _operator_outreach_business(biz_param, own_business, all_businesses):
    """Business shown to an operator on the outreach page (TEN-03: no hardcoded business ids).

    Explicit ?biz=<id> wins, then the operator's own business if it runs outreach, then the first business that
    runs outreach (OUTREACH_TENANTS / ACTIVE SENDER), then the operator's own business, then the first business."""
    from apps.discovery.tenants import outreach_tenants, tenant_business_id

    by_id = {b.id: b for b in all_businesses}
    if biz_param and str(biz_param).isdigit() and int(biz_param) in by_id:
        return by_id[int(biz_param)]
    outreach_ids = [bid for bid in (tenant_business_id(t) for t in outreach_tenants()) if bid in by_id]
    if own_business and own_business.id in outreach_ids:
        return own_business
    if outreach_ids:
        return by_id[outreach_ids[0]]
    return own_business or (all_businesses[0] if all_businesses else None)


@login_required
def outreach_view(request):
    """Business owner's dedicated Instagram Outreach & Sender Management page."""
    from apps.businesses.models import Business
    from apps.discovery.models import InstagramEngineAccount, InstagramEngineLead

    business = getattr(request.user, "business", None)

    # Operators pick a business with ?biz=<id>; otherwise their own, else the first one running outreach.
    all_businesses = []
    if is_operator(request.user):
        all_businesses = list(Business.objects.all().order_by("id"))
        business = _operator_outreach_business(request.GET.get("biz"), business, all_businesses)

    if not business:
        messages.error(request, "ابتدا اطلاعات کسب‌وکار خود را تکمیل نمایید.")
        return redirect("accounts:dashboard")

    tenant_key = f"tenant_{business.id}"
    tenant_keys = _outreach_tenant_keys(business)

    from apps.discovery.outreach.limits import TIER_LIMITS
    from apps.discovery.outreach.replies import get_lead_conversation_thread
    from apps.discovery.models import Customer

    accounts = InstagramEngineAccount.objects.filter(tenant_id__in=tenant_keys, role="SENDER").order_by("-id")
    sent_dms = InstagramEngineLead.objects.filter(tenant_id__in=tenant_keys, status__in=DM_DELIVERED_STATUSES).order_by("-updated_at")[:50]
    total_dms = InstagramEngineLead.objects.filter(tenant_id__in=tenant_keys, status__in=DM_DELIVERED_STATUSES).count()
    pending_leads = InstagramEngineLead.objects.filter(tenant_id__in=tenant_keys, status="NEW").count()

    # Leads who replied to our direct outreach or have active conversation
    from django.db.models import Q
    conversation_leads = list(InstagramEngineLead.objects.filter(
        tenant_id__in=tenant_keys
    ).filter(
        Q(has_replied=True) | Q(status__in=[*DM_DELIVERED_STATUSES, "QUEUED"]) | Q(initial_dm_text__isnull=False)
    ).order_by("-reply_at", "-updated_at")[:60])

    # Leads who replied to our direct outreach
    replied_leads_qs = InstagramEngineLead.objects.filter(
        tenant_id__in=tenant_keys,
        has_replied=True
    ).order_by("-reply_at")[:50]
    replied_leads = list(replied_leads_qs)
    replied_count = InstagramEngineLead.objects.filter(
        tenant_id__in=tenant_keys,
        has_replied=True
    ).count()

    # Decorate leads with full direct conversation thread and captured phone number (one Customer query)
    decorated_leads = conversation_leads + [lead for lead in replied_leads if lead.pk not in {c.pk for c in conversation_leads}]
    lead_pks = {str(lead.ig_user_pk) for lead in decorated_leads if lead.ig_user_pk}
    customer_phones = dict(
        Customer.objects.filter(business=business, external_user_id__in=lead_pks)
        .exclude(phone_number__isnull=True).exclude(phone_number="")
        .values_list("external_user_id", "phone_number")
    ) if lead_pks else {}
    for lead in decorated_leads:
        lead.thread = get_lead_conversation_thread(lead)
        lead.captured_phone = _captured_phone_for_lead(lead, lead.thread, customer_phones)

    # One consultant name + pitch per customer (tenant), used by all of its senders (tenants.tenant_pitch:
    # its profile, else the legacy per-username pitch of data/sarv_outreach_config.json)
    from apps.discovery.tenants import tenant_pitch

    accounts = list(accounts)
    pitch = tenant_pitch(tenant_key, [acc.username for acc in accounts])
    account_list = [{"obj": acc, "consultant_name": pitch["consultant_name"] or acc.username,
                     "template": pitch["pitch_template"]} for acc in accounts]
    default_pitch = pitch["pitch_template"]
    default_consultant = pitch["consultant_name"] or (accounts[0].username if accounts else "")
    default_username = accounts[0].username if accounts else ""

    # Top-Scoring Prioritized Leads (Ranked by 35-point scoring: Public + Intent + Freshness)
    from django.db.models import Q
    from datetime import timedelta
    from django.utils import timezone as dj_tz
    from apps.discovery.outreach.messages import build_bulletproof_message
    from apps.discovery.instagram_scoring import update_lead_scoring

    cutoff_5_days = dj_tz.now() - timedelta(days=5)
    top_leads_qs = list(
        InstagramEngineLead.objects.filter(
            tenant_id__in=tenant_keys
        ).filter(
            Q(comment_created_at__gte=cutoff_5_days)
            | Q(comment_created_at__isnull=True, created_at__gte=cutoff_5_days)
        ).order_by("-outreach_score", "-comment_created_at", "-created_at")[:60]
    )

    # Attach pitch preview and ensure score details
    for lead_item in top_leads_qs:
        if not lead_item.outreach_score:
            update_lead_scoring(lead_item, commit=False)   # GET: score in memory; the scraper stores it
        lead_item.pitch_preview = build_bulletproof_message(
            lead=lead_item,
            consultant_name=default_consultant,
            business_name=business.name,
        )

    # Re-sort after updating any uncalculated leads
    top_leads_qs.sort(key=lambda l: (l.outreach_score, l.comment_created_at or l.created_at), reverse=True)

    high_priority_count = sum(1 for l in top_leads_qs if l.outreach_score >= 30)
    public_leads_count = sum(1 for l in top_leads_qs if not l.is_private)
    inquiry_leads_count = sum(1 for l in top_leads_qs if l.comment_intent_type == "QUESTION_INQUIRY")

    return render(request, "accounts/outreach.html", {
        "user": request.user,
        "business": business,
        "accounts": account_list,
        "sent_dms": sent_dms,
        "total_dms": total_dms,
        "pending_leads": pending_leads,
        "replied_leads": replied_leads,
        "conversation_leads": conversation_leads,
        "replied_count": replied_count,
        "top_leads": top_leads_qs,
        "high_priority_count": high_priority_count,
        "public_leads_count": public_leads_count,
        "inquiry_leads_count": inquiry_leads_count,
        "tier_limits": TIER_LIMITS,
        "default_pitch": default_pitch,
        "default_consultant": default_consultant,
        "default_username": default_username,
        "all_businesses": all_businesses,
    })


def _staff(user) -> bool:
    return is_operator(user)


def _outreach_redirect(request, business_id=None):
    url = reverse("accounts:outreach")
    return redirect(url + (f"?biz={business_id}" if business_id and _staff(request.user) else ""))


@login_required
@require_POST
def save_pitch_view(request):
    """Saves the customer's consultant name and cold-DM pitch: one per customer, used by all of its senders.
    A customer edits only its own; staff pick the customer (biz, or the tenant of a sender username)."""
    from apps.businesses.models import Business
    from apps.discovery.models import InstagramEngineAccount

    business = getattr(request.user, "business", None)
    if _staff(request.user):
        biz = (request.POST.get("biz") or "").strip()
        username = (request.POST.get("username") or "").strip().lstrip("@")
        if biz.isdigit():
            business = Business.objects.filter(pk=int(biz)).first() or business
        elif username:
            acc = InstagramEngineAccount.objects.filter(username__iexact=username).first()
            tid = tenants.canonical_tenant(acc.tenant_id) if acc else ""
            if tid.startswith("tenant_") and tid[7:].isdigit():
                business = Business.objects.filter(pk=int(tid[7:])).first() or business
    if not business:
        messages.error(request, "دسترسی غیرمجاز.")
        return redirect("accounts:dashboard")

    template = (request.POST.get("template") or "").strip()
    if not template:
        messages.error(request, "متن پیام نمی‌تواند خالی باشد.")
        return _outreach_redirect(request, business.id)
    consultant_name = (request.POST.get("consultant_name") or "").strip()
    tenants.save_tenant_profile(f"tenant_{business.id}", consultant_name=consultant_name,
                            consultant_role=f"مشاور و پشتیبانی {business.name}", pitch_template=template)
    who = f" (مشاور: {consultant_name})" if consultant_name else ""
    messages.success(request, f"پیام دایرکت «{business.name}»{who} ذخیره شد؛ همه‌ی اکانت‌های فرستنده‌ی این کسب‌وکار "
                              "از همین پیام استفاده می‌کنند.")
    return _outreach_redirect(request, business.id)


@login_required
@require_POST
def add_outreach_account_view(request):
    """Staff only: adds a sender for the business shown on the outreach page and logs it in; the login continues on
    the account page (/manage/instagram/accounts/<id>/settings/), where Instagram's code is typed if it asks."""
    if not _staff(request.user):
        messages.error(request, "افزودن و اتصال اکانت اینستاگرام فقط توسط مدیران و پرسنل سیستم مجاز است.")
        return redirect("accounts:outreach")
    from apps.businesses.models import Business
    from apps.discovery import ig_accounts as svc

    business = getattr(request.user, "business", None)
    biz = (request.POST.get("biz") or "").strip()
    if biz.isdigit():
        business = Business.objects.filter(pk=int(biz)).first() or business
    tenant_key = (request.POST.get("tenant_id") or "").strip() if request.user.is_superuser else ""
    tenant_key = tenant_key or (f"tenant_{business.id}" if business else "")
    if not tenant_key:
        messages.error(request, "کسب‌وکار این اکانت مشخص نیست.")
        return redirect("accounts:outreach")

    acc, res = svc.add_account(
        username=request.POST.get("username", ""), password=request.POST.get("password", ""),
        sessionid=request.POST.get("sessionid", ""), role="SENDER", tenant_id=tenant_key,
        account_tier=request.POST.get("account_tier") or "NEW", daily_limit=request.POST.get("daily_limit"),
        proxy="system", actor=request.user.get_username(), login=True)
    if acc is None:
        messages.error(request, res.message)
        return _outreach_redirect(request, business.id if business else None)
    tenants.save_tenant_profile(tenant_key, consultant_name=request.POST.get("consultant_name", ""),
                            consultant_role=f"مشاور و پشتیبانی {business.name}" if business else "",
                            pitch_template=request.POST.get("pitch_template", ""), only_empty=True)
    (messages.success if res.ok else messages.warning)(request, res.message)
    return redirect(reverse("manage:ig_account_detail", args=[acc.pk]) + "#login")


@login_required
@require_POST
def generate_pitches_ajax_view(request):
    """Generate 3 high-converting AI pitch options based on business info and consultant persona."""
    from django.http import JsonResponse
    from apps.discovery.outreach.messages import generate_ai_outreach_pitches

    business = getattr(request.user, "business", None)
    if not business:
        return JsonResponse({"success": False, "error": "کسب‌وکار یافت نشد."}, status=400)

    consultant_name = request.POST.get("consultant_name", "").strip() or "مشاور"
    tone_style = request.POST.get("tone_style", "friendly").strip()
    focus_area = request.POST.get("focus_area", "").strip()

    pitches = generate_ai_outreach_pitches(
        business_name=business.name,
        consultant_name=consultant_name,
        business_desc=getattr(business, "description", "") or "",
        tone_style=tone_style,
        focus_area=focus_area,
    )
    return JsonResponse({"success": True, "pitches": pitches})


def _reply_sync_message(res: dict) -> str:
    """The seller's text for a ``request_reply_sync`` result."""
    status = res.get("status")
    if status == "throttled":
        minutes = max(1, (int(res.get("wait_seconds") or 0) + 59) // 60)
        return (f"صندوق پیام‌ها به‌تازگی بررسی شده است؛ بررسی بعدی حدود {minutes} دقیقه دیگر "
                f"(ساعت {res.get('next_run_at')}) مجاز است.")
    if status == "outside_shift":
        prefix = "درخواست بررسی صندوق از قبل ثبت شده است" if res.get("already_queued") else "درخواست بررسی صندوق ثبت شد"
        reason = res.get("reason") or ""
        when = f"حدود ساعت {res['next_run_at']}" if res.get("next_run_at") else (reason or "شروع شیفت بعدی")
        return f"{prefix}؛ ربات خارج از ساعت کاری است و بررسی در شروع شیفت بعدی ({when} — {reason}) انجام می‌شود."
    if status == "already_queued":
        return "درخواست بررسی صندوق از قبل در صف ربات پیام‌رسان است و در چرخه جاری انجام می‌شود."
    return "درخواست بررسی صندوق پیام‌ها برای ربات پیام‌رسان ثبت شد و در چرخه جاری (با رعایت فاصله ایمنی) انجام می‌شود."


@login_required
@require_POST
def sync_replies_view(request):
    """Queue an inbox reply check for the outreach dispatcher (never calls Instagram from the web process)."""
    from apps.businesses.models import Business
    from apps.discovery.outreach.replies import request_reply_sync

    business = getattr(request.user, "business", None)
    if _staff(request.user):
        biz = (request.POST.get("biz") or "").strip()
        if biz.isdigit():
            business = Business.objects.filter(pk=int(biz)).first() or business
        elif not business:
            business = _operator_outreach_business(None, business, list(Business.objects.all().order_by("id")))
    if not business:
        if wants_json(request):
            return JsonResponse({"success": False, "error": "کسب‌وکار یافت نشد."}, status=400)
        return redirect("accounts:dashboard")

    res = request_reply_sync(tenants.tenant_key(business))
    msg = _reply_sync_message(res)
    status_code = 429 if res.get("status") == "throttled" else 200
    if wants_json(request):
        return JsonResponse({"success": res.get("status") != "throttled", **res, "message": msg}, status=status_code)

    if res.get("status") == "throttled":
        messages.warning(request, msg)
    elif res.get("status") == "outside_shift":
        messages.info(request, msg)
    else:
        messages.success(request, msg)

    if _staff(request.user) and request.POST.get("next") == "manage":
        return redirect("manage:dashboard")
    return _outreach_redirect(request, business.id)


@login_required
@require_POST
def send_followup_view(request):
    """Enqueue an operator direct message / follow-up in the safe dispatch queue.

    The DM is delivered by the outreach dispatcher (shift hours, sender limits, warm-up and
    human pacing) instead of logging in from the web process and sending immediately.
    """
    from django.http import JsonResponse
    from apps.discovery.models import InstagramEngineLead
    from apps.discovery.outreach.outbox import enqueue_direct_message

    business = getattr(request.user, "business", None)
    is_staff = is_operator(request.user)
    is_ajax = wants_json(request)

    def _fail(error, status=400):
        if is_ajax:
            return JsonResponse({"success": False, "error": error}, status=status)
        messages.error(request, error)
        return redirect("accounts:outreach")

    if not business and not is_staff:
        return redirect("accounts:dashboard")

    lead_id = request.POST.get("lead_id", "").strip()
    sender_username = request.POST.get("sender_username", "").strip().lstrip("@")
    message_text = request.POST.get("message_text", "").strip()

    if not lead_id.isdigit() or not message_text:
        return _fail("لطفاً متن پیام را وارد فرمایید.")

    if not is_staff:
        tenant_keys = _outreach_tenant_keys(business)
        if not InstagramEngineLead.objects.filter(pk=int(lead_id), tenant_id__in=tenant_keys).exists():
            return _fail("مشتری یافت نشد.", status=404)
        if sender_username and not InstagramEngineAccount.objects.filter(
            username__iexact=sender_username, role="SENDER", tenant_id__in=tenant_keys
        ).exists():
            return _fail("اکانت فرستنده متعلق به این کسب‌وکار نیست.", status=403)

    res = enqueue_direct_message(int(lead_id), message_text, sender_username or None)
    if is_ajax:
        return JsonResponse(res, status=200 if res.get("success") else 400)

    if res.get("success"):
        messages.success(request, res.get("message") or "پیام در صف ارسال امن قرار گرفت.")
    else:
        messages.error(request, f"خطا در ثبت پیام در صف: {res.get('error')}")
    return redirect("accounts:outreach")


@login_required
def lead_chat_history_view(request, lead_id):
    """Return JSON conversation history and lead details for chat room."""
    from django.http import JsonResponse
    from django.db.models import Q
    from apps.discovery.models import InstagramEngineLead
    from apps.core.jalali import format_jalali_datetime

    business = getattr(request.user, "business", None)
    if not business and not request.user.is_superuser:
        return JsonResponse({"success": False, "error": "دسترسی غیرمجاز."}, status=403)

    lead_qs = InstagramEngineLead.objects.filter(pk=lead_id)
    if not request.user.is_superuser:
        lead_qs = lead_qs.filter(tenant_id__in=_outreach_tenant_keys(business))

    lead = lead_qs.first()
    if not lead:
        return JsonResponse({"success": False, "error": "مشتری یافت نشد."}, status=404)

    comment_text = lead.comment_text or ""
    source_post_url = lead.source_post_url or ""
    source_page = lead.source_page_username or lead.source_page_title or ""
    post_snippet = lead.post_caption_snippet or ""
    comment_dt = lead.comment_created_at or lead.created_at
    comment_time_jalali = format_jalali_datetime(comment_dt) if comment_dt else None

    # Fallback lookup in Opportunity if source_post_url is missing
    if not source_post_url:
        try:
            from apps.discovery.models import Opportunity
            opp = Opportunity.objects.filter(
                business_id=business.id if business else None,
                source_platform="instagram"
            ).filter(
                Q(customer__handle__iexact=lead.username) | Q(source_raw_message__icontains=lead.username)
            ).first()
            if opp:
                source_post_url = opp.source_message_id if (opp.source_message_id and "instagram.com" in opp.source_message_id) else ""
                source_page = source_page or opp.source_query or ""
                if not comment_text and opp.source_raw_message:
                    comment_text = opp.source_raw_message
                if not comment_time_jalali and opp.source_message_timestamp:
                    comment_time_jalali = format_jalali_datetime(opp.source_message_timestamp)
        except Exception:
            logger.warning("Could not look up the opportunity of lead %s", lead.pk, exc_info=True)

    sender_account = lead.sender_account or lead.second_sender_username or ""
    second_sender = lead.second_sender_username or ""
    from apps.discovery.outreach.replies import get_lead_conversation_thread
    captured_phone = _captured_phone_for_lead(lead, get_lead_conversation_thread(lead), business=business)

    return JsonResponse({
        "success": True,
        "lead": {
            "id": lead.id,
            "username": lead.username,
            "profile_url": lead.profile_url or f"https://instagram.com/{lead.username}",
            "intent_score": round(float(lead.intent_score or 0), 2),
            "outreach_score": lead.outreach_score or 0,
            "status": lead.status,
            "has_replied": bool(lead.has_replied),
            "sender_account": sender_account,
            "second_sender_username": second_sender,
            "comment_text": comment_text,
            "comment_created_at": comment_time_jalali,
            "source_post_url": source_post_url,
            "source_page_username": source_page,
            "post_caption_snippet": post_snippet,
            "initial_dm_text": lead.initial_dm_text or "",
            "initial_dm_at": format_jalali_datetime(lead.initial_dm_at) if lead.initial_dm_at else None,
            "reply_text": lead.reply_text or "",
            "reply_at": format_jalali_datetime(lead.reply_at) if lead.reply_at else None,
            "second_message_text": lead.second_message_text or "",
            "second_message_at": format_jalali_datetime(lead.second_message_at) if lead.second_message_at else None,
            "conversation_history": lead.conversation_history or [],
            "captured_phone": captured_phone or "",
        }
    })


@login_required
@require_POST
def update_lead_phone_view(request):
    """Manually update or save a customer's phone number from the direct outreach panel."""
    from django.http import JsonResponse
    from apps.discovery.models import InstagramEngineLead, Customer
    from apps.discovery.outreach.messages import extract_iranian_phone_number

    business = getattr(request.user, "business", None)
    if not business:
        return JsonResponse({"success": False, "error": "دسترسی غیرمجاز."}, status=403)

    lead_id = request.POST.get("lead_id")
    raw_phone = request.POST.get("phone_number", "").strip()

    if not lead_id or not raw_phone:
        return JsonResponse({"success": False, "error": "شناسه مشتری یا شماره تماس نامعتبر است."}, status=400)

    lead_qs = InstagramEngineLead.objects.filter(pk=lead_id)
    if not is_operator(request.user):
        lead_qs = lead_qs.filter(tenant_id__in=_outreach_tenant_keys(business))
    lead = lead_qs.first()
    if not lead:
        return JsonResponse({"success": False, "error": "لید یافت نشد."}, status=404)

    norm_phone = extract_iranian_phone_number(raw_phone) or raw_phone

    if lead.ig_user_pk:
        cust, _ = Customer.objects.get_or_create(
            business=business,
            source_platform="instagram",
            external_user_id=str(lead.ig_user_pk),
            defaults={
                "name": lead.full_name or lead.username,
                "source_username": lead.username,
                "source_profile_url": lead.profile_url or f"https://www.instagram.com/{lead.username}/",
            }
        )
        cust.phone_number = norm_phone
        cust.save(update_fields=["phone_number", "updated_at"])

    return JsonResponse({"success": True, "phone_number": norm_phone})


@login_required
@require_POST
def edit_outreach_account_view(request, pk):
    """Staff only (customers do not edit Instagram accounts; they edit their pitch): same as the dashboard editor."""
    from apps.billing.manage.ig_legacy import ig_edit_sender
    from apps.discovery.models import InstagramEngineAccount

    if not _staff(request.user):
        return JsonResponse({"success": False, "message": "ویرایش اکانت‌های اینستاگرام فقط توسط پشتیبانی انجام می‌شود."},
                            status=403)
    account = get_object_or_404(InstagramEngineAccount.objects.select_related("proxy_config"), pk=pk)
    return JsonResponse(ig_edit_sender(account, request.POST, request.user.get_username()))


@login_required
@require_POST
def clear_outreach_cooldown_view(request, pk):
    """Staff only: ends a protective rest early (an account that needs a login/code stays as it is)."""
    from apps.discovery import ig_accounts as svc

    if not _staff(request.user):
        return JsonResponse({"success": False, "message": "این کار فقط توسط پشتیبانی انجام می‌شود."}, status=403)
    account = get_object_or_404(InstagramEngineAccount, pk=pk)
    res = svc.clear_cooldown(account, actor=request.user.get_username())
    return JsonResponse({"success": True, "message": res.message, "status": account.status})


@login_required
def outreach_leads_export_csv(request):
    """
    خروجی فایل اکسل (CSV با یونیکد UTF-8 BOM) از لیدهای اولویت‌دار اینستاگرام اختصاصی تننت فروشگاه.
    """
    import io
    import csv
    from django.http import HttpResponse
    from django.utils import timezone
    from datetime import timedelta
    from django.db.models import Q
    from apps.discovery.models import InstagramEngineLead
    from apps.discovery.instagram_scoring import update_lead_scoring
    from apps.core.jalali import format_jalali_date

    business = getattr(request.user, "business", None)
    if not business:
        return HttpResponse("حساب کسب‌وکار مرتبط یافت نشد.", status=404)

    is_admin = is_operator(request.user)
    if is_admin and request.GET.get("business_id", "").strip().lower() == "all":
        qs = InstagramEngineLead.objects.all()
    else:
        qs = InstagramEngineLead.objects.filter(tenant_id__in=_outreach_tenant_keys(business))

    # فیلتر ۵ روز اخیر
    cutoff_5_days = timezone.now() - timedelta(days=5)
    filter_5days = request.GET.get("five_days", "1")
    if filter_5days == "1":
        qs = qs.filter(
            Q(comment_created_at__gte=cutoff_5_days)
            | Q(comment_created_at__isnull=True, created_at__gte=cutoff_5_days)
        )

    # مرتب‌سازی: بیشترین امتیاز، سپس جدیدترین زمان
    from django.db.models import F
    from django.db.models.functions import Coalesce
    qs = qs.order_by(
        F("outreach_score").desc(nulls_last=True),
        F("intent_score").desc(nulls_last=True),
        Coalesce("comment_created_at", "created_at").desc(nulls_last=True),
        "-id"
    )

    today_str = format_jalali_date(timezone.now().date(), persian_digits=False).replace("/", "-")
    filename = f"instagram_leads_{business.id}_{today_str}.csv"

    buffer = io.StringIO()
    writer = csv.writer(buffer, dialect="excel", quoting=csv.QUOTE_ALL, lineterminator="\r\n")

    headers = [
        "آیدی مشتری (متقاضی)",
        "وضعیت حساب کاربر (Public/Private)",
        "زمان ثبت کامنت (تقویم شمسی)",
        "امتیاز اولویت لید (از ۴۵)",
        "درصد احتمال موفقیت (٪)",
        "نوع نیت و تقاضای خریدار",
        "متن کامنت خریدار",
        "نام و عنوان پیج مبدا (ناشر پست)",
        "آدرس اینستاگرام پیج مبدا",
        "آدرس پست مبدا (لینک مستقیم)",
        "محتوا و خلاصه کپشن پست",
        "زمان انتشار پست مبدا",
        "پیام ساجست‌شده اختصاصی",
        "لینک پروفایل مشتری",
        "وضعیت ارتباط (Outreach Status)",
        "پاسخ مشتری به پیام دایرکت",
    ]
    writer.writerow(headers)

    intent_map = {
        "QUESTION_INQUIRY": "پرسش فنی، سوال از رو نیاز یا اقساط",
        "DIRECT_SHORT_CTA": "پیام کوتاه / تک کلمه / عدد",
        "PRICE_INQUIRY": "استعلام قیمت",
        "INTENT_TO_BUY": "قصد خرید جدی",
        "RESERVATION_REQUEST": "درخواست نوبت یا رزرو",
        "LOCATION_REQUEST": "آدرس یا تماس",
        "SHORT_ENGAGEMENT": "کامنت کوتاه و تایید",
        "UNKNOWN": "عمومی",
    }

    from apps.discovery.outreach.messages import build_bulletproof_message
    from apps.discovery.tenants import tenant_key as business_tenant_key, tenant_pitch
    default_consultant = (tenant_pitch(business_tenant_key(business))["consultant_name"] if business else "") or "مشاور فروشگاه"

    for lead in qs[:500]:
        if not lead.outreach_score:
            update_lead_scoring(lead, commit=False)   # GET: score in memory; the scraper stores it

        user_handle = f"@{lead.username.lstrip('@')}" if lead.username else "-"
        page_type = lead.display_page_type
        comment_time = lead.comment_created_at_jalali or "-"
        post_time = lead.post_created_at_jalali or "-"
        intent_label = intent_map.get(lead.comment_intent_type, lead.comment_intent_type or "نامشخص")
        post_link = lead.source_post_url or "-"
        source_title = lead.source_page_title or "-"
        source_user = f"@{lead.source_page_username.lstrip('@')}" if lead.source_page_username else "-"
        caption_snippet = (lead.post_caption_snippet or "").strip()
        suggested_pitch = (lead.initial_dm_text or "").strip()
        if not suggested_pitch:
            suggested_pitch = build_bulletproof_message(
                lead=lead,
                consultant_name=default_consultant,
                business_name=business.name if business else "فروشگاه",
            )
        profile_url = lead.profile_url or f"https://www.instagram.com/{lead.username}/"
        outreach_status_label = lead.get_status_display()
        reply_snippet = (lead.reply_text or "").strip() if lead.has_replied else "-"

        writer.writerow(safe_csv_row([
            user_handle,
            page_type,
            comment_time,
            lead.outreach_score or 0,
            f"{lead.success_probability}%" if lead.success_probability else "50%",
            intent_label,
            lead.comment_text or "-",
            source_title,
            source_user,
            post_link,
            caption_snippet,
            post_time,
            suggested_pitch,
            profile_url,
            outreach_status_label,
            reply_snippet,
        ]))

    # One BOM only (encoding with utf-8-sig on top of "\ufeff" wrote two).
    csv_data = "\ufeff" + buffer.getvalue()
    response = HttpResponse(csv_data.encode("utf-8"), content_type="text/csv; charset=utf-8")
    response["Content-Disposition"] = f'attachment; filename="{filename}"'
    return response
