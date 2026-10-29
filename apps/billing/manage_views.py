import json
import logging
import socket
from datetime import datetime, timezone
from decimal import Decimal
from functools import wraps
from urllib.parse import urlparse

from django.contrib import messages
from django.contrib.auth.views import redirect_to_login
from django.core.exceptions import PermissionDenied
from django.db.models import Count, Q, Sum
from django.http import JsonResponse, HttpResponseBadRequest
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone as dj_timezone
from django.views.decorators.http import require_POST, require_GET

from apps.businesses.models import Business
from apps.billing.models import Wallet, WalletTransaction, BillingSettings
from apps.discovery.models import InstagramEngineAccount, InstagramEngineLead, InstagramCrawlExecution

logger = logging.getLogger("management")


def manage_access_required(view_func):
    """Decorator ensuring user is authenticated and is staff or superuser."""
    @wraps(view_func)
    def _wrapped(request, *args, **kwargs):
        if not request.user.is_authenticated:
            return redirect_to_login(request.get_full_path())
        if not (request.user.is_staff or request.user.is_superuser):
            raise PermissionDenied("دسترسی به بخش مدیریت برای حساب کاربری شما مجاز نمی‌باشد.")
        return view_func(request, *args, **kwargs)
    return _wrapped


def _test_proxy_connection(proxy_url: str, timeout: float = 4.0) -> tuple[bool, str]:
    """Tests TCP connection to proxy host and port."""
    if not proxy_url or not proxy_url.strip():
        return True, "بدون پروکسی (اتصال مستقیم)"
    try:
        parsed = urlparse(proxy_url)
        host = parsed.hostname
        port = parsed.port or (1080 if "socks" in (parsed.scheme or "") else 8080)
        if not host:
            return False, "فرمت پروکسی نامعتبر است"
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(timeout)
        result = sock.connect_ex((host, port))
        sock.close()
        if result == 0:
            return True, f"پروکسی {host}:{port} متصل و در دسترس است"
        else:
            return False, f"خطای اتصال به پروکسی {host}:{port} (کد خطا: {result})"
    except Exception as exc:
        return False, f"خطای تست پروکسی: {exc}"


