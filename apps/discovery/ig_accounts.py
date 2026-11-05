"""Instagram accounts: the one place that adds, logs in, verifies, connects, re-proxies, pauses and exports them.

Views, workers and commands call this module; nothing else should log an account in or move it to another proxy.

* One phone per account: every login and session uses the account's own device profile (device_emulator).
* A login that needs a code keeps running in a background thread of the web process and waits for the code the
  operator types, so the code goes into the very login that asked for it (no second login, no second code).
* Every account talks through a proxy: its own (``custom``), or a system V2Ray config that stays with it until the
  config dies (``config``); without one it uses the shared xray pool (``INSTAGRAM_DEFAULT_PROXY_URL``), never the
  server's own IP.  The config can be exported (link + QR) to open the account on a phone from the same IP.
* Everything that happens is written to the account's timeline (InstagramAccountEvent).
"""
from __future__ import annotations

import logging
import re
import socket
import threading
import time
import uuid
from dataclasses import dataclass, field
from datetime import timedelta
from typing import Any, Callable
from urllib.parse import urlparse

from django.conf import settings
from django.db import connections, transaction
from django.db.models import Case, Count, F, FloatField, IntegerField, Value, When
from django.utils import timezone

from apps.discovery.device_emulator import account_device_settings, apply_device_profile_to_client, connect_saved_session
from apps.discovery.ig_session import ErrorKind, classify, redact
from apps.discovery.models import InstagramAccountEvent, InstagramEngineAccount, OutreachTenantProfile, ProxyConfig

log = logging.getLogger("instagram.accounts")

XRAY_HOST = "xray"
POOL_PORT = 1080
RESERVED_PORTS = (1080, 8080)          # xray's shared pool inbounds, never a dedicated config
LOGIN_WAIT_SECONDS = 600               # a started login waits this long for the operator's code
CODE_POLL_SECONDS = 2.0
HEARTBEAT_SECONDS = 10.0
# No heartbeat for this long: the login thread is gone (e.g. the web worker restarted). A thread waiting for the
# code beats every HEARTBEAT_SECONDS; one talking to Instagram (running/verifying) can be silent for a while.
STALE_AFTER_SECONDS = {"needs_code": 90, "running": 300, "verifying": 300}
SETTLE_MINUTES = 45                    # after an IP change no cold DM for this long (a new IP + instant DMs = flag)
TWO_FACTOR_ATTEMPTS = 3
LIVE_STATES = ("running", "needs_code", "verifying")
TENANT_ALIASES = {"sarv": "tenant_4"}  # legacy tenant id of business 4
USERNAME_RE = re.compile(r"^[A-Za-z0-9._]{1,30}$")
CODE_RE = re.compile(r"^\d{4,8}$")
PROXY_SCHEMES = ("http", "https", "socks5", "socks5h", "socks4", "socks4a")
_DIGITS = str.maketrans("۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩", "01234567890123456789")
_SECRET_KEYS = ("password", "sessionid", "code", "cookie", "authorization", "token")

_sleep = time.sleep  # indirection for tests


# ---------------------------------------------------------------------------
# small helpers
# ---------------------------------------------------------------------------
def _now_iso() -> str:
    return timezone.now().isoformat()


def _parse_iso(value) -> Any:
    from django.utils.dateparse import parse_datetime
    try:
        return parse_datetime(str(value)) if value else None
    except (TypeError, ValueError):
        return None


def normalize_digits(value) -> str:
    return str(value or "").translate(_DIGITS).strip()


def normalize_code(value) -> str:
    return re.sub(r"[\s\-]", "", normalize_digits(value))


def normalize_username(value) -> str:
    return str(value or "").strip().lstrip("@").strip()


def clean_sessionid(raw) -> str:
    """The sessionid value from a pasted cookie value, `sessionid=...` pair or whole Cookie header."""
    sid = str(raw or "").strip()
    m = re.search(r"sessionid=([^;\s]+)", sid)
    if m:
        sid = m.group(1)
    return sid.strip().strip('"').strip("'").strip(";").strip()


def _clean_data(data):
    if not isinstance(data, dict):
        return None if data is None else {"value": str(data)[:300]}
    out = {}
    for k, v in data.items():
        if any(s in str(k).lower() for s in _SECRET_KEYS):
            continue
        out[str(k)] = v if isinstance(v, (int, float, bool)) or v is None else str(v)[:300]
    return out


def log_event(account, kind: str, message: str = "", data=None, actor: str = ""):
    """Adds one line to the account's timeline; never raises (the action it describes already happened)."""
    pk = getattr(account, "pk", account)
    try:
        with transaction.atomic():   # a failed insert must not break the caller's transaction
            return InstagramAccountEvent.objects.create(account_id=pk, kind=kind, message=str(message or "")[:255],
                                                        data=_clean_data(data), actor=str(actor or "")[:150])
    except Exception as exc:
        log.warning("could not log %s event for account %s: %s", kind, pk, exc)
        return None


@dataclass
class Result:
    ok: bool
    message: str = ""
    status: str = ""
    reason: str = ""
    state: str = ""   # login state: running / needs_code / verifying / ok / failed / cancelled
    hint: str = ""
    extra: dict = field(default_factory=dict)

    def as_dict(self) -> dict:
        out = {"success": self.ok, "message": self.message, "status": self.status, "reason": self.reason,
               "state": self.state, "requires_code": self.state == "needs_code", "hint": self.hint,
               "challenge_required": self.status == "CHALLENGED"}
        out.update(self.extra)
        return out


def _result(account, ok: bool, message: str, **kw) -> Result:
    return Result(ok, message, status=account.status, reason=account.status_reason or "", **kw)


def has_session(account) -> bool:
    sj = account.session_json if isinstance(account.session_json, dict) else {}
    return bool((sj.get("cookies") or {}).get("sessionid") or (sj.get("authorization_data") or {}).get("sessionid"))


