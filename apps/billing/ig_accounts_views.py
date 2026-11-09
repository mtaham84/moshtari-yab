"""Instagram accounts panel (/manage/instagram/accounts/): the one place where staff add, log in (with the code
typed right here), check, connect, re-proxy, pause, export and delete Instagram accounts, and set each customer's
consultant name / pitch. Every action is done by apps.discovery.ig_accounts and lands on the account's timeline.
"""
from __future__ import annotations

import json
import logging

from django.contrib import messages
from django.db.models import Count
from django.http import HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_GET, require_http_methods, require_POST

from apps.billing.manage_views import manage_access_required
from apps.core.http import wants_json
from apps.discovery import ig_accounts as svc
from apps.discovery.models import InstagramEngineAccount, ProxyConfig
from apps.discovery.proxies import health, urls as proxy_urls

log = logging.getLogger("instagram.accounts")

ST_CLASS = {"ACTIVE": "ok", "CHALLENGED": "warn", "COOLDOWN": "warn", "BANNED": "down", "DISABLED": "PAUSED"}
PROXY_MODES = (("config", "کانفیگ اختصاصی سیستم"), ("pool", "استخر مشترک"), ("custom", "پروکسی اختصاصی"),
               ("legacy", "پورت بدون کانفیگ"))


def _actor(request) -> str:
    return request.user.get_username()


def _post(request) -> dict:
    """Form fields, or the JSON body of fetch() calls."""
    if request.content_type == "application/json":
        try:
            data = json.loads(request.body or b"{}")
            return data if isinstance(data, dict) else {}
        except ValueError:
            return {}
    return request.POST


def _detail_url(pk, anchor: str = "") -> str:
    return reverse("manage:ig_account_detail", args=[pk]) + anchor


def _respond(request, res, pk=None, anchor: str = ""):
    if wants_json(request):
        return JsonResponse(res.as_dict())
    if res.state == "needs_code":
        messages.warning(request, res.message)
    elif res.message:
        (messages.success if res.ok else messages.error)(request, res.message)
    return redirect(_detail_url(pk, anchor) if pk else reverse("manage:ig_accounts"))


def _config_choices():
    """ACTIVE system configs with how many Instagram accounts use them (for the connection selects)."""
    return list(ProxyConfig.objects.filter(status="ACTIVE", local_port__isnull=False)
                .exclude(local_port__in=proxy_urls.RESERVED_PORTS)
                .annotate(n_ig=Count("instagram_accounts")).order_by("n_ig", "ping_ms", "pk")[:200])


def _row(acc, names):
    meta = acc.proxy_meta
    pending = acc.pending_login or {}
    return {"a": acc, "st": ST_CLASS.get(acc.status, ""), "mode": proxy_urls.proxy_mode(acc), "label": proxy_urls.proxy_label(acc),
            "tenant": svc.tenant_label(acc.tenant_id, names), "ip": meta.get("last_ip") or "",
            "country": meta.get("country_code") or meta.get("country") or "",
            "login": pending.get("state", "") if svc._is_live(pending) else "",
            "has_session": svc.has_session(acc)}


