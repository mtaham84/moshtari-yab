"""Which proxy each account uses: sticky system configs, healing when a config dies, pruning dead configs.

* An Instagram account keeps its config while the config is ACTIVE and xray listens on it (its IP stays the same).
* A dead config's accounts move to a free config, else to the shared pool until one is free.
* Accounts with their own proxy (``custom``) are never moved. Every move is logged on the account's timeline.
"""
from __future__ import annotations

import logging
from datetime import timedelta
from typing import Any

from django.conf import settings
from django.db.models import Case, Count, F, FloatField, IntegerField, Value, When
from django.utils import timezone

from apps.discovery.ig_state import Result, log_event, result_for
from apps.discovery.models import InstagramEngineAccount, ProxyConfig
from apps.discovery.proxies import health
from apps.discovery.proxies.urls import (POOL_PORT, RESERVED_PORTS, XRAY_HOST, config_url, effective_proxy_url,
                                         normalize_proxy_url, proxy_label, proxy_mode, validate_proxy_url, xray_port)

log = logging.getLogger("instagram.accounts")


SETTLE_MINUTES = 45                    # after an IP change no cold DM for this long (a new IP + instant DMs = flag)


def per_config() -> int:
    try:
        return max(1, int(getattr(settings, "INSTAGRAM_ACCOUNTS_PER_CONFIG", 1) or 1))
    except (TypeError, ValueError):
        return 1


def pick_free_config(exclude_ids=(), max_accounts: int | None = None):
    """A live ACTIVE config with room (INSTAGRAM_ACCOUNTS_PER_CONFIG, default 1 = exclusive): fewest Instagram
    accounts first, configs used by Twitter last, then lowest ping. None when every config is taken."""
    limit = max_accounts or per_config()
    qs = (ProxyConfig.objects.filter(status="ACTIVE", local_port__isnull=False)
          .exclude(local_port__in=RESERVED_PORTS).exclude(pk__in=list(exclude_ids))
          .annotate(n_ig=Count("instagram_accounts"),
                    is_tw=Case(When(assigned_platform="TWITTER", then=Value(1)), default=Value(0),
                               output_field=IntegerField()),
                    ping_key=Case(When(ping_ms__lte=0, then=Value(99999.0)), default=F("ping_ms"),
                                  output_field=FloatField()))
          .filter(n_ig__lt=limit).order_by("n_ig", "is_tw", "ping_key", "pk"))
    for cfg in qs[:25]:
        if health.config_live(cfg):
            return cfg
    return None


def pool_config():
    """The config behind the shared pool port: xray routes 1080 to the ACTIVE config with the lowest port."""
    return (ProxyConfig.objects.filter(status="ACTIVE", local_port__isnull=False)
            .exclude(local_port__in=RESERVED_PORTS).order_by("local_port").first())