@manage_access_required
def manage_dashboard(request):
    """
    Main Management Dashboard at /manage/
    Displays Scrapers, Senders & Prioritized Leads Queue, and Financial Consumption.
    """
    # 1. Scraper Accounts
    scrapers = InstagramEngineAccount.objects.filter(role="SCRAPER").order_by("-id")
    scrapers_total = scrapers.count()
    scrapers_active = scrapers.filter(status="ACTIVE").count()
    scrapers_cooldown = scrapers.filter(status="COOLDOWN").count()
    scrapers_challenged = scrapers.filter(status="CHALLENGED").count()
    scrapers_banned = scrapers.filter(status="BANNED").count()

    # 2. Sender Accounts & Leads
    senders = InstagramEngineAccount.objects.filter(role="SENDER").order_by("-id")
    senders_total = senders.count()
    senders_active = senders.filter(status="ACTIVE").count()

    # Prioritized Leads: Ordered strictly by intent_score DESC, created_at ASC
    leads = InstagramEngineLead.objects.all().order_by("-intent_score", "created_at")[:60]
    total_leads = InstagramEngineLead.objects.count()
    dms_sent_total = InstagramEngineLead.objects.filter(status="DM_SENT").count()
    high_intent_leads = InstagramEngineLead.objects.filter(intent_score__gte=0.85).count()

    # 3. Settings & Exchange Rate
    billing_settings = BillingSettings.get()
    usd_rate = billing_settings.usd_to_toman or Decimal("268000")
    markup_percent = billing_settings.markup_percent or Decimal("150.0")

    # Scraper latest activity tracking
    latest_scraper = scrapers.filter(last_action_at__isnull=False).order_by("-last_action_at").first()
    latest_scraper_action_at = latest_scraper.last_action_at if latest_scraper else None
    latest_lead = InstagramEngineLead.objects.order_by("-created_at").first()
    latest_lead_at = latest_lead.created_at if latest_lead else None

    # 4. Multi-Platform Leads Matrix for all 3 Tenants
    from apps.discovery.models import Opportunity
    businesses = Business.objects.select_related("wallet", "user").all().order_by("id")

    tenant_matrix = []
    matrix_totals = {
        "instagram": 0,
        "telegram": 0,
        "x": 0,
        "total": 0,
        "high_intent": 0,
        "dms_sent": 0,
    }

    for b in businesses:
        ig_count = Opportunity.objects.filter(business=b, source_platform="instagram").count()
        tg_count = Opportunity.objects.filter(business=b, source_platform="telegram").count()
        x_count = Opportunity.objects.filter(business=b, source_platform="x").count()
        b_total = Opportunity.objects.filter(business=b).count()
        b_high_intent = Opportunity.objects.filter(business=b, ai_analysis__intent_score__gte=0.80).count()
        b_dms = InstagramEngineLead.objects.filter(tenant_id=f"tenant_{b.id}", status="DM_SENT").count()

        tenant_matrix.append({
            "business": b,
            "instagram": ig_count,
            "telegram": tg_count,
            "x": x_count,
            "total": b_total,
            "high_intent": b_high_intent,
            "dms_sent": b_dms,
        })
        matrix_totals["instagram"] += ig_count
        matrix_totals["telegram"] += tg_count
        matrix_totals["x"] += x_count
        matrix_totals["total"] += b_total
        matrix_totals["high_intent"] += b_high_intent
        matrix_totals["dms_sent"] += b_dms

    # 5. Financial & Credit Consumption per Business / Account
    financial_rows = []
    total_balance_all = Decimal("0")
    total_spent_all = Decimal("0")
    total_cost_usd_all = Decimal("0")
    total_raw_toman_all = Decimal("0")
    total_payable_all = Decimal("0")
    total_calls_all = 0

    for b in businesses:
        wallet = getattr(b, "wallet", None)
        balance = wallet.balance_toman if wallet else Decimal("0")
        total_balance_all += balance

        # Aggregated consumption from transactions
        if wallet:
            tx_usage = wallet.transactions.filter(kind="USAGE").aggregate(
                spent=Sum("amount_toman"),
                cost_usd=Sum("cost_usd"),
                calls=Sum("calls"),
            )
            spent = abs(tx_usage["spent"] or Decimal("0"))
            cost_usd = tx_usage["cost_usd"] or Decimal("0")
            calls = tx_usage["calls"] or 0
        else:
            spent = Decimal("0")
            cost_usd = Decimal("0")
            calls = 0

        raw_toman = (cost_usd * usd_rate).quantize(Decimal("1"))
        markup_toman = (raw_toman * (markup_percent / Decimal("100"))).quantize(Decimal("1"))
        total_payable = raw_toman + markup_toman

        total_spent_all += spent
        total_cost_usd_all += cost_usd
        total_raw_toman_all += raw_toman
        total_payable_all += total_payable
        total_calls_all += calls

        # Business associated Instagram leads & senders
        tenant_key = f"tenant_{b.id}"
        b_leads_count = InstagramEngineLead.objects.filter(tenant_id=tenant_key).count()
        b_dms_sent = InstagramEngineLead.objects.filter(tenant_id=tenant_key, status="DM_SENT").count()
        b_senders = InstagramEngineAccount.objects.filter(tenant_id=tenant_key, role="SENDER")

        financial_rows.append({
            "business": b,
            "balance": balance,
            "spent": spent,
            "cost_usd": cost_usd,
            "raw_toman": raw_toman,
            "markup_percent": markup_percent,
            "markup_toman": markup_toman,
            "total_payable": total_payable,
            "calls": calls,
            "leads_count": b_leads_count,
            "dms_sent": b_dms_sent,
            "senders": b_senders,
            "has_credit": balance > 0,
        })

    context = {
        "nav": "manage",
        "scrapers": scrapers,
        "scrapers_total": scrapers_total,
        "scrapers_active": scrapers_active,
        "scrapers_cooldown": scrapers_cooldown,
        "scrapers_challenged": scrapers_challenged,
        "scrapers_banned": scrapers_banned,
        "senders": senders,
        "senders_total": senders_total,
        "senders_active": senders_active,
        "leads": leads,
        "total_leads": total_leads,
        "dms_sent_total": dms_sent_total,
        "high_intent_leads": high_intent_leads,
        "billing_settings": billing_settings,
        "usd_rate": usd_rate,
        "markup_percent": markup_percent,
        "latest_scraper_action_at": latest_scraper_action_at,
        "latest_lead_at": latest_lead_at,
        "tenant_matrix": tenant_matrix,
        "matrix_totals": matrix_totals,
        "financial_rows": financial_rows,
        "total_balance_all": total_balance_all,
        "total_spent_all": total_spent_all,
        "total_cost_usd_all": total_cost_usd_all,
        "total_raw_toman_all": total_raw_toman_all,
        "total_payable_all": total_payable_all,
        "total_calls_all": total_calls_all,
        "businesses": businesses,
        "crawl_executions": InstagramCrawlExecution.objects.all().select_related("business", "account").order_by("-crawled_at")[:60],
        "now": dj_timezone.now(),
    }
    return render(request, "ops/manage_dashboard.html", context)


