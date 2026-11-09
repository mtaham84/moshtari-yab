"""Checks whether an X (Twitter) session cookie still works, through the account settings endpoint."""
import json
import logging
from datetime import timedelta
from enum import Enum
from typing import Any

try:
    from curl_cffi import requests
except ImportError:
    import requests

from django.utils import timezone as dj_timezone


from apps.discovery.models import (
    TwitterEngineAccount
)
from apps.discovery.proxies import health


logger = logging.getLogger("management")


TWITTER_SETTINGS_ENDPOINT = "https://x.com/i/api/1.1/account/settings.json"


class XSessionValidationResult(str, Enum):
    VALID = "VALID"
    INVALID = "INVALID"
    FORBIDDEN_UNKNOWN = "FORBIDDEN_UNKNOWN"
    ENDPOINT_ERROR = "ENDPOINT_ERROR"
    RATE_LIMITED = "RATE_LIMITED"
    NETWORK_ERROR = "NETWORK_ERROR"


def _get_or_generate_ct0(ct0: str | None) -> tuple[str, bool]:
    """
    Returns (ct0, was_generated). If ct0 is missing or empty, generates
    fallback ct0 ("a" * 32) matching original Twitter session behavior.
    """
    cleaned = (ct0 or "").strip()
    if cleaned:
        return cleaned, False
    return "a" * 32, True


def _extract_response_ct0(response: Any) -> str | None:
    """Extracts new ct0 cookie from response if set by server."""
    if response is None:
        return None
    cookies = getattr(response, "cookies", None)
    if cookies is not None:
        try:
            if hasattr(cookies, "get"):
                c = cookies.get("ct0")
                if isinstance(c, str) and c.strip():
                    return c.strip()
            if hasattr(cookies, "items"):
                for k, v in cookies.items():
                    if k == "ct0" and isinstance(v, str) and v.strip():
                        return v.strip()
        except Exception:   # cookie jars differ between requests and curl_cffi
            logger.debug("Could not read ct0 from the response cookies", exc_info=True)
    headers = getattr(response, "headers", None)
    if headers and hasattr(headers, "get"):
        sc = headers.get("set-cookie") or headers.get("Set-Cookie") or ""
        if isinstance(sc, str) and sc.strip():
            import re
            m = re.search(r"ct0=([^;,\s]+)", sc)
            if m:
                return m.group(1).strip()
    return None


def _call_twitter_settings_endpoint(
    auth_token: str,
    ct0: str,
    proxy_url: str | None = None,
    timeout: float = 12.0,
) -> tuple[Any, Exception | None]:
    """
    Performs the HTTP GET call to Twitter settings endpoint.
    Returns (response_object_or_None, exception_or_None).
    """
    headers = {
        "Authorization": "Bearer AAAAAAAAAAAAAAAAAAAAANRILgAAAAAAnNwIzUejRCOuH5E6I8xnZz4puTs%3D1Zv7ttfk8LF81IUq16cHjhLTvJu4FA33AGWWjCpTnA",
        "Cookie": f"auth_token={auth_token}; ct0={ct0};",
        "x-csrf-token": ct0,
        "X-Csrf-Token": ct0,
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    }
    proxies = {"http": proxy_url, "https": proxy_url} if proxy_url else None
    try:
        try:
            r = requests.get(
                TWITTER_SETTINGS_ENDPOINT,
                headers=headers,
                proxies=proxies,
                timeout=timeout,
                impersonate="chrome120",
            )
        except TypeError:
            r = requests.get(
                TWITTER_SETTINGS_ENDPOINT,
                headers=headers,
                proxies=proxies,
                timeout=timeout,
            )
        return r, None
    except Exception as exc:
        logger.warning("_call_twitter_settings_endpoint failed", exc_info=True)
        return None, exc