def sync_config_owner(config) -> None:
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
            return result_for(account, False, err)
        new_url, new_cfg = url, None
    elif mode == "config":
        if config is None or not config.local_port or config.local_port in RESERVED_PORTS:
            return result_for(account, False, "کانفیگ انتخاب‌شده پورت اختصاصی ندارد.")
        if config.status != "ACTIVE":
            return result_for(account, False, "کانفیگ انتخاب‌شده فعال نیست.")
        if (before_cfg is None or before_cfg.pk != config.pk) and not health.config_live(config):
            return result_for(account, False, f"پورت {config.local_port} این کانفیگ هنوز در xray باز نیست (کانفیگ تازه چند دقیقه "
                                           "بعد از ثبت فعال می‌شود)؛ کمی بعد دوباره امتحان کنید.")
        new_url, new_cfg = config_url(config), config
    elif mode == "system":   # automatic: keep a live config, else a free one, else the pool
        if proxy_mode(account) != "custom":
            moved = heal_account(account, actor or "system")
            account.refresh_from_db()
            return result_for(account, True, f"اتصال اکانت: {proxy_label(account)}" + ("" if moved else " (بدون تغییر)"),
                           extra={"changed": bool(moved and moved.get("changed")), "mode": proxy_mode(account)})
        cfg = pick_free_config()
        new_url, new_cfg = (config_url(cfg), cfg) if cfg else ("", None)
    elif mode == "new_config":   # a different free config (new IP), e.g. after the current IP got flagged
        cfg = pick_free_config(exclude_ids=[before_cfg.pk] if before_cfg else ())
        if cfg is None:
            return result_for(account, False, "کانفیگ آزاد دیگری نیست؛ اتصال تغییری نکرد.")
        new_url, new_cfg = config_url(cfg), cfg
    elif mode == "pool":
        new_url, new_cfg = "", None
    else:
        return result_for(account, False, "نوع اتصال نامعتبر است.")

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
        sync_config_owner(cfg)
    label = proxy_label(account)
    if changed:
        log_event(account, "proxy_changed", note or f"اتصال جدید: {label}",
                  {"from": before_url, "to": effective_proxy_url(account), "mode": proxy_mode(account),
                   "config_id": new_cfg.pk if new_cfg else None}, actor)
    return result_for(account, True, f"اتصال اکانت: {label}" + (f" · دایرکت سرد تا {SETTLE_MINUTES} دقیقه "
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
    xray_up = bool(accounts) and health.port_open(XRAY_HOST, POOL_PORT)
    for acc in accounts:
        log_event(acc, "proxy_down", note or f"کانفیگ #{config.pk} ({config.tag or config.server}) از کار افتاد",
                  {"config_id": config.pk}, actor)
        new = pick_free_config(exclude_ids=[config.pk]) if xray_up and acc.status not in ("BANNED", "DISABLED") else None
        if new is None and xray_up and acc.status not in ("BANNED", "DISABLED"):
            # Multi-tier failover: try picking an active config with room before pool
            new = pick_free_config(exclude_ids=[config.pk], max_accounts=max(3, per_config() + 1))

        if new is not None:
            res = set_proxy(acc, "config", config=new, actor=actor, note=f"کانفیگ #{new.pk} جایگزین کانفیگ حذف‌شده #{config.pk} شد")
        else:
            res = set_proxy(acc, "pool", actor=actor, note="موقتاً روی استخر مشترک تا کانفیگ تازه پیدا شود")

        # Lift cooldown if account was in cooldown due to dead proxy
        if acc.status == "COOLDOWN" and acc.status_reason == "proxy_down" and res and res.ok:
            acc.status = "ACTIVE"
            acc.status_reason = ""
            acc.cooldown_until = None
            acc.status_message = "کانفیگ سالم جدید جایگزین شد و اکانت فعال گردید"
            acc.save(update_fields=["status", "status_reason", "cooldown_until", "status_message"])
            log_event(acc, "cooldown_cleared", "استراحت ناشی از قطعی پروکسی پس از جایگزینی کانفیگ حذف‌شده لغو شد", {}, actor)

        moved.append(acc.username)
    return moved


def heal_account(acc, actor: str = "system", force_rotate: bool = False, failed_config_id: int | None = None) -> dict | None:
    """Sticky config for one account without its own proxy (see ensure_system_configs). None: nothing moved."""
    mode = proxy_mode(acc)
    if mode == "custom":
        if not force_rotate:
            return None
        why = "پروکسی اختصاصی از کار افتاد"
    parked = acc.status in ("BANNED", "DISABLED")   # keeps a live config, never takes a free one from the others
    if mode == "legacy":   # older rows: an xray port without the config link
        cfg = ProxyConfig.objects.filter(local_port=xray_port(acc.proxy_url)).first()
        if cfg is not None and cfg.status == "ACTIVE":
            acc.proxy_config = cfg
            acc.save(update_fields=["proxy_config"])
            sync_config_owner(cfg)
            mode = "config"
    old = None
    if mode == "config":
        cfg = acc.proxy_config
        is_current_failed = force_rotate or (failed_config_id is not None and cfg and cfg.pk == failed_config_id)
        live = health.config_live(cfg)
        if live and not is_current_failed:
            if acc.proxy_url != config_url(cfg):   # mirror drifted: same config, same IP
                acc.proxy_url = config_url(cfg)
                acc.save(update_fields=["proxy_url"])
            # shared by more accounts than allowed (left by the old round-robin): the oldest ones keep it,
            # the others move to a free config of their own when there is one
            keep = list(cfg.instagram_accounts.order_by("pk").values_list("pk", flat=True)[:per_config()])
            new = None if parked or acc.pk in keep else pick_free_config(exclude_ids=[cfg.pk])
            if new is None:
                return None
            res = set_proxy(acc, "config", config=new, actor=actor,
                            note=f"کانفیگ #{cfg.pk} بین چند اکانت مشترک بود؛ کانفیگ اختصاصی #{new.pk} داده شد")
            return {"username": acc.username, "from": cfg.pk, "to": new.pk, "new_port": new.local_port,
                    "changed": bool(res.extra.get("changed"))}
        old = cfg.pk if cfg else None
        old = cfg.pk if cfg else None
        if live:   # xray still serves it: only this account saw it fail, the others keep using it
            why = f"کانفیگ #{old} برای این اکانت جواب نداد"
        else:
            if old:
                ProxyConfig.objects.filter(pk=old).update(status="DEAD", ping_ms=-1.0)
                log_event(acc, "proxy_down", f"کانفیگ #{old} در دسترس نیست یا دچار قطعی شد",
                          {"config_id": old}, actor)
            why = f"کانفیگ #{old} از کار افتاد یا قطع شد"
    elif mode == "legacy":
        why = f"پورت {xray_port(acc.proxy_url)} کانفیگ فعالی ندارد"
    elif parked:
        return None
    elif mode != "custom":
        why = "قبلاً استخر مشترک"
    new = None if parked else pick_free_config(exclude_ids=[old] if old else ())
    if new is None and not parked:
        # Multi-tier failover: try picking an active config with room before pool
        new = pick_free_config(exclude_ids=[old] if old else (), max_accounts=max(3, per_config() + 1))
    if new is not None:
        res = set_proxy(acc, "config", config=new, actor=actor, note=f"{why}؛ کانفیگ اختصاصی #{new.pk} داده شد")
    elif mode == "pool":
        return None
    else:
        res = set_proxy(acc, "pool", actor=actor, note=f"{why}؛ کانفیگ آزادی نبود، موقتاً استخر مشترک")

    # If the account was in cooldown due to proxy failure and now has a working proxy, lift the cooldown
    if acc.status == "COOLDOWN" and acc.status_reason == "proxy_down" and res and res.ok:
        acc.status = "ACTIVE"
        acc.status_reason = ""
        acc.cooldown_until = None
        acc.status_message = "پروکسی سالم متصل شد و اکانت از استراحت خارج گردید"
        acc.save(update_fields=["status", "status_reason", "cooldown_until", "status_message"])
        log_event(acc, "cooldown_cleared", "استراحت ناشی از قطعی پروکسی پس از انتساب پروکسی جدید لغو شد", {}, actor)

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
    if not health.port_open(XRAY_HOST, POOL_PORT):
        return {"success": False, "skipped": "xray_down", "healed_count": 0, "details": details}
    if accounts is None:
        accounts = (InstagramEngineAccount.objects.select_related("proxy_config")
                    .annotate(_rank=Case(When(status="ACTIVE", then=Value(0)), When(status="COOLDOWN", then=Value(1)),
                                         When(status="CHALLENGED", then=Value(2)), default=Value(3),
                                         output_field=IntegerField()))
                    .order_by("_rank", "pk"))
    for acc in accounts:
        try:
            moved = heal_account(acc, actor)
        except Exception as exc:   # one bad row must not stop the others
            log.warning("could not heal the connection of @%s: %s", acc.username, exc)
            continue
        if moved:
            details.append(moved)
    return {"success": True, "healed_count": len(details), "details": details}


def prune_dead_configs() -> list[dict[str, Any]]:
    """
    Tests all ProxyConfigs in DB and deletes the dead ones (ping -1 / > 2000ms), then rebuilds config.json.

    Two phases: every dead config is first marked DEAD, then deleted, so an Instagram account leaving a dead config
    (pre_delete -> release_config) only moves to a config that just passed the test.
    """
    from xray.multi_proxy_manager import rebuild_xray_config_file, reload_xray_service

    removed: list[dict[str, Any]] = []
    dead = []
    for p in list(ProxyConfig.objects.all()):
        ok, ping_ms, _ = health.measure_config(p, timeout_sec=2.0)
        if ok:
            p.ping_ms = ping_ms
            p.status = "ACTIVE"
            p.last_checked_at = timezone.now()
            p.save(update_fields=["ping_ms", "status", "last_checked_at"])
            continue
        log.info("Pruning dead proxy #%s (%s, ping -1 or > 2000ms)", p.id, p.tag)
        removed.append({"id": p.id, "tag": p.tag or p.server, "protocol": p.protocol, "assigned_to": p.assigned_to})
        dead.append(p)
    if dead:
        ProxyConfig.objects.filter(pk__in=[p.pk for p in dead]).update(status="DEAD", last_checked_at=timezone.now())
        for p in dead:
            _detach_proxy_from_account(p)
            p.delete()   # its Instagram accounts move to a live config (pre_delete signal)

    if removed:
        rebuild_xray_config_file()
        reload_xray_service()
        assign_proxies_to_accounts()

    return removed


def _detach_proxy_from_account(proxy_obj) -> None:
    """X/Twitter accounts on a config that is being deleted go back to the shared pool port.

    Instagram accounts are moved by the ProxyConfig pre_delete signal (release_config), which also logs it on the
    account's timeline.
    """
    from apps.discovery.models import TwitterEngineAccount

    if proxy_obj.local_port:
        TwitterEngineAccount.objects.filter(proxy_url=f"socks5h://xray:{proxy_obj.local_port}").update(
            proxy_url="socks5h://xray:1080")


def assign_proxies_to_accounts() -> dict[str, Any]:
    """
    Sticky proxy assignment. (It used to re-deal every config to every account on each call - every 3 minutes - so
    every account's IP kept changing and even accounts with their own proxy were overwritten.)

    * Instagram: ensure_system_configs - an account keeps its config until the config
      dies; accounts with their own proxy are never touched; each move is logged on the account's timeline.
    * X/Twitter: an account keeps its xray port while that config is ACTIVE and no Instagram account uses it;
      otherwise it gets a config nobody uses, else the shared pool (1080). Its own (non-xray) proxy is never touched.
    """
    from apps.discovery.models import TwitterEngineAccount

    ig = ensure_system_configs()
    assignments: list[dict[str, Any]] = [
        {"account": f"@{d['username']}", "platform": "INSTAGRAM", "proxy": f"socks5h://xray:{d['new_port']}",
         "config_id": d.get("to")} for d in ig.get("details", [])]

    active = {p.local_port: p for p in ProxyConfig.objects.filter(status="ACTIVE", local_port__isnull=False)
              .exclude(local_port__in=RESERVED_PORTS)}
    taken = set(ProxyConfig.objects.filter(instagram_accounts__isnull=False).values_list("local_port", flat=True))
    need = []
    for acc in TwitterEngineAccount.objects.filter(is_active=True).order_by("id"):
        url = (acc.proxy_url or "").strip()
        port = xray_port(url)
        if url and port is None:
            continue   # its own proxy
        if port in active and port not in taken:
            taken.add(port)
            cfg = active[port]
            if cfg.assigned_platform != "TWITTER" or cfg.assigned_to != acc.username:
                ProxyConfig.objects.filter(pk=cfg.pk).update(assigned_platform="TWITTER", assigned_to=acc.username)
            continue
        need.append(acc)
    free = sorted((p for port, p in active.items() if port not in taken),
                  key=lambda p: (p.ping_ms if p.ping_ms and p.ping_ms > 0 else 99999.0, p.pk))
    for acc in need:
        cfg = free.pop(0) if free else None
        new_url = f"socks5h://xray:{cfg.local_port}" if cfg else "socks5h://xray:1080"
        if acc.proxy_url != new_url:
            acc.proxy_url = new_url
            acc.save(update_fields=["proxy_url"])
            assignments.append({"account": f"@{acc.username}", "platform": "TWITTER", "proxy": new_url,
                                "config_id": cfg.pk if cfg else None})
        if cfg is not None:
            ProxyConfig.objects.filter(pk=cfg.pk).update(assigned_platform="TWITTER", assigned_to=acc.username)

    on_configs = (InstagramEngineAccount.objects.filter(proxy_config__status="ACTIVE").count()
                  + sum(1 for u in TwitterEngineAccount.objects.filter(is_active=True).values_list("proxy_url", flat=True)
                        if xray_port(u or "") in active))
    return {"total_assigned": on_configs, "moved": len(assignments), "assignments": assignments,
            "instagram": {k: v for k, v in ig.items() if k != "details"}}
