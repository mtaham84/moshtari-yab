"""
Authentication views and throttle helpers for user signup, login, and logout.
Extracted as part of P08.15 (R39) to separate account auth views from settings/outreach.
"""

import logging
from django.contrib import messages
from django.contrib.auth import authenticate, get_user_model, login, logout
from django.core.cache import cache
from django.core.validators import EmailValidator, ValidationError as DjangoValidationError
from django.db import IntegrityError, transaction
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils.http import url_has_allowed_host_and_scheme

from apps.businesses.models import Business

User = get_user_model()
logger = logging.getLogger(__name__)

__all__ = [
    "get_client_ip",
    "check_auth_throttle",
    "increment_auth_throttle",
    "reset_auth_throttle",
    "is_valid_email",
    "signup_view",
    "login_view",
    "logout_view",
]


def get_client_ip(request) -> str:
    """Safely extracts client IP address, handling proxy headers."""
    x_forwarded_for = request.META.get("HTTP_X_FORWARDED_FOR")
    if x_forwarded_for:
        return x_forwarded_for.split(",")[0].strip()
    return request.META.get("REMOTE_ADDR", "127.0.0.1")


def check_auth_throttle(request, key_prefix: str, max_attempts: int = 5) -> bool:
    ip = get_client_ip(request)
    cache_key = f"auth_throttle:{key_prefix}:{ip}"
    try:
        attempts = cache.get(cache_key, 0)
        return attempts >= max_attempts
    except Exception:
        return False


def increment_auth_throttle(request, key_prefix: str, lock_seconds: int = 300) -> int:
    ip = get_client_ip(request)
    cache_key = f"auth_throttle:{key_prefix}:{ip}"
    try:
        attempts = cache.get(cache_key, 0) + 1
        cache.set(cache_key, attempts, lock_seconds)
        return attempts
    except Exception:
        return 1


def reset_auth_throttle(request, key_prefix: str) -> None:
    ip = get_client_ip(request)
    cache_key = f"auth_throttle:{key_prefix}:{ip}"
    try:
        cache.delete(cache_key)
    except Exception:
        pass


def is_valid_email(email: str) -> bool:
    if not email or "@" not in email:
        return False
    validator = EmailValidator()
    try:
        validator(email)
        return True
    except DjangoValidationError:
        return False


def signup_view(request):
    if request.user.is_authenticated:
        return redirect("accounts:dashboard")

    if request.method == "POST":
        if check_auth_throttle(request, "signup", max_attempts=15):
            messages.error(request, "تعداد ثبت‌نام‌های ارسالی از این آدرس بیش از حد مجاز است. لطفاً اندکی بعد مجدداً تلاش فرمایید.")
            return render(request, "accounts/signup.html")

        first_name = request.POST.get("first_name", "").strip()
        last_name = request.POST.get("last_name", "").strip()
        email = request.POST.get("email", "").strip().lower()
        # Do NOT strip password; preserve spaces intentionally chosen by user (R28)
        password = request.POST.get("password", "")
        business_name = request.POST.get("business_name", "").strip()
        business_type = request.POST.get("business_type", "PHYSICAL").strip()
        business_domain = request.POST.get("business_domain", "").strip()

        form_values = {
            "first_name": first_name,
            "last_name": last_name,
            "email": email,
            "business_name": business_name,
            "business_type": business_type,
            "business_domain": business_domain,
        }

        # Validations
        if not (first_name and last_name and email and business_name):
            messages.error(request, "لطفاً تمامی فیلدهای الزامی فرم را تکمیل فرمایید.")
            return render(request, "accounts/signup.html", form_values)

        if not is_valid_email(email):
            messages.error(request, "فرمت آدرس ایمیل وارد شده نامعتبر است.")
            return render(request, "accounts/signup.html", form_values)

        if len(password) < 8:
            messages.error(request, "کلمه عبور باید حداقل ۸ کاراکتر باشد.")
            return render(request, "accounts/signup.html", form_values)

        if len(password) > 128:
            messages.error(request, "طول کلمه عبور نمی‌تواند بیش از ۱۲۸ کاراکتر باشد.")
            return render(request, "accounts/signup.html", form_values)

        # Without email verification an existing account must never be taken over by signing up again.
        if User.objects.filter(email__iexact=email).exists() or User.objects.filter(username__iexact=email).exists():
            messages.error(request, "این ایمیل قبلاً ثبت شده است. لطفاً وارد شوید.")
            return render(request, "accounts/signup.html", form_values)

        # Atomic User + Business creation (prevents orphaned users or split-state)
        try:
            with transaction.atomic():
                user = User.objects.create_user(
                    username=email,
                    email=email,
                    password=password,
                    first_name=first_name,
                    last_name=last_name,
                )
                Business.objects.create(
                    user=user,
                    name=business_name,
                    business_type=business_type,
                    business_domain=business_domain,
                )
        except IntegrityError:
            messages.error(request, "این ایمیل قبلاً در سیستم ثبت شده است. لطفاً وارد شوید.")
            return render(request, "accounts/signup.html", form_values)
        except Exception as exc:
            logger.exception("Error creating user and business during signup: %s", exc)
            messages.error(request, "خطایی در ثبت‌نام رخ داد. لطفاً دوباره تلاش فرمایید.")
            return render(request, "accounts/signup.html", form_values)

        # Immediate login for MVP (No OTP / Verification needed per Decisive Directive 6)
        reset_auth_throttle(request, "signup")
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
        if check_auth_throttle(request, "login", max_attempts=5):
            messages.error(request, "تعداد تلاش‌های ناموفق بیش از حد مجاز است. لطفاً ۵ دقیقه دیگر مجدداً تلاش فرمایید.")
            return render(request, "accounts/login.html", {
                "username_or_email": request.POST.get("username_or_email", "").strip(),
                "active_tab": "password",
                "next": next_url,
            })

        # Standard Password Login (Primary)
        username_or_email = request.POST.get("username_or_email", "").strip()
        # Do NOT strip password
        password = request.POST.get("password", "")

        if not username_or_email or not password:
            messages.error(request, "لطفاً ایمیل / نام کاربری و کلمه عبور را وارد فرمایید.")
            return render(request, "accounts/login.html", {
                "username_or_email": username_or_email,
                "active_tab": "password",
                "next": next_url,
            })

        # Look up username by email or username
        matched_user = User.objects.filter(email__iexact=username_or_email).first()
        if not matched_user:
            matched_user = User.objects.filter(username__iexact=username_or_email).first()

        username_to_auth = matched_user.username if matched_user else username_or_email

        # Standard Django authentication
        auth_user = authenticate(request, username=username_to_auth, password=password)

        if auth_user is not None:
            reset_auth_throttle(request, "login")
            login(request, auth_user)
            name = auth_user.get_full_name() or auth_user.first_name or auth_user.username
            messages.success(request, f"خوش آمدید، {name} عزیز!")
            if not next_url and (auth_user.is_staff or auth_user.is_superuser):
                fallback_url = reverse("manage:dashboard")
            return redirect(next_url or fallback_url)
        else:
            # Check if user exists with correct password but is inactive (R28)
            if matched_user and matched_user.check_password(password) and not matched_user.is_active:
                messages.error(request, "حساب کاربری شما غیرفعال است.")
                return render(request, "accounts/login.html", {
                    "username_or_email": username_or_email,
                    "active_tab": "password",
                    "next": next_url,
                })

            increment_auth_throttle(request, "login", lock_seconds=300)
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