def _interpret_twitter_response(
    response: Any = None,
    error: Exception | None = None,
    username: str = "",
    *,
    status_code: int | None = None,
    response_text: str = "",
    response_data: dict | None = None,
    was_generated: bool = False,
) -> tuple[XSessionValidationResult, str, str]:
    """
    Interprets HTTP response or exception from Twitter settings endpoint.
    Returns (result_enum, message, confirmed_username).

    Status code mapping:
      200 -> VALID
      401 -> INVALID
      403 -> FORBIDDEN_UNKNOWN (logs snippet; neither confirms nor rejects)
      404 -> ENDPOINT_ERROR
      429 -> RATE_LIMITED
      SSL/Timeout/ConnectionError -> NETWORK_ERROR
      Any other -> ENDPOINT_ERROR (unknown status, keeps previous state)
    """
    if error is not None:
        err_msg = str(error)
        logger.warning("Twitter settings endpoint network error for user %s: %s", username, err_msg)
        return (
            XSessionValidationResult.NETWORK_ERROR,
            f"خطای ارتباط شبکه یا تایم‌اوت ({err_msg})",
            username,
        )

    code = status_code
    text = response_text
    data = response_data

    if response is not None:
        if code is None:
            code = getattr(response, "status_code", None)
        if not text:
            text = getattr(response, "text", "") or ""
        if data is None and hasattr(response, "json"):
            try:
                data = response.json()
            except Exception:
                logger.warning("_interpret_twitter_response failed", exc_info=True)
                data = None

    if code == 200:
        uname = username
        if isinstance(data, dict):
            uname = data.get("screen_name") or username
        return (
            XSessionValidationResult.VALID,
            f"اتصال به اکانت @{uname} تایید شد.",
            uname,
        )

    if code == 401 or (text and "Could not authenticate" in text):
        return (
            XSessionValidationResult.INVALID,
            "سشن نامعتبر یا منقضی شده است (HTTP 401). لطفاً سشن جدید را وارد نمایید.",
            username,
        )

    if code == 403:
        snippet = (text or "")[:200].replace("\r", " ").replace("\n", " ").strip()
        if was_generated:
            logger.warning(
                "Twitter settings endpoint returned 403 Forbidden with generated ct0 fallback. User: %s, Body snippet: %s",
                username,
                snippet,
            )
        else:
            logger.warning(
                "Twitter settings endpoint returned 403 Forbidden (unknown cause). User: %s, Body snippet: %s",
                username,
                snippet,
            )
        return (
            XSessionValidationResult.FORBIDDEN_UNKNOWN,
            f"دسترسی مسدود شد (HTTP 403؛ علت نامشخص). سشن نه تایید و نه رد شد. پاسخ سرور: {snippet[:80]}",
            username,
        )

    if code == 404:
        return (
            XSessionValidationResult.ENDPOINT_ERROR,
            "مسیر یا اندپوینت نامعتبر است (HTTP 404).",
            username,
        )

    if code == 429:
        return (
            XSessionValidationResult.RATE_LIMITED,
            "محدودیت موقت نرخ درخواست توییتر (HTTP 429).",
            username,
        )

    other_snippet = (text or "")[:100].replace("\r", " ").replace("\n", " ").strip()
    logger.info("Twitter settings endpoint returned unexpected status HTTP %s: %s", code, other_snippet)
    return (
        XSessionValidationResult.ENDPOINT_ERROR,
        f"پاسخ ناشناخته از توییتر (HTTP {code})؛ نتیجه اعتبارسنجی نامشخص است.",
        username,
    )


