import logging
from django.contrib import messages
from django.contrib.auth import get_user_model, login, logout
from django.contrib.auth.decorators import login_required
from django.utils.http import url_has_allowed_host_and_scheme
from django.http import JsonResponse
from django.shortcuts import redirect, render, get_object_or_404
from django.urls import reverse
from django.views.decorators.http import require_POST
from apps.businesses.models import Business, MessageStyle
from apps.discovery.models import Opportunity, InstagramEngineAccount
from apps.discovery.services import get_performance_analytics

logger = logging.getLogger(__name__)

User = get_user_model()

def signup_view(request):
    if request.user.is_authenticated:
        return redirect("accounts:dashboard")

    if request.method == "POST":
        first_name = request.POST.get("first_name", "").strip()
        last_name = request.POST.get("last_name", "").strip()
        email = request.POST.get("email", "").strip().lower()
        password = request.POST.get("password", "").strip()
        business_name = request.POST.get("business_name", "").strip()
        business_type = request.POST.get("business_type", "PHYSICAL").strip()
        business_domain = request.POST.get("business_domain", "").strip()

        # Validations
        if not (first_name and last_name and email and business_name):
            messages.error(request, "لطفاً تمامی فیلدهای الزامی فرم را تکمیل فرمایید.")
            return render(request, "accounts/signup.html", {
                "first_name": first_name,
                "last_name": last_name,
                "email": email,
                "business_name": business_name,
                "business_type": business_type,
                "business_domain": business_domain,
            })

        if "@" not in email or "." not in email:
            messages.error(request, "فرمت آدرس ایمیل وارد شده نامعتبر است.")
            return render(request, "accounts/signup.html", {
                "first_name": first_name,
                "last_name": last_name,
                "email": email,
                "business_name": business_name,
                "business_type": business_type,
                "business_domain": business_domain,
            })

        form_values = {
            "first_name": first_name,
            "last_name": last_name,
            "email": email,
            "business_name": business_name,
            "business_type": business_type,
            "business_domain": business_domain,
        }
        if len(password) < 8:
            messages.error(request, "کلمه عبور باید حداقل ۸ کاراکتر باشد.")
            return render(request, "accounts/signup.html", form_values)

        # Without email verification an existing account must never be taken over by signing up again.
        if User.objects.filter(email__iexact=email).exists() or User.objects.filter(username__iexact=email).exists():
            messages.error(request, "این ایمیل قبلاً ثبت شده است. لطفاً وارد شوید.")
            return render(request, "accounts/signup.html", form_values)

        # Direct Signup for Demo Mode (No OTP / Verification needed)
        user = User.objects.create_user(
            username=email,
            email=email,
            password=password,
            first_name=first_name,
            last_name=last_name,
        )

        # Create or update associated business
        Business.objects.update_or_create(
            user=user,
            defaults={
                "name": business_name,
                "business_type": business_type,
                "business_domain": business_domain,
            }
        )

        # Immediate login
        login(request, user)
        messages.success(request, "ثبت‌نام انجام شد. حالا نوع کسب‌وکار و دسته‌بندی محصولات خود را مشخص کنید.")
        return redirect("accounts:onboarding")

    return render(request, "accounts/signup.html")

