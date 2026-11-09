"""Logging an account in. A login that needs a code keeps running in a background thread of the web process and
waits for the code the operator types, so the code goes into the very login that asked for it (no second login,
no second code). Also: logging in with a pasted sessionid, and what a failed login does to the account."""
from __future__ import annotations

import logging
import re
import threading
import time
import uuid
from typing import Callable

from django.conf import settings
from django.db import connections, transaction
from django.utils import timezone

from apps.discovery import ig_client
from apps.discovery.device_emulator import apply_device_profile_to_client
from apps.discovery.ig_session import ErrorKind, classify, redact
from apps.discovery.ig_state import (Result, clean_sessionid, log_event, normalize_code, now_iso,
                                     parse_iso, result_for, set_state)
from apps.discovery.models import InstagramEngineAccount

log = logging.getLogger("instagram.accounts")


LOGIN_WAIT_SECONDS = 600               # a started login waits this long for the operator's code


CODE_POLL_SECONDS = 2.0


HEARTBEAT_SECONDS = 10.0


# No heartbeat for this long: the login thread is gone (e.g. the web worker restarted). A thread waiting for the
# code beats every HEARTBEAT_SECONDS; one talking to Instagram (running/verifying) can be silent for a while.
STALE_AFTER_SECONDS = {"needs_code": 90, "running": 300, "verifying": 300}


TWO_FACTOR_ATTEMPTS = 3


LIVE_STATES = ("running", "needs_code", "verifying")


CODE_RE = re.compile(r"^\d{4,8}$")


_sleep = time.sleep  # indirection for tests


_APP_APPROVAL = ("manual verification", "native challenge", "auth platform", "bloks redirect", "ufac", "selfie",
                 "recaptcha", "password change", "change_password", "select_contact_point_recovery", "submit_phone")


def classify_failure(exc, *, code_used: bool = False, timed_out: bool = False) -> tuple[str, str, str, int | None]:
    """(status, reason, Persian message, cooldown minutes) for a failed login / session use."""
    name = type(exc).__name__
    low = f"{name} {exc}".lower()
    kind = classify(exc)
    if name == "AccountSuspended" or "suspended" in low or "disabled your account" in low:
        return "BANNED", "suspended", "اینستاگرام اکانت را تعلیق یا غیرفعال کرده است.", None
    if any(k in low for k in _APP_APPROVAL):
        return ("CHALLENGED", "needs_app_approval",
                "اینستاگرام تأیید دستی می‌خواهد: اکانت را در اپ اینستاگرام روی گوشی (ترجیحاً با همین کانفیگ) باز کنید و "
                "«این من بودم» یا مراحل خواسته‌شده را انجام دهید، بعد دوباره «ورود» بزنید.", None)
    if timed_out:
        return "CHALLENGED", "needs_code", "زمان وارد کردن کد تمام شد؛ «ورود» را دوباره بزنید تا کد تازه بیاید.", None
    if code_used and (kind in (ErrorKind.CHALLENGE, ErrorKind.OTHER) or isinstance(exc, AssertionError)):
        return ("CHALLENGED", "needs_code",
                "اینستاگرام کد را نپذیرفت (اشتباه یا منقضی). «ورود» را دوباره بزنید و کد تازه را وارد کنید.", None)
    if kind == ErrorKind.CHALLENGE:
        return ("CHALLENGED", "needs_code",
                "اینستاگرام کد تأیید خواست؛ در صفحه‌ی اکانت «ورود» را بزنید و کد را همان‌جا وارد کنید.", None)
    if kind == ErrorKind.BAD_CREDENTIALS:
        return "CHALLENGED", "bad_password", "اینستاگرام پسورد را رد کرد؛ پسورد را اصلاح کنید و دوباره «ورود» بزنید.", None
    if kind == ErrorKind.RATE_LIMIT:
        return "COOLDOWN", "rate_limited", "اینستاگرام موقتاً محدود کرده است؛ ۳۰ دقیقه استراحت.", 30
    if kind == ErrorKind.NETWORK or any(k in low for k in ("proxy", "connection", "socks", "eof", "ssl")):
        return "COOLDOWN", "proxy_down", f"اتصال از طریق پروکسی/کانفیگ برقرار نشد ({redact(exc, 80)}).", 15
    if kind == ErrorKind.LOGIN_REQUIRED:
        return "CHALLENGED", "session_expired", "سشن منقضی شده است؛ «ورود» را بزنید.", None
    return "CHALLENGED", "error", f"ورود ناموفق: {redact(exc, 160)}", None