def _apply_state(account, status: str, reason: str = "", message: str = "", *, cooldown_minutes=None,
                 force: bool = False) -> list[str]:
    """Sets status/reason/message/cooldown on the instance; returns the fields to save.

    A paused (DISABLED) account stays paused (the outcome only goes to its message) unless `force` (resume).
    """
    if account.status == "DISABLED" and status != "DISABLED" and not force:
        account.status_message = (message or "")[:255]
        return ["status_message"]
    account.status = status
    account.status_reason = reason or ""
    account.status_message = (message or "")[:255]
    account.cooldown_until = timezone.now() + timedelta(minutes=cooldown_minutes) if cooldown_minutes else None
    return ["status", "status_reason", "status_message", "cooldown_until"]


def set_state(account, status: str, reason: str = "", message: str = "", *, cooldown_minutes=None,
              extra_fields=(), force: bool = False) -> None:
    fields = _apply_state(account, status, reason, message, cooldown_minutes=cooldown_minutes, force=force)
    account.save(update_fields=list(dict.fromkeys([*fields, *extra_fields])))


# ---------------------------------------------------------------------------
# proxy / config
# ---------------------------------------------------------------------------
def default_proxy_url() -> str:
    """Proxy for accounts without their own proxy or config ("direct"/"none" = server IP, not recommended)."""
    raw = str(getattr(settings, "INSTAGRAM_DEFAULT_PROXY_URL", f"socks5h://{XRAY_HOST}:{POOL_PORT}") or "").strip()
    return "" if raw.lower() in ("", "direct", "none", "off") else raw


def _strip_fragment(url) -> str:
    from apps.discovery.proxy_service import clean_proxy_url
    return clean_proxy_url(url or "")


def effective_proxy_url(account) -> str:
    """The proxy every Instagram client of `account` must use (its own/config, else the shared pool)."""
    return _strip_fragment(getattr(account, "proxy_url", "") or "") or default_proxy_url()


def xray_port(url) -> int | None:
    """Port of an xray inbound URL (socks5h://xray:1083), None for any other proxy."""
    try:
        parsed = urlparse(_strip_fragment(url))
        if (parsed.hostname or "").lower() != XRAY_HOST:
            return None
        return parsed.port
    except ValueError:
        return None


def config_url(config) -> str:
    return f"socks5h://{XRAY_HOST}:{config.local_port}"


def proxy_mode(account) -> str:
    """config (system V2Ray config) | custom (its own proxy) | legacy (xray port without config link) | pool."""
    if account.proxy_config_id:
        return "config"
    url = _strip_fragment(account.proxy_url)
    if not url:
        return "pool"
    port = xray_port(url)
    if port is None:
        return "custom"
    return "pool" if port in RESERVED_PORTS else "legacy"


def proxy_label(account) -> str:
    mode = proxy_mode(account)
    if mode == "config":
        cfg = account.proxy_config
        return f"کانفیگ سیستم #{cfg.pk} ({cfg.tag or cfg.server}) · پورت {cfg.local_port}"
    if mode == "custom":
        parsed = urlparse(_strip_fragment(account.proxy_url))
        return f"پروکسی اختصاصی {parsed.scheme}://{parsed.hostname}:{parsed.port or ''}"
    if mode == "legacy":
        return f"پورت xray {xray_port(account.proxy_url)} (کانفیگش پیدا نشد)"
    return "استخر مشترک سیستم" if default_proxy_url() else "بدون پروکسی (IP سرور)"


def normalize_proxy_url(raw) -> str:
    """http/socks URL; also accepts host:port and host:port:user:pass (as sold by most proxy providers)."""
    url = str(raw or "").strip()
    if not url:
        return ""
    if "://" not in url:
        parts = url.split(":")
        if len(parts) == 2:
            url = f"http://{parts[0]}:{parts[1]}"
        elif len(parts) == 4:
            url = f"http://{parts[2]}:{parts[3]}@{parts[0]}:{parts[1]}"
    return url


def validate_proxy_url(url) -> str:
    """Error text, or "" when `url` is a usable http/socks proxy URL."""
    try:
        parsed = urlparse(_strip_fragment(url))
        port = parsed.port
    except ValueError:
        return "فرمت پروکسی نامعتبر است."
    if parsed.scheme.lower() not in PROXY_SCHEMES:
        return "پروکسی باید با http:// یا socks5:// شروع شود (لینک vless/vmess را در «کانفیگ V2Ray» وارد کنید)."
    if not parsed.hostname or not port:
        return "آدرس و پورت پروکسی کامل نیست."
    return ""


def port_open(host: str, port: int, timeout: float = 1.5) -> bool:
    try:
        with socket.create_connection((host, int(port)), timeout=timeout):
            return True
    except (OSError, ValueError):
        return False


def proxy_reachable(url, timeout: float = 4.0) -> tuple[bool, str]:
    """TCP check of the proxy host:port (the Instagram check itself tells whether the upstream works)."""
    url = _strip_fragment(url)
    if not url:
        return True, "بدون پروکسی (IP سرور)"
    try:
        parsed = urlparse(url)
        host, port = parsed.hostname, parsed.port or (1080 if "socks" in (parsed.scheme or "") else 8080)
    except ValueError:
        return False, "فرمت پروکسی نامعتبر است"
    if not host:
        return False, "فرمت پروکسی نامعتبر است"
    if port_open(host, port, timeout):
        return True, f"پروکسی {host}:{port} در دسترس است"
    return False, f"پروکسی {host}:{port} در دسترس نیست"


def _config_live(config) -> bool:
    """The config is ACTIVE and xray really listens on its port (new configs only go live after xray reloads)."""
    return bool(config and config.status == "ACTIVE" and config.local_port
                and config.local_port not in RESERVED_PORTS and port_open(XRAY_HOST, config.local_port))


def _per_config() -> int:
    try:
        return max(1, int(getattr(settings, "INSTAGRAM_ACCOUNTS_PER_CONFIG", 1) or 1))
    except (TypeError, ValueError):
        return 1