@manage_access_required
@require_POST
def manage_exchange_rate_update(request):
    """Updates live USD to Toman exchange rate and tenant markup percentage."""
    usd_val = request.POST.get("usd_to_toman", "").strip()
    markup_val = request.POST.get("markup_percent", "").strip()

    s = BillingSettings.get()
    try:
        if usd_val:
            s.usd_to_toman = Decimal(usd_val.replace(",", "").replace("،", ""))
        if markup_val:
            s.markup_percent = Decimal(markup_val.replace("%", "").strip())
        s.save()
        messages.success(request, f"نرخ دلار ({s.usd_to_toman:,.0f} تومان) و کارمزد مستأجرین ({s.markup_percent}٪) با موفقیت به‌روزرسانی شد.")
    except Exception as exc:
        messages.error(request, f"خطا در به‌روزرسانی تنظیمات مالی: {exc}")

    return redirect("manage:dashboard")


def _normalize_digits(s: str) -> str:
    """Converts Persian and Arabic digits to ASCII digits."""
    if not s:
        return ""
    trans = str.maketrans("۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩", "01234567890123456789")
    return s.translate(trans).strip()


@manage_access_required
@require_POST
def manage_account_add(request):
    """
    Adds a new Instagram account (either SCRAPER or SENDER) and attempts immediate live authentication.
    If Instagram triggers an email/SMS verification code or 2FA challenge, captures the challenge state,
    sets status to CHALLENGED, and returns `requires_code: True` for the modal to prompt for the code.
    """
    is_ajax = (
        request.headers.get("x-requested-with") == "XMLHttpRequest"
        or request.content_type == "application/json"
        or "application/json" in request.headers.get("Accept", "")
    )

    username = request.POST.get("username", "").strip().lstrip("@")
    password = request.POST.get("password", "").strip()
    role = request.POST.get("role", "SCRAPER").strip().upper()
    proxy_url = request.POST.get("proxy_url", "").strip() or None
    tenant_id = request.POST.get("tenant_id", "tenant_default").strip()

    if not username or not password:
        err_msg = "نام کاربری و کلمه عبور اینستاگرام الزامی است."
        if is_ajax:
            return JsonResponse({"success": False, "requires_code": False, "message": err_msg})
        messages.error(request, err_msg)
        return redirect("manage:dashboard")

    if role not in ("SCRAPER", "SENDER"):
        role = "SCRAPER"

    # Check if account already exists
    existing = InstagramEngineAccount.objects.filter(username__iexact=username).first()
    if existing:
        if existing.status == "ACTIVE" and existing.session_json:
            err_msg = f"اکانت @{username} قبلاً در سیستم ثبت شده و فعال است."
            if is_ajax:
                return JsonResponse({"success": False, "requires_code": False, "message": err_msg})
            messages.warning(request, err_msg)
            return redirect("manage:dashboard")
        # Update credentials on existing account to allow re-authentication
        account = existing
        account.password = password
        account.role = role
        account.tenant_id = tenant_id
        account.proxy_url = proxy_url
        account.status = "CHALLENGED"
        account.save()
    else:
        account = InstagramEngineAccount.objects.create(
            tenant_id=tenant_id,
            username=username,
            password=password,
            role=role,
            proxy_url=proxy_url,
            status="CHALLENGED",
        )

    # 1. Test Proxy Reachability if specified
    proxy_ok = True
    proxy_msg = ""
    if account.proxy_url:
        proxy_ok, proxy_msg = _test_proxy_connection(account.proxy_url)
        if not proxy_ok:
            account.status = "BANNED"
            account.save(update_fields=["status"])
            err_msg = f"خطای پروکسی: {proxy_msg}"
            if is_ajax:
                return JsonResponse({
                    "success": False,
                    "requires_code": False,
                    "account_id": account.id,
                    "username": account.username,
                    "message": err_msg,
                })
            messages.error(request, err_msg)
            return redirect("manage:dashboard")

    # 2. Attempt real-time Instagram authentication
    try:
        from instagrapi import Client
        from instagrapi.exceptions import (
            ChallengeRequired,
            TwoFactorRequired,
            BadPassword,
            FeedbackRequired,
            PleaseWaitFewMinutes,
        )

        cl = Client()
        if account.proxy_url:
            cl.set_proxy(account.proxy_url)

        def _challenge_detector(u, choice=None):
            raise ChallengeRequired(f"کد تایید به روش {choice or 'ایمیل/پیامک'} ارسال شد.")
        cl.challenge_code_handler = _challenge_detector

        sessionid = (request.POST.get("sessionid") or "").strip()
        if sessionid:
            logger.info(f"Attempting direct sessionid login for @{account.username}...")
            try:
                cl.login_by_sessionid(sessionid)
                settings = cl.get_settings()
                account.session_json = settings
                account.device_settings = {"settings": settings}
                account.status = "ACTIVE"
                account.save(update_fields=["session_json", "device_settings", "status"])
                role_fa = "اسکرپر" if role == "SCRAPER" else "فرستنده / پاسخگو"
                succ_msg = f"اکانت @{account.username} با موفقیت توسط کوکی sessionid متصل و فعال شد!"
                if is_ajax:
                    return JsonResponse({
                        "success": True,
                        "requires_code": False,
                        "status": "ACTIVE",
                        "account_id": account.id,
                        "username": account.username,
                        "message": succ_msg,
                    })
                messages.success(request, succ_msg)
                return redirect("manage:dashboard")
            except Exception as se:
                logger.warning(f"sessionid login failed: {se}")

        logger.info(f"Attempting live Instagram login for newly added account @{account.username}...")
        try:
            cl.login(account.username, account.password)
            # Verified and logged in successfully
            settings = cl.get_settings()
            account.session_json = settings
            account.device_settings = {"settings": settings}
            account.status = "ACTIVE"
            account.save(update_fields=["session_json", "device_settings", "status"])

            role_fa = "اسکرپر" if role == "SCRAPER" else "فرستنده / پاسخگو"
            succ_msg = f"اکانت @{account.username} با نقش «{role_fa}» با موفقیت متصل، احراز هویت و فعال شد!"
            if is_ajax:
                return JsonResponse({
                    "success": True,
                    "requires_code": False,
                    "status": "ACTIVE",
                    "account_id": account.id,
                    "username": account.username,
                    "message": succ_msg,
                })
            messages.success(request, succ_msg)
            return redirect("manage:dashboard")

        except (ChallengeRequired, TwoFactorRequired) as ce:
            account.status = "CHALLENGED"
            account.device_settings = {
                "settings": cl.get_settings(),
                "last_json": getattr(cl, "last_json", None),
                "challenge_hint": str(ce),
            }
            account.save(update_fields=["status", "device_settings"])
            warn_msg = f"اینستاگرام کد تایید امنیتی به ایمیل یا پیامک اکانت ارسال کرد. لطفاً کد را وارد کنید."
            if is_ajax:
                return JsonResponse({
                    "success": False,
                    "requires_code": True,
                    "account_id": account.id,
                    "username": account.username,
                    "message": "کد تایید امنیتی اینستاگرام به ایمیل یا شماره موبایل اکانت ارسال شد. لطفاً کد ۶ رقمی را وارد نمایید:",
                })
            messages.warning(request, warn_msg)
            return redirect("manage:dashboard")

        except (FeedbackRequired, PleaseWaitFewMinutes) as fe:
            account.status = "COOLDOWN"
            account.save(update_fields=["status"])
            err_msg = f"اینستاگرام محدودیت موقت (Cooldown) اعمال کرده است: {fe}"
            if is_ajax:
                return JsonResponse({
                    "success": False,
                    "requires_code": False,
                    "account_id": account.id,
                    "username": account.username,
                    "message": err_msg,
                })
            messages.error(request, err_msg)
            return redirect("manage:dashboard")

        except BadPassword as bpe:
            account.status = "BANNED"
            account.save(update_fields=["status"])
            err_msg = f"نام کاربری یا کلمه عبور اینستاگرام اشتباه است: {bpe}"
            if is_ajax:
                return JsonResponse({
                    "success": False,
                    "requires_code": False,
                    "account_id": account.id,
                    "username": account.username,
                    "message": err_msg,
                })
            messages.error(request, err_msg)
            return redirect("manage:dashboard")

        except Exception as le:
            le_str = str(le).lower()
            if any(k in le_str for k in ["challenge", "checkpoint", "two_step", "two_factor", "two-factor", "verification", "sms", "code"]):
                account.status = "CHALLENGED"
                account.device_settings = {
                    "settings": cl.get_settings(),
                    "last_json": getattr(cl, "last_json", None),
                    "challenge_hint": str(le),
                }
                account.save(update_fields=["status", "device_settings"])
                if is_ajax:
                    return JsonResponse({
                        "success": False,
                        "requires_code": True,
                        "account_id": account.id,
                        "username": account.username,
                        "message": "اینستاگرام درخواست کد تایید هویت (ایمیل/پیامک) داده است. لطفاً کد را وارد کنید:",
                    })
                messages.warning(request, "اینستاگرام درخواست کد تایید هویت داده است.")
                return redirect("manage:dashboard")
            else:
                account.status = "BANNED"
                account.save(update_fields=["status"])
                err_msg = f"خطای ورود به اینستاگرام: {le}"
                if is_ajax:
                    return JsonResponse({
                        "success": False,
                        "requires_code": False,
                        "account_id": account.id,
                        "username": account.username,
                        "message": err_msg,
                    })
                messages.error(request, err_msg)
                return redirect("manage:dashboard")

    except ImportError:
        account.status = "ACTIVE"
        account.save(update_fields=["status"])
        role_fa = "اسکرپر" if role == "SCRAPER" else "فرستنده / پاسخگو"
        succ_msg = f"اکانت @{username} با نقش «{role_fa}» ثبت شد (محیط بدون instagrapi)."
        if is_ajax:
            return JsonResponse({
                "success": True,
                "requires_code": False,
                "status": "ACTIVE",
                "account_id": account.id,
                "username": account.username,
                "message": succ_msg,
            })
        messages.success(request, succ_msg)
        return redirect("manage:dashboard")

    except Exception as exc:
        err_msg = f"خطای غیرمنتظره در ثبت اکانت: {exc}"
        if is_ajax:
            return JsonResponse({
                "success": False,
                "requires_code": False,
                "account_id": account.id,
                "username": account.username,
                "message": err_msg,
            })
        messages.error(request, err_msg)
        return redirect("manage:dashboard")