@manage_access_required
@require_GET
def ig_accounts_list(request):
    f = {k: (request.GET.get(k) or "").strip() for k in ("status", "role", "tenant", "mode", "q")}
    qs = InstagramEngineAccount.objects.select_related("proxy_config").order_by("role", "tenant_id", "username")
    if f["status"]:
        qs = qs.filter(status=f["status"])
    if f["role"]:
        qs = qs.filter(role=f["role"])
    if f["tenant"]:
        qs = qs.filter(tenant_id__in=svc._tenant_ids(f["tenant"]))
    if f["q"]:
        qs = qs.filter(username__icontains=f["q"].lstrip("@"))
    names = svc.tenant_names()
    rows = [r for r in (_row(acc, names) for acc in qs) if not f["mode"] or r["mode"] == f["mode"]]

    by_status = dict(InstagramEngineAccount.objects.values_list("status").annotate(n=Count("id")))
    configs = ProxyConfig.objects.filter(status="ACTIVE", local_port__isnull=False).exclude(local_port__in=proxy_urls.RESERVED_PORTS)
    stats = {
        "total": sum(by_status.values()), "active": by_status.get("ACTIVE", 0),
        "challenged": by_status.get("CHALLENGED", 0), "cooldown": by_status.get("COOLDOWN", 0),
        "paused": by_status.get("DISABLED", 0), "banned": by_status.get("BANNED", 0),
        "on_config": InstagramEngineAccount.objects.filter(proxy_config__isnull=False).count(),
        "configs": configs.count(),
        "free_configs": configs.annotate(n_ig=Count("instagram_accounts")).filter(n_ig=0).count(),
        "xray_up": health.port_open(proxy_urls.XRAY_HOST, proxy_urls.POOL_PORT),
        "pool_url": proxy_urls.default_proxy_url(),
    }
    return render(request, "ops/ig_accounts/list.html", {
        "nav": "ig_accounts", "rows": rows, "f": f, "stats": stats,
        "statuses": InstagramEngineAccount.STATUS_CHOICES, "roles": InstagramEngineAccount.ROLE_CHOICES,
        "tiers": InstagramEngineAccount.TIER_CHOICES, "modes": PROXY_MODES, "tenants": svc.tenant_choices(),
        "configs": _config_choices(), "per_config": svc._per_config(),
    })


@manage_access_required
@require_POST
def ig_account_add(request):
    p = request.POST
    config = None
    if p.get("proxy") == "config" and (p.get("config_id") or "").isdigit():
        config = ProxyConfig.objects.filter(pk=int(p["config_id"])).first()
    acc, res = svc.add_account(
        username=p.get("username", ""), password=p.get("password", ""), sessionid=p.get("sessionid", ""),
        role=p.get("role", "SENDER"), tenant_id=p.get("tenant_id") or "tenant_default",
        account_tier=p.get("account_tier", "NEW"), daily_limit=p.get("daily_limit"), proxy=p.get("proxy") or "system",
        proxy_url=p.get("proxy_url", ""), config=config, actor=_actor(request), login=True)
    if acc is None:
        if wants_json(request):
            return JsonResponse(res.as_dict())
        messages.error(request, res.message)
        return redirect(reverse("manage:ig_accounts") + "#add")
    return _respond(request, res, acc.pk, "#login")


@manage_access_required
@require_POST
def ig_accounts_heal(request):
    out = svc.ensure_system_configs(actor=_actor(request))
    if out.get("skipped") == "xray_down":
        messages.error(request, "xray در دسترس نیست (پورت استخر 1080 بسته است)؛ تا بالا نیاید اتصال‌ها جابه‌جا نمی‌شوند.")
    elif out["healed_count"]:
        messages.success(request, f"اتصال {out['healed_count']} اکانت درست شد: " + "، ".join(
            f"@{d['username']} → {'کانفیگ #' + str(d['to']) if d['to'] else 'استخر مشترک'}" for d in out["details"]))
    else:
        messages.success(request, "همه‌ی اکانت‌ها اتصال سالم دارند؛ تغییری لازم نبود.")
    return redirect("manage:ig_accounts")


@manage_access_required
@require_GET
def ig_accounts_configs_txt(request):
    qs = InstagramEngineAccount.objects.select_related("proxy_config").order_by("tenant_id", "username")
    if request.GET.get("tenant"):
        qs = qs.filter(tenant_id__in=svc._tenant_ids(request.GET["tenant"]))
    if request.GET.get("role"):
        qs = qs.filter(role=request.GET["role"])
    accounts = list(qs)
    for acc in accounts:
        svc.log_event(acc, "config_exported", "خروجی گروهی کانفیگ‌ها (configs.txt)", None, _actor(request))
    resp = HttpResponse(svc.export_text(accounts), content_type="text/plain; charset=utf-8")
    resp["Content-Disposition"] = 'attachment; filename="instagram-configs.txt"'
    return resp