def pick_free_config(exclude_ids=(), max_accounts: int | None = None):
    """A live ACTIVE config with room (INSTAGRAM_ACCOUNTS_PER_CONFIG, default 1 = exclusive): fewest Instagram
    accounts first, configs used by Twitter last, then lowest ping. None when every config is taken."""
    limit = max_accounts or _per_config()
    qs = (ProxyConfig.objects.filter(status="ACTIVE", local_port__isnull=False)
          .exclude(local_port__in=RESERVED_PORTS).exclude(pk__in=list(exclude_ids))
          .annotate(n_ig=Count("instagram_accounts"),
                    is_tw=Case(When(assigned_platform="TWITTER", then=Value(1)), default=Value(0),
                               output_field=IntegerField()),
                    ping_key=Case(When(ping_ms__lte=0, then=Value(99999.0)), default=F("ping_ms"),
                                  output_field=FloatField()))
          .filter(n_ig__lt=limit).order_by("n_ig", "is_tw", "ping_key", "pk"))
    for cfg in qs[:25]:
        if _config_live(cfg):
            return cfg
    return None


def pool_config():
    """The config behind the shared pool port: xray routes 1080 to the ACTIVE config with the lowest port."""
    return (ProxyConfig.objects.filter(status="ACTIVE", local_port__isnull=False)
            .exclude(local_port__in=RESERVED_PORTS).order_by("local_port").first())


def _sync_config_owner(config) -> None:
    """ProxyConfig.assigned_* mirrors the Instagram accounts using it (Twitter keeps configs no IG account uses)."""
    if config is None or not config.pk:
        return
    first = config.instagram_accounts.order_by("pk").values_list("username", flat=True).first()
    if first:
        ProxyConfig.objects.filter(pk=config.pk).update(assigned_platform="INSTAGRAM", assigned_to=first)
    elif config.assigned_platform == "INSTAGRAM":
        ProxyConfig.objects.filter(pk=config.pk).update(assigned_platform="FREE", assigned_to="")


def set_proxy(account, mode: str, *, url: str = "", config=None, actor: str = "", note: str = "",
              settle: bool = True) -> Result:
    """Moves `account` to its own proxy (custom), a given system config (config), the automatic choice (system:
    keeps a live config, else the best free one, else the pool), a different free config (new_config) or the
    shared pool (pool). An IP change is logged and delays the next cold DM (settle time)."""
    before_url, before_cfg = effective_proxy_url(account), account.proxy_config
    if mode == "custom":
        url = normalize_proxy_url(url)
        err = validate_proxy_url(url)
        if err:
            return _result(account, False, err)
        new_url, new_cfg = url, None
    elif mode == "config":
        if config is None or not config.local_port or config.local_port in RESERVED_PORTS:
            return _result(account, False, "کانفیگ انتخاب‌شده پورت اختصاصی ندارد.")
        if config.status != "ACTIVE":
            return _result(account, False, "کانفیگ انتخاب‌شده فعال نیست.")
        if (before_cfg is None or before_cfg.pk != config.pk) and not _config_live(config):
            return _result(account, False, f"پورت {config.local_port} این کانفیگ هنوز در xray باز نیست (کانفیگ تازه چند دقیقه "
                                           "بعد از ثبت فعال می‌شود)؛ کمی بعد دوباره امتحان کنید.")
        new_url, new_cfg = config_url(config), config
    elif mode == "system":   # automatic: keep a live config, else a free one, else the pool
        if proxy_mode(account) != "custom":
            moved = _heal_account(account, actor or "system")
            account.refresh_from_db()
            return _result(account, True, f"اتصال اکانت: {proxy_label(account)}" + ("" if moved else " (بدون تغییر)"),
                           extra={"changed": bool(moved and moved.get("changed")), "mode": proxy_mode(account)})
        cfg = pick_free_config()
        new_url, new_cfg = (config_url(cfg), cfg) if cfg else ("", None)
    elif mode == "new_config":   # a different free config (new IP), e.g. after the current IP got flagged
        cfg = pick_free_config(exclude_ids=[before_cfg.pk] if before_cfg else ())
        if cfg is None:
            return _result(account, False, "کانفیگ آزاد دیگری نیست؛ اتصال تغییری نکرد.")
        new_url, new_cfg = config_url(cfg), cfg
    elif mode == "pool":
        new_url, new_cfg = "", None
    else:
        return _result(account, False, "نوع اتصال نامعتبر است.")

    account.proxy_url = new_url or None
    account.proxy_config = new_cfg
    fields = ["proxy_url", "proxy_config"]
    changed = effective_proxy_url(account) != before_url or (
        new_cfg is not None and before_cfg is not None and new_cfg.pk != before_cfg.pk)
    if changed:
        ds = dict(account.device_settings or {})
        if ds.pop("proxy_meta", None) is not None:   # old IP info no longer describes this connection
            account.device_settings = ds
            fields.append("device_settings")
        if settle and account.pk:
            until = timezone.now() + timedelta(minutes=SETTLE_MINUTES)
            if not account.next_outreach_at or account.next_outreach_at < until:
                account.next_outreach_at = until
                fields.append("next_outreach_at")
    account.save(update_fields=fields)
    for cfg in {before_cfg, new_cfg} - {None}:
        _sync_config_owner(cfg)
    label = proxy_label(account)
    if changed:
        log_event(account, "proxy_changed", note or f"اتصال جدید: {label}",
                  {"from": before_url, "to": effective_proxy_url(account), "mode": proxy_mode(account),
                   "config_id": new_cfg.pk if new_cfg else None}, actor)
    return _result(account, True, f"اتصال اکانت: {label}" + (f" · دایرکت سرد تا {SETTLE_MINUTES} دقیقه "
                                                             "بعد از تغییر IP ارسال نمی‌شود." if changed and settle else ""),
                   extra={"changed": changed, "mode": proxy_mode(account)})


