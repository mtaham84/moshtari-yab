"""X (Twitter) scraper accounts on the /manage/ dashboard: add, delete, test, set session, toggle."""
import logging

from django.contrib import messages
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect
from django.views.decorators.http import require_POST


from apps.discovery.models import (
    TwitterEngineAccount
)
from apps.core.http import wants_json
from apps.discovery.proxies import urls as proxy_urls

from apps.billing.manage.access import manage_access_required
from apps.billing.manage.x_session import XSessionValidationResult, _test_twitter_account_connection

logger = logging.getLogger("management")


@manage_access_required
@require_POST
def manage_twitter_account_add(request):
    is_ajax = wants_json(request)
    username = request.POST.get("username", "").strip().lstrip("@")
    password = request.POST.get("password", "").strip()
    auth_token = request.POST.get("auth_token", "").strip()
    ct0 = request.POST.get("ct0", "").strip()
    proxy_url = request.POST.get("proxy_url", "").strip() or proxy_urls.default_proxy_url() or "socks5h://xray:1080"

    if not username:
        err = "نام کاربری توییتر الزامی است."
        if is_ajax:
            return JsonResponse({"success": False, "message": err})
        messages.error(request, err)
        return redirect("manage:dashboard")

    if not auth_token:
        account, created = TwitterEngineAccount.objects.update_or_create(
            username=username,
            defaults={
                "password": password,
                "auth_token": "",
                "ct0": ct0,
                "proxy_url": proxy_url,
                "status": "CHALLENGED",
                "status_message": "نیازمند سشن (auth_token)",
                "is_active": True,
            }
        )
        msg = "اکانت توییتر ثبت شد ولی سشن (auth_token) وارد نشده است؛ وضعیت روی چالش قرار گرفت."
        if is_ajax:
            return JsonResponse({"success": False, "account_id": account.id, "username": username, "status": "CHALLENGED", "message": msg})
        messages.warning(request, msg)
        return redirect("manage:dashboard")

    account, created = TwitterEngineAccount.objects.update_or_create(
        username=username,
        defaults={
            "password": password,
            "auth_token": auth_token,
            "ct0": ct0,
            "proxy_url": proxy_url,
            "status": "CHALLENGED",
            "status_message": "در انتظار اعتبارسنجی",
            "is_active": True,
        }
    )

    ok, st, msg = _test_twitter_account_connection(account)
    if not ok:
        warn_msg = f"اکانت توییتر @{username} ثبت شد ولی اعتبارسنجی سشن تایید نشد ({msg})."
        if is_ajax:
            return JsonResponse({"success": False, "deleted": False, "account_id": account.id, "username": username, "status": st, "message": warn_msg})
        messages.warning(request, warn_msg)
        return redirect("manage:dashboard")

    succ_msg = f"اکانت توییتر @{username} با موفقیت ثبت شد. ({msg})"
    if is_ajax:
        return JsonResponse({"success": ok, "account_id": account.id, "username": username, "status": st, "message": succ_msg})
    messages.success(request, succ_msg)
    return redirect("manage:dashboard")


@manage_access_required
@require_POST
def manage_twitter_account_delete(request, pk):
    account = get_object_or_404(TwitterEngineAccount, pk=pk)
    uname = account.username
    account.delete()
    messages.success(request, f"اکانت توییتر @{uname} با موفقیت حذف گردید.")
    return redirect("manage:dashboard")


@manage_access_required
@require_POST
def manage_twitter_account_test(request, pk):
    account = get_object_or_404(TwitterEngineAccount, pk=pk)
    ok, status, message = _test_twitter_account_connection(account)
    return JsonResponse({
        "success": ok,
        "status": status,
        "status_display": account.get_status_display(),
        "message": message,
        "last_action_at": account.last_action_at.isoformat() if account.last_action_at else None,
    })


@manage_access_required
@require_POST
def manage_twitter_account_set_session(request, pk):
    account = get_object_or_404(TwitterEngineAccount, pk=pk)
    auth_token = request.POST.get("auth_token", "").strip()
    ct0 = request.POST.get("ct0", "").strip()
    password = request.POST.get("password", "").strip()

    # Rule 2: If auth_token is missing, return INVALID without making an HTTP request
    if not auth_token:
        return JsonResponse({
            "success": False,
            "deleted": False,
            "status": account.status,
            "status_display": account.get_status_display(),
            "message": "کوکی auth_token الزامی است و سشن بدون آن نامعتبر است.",
        })

    old_auth_token = account.auth_token
    old_ct0 = account.ct0
    old_password = account.password
    old_status = account.status

    if password:
        account.password = password
    account.auth_token = auth_token
    account.ct0 = ct0

    ok, status, message = _test_twitter_account_connection(account)
    validation_result = getattr(account, "_validation_result", None)
    if validation_result is None:
        validation_result = XSessionValidationResult.VALID if ok else XSessionValidationResult.INVALID

    if validation_result == XSessionValidationResult.VALID:
        account.status = "ACTIVE"
        account.status_message = message
        # account.ct0 was updated to effective_ct0 by _test_twitter_account_connection
        account.save()
        return JsonResponse({
            "success": True,
            "status": "ACTIVE",
            "status_display": account.get_status_display(),
            "message": message,
        })
    elif validation_result == XSessionValidationResult.INVALID:
        account.status = "CHALLENGED"
        account.status_message = message
        # Rule 4: a generated ct0 is not saved unless the result is VALID
        if not ct0:
            account.ct0 = old_ct0
        account.save()
        return JsonResponse({
            "success": False,
            "deleted": False,
            "status": "CHALLENGED",
            "status_display": account.get_status_display(),
            "message": f"{message} (سشن رد شد و اکانت در وضعیت چالش قرار گرفت).",
        })
    else:
        # Inconclusive (FORBIDDEN_UNKNOWN, ENDPOINT_ERROR, RATE_LIMITED, NETWORK_ERROR):
        # Keep previous state and credentials
        account.auth_token = old_auth_token
        account.ct0 = old_ct0
        account.password = old_password
        account.status = old_status
        account.status_message = message
        account.save()
        inconclusive_msg = f"اعتبارسنجی سشن نامشخص بود ({message}). وضعیت اکانت تغییر نکرد و سشن قبلی حفظ شد."
        return JsonResponse({
            "success": False,
            "deleted": False,
            "inconclusive": True,
            "status": old_status,
            "status_display": account.get_status_display(),
            "message": inconclusive_msg,
        })


@manage_access_required
@require_POST
def manage_twitter_account_toggle(request, pk):
    account = get_object_or_404(TwitterEngineAccount, pk=pk)
    account.is_active = not account.is_active
    account.save(update_fields=["is_active"])
    return JsonResponse({
        "success": True,
        "is_active": account.is_active,
        "message": f"وضعیت اکانت @{account.username} به {'فعال' if account.is_active else 'غیرفعال'} تغییر یافت.",
    })