def record_login_failure(account, exc, *, actor: str = "", code_used: bool = False, timed_out: bool = False) -> Result:
    """Status + timeline entry for a login that did not finish (also used by workers that log in on their own)."""
    status, reason, message, minutes = classify_failure(exc, code_used=code_used, timed_out=timed_out)
    set_state(account, status, reason, message, cooldown_minutes=minutes)
    log_event(account, "code_failed" if code_used else "login_failed", message,
              {"error": redact(exc, 200), "reason": reason}, actor)
    return result_for(account, False, message, state="failed")


class LoginAborted(Exception):
    """The login was cancelled or replaced by a newer one."""


def _read_pending(pk) -> dict:
    return InstagramEngineAccount.objects.filter(pk=pk).values_list("pending_login", flat=True).first() or {}


def _update_pending(pk, token, change: Callable[[dict], dict]) -> dict | None:
    """Row-locked read-modify-write of pending_login (the login thread and the code request both write it)."""
    with transaction.atomic():
        row = InstagramEngineAccount.objects.select_for_update().filter(pk=pk).values("pending_login").first()
        if row is None:
            return None
        cur = dict(row["pending_login"] or {})
        if token is not None and cur.get("token") != token:
            return None
        new = change(cur)
        InstagramEngineAccount.objects.filter(pk=pk).update(pending_login=new)
        return new


def _is_live(pending) -> bool:
    if not isinstance(pending, dict) or pending.get("state") not in LIVE_STATES:
        return False
    beat = parse_iso(pending.get("heartbeat") or pending.get("started_at"))
    return bool(beat and (timezone.now() - beat).total_seconds() < STALE_AFTER_SECONDS[pending["state"]])


def _contact_hint(last_json, choice=None) -> str:
    lj = last_json if isinstance(last_json, dict) else {}
    data = lj.get("step_data") if isinstance(lj.get("step_data"), dict) else {}
    where = data.get("contact_point") or data.get("email") or data.get("phone_number") or ""
    step = str(lj.get("step_name") or "")
    via = "پیامک" if ("SMS" in str(choice) or "phone" in step or "sms" in step) else "ایمیل"
    return f"{via} {where}".strip()


class CodeWaiter:
    """instagrapi challenge_code_handler that waits (in the login thread) for the code typed in the panel."""

    def __init__(self, pk: int, token: str, *, actor: str = "", interactive: bool = True,
                 timeout: float | None = None):
        self.pk, self.token, self.actor, self.interactive = pk, token, actor, interactive
        self.timeout = LOGIN_WAIT_SECONDS if timeout is None else timeout
        self.client = None
        self.used = False
        self.timed_out = False
        self.error = ""

    def __call__(self, username, choice=None) -> str:
        if not self.interactive:
            return ""
        return self.wait("challenge", _contact_hint(getattr(self.client, "last_json", None), choice)) or ""

    def wait(self, kind: str, hint: str = "") -> str:
        now = now_iso()
        pending = _update_pending(self.pk, self.token, lambda p: {
            **p, "state": "needs_code", "kind": kind, "hint": hint, "error": self.error, "asked_at": now,
            "heartbeat": now, "code": None})
        if pending is None:
            raise LoginAborted()
        where = f" به {hint}" if hint else ""
        message = (f"اینستاگرام کد تأیید{where} فرستاد؛ کد را در صفحه‌ی اکانت وارد کنید." if kind == "challenge"
                   else f"کد ورود دومرحله‌ای ({hint or 'پیامک یا اپ Authenticator'}) را وارد کنید.")
        (InstagramEngineAccount.objects.filter(pk=self.pk).exclude(status="DISABLED")
         .update(status="CHALLENGED", status_reason="needs_code", status_message=message[:255]))
        log_event(self.pk, "code_requested", message, {"kind": kind, "hint": hint}, self.actor)
        deadline = time.monotonic() + self.timeout
        last_beat = time.monotonic()
        while time.monotonic() < deadline:
            _sleep(CODE_POLL_SECONDS)
            cur = _read_pending(self.pk)
            if cur.get("token") != self.token or cur.get("state") == "cancelled":
                raise LoginAborted()
            code = cur.get("code")
            if code:
                _update_pending(self.pk, self.token, lambda p: {**p, "state": "verifying", "code": None, "error": "",
                                                                 "heartbeat": now_iso()})
                self.used = True
                return str(code)
            if time.monotonic() - last_beat >= HEARTBEAT_SECONDS:
                _update_pending(self.pk, self.token, lambda p: {**p, "heartbeat": now_iso()})
                last_beat = time.monotonic()
        self.timed_out = True
        return ""