@manage_access_required
@require_GET
def ig_account_detail(request, pk):
    acc = get_object_or_404(InstagramEngineAccount.objects.select_related("proxy_config"), pk=pk)
    ds = acc.device_settings if isinstance(acc.device_settings, dict) else {}
    dev = ds.get("device_settings") if isinstance(ds.get("device_settings"), dict) else {}
    mode = proxy_urls.proxy_mode(acc)
    now = timezone.now()
    return render(request, "ops/ig_accounts/detail.html", {
        "nav": "ig_accounts", "a": acc, "st": ST_CLASS.get(acc.status, ""), "mode": mode, "label": proxy_urls.proxy_label(acc),
        "meta": acc.proxy_meta,
        "login": svc.login_state(acc), "events": list(acc.events.all()[:80]), "configs": _config_choices(),
        "tenants": svc.tenant_choices(), "tenant": svc.tenant_label(acc.tenant_id),
        "has_session": svc.has_session(acc), "tiers": InstagramEngineAccount.TIER_CHOICES,
        "roles": InstagramEngineAccount.ROLE_CHOICES,
        "device": {"name": ds.get("device_name") or "", "model": dev.get("model") or "",
                   "manufacturer": dev.get("manufacturer") or "", "android": dev.get("android_release") or "",
                   "app": dev.get("app_version") or ""},
        "custom_url": proxy_urls.clean_proxy_url(acc.proxy_url) if mode == "custom" else "",
        "settling": acc.next_outreach_at if acc.next_outreach_at and acc.next_outreach_at > now else None,
        "resting": acc.cooldown_until if acc.cooldown_until and acc.cooldown_until > now else None,
        "has_password": bool(acc.password and acc.password != "session_only"),
        "pitch": svc.tenant_pitch(acc.tenant_id) if acc.role == "SENDER" else None,
        "settle_minutes": svc.SETTLE_MINUTES,
    })


@manage_access_required
@require_GET
def ig_account_login_state(request, pk):
    acc = get_object_or_404(InstagramEngineAccount, pk=pk)
    st = svc.login_state(acc)
    st["status_display"] = dict(InstagramEngineAccount.STATUS_CHOICES).get(st["status"], st["status"])
    st["reason_display"] = dict(InstagramEngineAccount.REASON_CHOICES).get(st["reason"], st["reason"])
    return JsonResponse(st)


@manage_access_required
@require_GET
def ig_account_config(request, pk):
    """The account's connection to open it on a phone from the same IP: V2Ray link + QR (or its own proxy)."""
    acc = get_object_or_404(InstagramEngineAccount.objects.select_related("proxy_config"), pk=pk)
    exp = svc.export_config(acc)
    svc.log_event(acc, "config_exported", f"خروجی کانفیگ ({exp['label']})", {"mode": exp["mode"]}, _actor(request))
    return render(request, "ops/ig_accounts/config.html", {
        "nav": "ig_accounts", "a": acc, "exp": exp, "qr": svc.qr_svg(exp.get("link") or "")})


@manage_access_required
@require_GET
def ig_account_config_txt(request, pk):
    acc = get_object_or_404(InstagramEngineAccount.objects.select_related("proxy_config"), pk=pk)
    svc.log_event(acc, "config_exported", "دانلود فایل کانفیگ", None, _actor(request))
    resp = HttpResponse(svc.export_text([acc]), content_type="text/plain; charset=utf-8")
    resp["Content-Disposition"] = f'attachment; filename="{acc.username}-config.txt"'
    return resp


def _do_proxy(acc, p, actor):
    mode = (p.get("mode") or "").strip()
    config = None
    if mode == "config":
        cid = str(p.get("config_id") or "")
        config = ProxyConfig.objects.filter(pk=int(cid)).first() if cid.isdigit() else None
        if config is None:
            return svc._result(acc, False, "کانفیگ را انتخاب کنید.")
    return svc.set_proxy(acc, mode, url=p.get("proxy_url", ""), config=config, actor=actor)


