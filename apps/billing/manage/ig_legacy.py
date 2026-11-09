"""Instagram account actions of the /manage/ dashboard (add, session, code, sender edit) and the lead exports.

The account pages under /manage/instagram/accounts/ (ig_accounts_views) are the main UI; these endpoints stay
because the dashboard JS still calls them. Both go through the same apps.discovery account services."""
import json
import logging

from django.contrib import messages
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect
from django.urls import reverse
from django.views.decorators.http import require_POST, require_GET


from apps.discovery.models import (
    InstagramEngineAccount, InstagramEngineLead, ProxyConfig
)
from apps.core.http import wants_json
from apps.discovery.proxies import assignment, urls as proxy_urls
from apps.discovery import ig_login, tenants

from apps.billing.manage.access import actor_name, manage_access_required

logger = logging.getLogger("management")


def _ig_proxy_choice(raw):
    """(mode, url, config) of a proxy typed in the old forms: empty or xray's shared ports = the system's choice,
    another xray port = that system config, anything else = the account's own proxy."""
    url = proxy_urls.normalize_proxy_url(raw) if (raw or "").strip() else ""
    if not url:
        return "system", "", None
    port = proxy_urls.xray_port(url)
    if port is None:
        return "custom", url, None
    cfg = None if port in proxy_urls.RESERVED_PORTS else ProxyConfig.objects.filter(local_port=port).first()
    return ("config", "", cfg) if cfg is not None else ("system", "", None)


@manage_access_required
@require_POST
def manage_account_add(request):
    """Adds an Instagram account (SCRAPER or SENDER) and logs it in.

    When Instagram asks for a code the login keeps waiting for it: the reply says requires_code and the code goes
    to the same login (account page /manage/instagram/accounts/<id>/settings/ or accounts/<id>/code/).
    """
    from apps.discovery import ig_accounts as svc, ig_state

    p = request.POST
    mode, url, cfg = _ig_proxy_choice(p.get("proxy_url"))
    acc, res = svc.add_account(
        username=p.get("username", ""), password=p.get("password", ""), sessionid=p.get("sessionid", ""),
        role=(p.get("role") or "SCRAPER"), tenant_id=(p.get("tenant_id") or "tenant_default").strip(),
        account_tier=p.get("account_tier") or "NEW", daily_limit=p.get("daily_limit"), proxy=mode, proxy_url=url,
        config=cfg, actor=actor_name(request), login=True, login_wait=25)
    if acc is not None and acc.role == "SENDER" and (p.get("consultant_name") or "").strip():
        tenants.save_tenant_profile(acc.tenant_id, consultant_name=p["consultant_name"], only_empty=True)
    out = res.as_dict()
    out.update(account_id=acc.pk if acc else None,
               username=acc.username if acc else ig_state.normalize_username(p.get("username")))
    if wants_json(request):
        return JsonResponse(out)
    if acc is not None and res.state in ig_login.LIVE_STATES:
        messages.warning(request, res.message)
        return redirect(reverse("manage:ig_account_detail", args=[acc.pk]) + "#login")
    (messages.success if res.ok else messages.error)(request, res.message)
    return redirect("manage:dashboard")


@manage_access_required
@require_POST
def manage_account_delete(request, pk):
    """Deletes an Instagram account (its system config becomes free for another account)."""
    from apps.discovery import ig_accounts as svc

    account = get_object_or_404(InstagramEngineAccount, pk=pk)
    username = svc.delete_account(account, actor=actor_name(request))
    messages.success(request, f"اکانت @{username} با موفقیت از سیستم حذف گردید.")
    return redirect("manage:dashboard")


@manage_access_required
@require_POST
def manage_account_set_session(request, pk):
    """Connects the account with a sessionid copied from a browser that is logged in (no password, no code)."""

    account = get_object_or_404(InstagramEngineAccount, pk=pk)
    if not (request.POST.get("sessionid") or "").strip():
        return JsonResponse({"success": False, "message": "لطفاً مقدار کوکی sessionid را وارد نمایید."})
    out = ig_login.login_with_sessionid(account, request.POST["sessionid"], actor=actor_name(request)).as_dict()
    out.setdefault("deleted", False)
    return JsonResponse(out)