def _two_factor_info(cl, exc) -> dict:
    """What finishing a two-factor login in the same client needs (legacy endpoint and Bloks/CAA flow)."""
    lj = getattr(cl, "last_json", None)
    lj = lj if isinstance(lj, dict) else {}
    info = lj.get("two_factor_info") if isinstance(lj.get("two_factor_info"), dict) else {}
    # instagrapi passes the login response as keyword arguments of the exception (ClientError sets them as attributes)
    login_json = {k: v for k, v in vars(exc).items() if k not in ("response", "message", "code") and not k.startswith("_")}
    finder = getattr(cl, "_find_login_response_value", None)

    def look(key):
        for data in (info, login_json, lj):
            try:
                value = finder(data, key) if callable(finder) else data.get(key)
            except Exception:
                value = data.get(key) if isinstance(data, dict) else None
            if value:
                return value
        return None

    context = look("two_step_verification_context")
    phone = look("obfuscated_phone_number") or ""
    totp = bool(look("totp_two_factor_on")) and not bool(look("sms_two_factor_on"))
    hint = "اپ Authenticator" if totp else (f"پیامک به {phone}" if phone else "")
    return {"identifier": str(look("two_factor_identifier") or ""), "totp": totp,
            "context": context.strip() if isinstance(context, str) else "",
            "login_json": login_json if login_json.get("two_step_verification_context") else (lj or login_json),
            "hint": hint}


def _submit_two_factor(cl, account, tf: dict, code: str, exc) -> None:
    """Finishes a two-factor login in the same client (no second login request, so no second code)."""
    from instagrapi.exceptions import UnknownError

    logged = False
    if tf["identifier"] and hasattr(cl, "private_request"):   # legacy endpoint, as instagrapi itself does it
        data = {"verification_code": code, "phone_id": cl.phone_id, "_csrftoken": cl.token,
                "two_factor_identifier": tf["identifier"], "username": account.username, "trust_this_device": "0",
                "guid": cl.uuid, "device_id": cl.android_device_id, "waterfall_id": str(uuid.uuid4()),
                "verification_method": "3"}
        try:
            logged = cl.private_request("accounts/two_factor_login/", data, login=True)
        except UnknownError as ue:
            if "invalid parameters" not in str(getattr(ue, "message", "") or ue).lower() or not tf["context"]:
                raise
            logged = cl._login_with_bloks_two_factor(code, tf["login_json"], ue)
        else:
            if logged:
                cl.authorization_data = cl.parse_authorization(cl.last_response.headers.get("ig-set-authorization"))
    elif tf["context"] and hasattr(cl, "_login_with_bloks_two_factor"):   # current Bloks/CAA flow
        logged = cl._login_with_bloks_two_factor(code, tf["login_json"], exc)
    else:
        cl.login(account.username, account.password, verification_code=code)   # older instagrapi: it does it all
        return
    if not logged:
        raise RuntimeError("two-factor code was not accepted")
    try:
        cl.login_flow()   # what the app requests right after logging in; the login itself already succeeded
    except Exception as flow_exc:
        log.info("login_flow after two-factor failed for @%s: %s", account.username, redact(flow_exc, 120))
    cl.last_login = time.time()


