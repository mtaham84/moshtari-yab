import secrets
from datetime import timedelta
from django.contrib import messages
from django.contrib.auth import get_user_model, login, logout
from django.contrib.auth.decorators import login_required
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from apps.businesses.models import Business
from apps.discovery.services import get_performance_analytics
from .emails import send_verification_email
from .models import EmailVerification

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

        # Check existing active block on this email
        recent_active = EmailVerification.objects.filter(email=email).first()
        if recent_active:
            is_blocked, remaining = recent_active.is_blocked()
            if is_blocked:
                messages.error(request, f"این آدرس ایمیل به دلیل تلاش‌های ناموفق مکرر مسدود است. لطفاً {remaining} ثانیه دیگر مجدداً تلاش نمایید.")
                return render(request, "accounts/signup.html", {
                    "first_name": first_name,
                    "last_name": last_name,
                    "email": email,
                    "business_name": business_name,
                    "business_type": business_type,
                    "business_domain": business_domain,
                })

        # Rate limit: Max 5 code requests in last 10 minutes per email
        ten_minutes_ago = timezone.now() - timedelta(minutes=10)
        recent_requests_count = EmailVerification.objects.filter(
            email=email,
            created_at__gte=ten_minutes_ago
        ).count()

        if recent_requests_count >= 5:
            messages.error(request, "تعداد درخواست‌های کد تأیید برای این ایمیل به سقف مجاز (۵ درخواست در ۱۰ دقیقه) رسیده است. لطفاً کمی صبر فرمایید.")
            return render(request, "accounts/signup.html", {
                "first_name": first_name,
                "last_name": last_name,
                "email": email,
                "business_name": business_name,
                "business_type": business_type,
                "business_domain": business_domain,
            })

        # Generate 6-digit numeric OTP
        code = f"{secrets.randbelow(900000) + 100000}"
        now = timezone.now()
        expires_at = now + timedelta(minutes=EmailVerification.EXPIRATION_MINUTES)

        session_data = {
            "first_name": first_name,
            "last_name": last_name,
            "password": password,
            "business_name": business_name,
            "business_type": business_type,
            "business_domain": business_domain,
        }

        verification = EmailVerification.objects.create(
            email=email,
            code=code,
            session_data=session_data,
            expires_at=expires_at,
        )

        # Send email via Gmail SMTP
        try:
            send_verification_email(email=email, code=code, first_name=first_name)
            messages.success(request, f"کد احراز هویت ۶ رقمی با موفقیت به {email} ارسال شد.")
        except Exception as e:
            messages.warning(request, f"خطا در ارسال ایمیل ({e}). برای تست در محیط توسعه، کد تأیید: {code}")

        request.session["pending_verification_id"] = verification.id
        request.session["pending_verification_email"] = email
        return redirect("accounts:verify")

    return render(request, "accounts/signup.html")

def verify_view(request):
    if request.user.is_authenticated:
        return redirect("accounts:dashboard")

    verification_id = request.session.get("pending_verification_id")
    email = request.session.get("pending_verification_email") or request.GET.get("email")

    if verification_id:
        verification = EmailVerification.objects.filter(id=verification_id).first()
    elif email:
        verification = EmailVerification.objects.filter(email=email).first()
    else:
        messages.info(request, "لطفاً ابتدا فرم ثبت‌نام را تکمیل نمایید.")
        return redirect("accounts:signup")

    if not verification:
        messages.error(request, "درخواست احراز هویت یافت نشد. لطفاً مجدداً اقدام فرمایید.")
        return redirect("accounts:signup")

    # Check block state
    is_blocked, remaining = verification.is_blocked()
    if not is_blocked:
        verification.reset_attempts_if_unblocked()

    if request.method == "POST":
        # Check if currently blocked
        is_blocked, remaining = verification.is_blocked()
        if is_blocked:
            messages.error(
                request,
                f"دسترسی شما به دلیل ۵ بار اشتباه به مدت ۲ دقیقه مسدود شده است. {remaining} ثانیه دیگر مجدداً تلاش کنید."
            )
            return render(request, "accounts/verify.html", {
                "verification": verification,
                "is_blocked": True,
                "remaining_seconds": remaining,
            })

        # Check expiration
        if verification.is_expired():
            messages.error(request, "کد تأیید منقضی شده است. لطفاً درخواست کد جدید ثبت نمایید.")
            return render(request, "accounts/verify.html", {
                "verification": verification,
                "is_expired": True,
            })

        entered_code = request.POST.get("code", "").strip()

        # Check code match
        if entered_code == verification.code:
            verification.is_verified = True
            verification.save(update_fields=["is_verified"])

            # Create or update user
            session_data = verification.session_data or {}
            first_name = session_data.get("first_name", "")
            last_name = session_data.get("last_name", "")
            username = verification.email

            user, created = User.objects.get_or_create(
                email=verification.email,
                defaults={
                    "username": username,
                    "first_name": first_name,
                    "last_name": last_name,
                    "is_active": True,
                }
            )

            if not created:
                user.first_name = first_name
                user.last_name = last_name
                user.save(update_fields=["first_name", "last_name"])

            raw_password = session_data.get("password")
            if raw_password:
                user.set_password(raw_password)
                user.save()

            # Create or update Business
            business_name = session_data.get("business_name", "کسب‌وکار من")
            business_type = session_data.get("business_type", "PHYSICAL")
            business_domain = session_data.get("business_domain", "")

            Business.objects.update_or_create(
                user=user,
                defaults={
                    "name": business_name,
                    "business_type": business_type,
                    "business_domain": business_domain,
                }
            )

            # Log the user in
            login(request, user)

            # Cleanup session
            request.session.pop("pending_verification_id", None)
            request.session.pop("pending_verification_email", None)

            messages.success(request, f"خوش آمدید {first_name}! حساب کاربری و کسب‌وکار شما با موفقیت فعال شد.")
            return redirect("accounts:dashboard")
        else:
            # Failed attempt logic
            verification.record_failed_attempt()
            is_blocked_now, block_secs = verification.is_blocked()

            if is_blocked_now:
                messages.error(
                    request,
                    "کد واردشده اشتباه است. شما ۵ بار تلاش ناموفق داشتید و دسترسی شما به مدت ۲ دقیقه مسدود گردید."
                )
                return render(request, "accounts/verify.html", {
                    "verification": verification,
                    "is_blocked": True,
                    "remaining_seconds": block_secs,
                })
            else:
                remaining_tries = EmailVerification.MAX_ATTEMPTS - verification.attempts
                messages.error(
                    request,
                    f"کد وارد شده اشتباه است. ({remaining_tries} تلاش دیگر تا مسدود شدن موقت)"
                )
                return render(request, "accounts/verify.html", {
                    "verification": verification,
                    "is_blocked": False,
                    "remaining_tries": remaining_tries,
                })

    return render(request, "accounts/verify.html", {
        "verification": verification,
        "is_blocked": is_blocked,
        "remaining_seconds": remaining,
    })

