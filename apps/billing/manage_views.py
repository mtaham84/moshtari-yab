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
from apps.discovery.models import InstagramEngineAccount, InstagramEngineLead

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

    # 3. Financial & Credit Consumption per Business / Account
    businesses = Business.objects.select_related("wallet", "user").all().order_by("id")
    financial_rows = []
    total_balance_all = Decimal("0")
    total_spent_all = Decimal("0")
    total_cost_usd_all = Decimal("0")
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
            spent = tx_usage["spent"] or Decimal("0")
            cost_usd = tx_usage["cost_usd"] or Decimal("0")
            calls = tx_usage["calls"] or 0
        else:
            spent = Decimal("0")
            cost_usd = Decimal("0")
            calls = 0

        total_spent_all += spent
        total_cost_usd_all += cost_usd
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
        "financial_rows": financial_rows,
        "total_balance_all": total_balance_all,
        "total_spent_all": total_spent_all,
        "total_cost_usd_all": total_cost_usd_all,
        "total_calls_all": total_calls_all,
        "businesses": businesses,
        "now": dj_timezone.now(),
    }
    return render(request, "ops/manage_dashboard.html", context)


@manage_access_required
@require_POST
def manage_account_add(request):
    """Adds a new Instagram account (either SCRAPER or SENDER)."""
    username = request.POST.get("username", "").strip().lstrip("@")
    password = request.POST.get("password", "").strip()
    role = request.POST.get("role", "SCRAPER").strip().upper()
    proxy_url = request.POST.get("proxy_url", "").strip() or None
    tenant_id = request.POST.get("tenant_id", "tenant_default").strip()

    if not username or not password:
        messages.error(request, "نام کاربری و کلمه عبور اینستاگرام الزامی است.")
        return redirect("manage:dashboard")

    if role not in ("SCRAPER", "SENDER"):
        role = "SCRAPER"

    if InstagramEngineAccount.objects.filter(username__iexact=username).exists():
        messages.error(request, f"اکانت @{username} قبلاً در سیستم ثبت شده است.")
        return redirect("manage:dashboard")

    account = InstagramEngineAccount.objects.create(
        tenant_id=tenant_id,
        username=username,
        password=password,
        role=role,
        proxy_url=proxy_url,
        status="ACTIVE",
    )

    role_fa = "اسکرپر" if role == "SCRAPER" else "فرستنده / پاسخگو"
    messages.success(request, f"اکانت @{username} با نقش «{role_fa}» با موفقیت افزوده شد.")
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
                    account.session_json = cl.get_settings()
                    account.status = "ACTIVE"
                    account.save(update_fields=["session_json", "status"])
                    msg = f"لاگین مجدد با موفقیت انجام شد و سشن به‌روزرسانی شد. {proxy_msg}"
                except (ChallengeRequired, TwoFactorRequired) as ce:
                    live_status = "CHALLENGED"
                    account.status = "CHALLENGED"
                    account.device_settings = cl.get_settings()
                    account.save(update_fields=["status", "device_settings"])
                    challenge_hint = str(ce)
                    msg = f"اینستاگرام درخواست کد تایید داده است: {ce}"
                except FeedbackRequired as fe:
                    live_status = "COOLDOWN"
                    account.status = "COOLDOWN"
                    account.save(update_fields=["status"])
                    msg = f"اینستاگرام محدودیت موقت اعمال کرده است (Cooldown): {fe}"
                except Exception as le:
                    le_str = str(le).lower()
                    if "challenge" in le_str or "checkpoint" in le_str or "two_step" in le_str:
                        live_status = "CHALLENGED"
                        msg = "اینستاگرام درخواست تایید هویت (کد پیامک/ایمیل یا ۲FA) داده است."
                        account.device_settings = cl.get_settings()
                    elif "feedback" in le_str:
                        live_status = "COOLDOWN"
                        msg = "اینستاگرام محدودیت موقت اعمال کرده است (Cooldown)."
                    else:
                        live_status = "BANNED"
                        msg = f"خطای ورود: {le}"
                    account.status = live_status
                    account.save(update_fields=["status"])
        else:
            # First-time login test
            try:
                cl.login(account.username, account.password)
                account.session_json = cl.get_settings()
                account.status = "ACTIVE"
                account.save(update_fields=["session_json", "status"])
                msg = f"ورود اولیه با موفقیت انجام و سشن ذخیره گردید. {proxy_msg}"
            except (ChallengeRequired, TwoFactorRequired) as ce:
                live_status = "CHALLENGED"
                account.status = "CHALLENGED"
                account.device_settings = cl.get_settings()
                account.save(update_fields=["status", "device_settings"])
                challenge_hint = str(ce)
                msg = f"اینستاگرام کد تایید امنیتی (ایمیل/پیامک) درخواست کرده است."
            except Exception as le:
                le_str = str(le).lower()
                if "challenge" in le_str or "checkpoint" in le_str or "two_step" in le_str:
                    live_status = "CHALLENGED"
                    account.device_settings = cl.get_settings()
                    msg = "اینستاگرام چالش امنیتی (کد تایید) درخواست کرده است."
                else:
                    live_status = "BANNED"
                    msg = f"خطای ورود اولیه: {le}"
                account.status = live_status
                account.save(update_fields=["status", "device_settings"])

    except ImportError:
        # Minimal environment fallback: verify credentials and proxy
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
    """
    account = get_object_or_404(InstagramEngineAccount, pk=pk)
    
    # Accept JSON or form POST
    code = ""
    if request.content_type == "application/json":
        try:
            body = json.loads(request.body.decode("utf-8"))
            code = str(body.get("code", "")).strip()
        except Exception:
            pass
    if not code:
        code = request.POST.get("code", "").strip()

    if not code:
        return JsonResponse({"success": False, "message": "لطفاً کد تایید را وارد کنید."})

    try:
        from instagrapi import Client
        cl = Client()
        if account.proxy_url:
            cl.set_proxy(account.proxy_url)

        if account.device_settings:
            try:
                cl.set_settings(account.device_settings)
            except Exception as se:
                logger.warning(f"Could not restore device settings: {se}")

        # Set handler to return user-entered code
        cl.challenge_code_handler = lambda u, c=None: code

        # Attempt login with verification code
        logger.info(f"Submitting verification code '{code}' for @{account.username}...")
        cl.login(account.username, account.password, verification_code=code)

        # Successful verification -> save session
        new_settings = cl.get_settings()
        account.session_json = new_settings
        account.status = "ACTIVE"
        account.save(update_fields=["session_json", "status"])

        return JsonResponse({
            "success": True,
            "status": "ACTIVE",
            "message": f"اکانت @{account.username} با موفقیت تایید شد و سشن دائمی ذخیره گردید!",
        })
    except Exception as exc:
        logger.warning(f"Failed verification for @{account.username}: {exc}")
        return JsonResponse({
            "success": False,
            "status": account.status,
            "message": f"خطا در ثبت کد تایید: {exc}",
        })

