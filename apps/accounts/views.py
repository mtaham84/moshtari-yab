from django.contrib import messages
from django.contrib.auth import get_user_model, login, logout
from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.shortcuts import redirect, render
from django.urls import reverse
from django.views.decorators.http import require_POST
from apps.businesses.models import Business, MessageStyle
from apps.discovery.models import Opportunity
from apps.discovery.services import get_performance_analytics

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
        if not (first_name and last_name and email and business_name and business_domain):
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
        messages.success(
            request,
            "ثبت‌نام با موفقیت انجام شد! با توجه به نسخه دمو، شرایط احراز هویت ایمیل غیرفعال شده و حساب کسب‌وکار شما فوراً فعال گردید."
        )
        return redirect("accounts:dashboard")

    return render(request, "accounts/signup.html")

def login_view(request):
    if request.user.is_authenticated:
        return redirect("accounts:dashboard")

    next_url = request.GET.get("next") or request.POST.get("next") or "accounts:dashboard"

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
            return redirect(next_url)
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