def login_view(request):
    if request.user.is_authenticated:
        return redirect("accounts:dashboard")

    # QueryDict.getlist keeps a second attacker-supplied ``next`` value from overriding
    # the one that was validated here when the form is submitted.
    next_values = request.GET.getlist("next") or request.POST.getlist("next")
    next_url = next_values[0] if next_values else ""
    if not url_has_allowed_host_and_scheme(
        next_url, allowed_hosts={request.get_host()}, require_https=request.is_secure()
    ):
        next_url = ""
    fallback_url = reverse("accounts:dashboard")

    if request.method == "POST":
        # Standard Password Login (Primary)
        username_or_email = request.POST.get("username_or_email", "").strip()
        password = request.POST.get("password", "")

        if not username_or_email or not password:
            messages.error(request, "لطفاً ایمیل / نام کاربری و کلمه عبور را وارد فرمایید.")
            return render(request, "accounts/login.html", {
                "username_or_email": username_or_email,
                "active_tab": "password",
                "next": next_url,
            })

        # Look up user by email or username
        user = User.objects.filter(email__iexact=username_or_email).first()
        if not user:
            user = User.objects.filter(username__iexact=username_or_email).first()

        if user and user.check_password(password):
            if not user.is_active:
                messages.error(request, "حساب کاربری شما غیرفعال است.")
                return render(request, "accounts/login.html", {
                    "username_or_email": username_or_email,
                    "active_tab": "password",
                    "next": next_url,
                })

            login(request, user)
            name = user.get_full_name() or user.first_name or user.username
            messages.success(request, f"خوش آمدید، {name} عزیز!")
            if not next_url and (user.is_staff or user.is_superuser):
                fallback_url = reverse("manage:dashboard")
            return redirect(next_url or fallback_url)
        else:
            messages.error(request, "نام کاربری/ایمیل یا کلمه عبور وارد شده نادرست است.")
            return render(request, "accounts/login.html", {
                "username_or_email": username_or_email,
                "active_tab": "password",
                "next": next_url,
            })

    return render(request, "accounts/login.html", {
        "active_tab": "password",
        "next": next_url,
    })

def logout_view(request):
    logout(request)
    messages.info(request, "با موفقیت از حساب کاربری خود خارج شدید.")
    return redirect("core:landing")

@login_required
def dashboard_view(request):
    business = getattr(request.user, "business", None)
    if not business:
        business = Business.objects.create(
            user=request.user,
            name=f"کسب‌وکار {request.user.first_name}",
            business_type="PHYSICAL",
            business_domain="عمومی"
        )

    if request.method == "POST":
        if request.POST.get("update_targeting_constraints"):
            locations = request.POST.get("target_locations", "").strip()
            min_age_val = request.POST.get("target_min_age", "").strip()
            max_age_val = request.POST.get("target_max_age", "").strip()

            business.target_locations = locations or "سراسر کشور"
            business.target_min_age = int(min_age_val) if min_age_val.isdigit() else None
            business.target_max_age = int(max_age_val) if max_age_val.isdigit() else None
            business.save(update_fields=["target_locations", "target_min_age", "target_max_age", "updated_at"])
            messages.success(request, "محدوده‌های هدف‌گذاری اختیاری (موقعیت جغرافیایی و بازه سنی) با موفقیت به‌روزرسانی شد.")
            return redirect(request.META.get("HTTP_REFERER") or "accounts:dashboard")


    analytics_data = get_performance_analytics(business)

    return render(request, "accounts/dashboard.html", {
        "user": request.user,
        "business": business,
        "analytics": analytics_data,
        "recent_opportunities": Opportunity.objects.filter(business=business).select_related(
            "customer", "ai_analysis").prefetch_related("product_matches__product").order_by("-created_at")[:5],
    })


@login_required
def settings_view(request):
    """
    Dedicated settings page for business profile, targeting constraints,
    and Telegram / messaging configuration.
    """
    business = getattr(request.user, "business", None)
    if not business:
        business = Business.objects.create(
            user=request.user,
            name=f"کسب‌وکار {request.user.first_name or request.user.username}",
            business_type="PHYSICAL",
            business_domain="عمومی"
        )

    if request.method == "POST":
        section = request.POST.get("section", "all")

        # 1. Business Profile
        if section in ["profile", "all"]:
            business.name = request.POST.get("name", business.name).strip()
            business.business_type = request.POST.get("business_type", business.business_type)
            business.business_domain = request.POST.get("business_domain", business.business_domain).strip()
            business.description = request.POST.get("description", business.description).strip()
            business.target_customer_description = request.POST.get("target_customer_description", business.target_customer_description).strip()

        # 2. Targeting Constraints
        if section in ["targeting", "all"]:
            locations = request.POST.get("target_locations", "").strip()
            business.target_locations = locations or "سراسر کشور"
            min_age_val = request.POST.get("target_min_age", "").strip()
            max_age_val = request.POST.get("target_max_age", "").strip()
            business.target_min_age = int(min_age_val) if min_age_val.isdigit() else None
            business.target_max_age = int(max_age_val) if max_age_val.isdigit() else None

        # 3. Message style (how reply drafts are written). Telegram account/bot and the daily cap had no effect
        #    anywhere and are no longer shown.
        if section == "style":
            style = _style_from_post(request.POST, MessageStyle.for_business(business))
            style.save()
            messages.success(request, "سبک پیام ذخیره شد؛ پیش‌نویس‌های بعدی با همین سبک نوشته می‌شوند.")
            return redirect(f"{reverse('accounts:settings')}?tab=style")

        business.save()
        messages.success(request, "تنظیمات کسب‌وکار و حساب با موفقیت ذخیره شد.")
        return redirect("accounts:settings")

    return render(request, "accounts/settings.html", {
        "user": request.user,
        "business": business,
        "style": MessageStyle.for_business(business),
        "tone_choices": MessageStyle.TONE_CHOICES,
        "sample_products": business.products.order_by("-created_at")[:50],
        "active_tab": request.GET.get("tab", ""),
    })