def ig_edit_sender(account, data, actor: str = "") -> dict:
    """The old sender editor's fields (username, customer, password, sessionid, consultant name, tier, daily limit,
    proxy, status, clear_cooldown) applied through apps.discovery.ig_accounts; the dashboard's JSON reply."""
    from apps.discovery import ig_accounts as svc, ig_state

    ok, notes, session_updated = True, [], False
    res = svc.update_settings(account, actor=actor, **{k: data.get(k, "") for k in (
        "username", "tenant_id", "account_tier", "daily_limit", "password")})
    if not res.ok:
        ok = False
        notes.append(res.message)
    elif res.extra.get("changed"):
        notes.append(res.message)

    consultant = (data.get("consultant_name") or "").strip()
    if consultant and consultant != tenants.tenant_pitch(account.tenant_id).get("consultant_name"):
        tenants.save_tenant_profile(account.tenant_id, consultant_name=consultant)
        notes.append(f"نام مشاور مشتری «{tenants.tenant_label(account.tenant_id)}»: {consultant}")

    if "proxy_url" in data and (data.get("proxy_url") or "").strip() != (account.proxy_url or "").strip():
        mode, url, cfg = _ig_proxy_choice(data.get("proxy_url"))
        res = assignment.set_proxy(account, mode, url=url, config=cfg, actor=actor)
        ok = ok and res.ok
        if not res.ok or res.extra.get("changed"):
            notes.append(res.message)

    sessionid = (data.get("sessionid") or "").strip()
    if sessionid:
        res = ig_login.login_with_sessionid(account, sessionid, actor=actor)
        session_updated = res.ok
        ok = ok and res.ok
        notes.append(res.message)

    status = (data.get("status") or "").strip().upper()
    if status and status != account.status and status in dict(InstagramEngineAccount.STATUS_CHOICES) and not sessionid:
        if status == "DISABLED":
            res = svc.pause(account, actor=actor)
        elif status == "ACTIVE":
            res = svc.resume(account, actor=actor)
        else:
            ig_state.set_state(account, status, "", "وضعیت به‌صورت دستی تغییر کرد", force=True)
            ig_state.log_event(account, "status_changed", f"وضعیت دستی: {account.get_status_display()}", {"status": status},
                          actor)
            res = ig_state.Result(True, f"وضعیت: {account.get_status_display()}")
        notes.append(res.message)
    if data.get("clear_cooldown") in ("1", "true", "True", "on"):
        notes.append(svc.clear_cooldown(account, actor=actor).message)

    account.refresh_from_db()
    return {"success": ok, "message": "؛ ".join(n for n in notes if n) or f"اکانت @{account.username} تغییری نکرد.",
            "status": account.status, "session_updated": session_updated}


@manage_access_required
@require_POST
def manage_sender_account_edit(request, pk):
    """The old sender editor of the dashboard (see ig_edit_sender)."""
    account = get_object_or_404(InstagramEngineAccount.objects.select_related("proxy_config"), pk=pk)
    out = ig_edit_sender(account, request.POST, actor_name(request))
    if wants_json(request):
        return JsonResponse(out)
    (messages.success if out["success"] else messages.error)(request, out["message"])
    return redirect("/manage/?tab=outreach")


@manage_access_required
@require_POST
def manage_sender_clear_cooldown(request, pk):
    """Ends a protective rest early (an account that needs a code or a login stays as it is)."""
    from apps.discovery import ig_accounts as svc

    account = get_object_or_404(InstagramEngineAccount, pk=pk)
    res = svc.clear_cooldown(account, actor=actor_name(request))
    if wants_json(request):
        return JsonResponse({"success": True, "message": res.message, "status": account.status})
    messages.success(request, res.message)
    return redirect("/manage/?tab=outreach")