def resend_code_view(request):
    verification_id = request.session.get("pending_verification_id")
    email = request.session.get("pending_verification_email") or request.GET.get("email")

    if verification_id:
        verification = EmailVerification.objects.filter(id=verification_id).first()
    elif email:
        verification = EmailVerification.objects.filter(email=email).first()
    else:
        messages.error(request, "درخواستی یافت نشد.")
        return redirect("accounts:signup")

    if not verification:
        messages.error(request, "درخواست احراز هویت یافت نشد.")
        return redirect("accounts:signup")

    is_blocked, remaining = verification.is_blocked()
    if is_blocked:
        messages.error(request, f"حساب شما مسدود است. لطفاً {remaining} ثانیه دیگر صبر کنید.")
        return redirect("accounts:verify")

    # Generate new code
    new_code = f"{secrets.randbelow(900000) + 100000}"
    verification.code = new_code
    verification.expires_at = timezone.now() + timedelta(minutes=EmailVerification.EXPIRATION_MINUTES)
    verification.save(update_fields=["code", "expires_at"])

    first_name = (verification.session_data or {}).get("first_name", "")
    try:
        send_verification_email(email=verification.email, code=new_code, first_name=first_name)
        messages.success(request, "کد احراز هویت جدید به ایمیل شما ارسال شد.")
    except Exception as e:
        messages.warning(request, f"کد جدید صادر شد: {new_code}")

    return redirect("accounts:verify")

def login_view(request):
    if request.user.is_authenticated:
        return redirect("accounts:dashboard")

    next_url = request.GET.get("next") or request.POST.get("next") or "accounts:dashboard"

    if request.method == "POST":
        auth_method = request.POST.get("auth_method", "password")

        if auth_method == "otp":
            email = request.POST.get("email", "").strip().lower()
            if not email:
                messages.error(request, "لطفاً آدرس ایمیل خود را برای دریافت کد یکبار مصرف وارد نمایید.")
                return render(request, "accounts/login.html", {"active_tab": "otp", "next": next_url})

            user = User.objects.filter(email__iexact=email).first()
            if not user:
                messages.error(request, "کاربری با این ایمیل یافت نشد. لطفاً ابتدا ثبت‌نام کنید.")
                return redirect("accounts:signup")

            # Check existing active block
            recent_active = EmailVerification.objects.filter(email=email).first()
            if recent_active:
                is_blocked, remaining = recent_active.is_blocked()
                if is_blocked:
                    messages.error(request, f"این حساب کاربری به دلیل تلاش‌های ناموفق مکرر مسدود است. لطفاً {remaining} ثانیه دیگر مجدداً تلاش نمایید.")
                    return render(request, "accounts/login.html", {"active_tab": "otp", "next": next_url})

            # Create OTP verification for login
            code = f"{secrets.randbelow(900000) + 100000}"
            verification = EmailVerification.objects.create(
                email=email,
                code=code,
                session_data={"first_name": user.first_name, "last_name": user.last_name},
                expires_at=timezone.now() + timedelta(minutes=10),
            )

            try:
                send_verification_email(email=email, code=code, first_name=user.first_name)
                messages.success(request, f"کد یکبار مصرف ورود به ایمیل {email} ارسال شد.")
            except Exception:
                messages.info(request, f"کد ورود صادر شد: {code}")

            request.session["pending_verification_id"] = verification.id
            request.session["pending_verification_email"] = email
            return redirect("accounts:verify")

        else:
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

        if request.POST.get("update_social_config"):
            business.telegram_account_handle = request.POST.get("telegram_account_handle", "").strip()
            business.x_account_handle = request.POST.get("x_account_handle", "").strip()
            mode = request.POST.get("preferred_outreach_mode", "DIRECT").strip()
            if mode in ["DIRECT", "COMMENT"]:
                business.preferred_outreach_mode = mode
            limit = request.POST.get("daily_discovery_limit", "").strip()
            if limit.isdigit():
                business.daily_discovery_limit = max(1, int(limit))
            business.save()
            messages.success(request, "تنظیمات اتصال حساب‌های شبکه‌های اجتماعی و سقف پایش با موفقیت به‌روزرسانی شد.")
            return redirect(request.META.get("HTTP_REFERER") or "accounts:dashboard")

    analytics_data = get_performance_analytics(business)

    return render(request, "accounts/dashboard.html", {
        "user": request.user,
        "business": business,
        "analytics": analytics_data,
    })