def assign_system_config(account, *, actor: str = "system", note: str = "") -> Result | None:
    """Gives an account without its own proxy a system config of its own (if one is free). Never touches custom."""
    if proxy_mode(account) == "custom":
        return None
    cfg = pick_free_config()
    if cfg is None:
        return None
    return set_proxy(account, "config", config=cfg, actor=actor, note=note or f"کانفیگ اختصاصی #{cfg.pk} داده شد")


def release_config(config, *, actor: str = "system", note: str = "") -> list[str]:
    """Called before a config is deleted/marked dead: its accounts move to a free config (else the pool until
    ensure_system_configs finds one). Returns the usernames moved."""
    moved = []
    port_url = config_url(config) if config.local_port else None
    qs = InstagramEngineAccount.objects.filter(proxy_config=config)
    if port_url:
        qs = qs | InstagramEngineAccount.objects.filter(proxy_config__isnull=True, proxy_url=port_url)
    accounts = list(qs.distinct())
    xray_up = bool(accounts) and port_open(XRAY_HOST, POOL_PORT)
    for acc in accounts:
        log_event(acc, "proxy_down", note or f"کانفیگ #{config.pk} ({config.tag or config.server}) از کار افتاد",
                  {"config_id": config.pk}, actor)
        new = pick_free_config(exclude_ids=[config.pk]) if xray_up and acc.status not in ("BANNED", "DISABLED") else None
        if new is not None:
            set_proxy(acc, "config", config=new, actor=actor, note=f"کانفیگ #{new.pk} جایگزین کانفیگ #{config.pk} شد")
        else:
            set_proxy(acc, "pool", actor=actor, note="موقتاً روی استخر مشترک تا کانفیگ تازه پیدا شود")
        moved.append(acc.username)
    return moved


def _heal_account(acc, actor: str = "system") -> dict | None:
    """Sticky config for one account without its own proxy (see ensure_system_configs). None: nothing moved."""
    mode = proxy_mode(acc)
    if mode == "custom":
        return None
    parked = acc.status in ("BANNED", "DISABLED")   # keeps a live config, never takes a free one from the others
    if mode == "legacy":   # older rows: an xray port without the config link
        cfg = ProxyConfig.objects.filter(local_port=xray_port(acc.proxy_url)).first()
        if cfg is not None and cfg.status == "ACTIVE":
            acc.proxy_config = cfg
            acc.save(update_fields=["proxy_config"])
            _sync_config_owner(cfg)
            mode = "config"
    old = None
    if mode == "config":
        cfg = acc.proxy_config
        if _config_live(cfg):
            if acc.proxy_url != config_url(cfg):   # mirror drifted: same config, same IP
                acc.proxy_url = config_url(cfg)
                acc.save(update_fields=["proxy_url"])
            # shared by more accounts than allowed (left by the old round-robin): the oldest ones keep it,
            # the others move to a free config of their own when there is one
            keep = list(cfg.instagram_accounts.order_by("pk").values_list("pk", flat=True)[:_per_config()])
            new = None if parked or acc.pk in keep else pick_free_config(exclude_ids=[cfg.pk])
            if new is None:
                return None
            res = set_proxy(acc, "config", config=new, actor=actor,
                            note=f"کانفیگ #{cfg.pk} بین چند اکانت مشترک بود؛ کانفیگ اختصاصی #{new.pk} داده شد")
            return {"username": acc.username, "from": cfg.pk, "to": new.pk, "new_port": new.local_port,
                    "changed": bool(res.extra.get("changed"))}
        old = cfg.pk
        log_event(acc, "proxy_down", f"کانفیگ #{cfg.pk} در دسترس نیست (وضعیت {cfg.status}، پورت {cfg.local_port})",
                  {"config_id": cfg.pk}, actor)
        why = f"کانفیگ #{cfg.pk} از کار افتاد"
    elif mode == "legacy":
        why = f"پورت {xray_port(acc.proxy_url)} کانفیگ فعالی ندارد"
    elif parked:
        return None
    else:
        why = "قبلاً استخر مشترک"
    new = None if parked else pick_free_config(exclude_ids=[old] if old else ())
    if new is not None:
        res = set_proxy(acc, "config", config=new, actor=actor, note=f"{why}؛ کانفیگ اختصاصی #{new.pk} داده شد")
    elif mode == "pool":
        return None
    else:
        res = set_proxy(acc, "pool", actor=actor, note=f"{why}؛ کانفیگ آزادی نبود، موقتاً استخر مشترک")
    return {"username": acc.username, "from": old, "to": new.pk if new else None,
            "new_port": new.local_port if new else POOL_PORT, "changed": bool(res.extra.get("changed"))}


def ensure_system_configs(*, actor: str = "system", accounts=None) -> dict:
    """Heal + sticky assignment for every account without its own proxy (replaces the old round-robin).

    * config still ACTIVE and listening -> untouched (the account keeps its IP)
    * config dead/gone/not loaded in xray -> another free config (logged, settle time), else the pool
    * xray port without a config link (older rows) -> linked to the config on that port
    * pool -> a free config of its own when one exists (working accounts first)
    * paused/banned accounts keep a live config but never take a free one
    Nothing happens when xray itself is down (pool port closed): moving every account would not help.
    """
    details: list[dict] = []
    if not port_open(XRAY_HOST, POOL_PORT):
        return {"success": False, "skipped": "xray_down", "healed_count": 0, "details": details}
    if accounts is None:
        accounts = (InstagramEngineAccount.objects.select_related("proxy_config")
                    .annotate(_rank=Case(When(status="ACTIVE", then=Value(0)), When(status="COOLDOWN", then=Value(1)),
                                         When(status="CHALLENGED", then=Value(2)), default=Value(3),
                                         output_field=IntegerField()))
                    .order_by("_rank", "pk"))
    for acc in accounts:
        try:
            moved = _heal_account(acc, actor)
        except Exception as exc:   # one bad row must not stop the others
            log.warning("could not heal the connection of @%s: %s", acc.username, exc)
            continue
        if moved:
            details.append(moved)
    return {"success": True, "healed_count": len(details), "details": details}