@manage_access_required
@require_POST
def manage_account_test(request, pk):
    """Connection test on the account's own phone, session and connection (what the workers use). An expired session
    with a saved password starts a login; if Instagram asks for a code, it is typed on the account page."""
    from apps.discovery import ig_accounts as svc

    account = get_object_or_404(InstagramEngineAccount.objects.select_related("proxy_config"), pk=pk)
    actor = actor_name(request)
    res = svc.check(account, actor=actor)
    account.refresh_from_db()
    if (not res.ok and account.status == "CHALLENGED" and account.status_reason == "session_expired"
            and account.password and account.password != "session_only"):
        res = ig_login.start_login(account, actor=actor, wait=25)
    elif res.state in ig_login.LIVE_STATES:
        res = ig_login.wait_for_login(account, timeout=25)
    account.refresh_from_db()
    out = res.as_dict()
    out.update(success=res.ok and account.status != "CHALLENGED", status=account.status,
               challenge_required=account.status == "CHALLENGED", challenge_hint=res.hint)
    return JsonResponse(out)


@manage_access_required
@require_POST
def manage_account_submit_code(request, pk):
    """Hands Instagram's code to the login that is waiting for it (the same login that asked for it)."""

    account = get_object_or_404(InstagramEngineAccount, pk=pk)
    raw_code = ""
    if request.content_type == "application/json":
        try:
            body = json.loads(request.body.decode("utf-8") or "{}")
            raw_code = str(body.get("code", "")).strip() if isinstance(body, dict) else ""
        except ValueError:
            pass
    if not raw_code:
        raw_code = (request.POST.get("code") or "").strip()
    if not raw_code:
        return JsonResponse({"success": False, "message": "لطفاً کد تایید را وارد کنید."})
    res = ig_login.submit_code(account, raw_code, actor=actor_name(request), wait=25)
    account.refresh_from_db()
    out = res.as_dict()
    out["status"] = account.status
    return JsonResponse(out)


@manage_access_required
@require_GET
def manage_leads_export_csv(request):
    """
    Exports all extracted leads as a UTF-8 BOM CSV dataset (streamed, formula-safe).
    """
    from django.db.models import F
    from django.db.models.functions import Coalesce
    from django.http import StreamingHttpResponse

    from apps.discovery.exports import instagram_lead_row, stream_csv_rows

    leads = InstagramEngineLead.objects.all().order_by(
        F("outreach_score").desc(nulls_last=True),
        F("intent_score").desc(nulls_last=True),
        Coalesce("comment_created_at", "created_at").desc(nulls_last=True),
        "-id"
    )
    header = [
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
    ]

    def rows():
        for lead in leads.iterator(chunk_size=500):
            r = instagram_lead_row(lead)
            yield [
                r["ig_user_pk"], r["username"], r["full_name"], r["profile_url"], r["comment_text"],
                r["source_post_url"], r["post_caption_snippet"], r["matched_cta"], f"{r['intent_score']:.2f}",
                r["status_display"], r["tenant_id"],
                r["created_at"].strftime("%Y-%m-%d %H:%M:%S") if r["created_at"] else "",
            ]

    response = StreamingHttpResponse(stream_csv_rows(header, rows()), content_type="text/csv; charset=utf-8")
    response["Content-Disposition"] = 'attachment; filename="instagram_leads_dataset.csv"'
    return response


@manage_access_required
@require_GET
def manage_leads_export_json(request):
    """
    Exports all extracted leads as a JSON dataset.
    """
    from apps.discovery.exports import instagram_lead_row

    data = []
    for lead in InstagramEngineLead.objects.all().order_by("-intent_score", "-created_at").iterator(chunk_size=500):
        r = instagram_lead_row(lead)
        r.pop("status_display")
        r["created_at"] = r["created_at"].isoformat() if r["created_at"] else None
        data.append(r)

    response = JsonResponse(data, safe=False, json_dumps_params={"ensure_ascii": False, "indent": 2})
    response["Content-Disposition"] = 'attachment; filename="instagram_leads_dataset.json"'
    return response