def _style_from_post(post, style: MessageStyle) -> MessageStyle:
    """Form → MessageStyle (not saved). Values are clamped; the engine validates them again."""
    tone = post.get("tone", style.tone)
    style.tone = tone if tone in dict(MessageStyle.TONE_CHOICES) else "FRIENDLY"
    raw = (post.get("max_sentences") or "").strip()
    style.max_sentences = min(5, max(1, int(raw))) if raw.isdigit() else style.max_sentences
    style.use_emoji = post.get("use_emoji") in ("on", "true", "1")
    style.include_link = post.get("include_link") in ("on", "true", "1")
    style.signature = (post.get("signature") or "").strip()[:100]
    style.extra_instructions = (post.get("extra_instructions") or "").strip()[:500]
    return style


@login_required
@require_POST
def style_sample_view(request):
    """«نمونه بساز»: a draft with the style currently in the form (not saved) for one of the seller's products."""
    from apps.core.agent import AgentUnavailable, sample_reply

    business = getattr(request.user, "business", None)
    if business is None:
        return JsonResponse({"status": "error", "message": "ابتدا مشخصات کسب‌وکار را ثبت کنید."}, status=400)
    pid = (request.POST.get("product_id") or "").strip()
    product = business.products.filter(pk=pid).first() if pid.isdigit() else business.products.order_by("-created_at").first()
    if product is None:
        return JsonResponse({"status": "error", "message": "برای ساخت نمونه، ابتدا یک محصول اضافه کنید."}, status=400)
    style = _style_from_post(request.POST, MessageStyle(business=business))
    try:
        text = sample_reply(business, style, product)
    except AgentUnavailable as e:
        return JsonResponse({"status": "error", "message": str(e)}, status=503)
    return JsonResponse({"status": "success", "product": product.name, "reply": text})


def _outreach_tenant_keys(business) -> list[str]:
    """Lead/sender tenant ids that belong to a business (tenant_4 also owns legacy 'sarv' rows)."""
    tenant_key = f"tenant_{business.id}"
    return [tenant_key, "sarv"] if business.id == 4 else [tenant_key]


def _captured_phone_for_lead(lead, thread, customer_phones: dict | None = None, business=None):
    """Phone saved on the lead's Customer, else the first Iranian phone number the lead sent us in DM."""
    from apps.discovery.models import Customer
    from apps.discovery.outreach_service import extract_iranian_phone_number

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


