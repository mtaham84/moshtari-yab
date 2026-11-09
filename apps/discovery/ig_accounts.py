"""Instagram accounts: adding, editing, pausing, deleting and connection-testing them.

Views, workers and commands go through these modules; nothing else logs an account in or moves it to another proxy:

* ig_login: logging in (a login that needs a code waits in a background thread for the code the operator types).
* ig_client: instagrapi clients on the account's own phone profile and proxy.
* proxies/: which proxy an account uses (its own, a sticky system V2Ray config, or the shared xray pool, never the
  server's own IP), healing when a config dies, and exporting the connection (link + QR) for a phone.
* ig_state: status changes and the account's timeline (InstagramAccountEvent), where everything is written.
* tenants: the customer an account works for and its consultant name / pitch.
"""
from __future__ import annotations

import logging
import re
from typing import Any

from django.utils import timezone

from apps.discovery.ig_session import redact
from apps.discovery.models import InstagramEngineAccount
from apps.discovery.ig_state import (Result, clean_sessionid, has_session, log_event, normalize_digits, normalize_username, result_for, set_state)
from apps.discovery.ig_client import connect, save_session
from apps.discovery.ig_login import classify_failure, login_with_sessionid, start_login
from apps.discovery.proxies import health
from apps.discovery.proxies.assignment import heal_account, set_proxy, sync_config_owner
from apps.discovery.proxies.urls import (POOL_PORT, XRAY_HOST, effective_proxy_url, normalize_proxy_url, proxy_label, proxy_mode,
                                         validate_proxy_url)

log = logging.getLogger("instagram.accounts")

USERNAME_RE = re.compile(r"^[A-Za-z0-9._]{1,30}$")