def _test_twitter_account_connection(account: TwitterEngineAccount) -> tuple[bool, str, str]:
    """Tests Twitter account connectivity and session validity."""
    auth_token = (account.auth_token or "").strip()
    if not auth_token:
        account.status = "CHALLENGED"
        account.status_message = "کوکی auth_token تنظیم نشده است"
        account.save(update_fields=["status", "status_message"])
        account._validation_result = XSessionValidationResult.INVALID
        return False, "CHALLENGED", "کوکی auth_token تنظیم نشده است و بدون آن سشن نامعتبر است."

    if account.proxy_url:
        proxy_ok, proxy_msg = health.proxy_reachable(account.proxy_url)
        if not proxy_ok:
            account._validation_result = XSessionValidationResult.NETWORK_ERROR
            return False, account.status, f"خطای پروکسی: {proxy_msg}"

    now = dj_timezone.now()
    if account.cooldown_until and account.cooldown_until > now:
        rem_min = int((account.cooldown_until - now).total_seconds()) // 60
        account._validation_result = XSessionValidationResult.RATE_LIMITED
        return False, account.status, f"اکانت در دوره استراحت است ({rem_min} دقیقه باقی‌مانده)"

    ct0, was_generated = _get_or_generate_ct0(account.ct0)

    import os
    import shutil
    import subprocess

    executable = shutil.which("twitter") or shutil.which("twitter-cli")
    if executable:
        sub_env = os.environ.copy()
        sub_env["TWITTER_AUTH_TOKEN"] = auth_token
        sub_env["TWITTER_CT0"] = ct0
        if account.proxy_url:
            sub_env["TWITTER_PROXY"] = account.proxy_url
        try:
            completed = subprocess.run(
                [executable, "search", "iran", "-t", "Latest", "--exclude", "retweets", "--max", "1", "--json"],
                capture_output=True, text=True, encoding="utf-8", timeout=25, check=False, env=sub_env
            )
            try:
                payload = json.loads(completed.stdout) if completed.stdout.strip() else {}
            except Exception:
                logger.warning("_test_twitter_account_connection failed", exc_info=True)
                payload = {}

            if completed.returncode == 0 and payload.get("ok") is True:
                account.status = "ACTIVE"
                account.status_message = "متصل و فعال"
                # Rule 4: Only persist a ct0 to the database when the result is VALID
                account.ct0 = ct0
                account.save(update_fields=["status", "status_message", "ct0"])
                account._validation_result = XSessionValidationResult.VALID
                return True, "ACTIVE", f"اتصال به اکانت @{account.username} تایید شد."
            elif payload.get("ok") is False:
                err = payload.get("error") if isinstance(payload.get("error"), dict) else {}
                code = str(err.get("code") or "")
                if code == "rate_limited":
                    account.status = "COOLDOWN"
                    account.cooldown_until = dj_timezone.now() + timedelta(minutes=15)
                    account.status_message = "محدودیت موقت نرخ درخواست (429)"
                    account.save(update_fields=["status", "status_message", "cooldown_until"])
                    account._validation_result = XSessionValidationResult.RATE_LIMITED
                    # A rate-limited session is not a confirmed session: report
                    # failure so callers don't show "connected" for it.
                    return False, "COOLDOWN", "اکانت با محدودیت موقت ریت‌لیمیت توییتر مواجه شد (۱۵ دقیقه استراحت)."
                elif code in ("not_authenticated", "auth_failed"):
                    account.status = "CHALLENGED"
                    account.status_message = "سشن منقضی یا نامعتبر است (HTTP 401) — auth_token/ct0 جدید ثبت کنید"
                    account.save(update_fields=["status", "status_message"])
                    account._validation_result = XSessionValidationResult.INVALID
                    return False, "CHALLENGED", "سشن منقضی یا نامعتبر است. لطفاً auth_token و ct0 جدید را وارد نمایید."
                else:
                    msg = err.get("message") or completed.stderr or "خطای اجرای ابزار توییتر"
                    account._validation_result = XSessionValidationResult.ENDPOINT_ERROR
                    return False, account.status, f"خطای تست توییتر: {msg}"
        except subprocess.TimeoutExpired:
            account._validation_result = XSessionValidationResult.NETWORK_ERROR
            return False, account.status, "زمان پاسخ‌گویی موتور توییتر به پایان رسید (تایم‌اوت)."
        except Exception:
            # Fall back to the HTTP check below, but leave a trace of why the
            # CLI path failed instead of swallowing it silently.
            logger.warning("twitter-cli session check failed for @%s; falling back to HTTP", account.username, exc_info=True)

    response, exc = _call_twitter_settings_endpoint(auth_token, ct0, proxy_url=account.proxy_url)
    resp_ct0 = _extract_response_ct0(response)
    effective_ct0 = resp_ct0 or ct0

    result, msg, confirmed_uname = _interpret_twitter_response(
        response=response,
        error=exc,
        username=account.username,
        was_generated=was_generated,
    )
    account._validation_result = result

    if result == XSessionValidationResult.VALID:
        account.status = "ACTIVE"
        account.status_message = msg
        # Rule 4: Only persist a ct0 to the database when the result is VALID
        account.ct0 = effective_ct0
        account.save(update_fields=["status", "status_message", "ct0"])
        return True, "ACTIVE", msg
    elif result == XSessionValidationResult.INVALID:
        account.status = "CHALLENGED"
        account.status_message = msg
        account.save(update_fields=["status", "status_message"])
        return False, "CHALLENGED", msg
    else:
        # FORBIDDEN_UNKNOWN, ENDPOINT_ERROR, RATE_LIMITED, NETWORK_ERROR:
        # Keep previous state! Do NOT persist generated ct0!
        inconclusive_msg = f"نتیجه اعتبارسنجی نامشخص است ({msg})؛ وضعیت قبلی اکانت حفظ شد."
        account.status_message = inconclusive_msg
        account.save(update_fields=["status_message"])
        return False, account.status, inconclusive_msg