def _run_login(pk: int, token: str, actor: str = "", *, interactive: bool = True) -> Result:
    account = InstagramEngineAccount.objects.get(pk=pk)
    waiter = CodeWaiter(pk, token, actor=actor, interactive=interactive)
    cl = ig_client.new_client(account, code_handler=waiter)
    waiter.client = cl
    apply_device_profile_to_client(cl, account)   # the account's one phone
    ig_client.mount_timeouts(cl)
    two_factor_used = False
    try:
        try:
            cl.login(account.username, account.password)
        except Exception as exc:
            if type(exc).__name__ != "TwoFactorRequired" or not interactive:
                raise
            tf = _two_factor_info(cl, exc)
            for attempt in range(TWO_FACTOR_ATTEMPTS):
                code = waiter.wait("two_factor", tf["hint"])
                if not code:
                    raise exc
                two_factor_used = True
                try:
                    _submit_two_factor(cl, account, tf, code, exc)
                    break
                except Exception as tf_exc:
                    if attempt == TWO_FACTOR_ATTEMPTS - 1 or classify(tf_exc) in (ErrorKind.NETWORK, ErrorKind.RATE_LIMIT):
                        raise tf_exc from exc
                    waiter.error = "کد قبلی پذیرفته نشد؛ کد تازه را وارد کنید."
    except LoginAborted:
        return Result(False, "ورود لغو شد.", state="cancelled")
    except Exception as exc:
        account.refresh_from_db()
        res = record_login_failure(account, exc, actor=actor, code_used=waiter.used or two_factor_used,
                                   timed_out=waiter.timed_out)
        _update_pending(pk, token, lambda p: {**p, "state": "failed", "message": res.message, "code": None,
                                              "finished_at": now_iso()})
        return res
    account.refresh_from_db()
    used = waiter.used or two_factor_used
    message = "کد تأیید پذیرفته شد و اکانت وارد شد." if used else "ورود موفق؛ سشن ذخیره شد."
    ig_client.save_session(account, cl, kind="code_ok" if used else "login_ok", message=message, actor=actor)
    _update_pending(pk, token, lambda p: {**p, "state": "ok", "message": message, "code": None,
                                          "finished_at": now_iso()})
    return result_for(account, True, message, state="ok")


def _spawn(fn, *args, **kwargs) -> None:
    """Runs the login in a daemon thread of this process (INSTAGRAM_LOGIN_IN_BACKGROUND=False: inline, for tests)."""
    if not getattr(settings, "INSTAGRAM_LOGIN_IN_BACKGROUND", True):
        fn(*args, **kwargs)
        return

    def runner():
        try:
            fn(*args, **kwargs)
        except Exception:
            log.exception("instagram login thread crashed")
        finally:
            connections.close_all()

    threading.Thread(target=runner, name=f"ig-login-{args[0] if args else ''}", daemon=True).start()


def login_state(account) -> dict:
    """What the account page polls while a login runs."""
    p = _read_pending(account.pk) if account.pk else {}
    acc = InstagramEngineAccount.objects.filter(pk=account.pk).values("status", "status_reason", "status_message").first() or {}
    state = p.get("state") or ""
    live = _is_live(p)
    if state in LIVE_STATES and not live:
        state = "stale"
    return {"state": state, "live": live, "kind": p.get("kind", ""), "hint": p.get("hint", ""),
            "error": p.get("error", ""), "message": p.get("message", "") or acc.get("status_message", ""),
            "status": acc.get("status", ""), "reason": acc.get("status_reason", ""),
            "started_at": p.get("started_at"), "finished_at": p.get("finished_at"),
            "requires_code": state == "needs_code"}


def start_login(account, *, actor: str = "", wait: float = 0) -> Result:
    """Logs in with username/password in the background; poll login_state() / pass a code to submit_code().

    wait > 0 blocks up to that many seconds for the first outcome (needs_code / ok / failed), for old endpoints.
    """
    if not account.password or account.password == "session_only":
        return result_for(account, False, "برای ورود، پسورد اکانت را ثبت کنید (یا sessionid بدهید).")
    pending = account.pending_login or {}
    if _is_live(pending):
        return result_for(account, True, "ورود این اکانت در جریان است.", state=pending.get("state", ""),
                       hint=pending.get("hint", ""))
    token = uuid.uuid4().hex
    now = now_iso()
    InstagramEngineAccount.objects.filter(pk=account.pk).update(
        pending_login={"state": "running", "token": token, "started_at": now, "heartbeat": now, "actor": actor})
    account.pending_login = _read_pending(account.pk)
    _spawn(_run_login, account.pk, token, actor)
    if wait:
        return wait_for_login(account, token, timeout=wait)
    account.refresh_from_db()
    return _login_result(account)


def _login_result(account) -> Result:
    st = login_state(account)
    state = st["state"]
    if state == "needs_code":
        msg = (st["error"] + " " if st["error"] else "") + (st["message"] or "کد تأیید را وارد کنید.")
        return result_for(account, False, msg.strip(), state=state, hint=st["hint"])
    if state == "ok":
        return result_for(account, True, st["message"] or "ورود موفق.", state=state)
    if state in ("failed", "stale", "cancelled"):
        return result_for(account, False, st["message"] or "ورود انجام نشد.", state=state)
    return result_for(account, True, "ورود به اینستاگرام شروع شد؛ چند ثانیه صبر کنید…", state=state or "running")