def check(account, *, actor: str = "", allow_login: bool = True) -> Result:
    """Connection test on the account's own phone and session (what the workers use): never replaces a working
    session, never logs in again while the session works; logs in (background) only when there is no session."""
    proxy = effective_proxy_url(account)
    ok, proxy_msg = health.proxy_reachable(proxy)
    if not ok and proxy_mode(account) != "custom" and health.port_open(XRAY_HOST, POOL_PORT):
        heal_account(account, actor or "system")   # its config died: the next free one (or the pool)
        account.refresh_from_db()
        proxy = effective_proxy_url(account)
        ok, proxy_msg = health.proxy_reachable(proxy)
    if not ok:
        message = f"پروکسی/کانفیگ در دسترس نیست: {proxy_msg}"
        set_state(account, "COOLDOWN", "proxy_down", message, cooldown_minutes=15)
        log_event(account, "check_failed", message, {"proxy": proxy}, actor)
        return result_for(account, False, message)
    now = timezone.now()
    if account.cooldown_until and account.cooldown_until > now:
        minutes = int((account.cooldown_until - now).total_seconds() // 60) + 1
        return result_for(account, True, f"اکانت در استراحت است (حدود {minutes} دقیقه مانده)؛ تست انجام نشد.")
    if not has_session(account):
        if allow_login and account.password and account.password != "session_only":
            return start_login(account, actor=actor)
        return result_for(account, False, "اکانت نه سشن دارد نه پسورد؛ sessionid یا پسورد را ثبت کنید.")
    try:
        cl = connect(account)
        cl.get_timeline_feed()   # what the app itself requests when it is opened
    except Exception as exc:
        status, reason, message, minutes = classify_failure(exc)
        if reason == "needs_code":
            reason, message = "session_expired", "اینستاگرام برای این سشن تأیید خواست؛ «ورود» را بزنید و کد را وارد کنید."
        set_state(account, status, reason, message, cooldown_minutes=minutes)
        log_event(account, "session_expired" if reason == "session_expired" else "check_failed", message,
                  {"error": redact(exc, 200)}, actor)
        return result_for(account, False, message)
    message = f"سشن معتبر است و اتصال برقرار شد ({proxy_label(account)})."
    save_session(account, cl, kind="check_ok", message=message, actor=actor)
    return result_for(account, True, message)


# ---------------------------------------------------------------------------
# settings, pause/resume, add, delete
# ---------------------------------------------------------------------------
def update_settings(account, *, actor: str = "", **values) -> Result:
    """username / role / tenant_id / account_tier / daily_limit / password (only the given, non-empty values)."""
    from apps.discovery.outreach_service import get_tier_specs

    changed = []
    old_username = account.username
    username = normalize_username(values.get("username"))
    if username and username.lower() != account.username.lower():   # Instagram usernames ignore case
        if not USERNAME_RE.match(username):
            return result_for(account, False, "نام کاربری اینستاگرام معتبر نیست.")
        if InstagramEngineAccount.objects.filter(username__iexact=username).exclude(pk=account.pk).exists():
            return result_for(account, False, f"اکانت @{username} از قبل وجود دارد.")
        account.username = username
        changed.append("username")
    role = str(values.get("role") or "").upper()
    if role in ("SCRAPER", "SENDER") and role != account.role:
        account.role = role
        changed.append("role")
    tenant = str(values.get("tenant_id") or "").strip()
    if tenant and tenant != account.tenant_id:
        account.tenant_id = tenant[:64]
        changed.append("tenant_id")
    tier = str(values.get("account_tier") or "").upper()
    if tier in ("NEW", "MEDIUM", "AGED") and tier != account.account_tier:
        account.account_tier = tier
        account.hourly_limit = get_tier_specs(tier)["hourly_speed"]
        changed += ["account_tier", "hourly_limit"]
    daily = normalize_digits(values.get("daily_limit"))
    if daily.isdigit() and int(daily) > 0 and int(daily) != account.daily_limit:
        account.daily_limit = min(int(daily), 500)
        changed.append("daily_limit")
    password = str(values.get("password") or "")
    if password.strip() and password != account.password:
        account.password = password.strip()
        changed.append("password")
    if not changed:
        return result_for(account, True, "تغییری داده نشد.", extra={"changed": []})
    account.save(update_fields=changed)
    if "username" in changed:   # renamed on Instagram: its leads and its config follow it
        from apps.discovery.models import InstagramEngineLead
        InstagramEngineLead.objects.filter(sender_account__iexact=old_username).update(sender_account=account.username)
        InstagramEngineLead.objects.filter(second_sender_username__iexact=old_username).update(
            second_sender_username=account.username)
        sync_config_owner(account.proxy_config)
    labels = {"username": "نام کاربری", "role": "نقش", "tenant_id": "مشتری", "account_tier": "رده",
              "hourly_limit": "سقف ساعتی", "daily_limit": "سقف روزانه", "password": "پسورد"}
    message = "تغییر: " + "، ".join(dict.fromkeys(labels[f] for f in changed))
    log_event(account, "edited", message, {"fields": ",".join(changed)}, actor)
    return result_for(account, True, message, extra={"changed": changed})


def pause(account, *, actor: str = "", note: str = "") -> Result:
    message = note or "توسط مدیر متوقف شد"
    account.status, account.status_reason, account.status_message = "DISABLED", "paused", message[:255]
    account.save(update_fields=["status", "status_reason", "status_message"])
    log_event(account, "status_changed", message, {"status": "DISABLED"}, actor)
    return result_for(account, True, "اکانت متوقف شد؛ هیچ سرویسی از آن استفاده نمی‌کند.")


def resume(account, *, actor: str = "") -> Result:
    if has_session(account):
        set_state(account, "ACTIVE", "", "توسط مدیر دوباره فعال شد", force=True)
    else:
        set_state(account, "CHALLENGED", "session_expired", "فعال شد ولی سشن ندارد؛ «ورود» را بزنید.", force=True)
    log_event(account, "status_changed", account.status_message, {"status": account.status}, actor)
    return result_for(account, True, account.status_message)


def clear_cooldown(account, *, actor: str = "") -> Result:
    account.cooldown_until = None
    fields = ["cooldown_until", "status_message"]
    account.status_message = "استراحت دستی لغو شد"
    if account.status == "COOLDOWN":
        account.status, account.status_reason = "ACTIVE", ""
        fields += ["status", "status_reason"]
    account.save(update_fields=fields)
    log_event(account, "status_changed", "استراحت توسط مدیر لغو شد", {"status": account.status}, actor)
    return result_for(account, True, f"استراحت اکانت @{account.username} لغو شد.")


def delete_account(account, *, actor: str = "") -> str:
    username, cfg = account.username, account.proxy_config
    account.delete()
    sync_config_owner(cfg)
    log.info("instagram account @%s deleted by %s", username, actor or "system")
    return username


def add_account(*, username, password: str = "", role: str = "SENDER", tenant_id: str = "tenant_default",
                account_tier: str = "NEW", daily_limit=None, proxy: str = "system", proxy_url: str = "",
                config=None, sessionid: str = "", actor: str = "", login: bool = True,
                login_wait: float = 0) -> tuple[Any, Result]:
    """Creates (or refreshes the credentials of) an account, connects it and logs it in.

    proxy: system (a free system config, else the pool) | config (`config`) | custom (`proxy_url`) | pool.
    """
    from apps.discovery.outreach_service import get_tier_specs

    username = normalize_username(username).lower()   # how Instagram itself writes usernames
    if not USERNAME_RE.match(username):
        return None, Result(False, "نام کاربری اینستاگرام معتبر نیست.")
    password = str(password or "").strip()
    if not password and not clean_sessionid(sessionid):
        return None, Result(False, "پسورد یا sessionid اکانت لازم است.")
    role = role.upper() if str(role or "").upper() in ("SCRAPER", "SENDER") else "SENDER"
    tier = account_tier.upper() if str(account_tier or "").upper() in ("NEW", "MEDIUM", "AGED") else "NEW"
    specs = get_tier_specs(tier)
    daily = normalize_digits(daily_limit)
    daily = int(daily) if daily.isdigit() and int(daily) > 0 else specs["default_daily"]
    if proxy == "custom":
        url = normalize_proxy_url(proxy_url)
        err = validate_proxy_url(url)
        if err:
            return None, Result(False, err)

    account = InstagramEngineAccount.objects.filter(username__iexact=username).first()
    if account is not None:
        if account.status == "ACTIVE" and account.session_json:
            return account, result_for(account, False, f"اکانت @{account.username} قبلاً ثبت شده و فعال است؛ از صفحه‌ی "
                                                     "خودش مدیریتش کنید.", extra={"exists": True})
        if password:
            account.password = password
        account.role, account.tenant_id, account.account_tier = role, (tenant_id or account.tenant_id)[:64], tier
        account.daily_limit, account.hourly_limit = daily, specs["hourly_speed"]
        account.save(update_fields=["password", "role", "tenant_id", "account_tier", "daily_limit", "hourly_limit"])
        log_event(account, "edited", "دوباره اضافه شد (مشخصات به‌روز شد)", {"role": role, "tenant": tenant_id}, actor)
    else:
        account = InstagramEngineAccount.objects.create(
            username=username, password=password or "session_only", role=role, tenant_id=(tenant_id or "tenant_default")[:64],
            account_tier=tier, daily_limit=daily, hourly_limit=specs["hourly_speed"], status="CHALLENGED",
            status_reason="session_expired", status_message="هنوز وارد نشده است")
        log_event(account, "created", f"اکانت اضافه شد ({account.get_role_display()})", {"role": role,
                                                                                         "tenant": account.tenant_id}, actor)

    if proxy == "custom":
        set_proxy(account, "custom", url=proxy_url, actor=actor, settle=False)
    elif proxy == "config" and config is not None:
        res = set_proxy(account, "config", config=config, actor=actor, settle=False)
        if not res.ok:
            return account, res
    elif proxy == "pool":
        set_proxy(account, "pool", actor=actor, settle=False)
    elif proxy_mode(account) != "custom":   # system: keep a live config, else a free one, else the pool
        heal_account(account, actor or "system")

    ok, proxy_msg = health.proxy_reachable(effective_proxy_url(account))
    if not ok:
        message = f"اکانت ثبت شد ولی پروکسی/کانفیگش در دسترس نیست ({proxy_msg})؛ اتصال را درست کنید و «ورود» بزنید."
        set_state(account, "CHALLENGED", "proxy_down", message)
        log_event(account, "check_failed", message, None, actor)
        return account, result_for(account, False, message)
    if clean_sessionid(sessionid):
        res = login_with_sessionid(account, sessionid, actor=actor)
        if res.ok or not (login and account.password and account.password != "session_only"):
            return account, res
    if login and account.password and account.password != "session_only":
        return account, start_login(account, actor=actor, wait=login_wait)
    return account, result_for(account, True, f"اکانت @{account.username} ثبت شد.")