@manage_access_required
@require_POST
def manage_account_delete(request, pk):
    """Deletes an Instagram account."""
    account = get_object_or_404(InstagramEngineAccount, pk=pk)
    username = account.username
    account.delete()
    messages.success(request, f"اکانت @{username} با موفقیت از سیستم حذف گردید.")
    return redirect("manage:dashboard")


@manage_access_required
@require_POST
def manage_account_set_session(request, pk):
    """
    Directly updates or injects a valid Instagram sessionid cookie from a web browser.
    This bypasses username/password authentication hurdles and security challenges.
    """
    account = get_object_or_404(InstagramEngineAccount, pk=pk)
    sessionid = (request.POST.get("sessionid") or "").strip()
    if not sessionid:
        return JsonResponse({"success": False, "message": "لطفاً مقدار کوکی sessionid را وارد نمایید."})

    from instagrapi import Client
    cl = Client()
    if account.proxy_url:
        cl.set_proxy(account.proxy_url)

    if account.session_json:
        try:
            cl.set_settings(account.session_json)
        except Exception:
            pass

    try:
        cl.login_by_sessionid(sessionid)
        settings = cl.get_settings()
        account.session_json = settings
        account.device_settings = {"settings": settings}
        account.status = "ACTIVE"
        account.save(update_fields=["session_json", "device_settings", "status"])
        return JsonResponse({
            "success": True,
            "status": "ACTIVE",
            "message": f"سشن اکانت @{account.username} با موفقیت ثبت شد و اکانت فعال گردید (بدون چالش پسورد).",
        })
    except Exception as exc:
        return JsonResponse({
            "success": False,
            "status": account.status,
            "message": f"خطا در اعتبارسنجی sessionid: {exc}",
        })


