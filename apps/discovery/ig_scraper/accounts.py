"""Which scraper accounts may work, the anti-ban request budget, and how an Instagram error changes an account."""
from django.utils import timezone as dj_timezone

from apps.discovery.ig_scraper import clients, session
from apps.discovery.ig_session import ErrorKind, classify, redact
from apps.discovery.models import InstagramEngineAccount

# Error kind -> (account status, rest hours). LOGIN_REQUIRED/NETWORK/OTHER are handled separately.
KIND_POLICY = {
    ErrorKind.CHALLENGE: ("CHALLENGED", None),
    ErrorKind.BAD_CREDENTIALS: ("CHALLENGED", None),
    ErrorKind.RATE_LIMIT: ("COOLDOWN", 2.0),
}


def available_scraper_accounts(run):
    """Accounts with a stored session whose cooldown has expired; expired cooldowns are lifted."""
    from apps.discovery.anti_ban import InstagramAntiBanManager

    now = dj_timezone.now()
    candidates = (
        InstagramEngineAccount.objects.filter(role="SCRAPER", status__in=["ACTIVE", "COOLDOWN"])
        .exclude(session_json__isnull=True)
        .order_by("last_action_at")
    )
    usable = []
    for acc in candidates:
        InstagramAntiBanManager.ensure_account_isolated_profile(acc)

        if acc.cooldown_until and acc.cooldown_until > now:
            continue
        if acc.status == "COOLDOWN":
            acc.status = "ACTIVE"
            acc.cooldown_until = None
            acc.save(update_fields=["status", "cooldown_until"])

        is_safe, reason = InstagramAntiBanManager.check_account_safety(acc)
        if is_safe:
            usable.append(acc)
        else:
            run.stdout.write(run.style.WARNING(f"  اکانت @{acc.username} در حال استراحت: {reason}"))
    return usable


def is_usable(account) -> bool:
    from apps.discovery.anti_ban import InstagramAntiBanManager
    account.refresh_from_db(fields=["status", "cooldown_until"])
    is_safe, _ = InstagramAntiBanManager.check_account_safety(account)
    return is_safe


def note_account_error(run, account, err_text: str, cl=None, exc=None) -> bool:
    """
    Classify the failure and react conservatively. A login is only ever considered for a rejected session
    (LOGIN_REQUIRED) and only when auto-reauth is enabled; challenges, restrictions, network and unknown errors
    never log in. Returns True when the account was taken out of rotation.
    """

    kind = classify(exc if exc is not None else err_text)
    if kind is ErrorKind.LOGIN_REQUIRED:
        if session.auto_renew_session(run, cl, account):
            return False
        if account.status != "ACTIVE":
            return True
        if run.policy.auto_reauth:  # enabled but already tried this incident: stop, do not loop
            session.set_state(account, "CHALLENGED", "لاگین مجدد انجام شد یا ممکن نبود و سشن همچنان رد می‌شود؛ بررسی دستی لازم است.")
            return True
        session.set_state(account, "CHALLENGED", "سشن توسط اینستاگرام رد شد و لاگین خودکار خاموش است؛ sessionid جدید ثبت کنید.")
        run.stdout.write(run.style.WARNING(
            f"    session_rejected: account {account.id} paused; set a fresh sessionid (auto re-login is off)."))
        return True
    if kind in KIND_POLICY:
        status, hours = KIND_POLICY[kind]
        msgs = {ErrorKind.CHALLENGE: "اینستاگرام چالش امنیتی/تایید هویت خواست؛ کد را در داشبورد وارد کنید.",
                ErrorKind.BAD_CREDENTIALS: "لاگین ناموفق: رمز عبور رد شد.",
                ErrorKind.RATE_LIMIT: "محدودیت موقت اینستاگرام (۲ ساعت استراحت)."}
        session.set_state(account, status, msgs[kind], hours)
        run.stdout.write(run.style.WARNING(
            f"    {kind.value}: account {account.id} -> {status}{f' ({hours:g}h)' if hours else ''}; no login attempted."))
        return True
    if kind is ErrorKind.NETWORK:
        run.stderr.write(f"    Network/Proxy error encountered on @{account.username}; triggering automatic proxy failover...")
        if clients.rotate_or_heal_proxy(run, account, cl=cl):
            return False  # successfully failed over to a new proxy, keep account in rotation!
    return False  # network / unclassified: no state change, never a login


def request_budget_available(run, account) -> bool:
    from apps.discovery.anti_ban import InstagramAntiBanManager
    is_safe, _ = InstagramAntiBanManager.check_account_safety(
        account, extra_requests=run.api_requests
    )
    return is_safe


def instagram_api_call(run, account, method, *args, **kwargs):
    from apps.discovery.anti_ban import InstagramAntiBanManager
    is_safe, reason = InstagramAntiBanManager.check_account_safety(
        account, extra_requests=run.api_requests
    )
    if not is_safe:
        raise RuntimeError(f"محدودیت ضدبن اینستاگرام: {reason}")
    run.api_requests = run.api_requests + 1
    try:
        return method(*args, **kwargs)
    except Exception as exc:
        err_str = str(exc).lower()
        is_net_err = any(k in err_str for k in [
            "connection", "timeout", "timed out", "reset", "transport", "proxy", "eof", "ssl",
            "56", "connection closed", "refused", "proxyerror", "socks"
        ])
        if is_net_err:
            run.stderr.write(f"    [API Call Warning]: Proxy/Network error during call ({redact(exc, 100)}); initiating auto-failover...")
            cl = getattr(method, "__self__", None)
            if clients.rotate_or_heal_proxy(run, account, cl=cl):
                run.stdout.write(run.style.SUCCESS("    [API Call Retry]: Retrying operation with rotated proxy..."))
                return method(*args, **kwargs)
        raise