@login_required
def outreach_view(request):
    """Business owner's dedicated Instagram Outreach & Sender Management page."""
    from apps.businesses.models import Business
    from apps.discovery.models import InstagramEngineAccount, InstagramEngineLead

    business = getattr(request.user, "business", None)

    # For staff / superuser, support ?biz=<id> or default to tenant_4 (Sarv Dental) if current business is empty
    all_businesses = []
    if request.user.is_staff or request.user.is_superuser:
        all_businesses = list(Business.objects.all().order_by("id"))
        biz_param = request.GET.get("biz")
        if biz_param and biz_param.isdigit():
            b = Business.objects.filter(id=int(biz_param)).first()
            if b:
                business = b
        elif not business or business.id not in [4, 6]:
            sarv_biz = Business.objects.filter(id=4).first()
            if sarv_biz:
                business = sarv_biz

    if not business:
        messages.error(request, "ابتدا اطلاعات کسب‌وکار خود را تکمیل نمایید.")
        return redirect("accounts:dashboard")

    tenant_key = f"tenant_{business.id}"
    tenant_keys = [tenant_key, "sarv"] if business.id == 4 else [tenant_key]

    from apps.discovery.outreach_service import (
        TIER_LIMITS,
        get_lead_conversation_thread,
    )
    from apps.discovery.models import Customer

    accounts = InstagramEngineAccount.objects.filter(tenant_id__in=tenant_keys, role="SENDER").order_by("-id")
    sent_dms = InstagramEngineLead.objects.filter(tenant_id__in=tenant_keys, status__in=["DM_SENT", "REPLIED", "FOLLOWUP_SENT"]).order_by("-updated_at")[:50]
    total_dms = InstagramEngineLead.objects.filter(tenant_id__in=tenant_keys, status__in=["DM_SENT", "REPLIED", "FOLLOWUP_SENT"]).count()
    pending_leads = InstagramEngineLead.objects.filter(tenant_id__in=tenant_keys, status="NEW").count()

    # Leads who replied to our direct outreach or have active conversation
    from django.db.models import Q
    conversation_leads = list(InstagramEngineLead.objects.filter(
        tenant_id__in=tenant_keys
    ).filter(
        Q(has_replied=True) | Q(status__in=["DM_SENT", "REPLIED", "FOLLOWUP_SENT", "QUEUED"]) | Q(initial_dm_text__isnull=False)
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

    # One consultant name + pitch per customer (tenant), used by all of its senders (ig_accounts.tenant_pitch:
    # its profile, else the legacy per-username pitch of data/sarv_outreach_config.json)
    from apps.discovery.ig_accounts import tenant_pitch

    accounts = list(accounts)
    pitch = tenant_pitch(tenant_key, [acc.username for acc in accounts])
    account_list = [{"obj": acc, "consultant_name": pitch["consultant_name"] or acc.username,
                     "template": pitch["pitch_template"]} for acc in accounts]
    default_pitch = pitch["pitch_template"]
    default_consultant = pitch["consultant_name"] or (accounts[0].username if accounts else "")
    default_username = accounts[0].username if accounts else ""
    if not default_pitch and business.id == 4:
        default_pitch = (
            "درود بر شما \n"
            "بنده مشاور و پشتیبانی تخصصی دندان پزشکی هستم، متوجه شدم پیگیر امور زیبایی و سلامتی دندونتون هستید\n"
            "میتونید هر سوالی دارید از بنده بپرسید. \n"
            "اگر هم فکر میکنید موثر هست ، شماره تماستون رو بزارید برای ۱۰ دقیقه مشاوره تخصصی رایگان با شما تماس میگیرم"
        )
        default_consultant = pitch["consultant_name"] or "مشاور دندان‌پزشکی"

    # Top-Scoring Prioritized Leads (Ranked by 35-point scoring: Public + Intent + Freshness)
    from django.db.models import Q
    from datetime import timedelta
    from django.utils import timezone as dj_tz
    from apps.discovery.outreach_service import build_bulletproof_message
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
            update_lead_scoring(lead_item, commit=True)
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
    return bool(user.is_staff or user.is_superuser)


def _outreach_redirect(request, business_id=None):
    url = reverse("accounts:outreach")
    return redirect(url + (f"?biz={business_id}" if business_id and _staff(request.user) else ""))


@login_required
@require_POST
def save_pitch_view(request):
    """Saves the customer's consultant name and cold-DM pitch: one per customer, used by all of its senders.
    A customer edits only its own; staff pick the customer (biz, or the tenant of a sender username)."""
    from apps.businesses.models import Business
    from apps.discovery import ig_accounts as svc
    from apps.discovery.models import InstagramEngineAccount

    business = getattr(request.user, "business", None)
    if _staff(request.user):
        biz = (request.POST.get("biz") or "").strip()
        username = (request.POST.get("username") or "").strip().lstrip("@")
        if biz.isdigit():
            business = Business.objects.filter(pk=int(biz)).first() or business
        elif username:
            acc = InstagramEngineAccount.objects.filter(username__iexact=username).first()
            tid = svc.canonical_tenant(acc.tenant_id) if acc else ""
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
    svc.save_tenant_profile(f"tenant_{business.id}", consultant_name=consultant_name,
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
    svc.save_tenant_profile(tenant_key, consultant_name=request.POST.get("consultant_name", ""),
                            consultant_role=f"مشاور و پشتیبانی {business.name}" if business else "",
                            pitch_template=request.POST.get("pitch_template", ""), only_empty=True)
    (messages.success if res.ok else messages.warning)(request, res.message)
    return redirect(reverse("manage:ig_account_detail", args=[acc.pk]) + "#login")


@login_required
@require_POST
def generate_pitches_ajax_view(request):
    """Generate 3 high-converting AI pitch options based on business info and consultant persona."""
    from django.http import JsonResponse
    from apps.discovery.outreach_service import generate_ai_outreach_pitches

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


@login_required
def sync_replies_view(request):
    """Check direct inbox for any replies from leads across all sender accounts."""
    from django.http import JsonResponse
    from apps.discovery.outreach_service import check_and_sync_lead_replies

    business = getattr(request.user, "business", None)
    if not business:
        return redirect("accounts:dashboard")

    tenant_key = f"tenant_{business.id}"
    tenant_keys = [tenant_key, "sarv"] if business.id == 4 else [tenant_key]

    res = check_and_sync_lead_replies(tenant_keys)
    if request.headers.get("x-requested-with") == "XMLHttpRequest" or request.GET.get("format") == "json":
        return JsonResponse({"success": True, "replies_found": res.get("replies_found", 0), "details": res.get("details", [])})

    found = res.get("replies_found", 0)
    if found > 0:
        messages.success(request, f"پایش صندوق پیام‌ها انجام شد: {found} پاسخ جدید از خریداران دریافت و ثبت شد!")
    else:
        messages.info(request, "صندوق پیام‌ها پایش شد؛ پاسخ جدیدی تا این لحظه دریافت نشده است.")
    return redirect("accounts:outreach")


@login_required
@require_POST
def send_followup_view(request):
    """Enqueue an operator direct message / follow-up in the safe dispatch queue.

    The DM is delivered by the outreach dispatcher (shift hours, sender limits, warm-up and
    human pacing) instead of logging in from the web process and sending immediately.
    """
    from django.http import JsonResponse
    from apps.discovery.models import InstagramEngineLead
    from apps.discovery.outreach_service import enqueue_direct_message

    business = getattr(request.user, "business", None)
    is_staff = request.user.is_staff or request.user.is_superuser
    is_ajax = request.headers.get("x-requested-with") == "XMLHttpRequest"

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
            pass

    sender_account = lead.sender_account or lead.second_sender_username or ""
    second_sender = lead.second_sender_username or ""
    from apps.discovery.outreach_service import get_lead_conversation_thread
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
    from apps.discovery.outreach_service import extract_iranian_phone_number

    business = getattr(request.user, "business", None)
    if not business:
        return JsonResponse({"success": False, "error": "دسترسی غیرمجاز."}, status=403)

    lead_id = request.POST.get("lead_id")
    raw_phone = request.POST.get("phone_number", "").strip()

    if not lead_id or not raw_phone:
        return JsonResponse({"success": False, "error": "شناسه مشتری یا شماره تماس نامعتبر است."}, status=400)

    lead_qs = InstagramEngineLead.objects.filter(pk=lead_id)
    if not (request.user.is_staff or request.user.is_superuser):
        lead_qs = lead_qs.filter(tenant_id__in=_outreach_tenant_keys(business))
    lead = lead_qs.first()
    if not lead:
        return JsonResponse({"success": False, "error": "لید یافت نشد."}, status=404)

    norm_phone = extract_iranian_phone_number(raw_phone) or raw_phone

    if lead.ig_user_pk:
        cust, _ = Customer.objects.get_or_create(
            business=business,
            external_user_id=str(lead.ig_user_pk),
            defaults={
                "name": lead.full_name or lead.username,
                "source_platform": "instagram",
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
    from apps.billing.manage_views import ig_edit_sender
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


def request_activation_view(request):
    """
    Renders the public demo/activation request page and processes customer submissions.
    """
    if request.method == "POST":
        full_name = request.POST.get("full_name", "").strip()
        team_size = request.POST.get("team_size", "").strip()
        phone_number = request.POST.get("phone_number", "").strip()
        company_name = request.POST.get("company_name", "").strip()
        email = request.POST.get("email", "").strip().lower()
        problem_statement = request.POST.get("problem_statement", "").strip()

        form_data = {
            "full_name": full_name,
            "team_size": team_size,
            "phone_number": phone_number,
            "company_name": company_name,
            "email": email,
            "problem_statement": problem_statement,
        }

        if not (full_name and company_name and phone_number and email):
            return render(request, "accounts/request_demo.html", {
                "form_data": form_data,
                "error_message": "لطفاً تمامی فیلدهای الزامی (نام، شرکت، شماره تماس و ایمیل) را تکمیل فرمایید.",
            })

        if "@" not in email or "." not in email:
            return render(request, "accounts/request_demo.html", {
                "form_data": form_data,
                "error_message": "فرمت آدرس ایمیل وارد شده معتبر نمی‌باشد.",
            })

        from apps.accounts.models import ActivationRequest
        from apps.accounts.services import notify_new_activation_request

        req = ActivationRequest.objects.create(
            full_name=full_name,
            company_name=company_name,
            phone_number=phone_number,
            email=email,
            team_size=team_size,
            problem_statement=problem_statement,
            status=ActivationRequest.STATUS_PENDING,
        )

        # Trigger immediate Telegram notification to admin subscribers
        try:
            notify_new_activation_request(req)
        except Exception as e:
            logger.warning("Failed sending Telegram alert for activation request #%s: %s", req.id, e)

        return render(request, "accounts/request_demo.html", {
            "success": True,
            "req": req,
        })

    return render(request, "accounts/request_demo.html")


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

    is_admin = request.user.is_staff or request.user.is_superuser
    if is_admin and request.GET.get("business_id", "").strip().lower() == "all":
        qs = InstagramEngineLead.objects.all()
    else:
        tenant_keys = [f"tenant_{business.id}"]
        if business.id == 4:
            tenant_keys.append("sarv_dental")
        qs = InstagramEngineLead.objects.filter(tenant_id__in=tenant_keys)

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

    status_map = {
        "NEW": "در صف ارسال",
        "APPROVED": "تایید شده",
        "PITCH_GENERATED": "آماده ارسال",
        "SENT": "ارسال شده",
        "DM_SENT": "ارسال شده",
        "FOLLOWUP_1": "پیگیری اول",
        "FOLLOWUP_2": "پیگیری دوم",
        "FOLLOWUP_SENT": "پیگیری شده",
        "REPLIED": "پاسخ داده",
        "CONVERTED": "موفق",
        "FAILED": "ناموفق",
        "REJECTED": "رد شده",
        "SKIPPED": "رد شده (حفاظت)",
    }

    from apps.discovery.outreach_service import build_bulletproof_message
    default_consultant = "مشاور فروشگاه"
    if business and business.id == 4:
        default_consultant = "مشاور دندان‌پزشکی"

    for lead in qs[:500]:
        if not lead.outreach_score:
            update_lead_scoring(lead, commit=True)

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

        writer.writerow([
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
        ])

    csv_data = "\ufeff" + buffer.getvalue()
    response = HttpResponse(csv_data.encode("utf-8-sig"), content_type="text/csv; charset=utf-8")
    response["Content-Disposition"] = f'attachment; filename="{filename}"'
    return response