@manage_access_required
@require_POST
def manage_account_test(request, pk):
    """
    Performs a real-time connectivity and status health check on an account.
    Returns JSON response for instant asynchronous UI update.
    """
    account = get_object_or_404(InstagramEngineAccount, pk=pk)

    # 1. Test Proxy Reachability
    proxy_ok = True
    proxy_msg = ""
    if account.proxy_url:
        proxy_ok, proxy_msg = _test_proxy_connection(account.proxy_url)
        if not proxy_ok:
            return JsonResponse({
                "success": False,
                "status": account.status,
                "message": f"خطای پروکسی: {proxy_msg}",
            })

    # 2. Check Cooldown
    now = dj_timezone.now()
    if account.cooldown_until and account.cooldown_until > now:
        rem_seconds = int((account.cooldown_until - now).total_seconds())
        rem_min = rem_seconds // 60
        return JsonResponse({
            "success": True,
            "status": "COOLDOWN",
            "message": f"اکانت در دوره استراحت است (حدود {rem_min} دقیقه باقی‌مانده)",
        })

    # 3. Live Instagram Login / Session Test
    live_status = account.status
    msg = "اتصال و وضعیت اکانت با موفقیت تایید شد."
    challenge_hint = ""
    try:
        from instagrapi import Client
        from instagrapi.exceptions import ChallengeRequired, FeedbackRequired, BadPassword, TwoFactorRequired
        cl = Client()
        if account.proxy_url:
            cl.set_proxy(account.proxy_url)

        def _non_interactive_challenge(username, choice=None):
            raise ChallengeRequired(f"کد تایید امنیتی به روش {choice} ارسال شد.")
        cl.challenge_code_handler = _non_interactive_challenge

        if account.session_json:
            cl.set_settings(account.session_json)
            try:
                cl.get_timeline_feed()
                live_status = "ACTIVE"
                account.status = "ACTIVE"
                account.save(update_fields=["status"])
                msg = f"سشن اینستاگرام معتبر است و ارتباط با موفقیت برقرار شد. {proxy_msg}"
            except Exception as se:
                msg = f"سشن منقضی شده، تلاش برای لاگین مجدد: {se}"
                try:
                    cl.login(account.username, account.password)
                    new_settings = cl.get_settings()
                    account.session_json = new_settings
                    account.device_settings = {"settings": new_settings}
                    account.status = "ACTIVE"
                    account.save(update_fields=["session_json", "device_settings", "status"])
                    msg = f"لاگین مجدد با موفقیت انجام شد و سشن به‌روزرسانی شد. {proxy_msg}"
                except (ChallengeRequired, TwoFactorRequired) as ce:
                    live_status = "CHALLENGED"
                    account.status = "CHALLENGED"
                    account.device_settings = {
                        "settings": cl.get_settings(),
                        "last_json": getattr(cl, "last_json", None),
                        "challenge_hint": str(ce),
                    }
                    account.save(update_fields=["status", "device_settings"])
                    challenge_hint = str(ce)
                    msg = f"اینستاگرام درخواست کد تایید داده است: {ce}"
                except (FeedbackRequired, PleaseWaitFewMinutes) as fe:
                    live_status = "COOLDOWN"
                    account.status = "COOLDOWN"
                    account.cooldown_until = dj_timezone.now() + timedelta(minutes=15)
                    account.save(update_fields=["status", "cooldown_until"])
                    msg = f"اینستاگرام محدودیت موقت اعمال کرده است (Cooldown / چند دقیقه استراحت): {fe}"
                except Exception as le:
                    le_str = str(le).lower()
                    if "pleasewaitfewminutes" in le_str or "429" in le_str or "feedback" in le_str:
                        live_status = "COOLDOWN"
                        account.cooldown_until = dj_timezone.now() + timedelta(minutes=15)
                        msg = "محدودیت موقت اینستاگرام (PleaseWaitFewMinutes). اکانت برای چند دقیقه در حالت استراحت قرار گرفت."
                    elif "suspended" in le_str:
                        live_status = "CHALLENGED"
                        msg = "اکانت توسط اینستاگرام به صفحه تعلیق (Suspended) منتقل شده است و نیاز به تایید هویت در مرورگر دارد."
                        account.device_settings = {
                            "settings": cl.get_settings(),
                            "last_json": getattr(cl, "last_json", None),
                            "challenge_hint": str(le),
                        }
                    elif "challenge" in le_str or "checkpoint" in le_str or "two_step" in le_str:
                        live_status = "CHALLENGED"
                        msg = "اینستاگرام درخواست تایید هویت (کد پیامک/ایمیل یا ۲FA) داده است."
                        account.device_settings = {
                            "settings": cl.get_settings(),
                            "last_json": getattr(cl, "last_json", None),
                            "challenge_hint": str(le),
                        }
                    elif "bad_password" in le_str or "password" in le_str:
                        live_status = "BANNED"
                        msg = f"رمز عبور اینستاگرام اشتباه است: {le}"
                    else:
                        live_status = "COOLDOWN"
                        msg = f"پاسخ ناپایدار از اینستاگرام (استراحت موقت): {le}"
                    account.status = live_status
                    account.save(update_fields=["status", "device_settings", "cooldown_until"] if account.cooldown_until else ["status", "device_settings"])
        else:
            # First-time login test
            try:
                cl.login(account.username, account.password)
                new_settings = cl.get_settings()
                account.session_json = new_settings
                account.device_settings = {"settings": new_settings}
                account.status = "ACTIVE"
                account.save(update_fields=["session_json", "device_settings", "status"])
                msg = f"ورود اولیه با موفقیت انجام و سشن ذخیره گردید. {proxy_msg}"
            except (ChallengeRequired, TwoFactorRequired) as ce:
                live_status = "CHALLENGED"
                account.status = "CHALLENGED"
                account.device_settings = {
                    "settings": cl.get_settings(),
                    "last_json": getattr(cl, "last_json", None),
                    "challenge_hint": str(ce),
                }
                account.save(update_fields=["status", "device_settings"])
                challenge_hint = str(ce)
                msg = f"اینستاگرام کد تایید امنیتی (ایمیل/پیامک) درخواست کرده است."
            except (FeedbackRequired, PleaseWaitFewMinutes) as fe:
                live_status = "COOLDOWN"
                account.status = "COOLDOWN"
                account.cooldown_until = dj_timezone.now() + timedelta(minutes=15)
                account.save(update_fields=["status", "cooldown_until"])
                msg = f"اینستاگرام محدودیت موقت چند دقیقه‌ای اعمال کرده است (Cooldown): {fe}"
            except Exception as le:
                le_str = str(le).lower()
                if "pleasewaitfewminutes" in le_str or "429" in le_str or "feedback" in le_str:
                    live_status = "COOLDOWN"
                    account.cooldown_until = dj_timezone.now() + timedelta(minutes=15)
                    msg = "اینستاگرام محدودیت موقت اعمال کرده است (PleaseWaitFewMinutes)."
                elif "suspended" in le_str:
                    live_status = "CHALLENGED"
                    msg = "اکانت توسط اینستاگرام به صفحه تعلیق (Suspended) منتقل شده است."
                elif "challenge" in le_str or "checkpoint" in le_str or "two_step" in le_str:
                    live_status = "CHALLENGED"
                    account.device_settings = {
                        "settings": cl.get_settings(),
                        "last_json": getattr(cl, "last_json", None),
                        "challenge_hint": str(le),
                    }
                    msg = "اینستاگرام چالش امنیتی (کد تایید) درخواست کرده است."
                elif "bad_password" in le_str or "password" in le_str:
                    live_status = "BANNED"
                    msg = f"کلمه عبور اشتباه است: {le}"
                else:
                    live_status = "COOLDOWN"
                    msg = f"خطای ورود اولیه (استراحت موقت): {le}"
                account.status = live_status
                account.save(update_fields=["status", "device_settings", "cooldown_until"] if account.cooldown_until else ["status", "device_settings"])

    except ImportError:
        if proxy_ok:
            live_status = account.status
            msg = f"پروکسی و مشخصات ذخیره‌شده بررسی شد (پروکسی متصل است). {proxy_msg}"
        else:
            live_status = "BANNED"
            msg = f"پروکسی قطع می‌باشد: {proxy_msg}"

    except Exception as exc:
        msg = f"خطای غیرمنتظره در بررسی وضعیت: {exc}"

    return JsonResponse({
        "success": live_status == "ACTIVE",
        "status": account.status,
        "challenge_required": account.status == "CHALLENGED",
        "challenge_hint": challenge_hint,
        "message": msg,
    })