# ---------------------------------------------------------------------------
# config export (open the account on a phone from the same IP)
# ---------------------------------------------------------------------------
def export_config(account) -> dict:
    mode = proxy_mode(account)
    out = {"mode": mode, "label": proxy_label(account), "link": "", "kind": "", "note": "", "shared_with": []}
    if mode in ("config", "legacy"):
        cfg = account.proxy_config if mode == "config" else \
            ProxyConfig.objects.filter(local_port=xray_port(account.proxy_url)).first()
        if cfg is None:
            out["note"] = "کانفیگ این اکانت دیگر وجود ندارد؛ «بررسی کانفیگ» را بزنید تا کانفیگ تازه بگیرد."
            return out
        others = list(cfg.instagram_accounts.exclude(pk=account.pk).values_list("username", flat=True))
        out.update(kind="v2ray", link=cfg.raw_url, title=cfg.tag or cfg.server, protocol=cfg.protocol,
                   server=f"{cfg.server}:{cfg.port}", config_id=cfg.pk, shared_with=others,
                   note="این لینک را در v2rayNG / v2rayN / Streisand / Hiddify وارد و وصل کنید، بعد اینستاگرام را باز کنید: "
                        "همان IP که سیستم با آن به این اکانت وصل می‌شود.")
        return out
    if mode == "custom":
        url = _strip_fragment(account.proxy_url)
        parsed = urlparse(url)
        out.update(kind="proxy", link=url, scheme=parsed.scheme, host=parsed.hostname, port=parsed.port,
                   username=parsed.username or "",
                   note="پروکسی اختصاصی این اکانت: در تنظیمات پروکسی گوشی یا اپ‌هایی مثل Super Proxy / v2rayNG "
                        "(نوع HTTP یا SOCKS) وارد کنید.")
        return out
    cfg = pool_config()
    if cfg is not None:
        out.update(kind="v2ray", link=cfg.raw_url, title=cfg.tag or cfg.server, protocol=cfg.protocol,
                   server=f"{cfg.server}:{cfg.port}", config_id=cfg.pk, shared=True,
                   note="این اکانت کانفیگ اختصاصی ندارد و از استخر مشترک استفاده می‌کند (IP مشترک بین اکانت‌ها و با "
                        "تغییر کانفیگ‌ها عوض می‌شود). برای IP ثابت «کانفیگ اختصاصی» بدهید.")
    else:
        out["note"] = "استخر مشترک از تونل پشتیبان سیستم استفاده می‌کند و لینک قابل خروجی ندارد."
    return out


def export_text(accounts) -> str:
    lines = []
    for acc in accounts:
        exp = export_config(acc)
        lines += [f"@{acc.username} | {acc.get_role_display()} | {acc.tenant_id}", f"اتصال: {exp['label']}"]
        if exp.get("link"):
            lines.append(exp["link"])
        if exp.get("shared_with"):
            lines.append("مشترک با: " + ", ".join("@" + u for u in exp["shared_with"]))
        if exp.get("note"):
            lines.append(exp["note"])
        lines.append("")
    return "\n".join(lines).strip() + "\n"


def qr_svg(text: str, scale: int = 4) -> str | None:
    """SVG QR code of `text` (None when segno is not installed or the text is too long)."""
    if not text:
        return None
    try:
        import io
        import segno
        buf = io.BytesIO()
        segno.make(text, error="l").save(buf, kind="svg", scale=scale, border=2, xmldecl=False)
        return buf.getvalue().decode("utf-8")
    except Exception as exc:  # ImportError or DataOverflowError
        log.info("QR code unavailable: %s", exc.__class__.__name__)
        return None


def check_ip(account, *, actor: str = "") -> Result:
    """Public IP / ISP / country seen through the account's connection (stored in proxy_meta, logged)."""
    from apps.discovery.proxy_service import inspect_proxy_egress

    info = inspect_proxy_egress(effective_proxy_url(account))
    ds = dict(account.device_settings or {})
    meta = dict(ds.get("proxy_meta") or {})
    prev = meta.get("last_ip")
    ok = info.get("status") in ("success", "direct")
    if ok:
        meta.update(last_ip=info.get("ip", ""), isp=info.get("isp", ""), country=info.get("country", ""),
                    country_code=info.get("country_code", ""), classification=info.get("classification", ""),
                    risk_score=info.get("risk_score", ""), checked_at=_now_iso())
        ds["proxy_meta"] = meta
        account.device_settings = ds
        account.save(update_fields=["device_settings"])
        changed = bool(prev and prev != meta["last_ip"])
        msg = f"IP خروجی {meta['last_ip']} · {meta['country']} · {meta['isp']}" + (f" (قبلاً {prev})" if changed else "")
        log_event(account, "ip_checked", msg, {"ip": meta["last_ip"], "country": meta["country"], "isp": meta["isp"],
                                               "classification": meta["classification"], "previous": prev}, actor)
        return _result(account, True, msg, extra={"ip": meta["last_ip"], "changed": changed})
    log_event(account, "ip_checked", "IP خروجی پیدا نشد (اتصال پروکسی/کانفیگ برقرار نشد)", None, actor)
    return _result(account, False, "IP خروجی پیدا نشد؛ اتصال پروکسی/کانفیگ برقرار نشد.")


# ---------------------------------------------------------------------------
# instagrapi client
# ---------------------------------------------------------------------------
def _client_class():
    from instagrapi import Client  # looked up at call time (tests patch instagrapi.Client)
    return Client


def _mount_timeouts(cl, connect: float = 10, read: float = 35) -> None:
    """Network timeouts for every request (instagrapi has none; a dead proxy would hang a login forever).
    Mount after settings are loaded: instagrapi re-mounts its own adapters in set_settings()."""
    try:
        from requests.adapters import HTTPAdapter
    except ImportError:
        return

    class _TimeoutAdapter(HTTPAdapter):
        def send(self, request, **kwargs):
            if kwargs.get("timeout") is None:
                kwargs["timeout"] = (connect, read)
            return super().send(request, **kwargs)

    adapter = _TimeoutAdapter()
    for session in (getattr(cl, "private", None), getattr(cl, "public", None)):
        try:
            session.mount("https://", adapter)
            session.mount("http://", adapter)
        except Exception:
            pass