def wait_for_login(account, token: str | None = None, *, timeout: float = 40, until=("needs_code", "ok", "failed",
                                                                                         "cancelled")) -> Result:
    deadline = time.monotonic() + timeout
    while True:
        p = _read_pending(account.pk)
        if (token and p.get("token") != token) or p.get("state") in until or time.monotonic() >= deadline:
            break
        if p.get("state") in LIVE_STATES and not _is_live(p):
            break
        _sleep(1.0)
    account.refresh_from_db()
    return _login_result(account)


def submit_code(account, raw_code, *, actor: str = "", wait: float = 0) -> Result:
    """Hands the operator's code to the login that is waiting for it."""
    code = normalize_code(raw_code)
    if not CODE_RE.match(code):
        return result_for(account, False, "کد باید ۴ تا ۸ رقم باشد.")
    pending = _read_pending(account.pk)
    if not _is_live(pending) or pending.get("state") != "needs_code":
        return result_for(account, False, "هیچ ورودی منتظر کد نیست (یا مهلتش تمام شده). «ورود» را بزنید تا کد تازه بیاید و "
                                       "همان را وارد کنید.", state=login_state(account)["state"])
    token = pending.get("token")
    new = _update_pending(account.pk, token, lambda p: {**p, "code": code, "state": "verifying",
                                                         "code_at": now_iso()} if p.get("state") == "needs_code" else p)
    if not new or new.get("state") != "verifying":
        return result_for(account, False, "این ورود دیگر منتظر کد نیست؛ صفحه را تازه کنید.")
    if wait:
        return wait_for_login(account, token, timeout=wait, until=("ok", "failed", "cancelled", "needs_code"))
    return result_for(account, True, "کد ثبت شد؛ در حال تأیید با اینستاگرام…", state="verifying")


def cancel_login(account, *, actor: str = "") -> Result:
    pending = _read_pending(account.pk)
    if pending.get("state") in LIVE_STATES:
        _update_pending(account.pk, pending.get("token"), lambda p: {**p, "state": "cancelled", "code": None,
                                                                      "finished_at": now_iso()})
        log_event(account, "login_failed", "ورود توسط مدیر لغو شد", None, actor)
    account.refresh_from_db()
    if account.status == "CHALLENGED" and account.status_reason == "needs_code":   # nobody waits for a code now
        set_state(account, "CHALLENGED", "session_expired", "ورود لغو شد؛ برای ورود «ورود» را دوباره بزنید.")
    return result_for(account, True, "ورود لغو شد.", state="cancelled")


def login_with_sessionid(account, raw_sessionid, *, actor: str = "") -> Result:
    """Connects with a sessionid copied from a browser that is logged in right now (same phone profile)."""
    sid = clean_sessionid(raw_sessionid)
    if not sid:
        return result_for(account, False, "مقدار کوکی sessionid را وارد کنید.")
    cl = ig_client.new_client(account)
    apply_device_profile_to_client(cl, account)
    ig_client.mount_timeouts(cl)
    try:
        cl.login_by_sessionid(sid)
    except Exception as exc:
        kind = classify(exc)
        if kind == ErrorKind.NETWORK:
            message = f"اتصال برقرار نشد ({redact(exc, 120)}); وضعیت اکانت تغییری نکرد."
            log_event(account, "login_failed", message, {"error": redact(exc, 200), "via": "sessionid"}, actor)
            return result_for(account, False, message, extra={"deleted": False})
        message = (f"سشن واردشده معتبر نبود ({redact(exc, 120)}). sessionid را از مرورگری کپی کنید که همین حالا "
                   "در اکانت لاگین است.")
        set_state(account, "CHALLENGED", "session_expired", message)
        log_event(account, "login_failed", message, {"error": redact(exc, 200), "via": "sessionid"}, actor)
        return result_for(account, False, message, extra={"deleted": False})
    if (account.pending_login or {}).get("state") in LIVE_STATES:
        cancel_login(account, actor=actor)
    ig_client.save_session(account, cl, kind="session_set", message="", actor=actor)
    return result_for(account, True, f"سشن اکانت @{account.username} ثبت شد و اکانت فعال است.",
                   extra={"deleted": False})