ACTIONS = {
    "login": lambda acc, p, actor: svc.start_login(acc, actor=actor),
    "code": lambda acc, p, actor: svc.submit_code(acc, p.get("code", ""), actor=actor),
    "cancel": lambda acc, p, actor: svc.cancel_login(acc, actor=actor),
    "check": lambda acc, p, actor: svc.check(acc, actor=actor),
    "check_ip": lambda acc, p, actor: svc.check_ip(acc, actor=actor),
    "pause": lambda acc, p, actor: svc.pause(acc, actor=actor, note=str(p.get("note") or "").strip()),
    "resume": lambda acc, p, actor: svc.resume(acc, actor=actor),
    "clear_cooldown": lambda acc, p, actor: svc.clear_cooldown(acc, actor=actor),
    "session": lambda acc, p, actor: svc.login_with_sessionid(acc, p.get("sessionid", ""), actor=actor),
    "proxy": _do_proxy,
    "settings": lambda acc, p, actor: svc.update_settings(
        acc, actor=actor, **{k: p.get(k, "") for k in ("username", "role", "tenant_id", "account_tier", "daily_limit",
                                                         "password")}),
}
ANCHORS = {"login": "#login", "code": "#login", "cancel": "#login", "session": "#login", "proxy": "#connection",
           "check_ip": "#connection", "settings": "#settings"}


@manage_access_required
@require_POST
def ig_account_action(request, pk, action):
    acc = get_object_or_404(InstagramEngineAccount.objects.select_related("proxy_config"), pk=pk)
    if action == "delete":
        username = svc.delete_account(acc, actor=_actor(request))
        if wants_json(request):
            return JsonResponse({"success": True, "message": f"اکانت @{username} حذف شد.", "deleted": True})
        messages.success(request, f"اکانت @{username} حذف شد.")
        return redirect("manage:ig_accounts")
    handler = ACTIONS.get(action)
    if handler is None:
        return JsonResponse({"success": False, "message": "عملیات نامعتبر است."}, status=404)
    try:
        res = handler(acc, _post(request), _actor(request))
    except Exception as exc:   # never a 500 page for an account action; the reason goes to the operator
        log.exception("instagram account action %s failed for @%s", action, acc.username)
        res = svc.Result(False, f"خطای غیرمنتظره: {svc.redact(exc, 200)}", status=acc.status)
    return _respond(request, res, acc.pk, ANCHORS.get(action, ""))


@manage_access_required
@require_http_methods(["GET", "POST"])
def ig_tenants(request):
    """Each customer's consultant name / role and the pitch its cold DMs use (customers edit theirs on /accounts/outreach/)."""
    if request.method == "POST":
        tid = (request.POST.get("tenant_id") or "").strip()
        if not tid:
            messages.error(request, "مشتری را انتخاب کنید.")
        else:
            svc.save_tenant_profile(tid, consultant_name=request.POST.get("consultant_name", ""),
                                    consultant_role=request.POST.get("consultant_role", ""),
                                    pitch_template=request.POST.get("pitch_template", ""))
            messages.success(request, f"پیام و نام مشاور «{svc.tenant_label(tid)}» ذخیره شد.")
        return redirect(reverse("manage:ig_tenants") + (f"#t-{svc.canonical_tenant(tid)}" if tid else ""))
    counts = {}
    for tid, n in (InstagramEngineAccount.objects.filter(role="SENDER").values_list("tenant_id")
                   .annotate(n=Count("id"))):
        counts[svc.canonical_tenant(tid)] = counts.get(svc.canonical_tenant(tid), 0) + n
    rows, seen = [], set()
    for tid, label in svc.tenant_choices():
        ctid = svc.canonical_tenant(tid)
        if ctid in seen:
            continue
        seen.add(ctid)
        rows.append({"tenant_id": ctid, "label": label, "senders": counts.get(ctid, 0), "pitch": svc.tenant_pitch(ctid)})
    rows.sort(key=lambda r: (-r["senders"], r["tenant_id"]))
    return render(request, "ops/ig_accounts/tenants.html", {"nav": "ig_tenants", "rows": rows})


__all__ = ["ig_accounts_list", "ig_account_add", "ig_accounts_heal", "ig_accounts_configs_txt", "ig_account_detail",
           "ig_account_login_state", "ig_account_config", "ig_account_config_txt", "ig_account_action", "ig_tenants"]