def _no_code(username, choice=None):
    return ""   # instagrapi raises ChallengeRequired at once: nobody is there to type a code


def _no_new_password(username):
    from instagrapi.exceptions import ChallengeRequired
    raise ChallengeRequired("Instagram asks for a password change: do it in the Instagram app, then save it here")


def new_client(account, *, code_handler: Callable | None = None):
    """instagrapi Client bound to the account's proxy (never the server IP unless configured so)."""
    cl = _client_class()()
    proxy = effective_proxy_url(account)
    if proxy:
        cl.set_proxy(proxy)
    cl.challenge_code_handler = code_handler or _no_code
    cl.change_password_handler = _no_new_password
    return cl


def connect(account):
    """Client authenticated from the saved session on the account's own phone (no login request when the saved
    session is complete). Raises like instagrapi when the session is missing/rejected."""
    cl = new_client(account)
    connect_saved_session(cl, account)
    _mount_timeouts(cl)
    return cl


def save_session(account, cl, *, kind: str = "login_ok", message: str = "", actor: str = "",
                 activate: bool = True) -> None:
    """Stores the client's settings (session + same phone) and, by default, puts the account back to work."""
    fresh = cl.get_settings()
    account.session_json = fresh
    account.device_settings = account_device_settings(account, client_settings=fresh)
    fields = ["session_json", "device_settings"]
    if activate:
        fields += _apply_state(account, "ACTIVE", "", message)
    account.save(update_fields=fields)
    if kind:
        log_event(account, kind, message, None, actor)


# ---------------------------------------------------------------------------
# failure mapping
# ---------------------------------------------------------------------------
_APP_APPROVAL = ("manual verification", "native challenge", "auth platform", "bloks redirect", "ufac", "selfie",
                 "recaptcha", "password change", "change_password", "select_contact_point_recovery", "submit_phone")


def _failure(exc, *, code_used: bool = False, timed_out: bool = False) -> tuple[str, str, str, int | None]:
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
    status, reason, message, minutes = _failure(exc, code_used=code_used, timed_out=timed_out)
    set_state(account, status, reason, message, cooldown_minutes=minutes)
    log_event(account, "code_failed" if code_used else "login_failed", message,
              {"error": redact(exc, 200), "reason": reason}, actor)
    return _result(account, False, message, state="failed")


# ---------------------------------------------------------------------------
# login with a code typed by the operator
# ---------------------------------------------------------------------------
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
    beat = _parse_iso(pending.get("heartbeat") or pending.get("started_at"))
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
        now = _now_iso()
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
                                                                 "heartbeat": _now_iso()})
                self.used = True
                return str(code)
            if time.monotonic() - last_beat >= HEARTBEAT_SECONDS:
                _update_pending(self.pk, self.token, lambda p: {**p, "heartbeat": _now_iso()})
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
    cl = new_client(account, code_handler=waiter)
    waiter.client = cl
    apply_device_profile_to_client(cl, account)   # the account's one phone
    _mount_timeouts(cl)
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
                                              "finished_at": _now_iso()})
        return res
    account.refresh_from_db()
    used = waiter.used or two_factor_used
    message = "کد تأیید پذیرفته شد و اکانت وارد شد." if used else "ورود موفق؛ سشن ذخیره شد."
    save_session(account, cl, kind="code_ok" if used else "login_ok", message=message, actor=actor)
    _update_pending(pk, token, lambda p: {**p, "state": "ok", "message": message, "code": None,
                                          "finished_at": _now_iso()})
    return _result(account, True, message, state="ok")


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
        return _result(account, False, "برای ورود، پسورد اکانت را ثبت کنید (یا sessionid بدهید).")
    pending = account.pending_login or {}
    if _is_live(pending):
        return _result(account, True, "ورود این اکانت در جریان است.", state=pending.get("state", ""),
                       hint=pending.get("hint", ""))
    token = uuid.uuid4().hex
    now = _now_iso()
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
        return _result(account, False, msg.strip(), state=state, hint=st["hint"])
    if state == "ok":
        return _result(account, True, st["message"] or "ورود موفق.", state=state)
    if state in ("failed", "stale", "cancelled"):
        return _result(account, False, st["message"] or "ورود انجام نشد.", state=state)
    return _result(account, True, "ورود به اینستاگرام شروع شد؛ چند ثانیه صبر کنید…", state=state or "running")


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
        return _result(account, False, "کد باید ۴ تا ۸ رقم باشد.")
    pending = _read_pending(account.pk)
    if not _is_live(pending) or pending.get("state") != "needs_code":
        return _result(account, False, "هیچ ورودی منتظر کد نیست (یا مهلتش تمام شده). «ورود» را بزنید تا کد تازه بیاید و "
                                       "همان را وارد کنید.", state=login_state(account)["state"])
    token = pending.get("token")
    new = _update_pending(account.pk, token, lambda p: {**p, "code": code, "state": "verifying",
                                                         "code_at": _now_iso()} if p.get("state") == "needs_code" else p)
    if not new or new.get("state") != "verifying":
        return _result(account, False, "این ورود دیگر منتظر کد نیست؛ صفحه را تازه کنید.")
    if wait:
        return wait_for_login(account, token, timeout=wait, until=("ok", "failed", "cancelled", "needs_code"))
    return _result(account, True, "کد ثبت شد؛ در حال تأیید با اینستاگرام…", state="verifying")