@manage_access_required
@require_POST
def manage_account_submit_code(request, pk):
    """
    Submits a 2FA or email/SMS verification code for an account in CHALLENGED status.
    Resolves the challenge, verifies session with timeline feed, saves valid session_json,
    and updates status to ACTIVE.
    """
    account = get_object_or_404(InstagramEngineAccount, pk=pk)
    
    # Accept JSON or form POST
    raw_code = ""
    if request.content_type == "application/json":
        try:
            body = json.loads(request.body.decode("utf-8"))
            raw_code = str(body.get("code", "")).strip()
        except Exception:
            pass
    if not raw_code:
        raw_code = request.POST.get("code", "").strip()

    code = _normalize_digits(raw_code).replace(" ", "").replace("-", "")

    if not code:
        return JsonResponse({"success": False, "message": "لطفاً کد تایید را وارد کنید."})

    try:
        from instagrapi import Client
        cl = Client()
        if account.proxy_url:
            cl.set_proxy(account.proxy_url)

        saved_data = account.device_settings or {}
        saved_settings = (
            saved_data.get("settings")
            if isinstance(saved_data, dict) and "settings" in saved_data
            else saved_data
        )
        last_json = saved_data.get("last_json") if isinstance(saved_data, dict) else None

        if saved_settings and isinstance(saved_settings, dict):
            try:
                cl.set_settings(saved_settings)
            except Exception as se:
                logger.warning(f"Could not restore settings for @{account.username}: {se}")

        # Set handler to return user-entered code
        cl.challenge_code_handler = lambda u, c=None: code

        success = False
        last_error = None

        # 1. First attempt: If last_json was saved with a pending contact challenge
        if last_json and isinstance(last_json, dict) and "challenge" in last_json:
            try:
                logger.info(f"Attempting challenge_resolve from saved last_json for @{account.username} with code {code}...")
                cl.last_json = last_json
                if cl.challenge_resolve(last_json):
                    success = True
                    logger.info(f"challenge_resolve succeeded for @{account.username}")
            except Exception as ce1:
                last_error = ce1
                logger.warning(f"challenge_resolve using saved last_json failed: {ce1}")

        # 2. Second attempt: Direct login with verification_code (handles Bloks 2FA / TOTP / SMS 2FA)
        if not success:
            try:
                logger.info(f"Attempting cl.login(verification_code='{code}') for @{account.username}...")
                cl.login(account.username, account.password, verification_code=code)
                success = True
                logger.info(f"cl.login with verification_code succeeded for @{account.username}")
            except Exception as le:
                last_error = le
                logger.warning(f"cl.login with verification_code failed: {le}")

                # 3. Third attempt: If login triggered challenge_required and cl.last_json has challenge
                cl_last = getattr(cl, "last_json", None)
                if isinstance(cl_last, dict) and "challenge" in cl_last:
                    try:
                        logger.info(f"Attempting challenge_resolve with newly received challenge for @{account.username}...")
                        if cl.challenge_resolve(cl_last):
                            success = True
                            logger.info(f"challenge_resolve with new challenge succeeded for @{account.username}")
                    except Exception as ce2:
                        last_error = ce2
                        logger.warning(f"challenge_resolve retry failed: {ce2}")

        if not success:
            err_str = str(last_error or "کد تایید وارد شده نامعتبر یا منقضی است.")
            return JsonResponse({
                "success": False,
                "status": account.status,
                "message": f"خطا در تایید کد امنیتی: {err_str}",
            })

        # Successful verification -> verify feed and save session
        try:
            cl.get_timeline_feed()
        except Exception as fe:
            logger.debug(f"Feed test after verification: {fe}")

        new_settings = cl.get_settings()
        account.session_json = new_settings
        account.device_settings = {"settings": new_settings}
        account.status = "ACTIVE"
        account.save(update_fields=["session_json", "device_settings", "status"])

        return JsonResponse({
            "success": True,
            "status": "ACTIVE",
            "message": f"اکانت @{account.username} با موفقیت تایید و سشن دائمی آن فعال شد!",
        })
    except Exception as exc:
        logger.warning(f"Failed verification for @{account.username}: {exc}")
        return JsonResponse({
            "success": False,
            "status": account.status,
            "message": f"خطا در ثبت کد تایید: {exc}",
        })