def cancel_login(account, *, actor: str = "") -> Result:
    pending = _read_pending(account.pk)
    if pending.get("state") in LIVE_STATES:
        _update_pending(account.pk, pending.get("token"), lambda p: {**p, "state": "cancelled", "code": None,
                                                                      "finished_at": _now_iso()})
        log_event(account, "login_failed", "ورود توسط مدیر لغو شد", None, actor)
    account.refresh_from_db()
    if account.status == "CHALLENGED" and account.status_reason == "needs_code":   # nobody waits for a code now
        set_state(account, "CHALLENGED", "session_expired", "ورود لغو شد؛ برای ورود «ورود» را دوباره بزنید.")
    return _result(account, True, "ورود لغو شد.", state="cancelled")


def login_with_sessionid(account, raw_sessionid, *, actor: str = "") -> Result:
    """Connects with a sessionid copied from a browser that is logged in right now (same phone profile)."""
    sid = clean_sessionid(raw_sessionid)
    if not sid:
        return _result(account, False, "مقدار کوکی sessionid را وارد کنید.")
    cl = new_client(account)
    apply_device_profile_to_client(cl, account)
    _mount_timeouts(cl)
    try:
        cl.login_by_sessionid(sid)
    except Exception as exc:
        kind = classify(exc)
        if kind == ErrorKind.NETWORK:
            message = f"اتصال برقرار نشد ({redact(exc, 120)}); وضعیت اکانت تغییری نکرد."
            log_event(account, "login_failed", message, {"error": redact(exc, 200), "via": "sessionid"}, actor)
            return _result(account, False, message, extra={"deleted": False})
        message = (f"سشن واردشده معتبر نبود ({redact(exc, 120)}). sessionid را از مرورگری کپی کنید که همین حالا "
                   "در اکانت لاگین است.")
        set_state(account, "CHALLENGED", "session_expired", message)
        log_event(account, "login_failed", message, {"error": redact(exc, 200), "via": "sessionid"}, actor)
        return _result(account, False, message, extra={"deleted": False})
    if (account.pending_login or {}).get("state") in LIVE_STATES:
        cancel_login(account, actor=actor)
    save_session(account, cl, kind="session_set", message="", actor=actor)
    return _result(account, True, f"سشن اکانت @{account.username} ثبت شد و اکانت فعال است.",
                   extra={"deleted": False})


def check(account, *, actor: str = "", allow_login: bool = True) -> Result:
    """Connection test on the account's own phone and session (what the workers use): never replaces a working
    session, never logs in again while the session works; logs in (background) only when there is no session."""
    proxy = effective_proxy_url(account)
    ok, proxy_msg = proxy_reachable(proxy)
    if not ok and proxy_mode(account) != "custom" and port_open(XRAY_HOST, POOL_PORT):
        _heal_account(account, actor or "system")   # its config died: the next free one (or the pool)
        account.refresh_from_db()
        proxy = effective_proxy_url(account)
        ok, proxy_msg = proxy_reachable(proxy)
    if not ok:
        message = f"پروکسی/کانفیگ در دسترس نیست: {proxy_msg}"
        set_state(account, "COOLDOWN", "proxy_down", message, cooldown_minutes=15)
        log_event(account, "check_failed", message, {"proxy": proxy}, actor)
        return _result(account, False, message)
    now = timezone.now()
    if account.cooldown_until and account.cooldown_until > now:
        minutes = int((account.cooldown_until - now).total_seconds() // 60) + 1
        return _result(account, True, f"اکانت در استراحت است (حدود {minutes} دقیقه مانده)؛ تست انجام نشد.")
    if not has_session(account):
        if allow_login and account.password and account.password != "session_only":
            return start_login(account, actor=actor)
        return _result(account, False, "اکانت نه سشن دارد نه پسورد؛ sessionid یا پسورد را ثبت کنید.")
    try:
        cl = connect(account)
        cl.get_timeline_feed()   # what the app itself requests when it is opened
    except Exception as exc:
        status, reason, message, minutes = _failure(exc)
        if reason == "needs_code":
            reason, message = "session_expired", "اینستاگرام برای این سشن تأیید خواست؛ «ورود» را بزنید و کد را وارد کنید."
        set_state(account, status, reason, message, cooldown_minutes=minutes)
        log_event(account, "session_expired" if reason == "session_expired" else "check_failed", message,
                  {"error": redact(exc, 200)}, actor)
        return _result(account, False, message)
    message = f"سشن معتبر است و اتصال برقرار شد ({proxy_label(account)})."
    save_session(account, cl, kind="check_ok", message=message, actor=actor)
    return _result(account, True, message)


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
            return _result(account, False, "نام کاربری اینستاگرام معتبر نیست.")
        if InstagramEngineAccount.objects.filter(username__iexact=username).exclude(pk=account.pk).exists():
            return _result(account, False, f"اکانت @{username} از قبل وجود دارد.")
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
        return _result(account, True, "تغییری داده نشد.", extra={"changed": []})
    account.save(update_fields=changed)
    if "username" in changed:   # renamed on Instagram: its leads and its config follow it
        from apps.discovery.models import InstagramEngineLead
        InstagramEngineLead.objects.filter(sender_account__iexact=old_username).update(sender_account=account.username)
        InstagramEngineLead.objects.filter(second_sender_username__iexact=old_username).update(
            second_sender_username=account.username)
        _sync_config_owner(account.proxy_config)
    labels = {"username": "نام کاربری", "role": "نقش", "tenant_id": "مشتری", "account_tier": "رده",
              "hourly_limit": "سقف ساعتی", "daily_limit": "سقف روزانه", "password": "پسورد"}
    message = "تغییر: " + "، ".join(dict.fromkeys(labels[f] for f in changed))
    log_event(account, "edited", message, {"fields": ",".join(changed)}, actor)
    return _result(account, True, message, extra={"changed": changed})


def pause(account, *, actor: str = "", note: str = "") -> Result:
    message = note or "توسط مدیر متوقف شد"
    account.status, account.status_reason, account.status_message = "DISABLED", "paused", message[:255]
    account.save(update_fields=["status", "status_reason", "status_message"])
    log_event(account, "status_changed", message, {"status": "DISABLED"}, actor)
    return _result(account, True, "اکانت متوقف شد؛ هیچ سرویسی از آن استفاده نمی‌کند.")


def resume(account, *, actor: str = "") -> Result:
    if has_session(account):
        set_state(account, "ACTIVE", "", "توسط مدیر دوباره فعال شد", force=True)
    else:
        set_state(account, "CHALLENGED", "session_expired", "فعال شد ولی سشن ندارد؛ «ورود» را بزنید.", force=True)
    log_event(account, "status_changed", account.status_message, {"status": account.status}, actor)
    return _result(account, True, account.status_message)


def clear_cooldown(account, *, actor: str = "") -> Result:
    account.cooldown_until = None
    fields = ["cooldown_until", "status_message"]
    account.status_message = "استراحت دستی لغو شد"
    if account.status == "COOLDOWN":
        account.status, account.status_reason = "ACTIVE", ""
        fields += ["status", "status_reason"]
    account.save(update_fields=fields)
    log_event(account, "status_changed", "استراحت توسط مدیر لغو شد", {"status": account.status}, actor)
    return _result(account, True, f"استراحت اکانت @{account.username} لغو شد.")


def delete_account(account, *, actor: str = "") -> str:
    username, cfg = account.username, account.proxy_config
    account.delete()
    _sync_config_owner(cfg)
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
            return account, _result(account, False, f"اکانت @{account.username} قبلاً ثبت شده و فعال است؛ از صفحه‌ی "
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
        _heal_account(account, actor or "system")

    ok, proxy_msg = proxy_reachable(effective_proxy_url(account))
    if not ok:
        message = f"اکانت ثبت شد ولی پروکسی/کانفیگش در دسترس نیست ({proxy_msg})؛ اتصال را درست کنید و «ورود» بزنید."
        set_state(account, "CHALLENGED", "proxy_down", message)
        log_event(account, "check_failed", message, None, actor)
        return account, _result(account, False, message)
    if clean_sessionid(sessionid):
        res = login_with_sessionid(account, sessionid, actor=actor)
        if res.ok or not (login and account.password and account.password != "session_only"):
            return account, res
    if login and account.password and account.password != "session_only":
        return account, start_login(account, actor=actor, wait=login_wait)
    return account, _result(account, True, f"اکانت @{account.username} ثبت شد.")


# ---------------------------------------------------------------------------
# tenants (customers): consultant name / role / pitch
# ---------------------------------------------------------------------------
def canonical_tenant(tenant_id) -> str:
    tid = str(tenant_id or "").strip()
    return TENANT_ALIASES.get(tid, tid)


def tenant_profile(tenant_id) -> dict:
    """{"consultant_name", "consultant_role", "pitch_template"} of the tenant ({} when it has none yet)."""
    row = OutreachTenantProfile.objects.filter(tenant_id=canonical_tenant(tenant_id)).first()
    if row is None:
        return {}
    return {"consultant_name": row.consultant_name, "consultant_role": row.consultant_role,
            "pitch_template": row.pitch_template, "updated_at": row.updated_at}


def save_tenant_profile(tenant_id, *, consultant_name: str = "", consultant_role: str = "", pitch_template: str = "",
                        only_empty: bool = False):
    tid = canonical_tenant(tenant_id)
    row, _ = OutreachTenantProfile.objects.get_or_create(tenant_id=tid)
    values = {"consultant_name": consultant_name.strip()[:100], "consultant_role": consultant_role.strip()[:150],
              "pitch_template": pitch_template.strip()}
    changed = []
    for k, v in values.items():
        if v and (not only_empty or not getattr(row, k)) and getattr(row, k) != v:
            setattr(row, k, v)
            changed.append(k)
    if changed:
        row.save()
    return row


def tenant_label(tenant_id, names: dict | None = None) -> str:
    tid = str(tenant_id or "")
    names = names if names is not None else tenant_names()
    return names.get(tid) or names.get(canonical_tenant(tid)) or tid


def tenant_names() -> dict:
    from apps.businesses.models import Business
    return {f"tenant_{pk}": name for pk, name in Business.objects.values_list("pk", "name")}


def tenant_choices() -> list[tuple[str, str]]:
    names = tenant_names()
    used = set(InstagramEngineAccount.objects.values_list("tenant_id", flat=True).distinct())
    keys = sorted(set(names) | used, key=lambda t: (not t.startswith("tenant_"), t))
    return [(t, f"{names.get(t) or names.get(canonical_tenant(t)) or t} ({t})") for t in keys]


def _tenant_ids(tenant_id) -> set[str]:
    tid = canonical_tenant(tenant_id)
    return {tid, str(tenant_id or "")} | {alias for alias, target in TENANT_ALIASES.items() if target == tid}


def tenant_pitch(tenant_id, usernames=None) -> dict:
    """The consultant name / pitch the tenant's cold DMs use (what _pitch_for resolves): its profile, field by field
    over the legacy per-username pitch of its first sender that has one. source: profile | legacy | ""."""
    from apps.discovery.outreach_engine import load_outreach_config

    out = {"consultant_name": "", "consultant_role": "", "pitch_template": "", "source": ""}
    if usernames is None:
        usernames = list(InstagramEngineAccount.objects.filter(tenant_id__in=_tenant_ids(tenant_id), role="SENDER")
                         .order_by("pk").values_list("username", flat=True))
    pitches = load_outreach_config().get("sender_pitches", {})
    for username in usernames:
        legacy = pitches.get(username) or pitches.get(str(username).lower())
        if legacy:
            out.update(consultant_name=legacy.get("consultant_name", ""), consultant_role=legacy.get("consultant_role", ""),
                       pitch_template=legacy.get("template", ""), source="legacy")
            break
    prof = tenant_profile(tenant_id)
    for key in ("consultant_name", "consultant_role", "pitch_template"):
        if prof.get(key):
            out[key] = prof[key]
            out["source"] = "profile"
    return out