@manage_access_required
@require_GET
def manage_leads_export_csv(request):
    """
    Exports all extracted leads as a UTF-8 BOM CSV dataset.
    """
    import csv
    from django.http import HttpResponse

    leads = InstagramEngineLead.objects.all().order_by("-intent_score", "-created_at")

    response = HttpResponse(content_type="text/csv; charset=utf-8-sig")
    response["Content-Disposition"] = 'attachment; filename="instagram_leads_dataset.csv"'
    response.write('\ufeff')

    writer = csv.writer(response)
    writer.writerow([
        "شناسه عددی کاربر (IG PK)",
        "نام کاربری (Username)",
        "نام کامل (Full Name)",
        "لینک پروفایل (Profile URL)",
        "متن کامنت خریدار (Comment)",
        "لینک پست مبدا (Source Post URL)",
        "خلاصه کپشن پست (Post Caption)",
        "کال‌تو‌اکشن منطبق (Trigger / CTA)",
        "ضریب احتمال خرید (Intent Score)",
        "وضعیت (Status)",
        "مستأجر / فروشگاه (Tenant ID)",
        "زمان کشف (Created At)",
    ])

    for l in leads:
        writer.writerow([
            l.ig_user_pk,
            l.username,
            l.full_name or "",
            l.profile_url or f"https://www.instagram.com/{l.username}/",
            l.comment_text,
            l.source_post_url or "",
            l.post_caption_snippet or "",
            l.matched_cta or "",
            f"{l.intent_score:.2f}",
            l.get_status_display(),
            l.tenant_id,
            l.created_at.strftime("%Y-%m-%d %H:%M:%S") if l.created_at else "",
        ])

    return response


@manage_access_required
@require_GET
def manage_leads_export_json(request):
    """
    Exports all extracted leads as a JSON dataset.
    """
    leads = InstagramEngineLead.objects.all().order_by("-intent_score", "-created_at")
    data = []
    for l in leads:
        data.append({
            "ig_user_pk": l.ig_user_pk,
            "username": l.username,
            "full_name": l.full_name,
            "profile_url": l.profile_url or f"https://www.instagram.com/{l.username}/",
            "comment_text": l.comment_text,
            "source_post_url": l.source_post_url,
            "post_caption_snippet": l.post_caption_snippet,
            "matched_cta": l.matched_cta,
            "intent_score": l.intent_score,
            "status": l.status,
            "tenant_id": l.tenant_id,
            "created_at": l.created_at.isoformat() if l.created_at else None,
        })

    response = JsonResponse(data, safe=False, json_dumps_params={"ensure_ascii": False, "indent": 2})
    response["Content-Disposition"] = 'attachment; filename="instagram_leads_dataset.json"'
    return response


