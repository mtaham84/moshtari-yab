"""What an account's connection looks like from outside: its config/proxy as a link or QR code (to open the account
on a phone from the same IP), and the egress IP the account is seen from."""
from __future__ import annotations

import logging
from urllib.parse import urlparse

from apps.discovery.ig_state import Result, log_event, now_iso, result_for
from apps.discovery.models import ProxyConfig
from apps.discovery.proxies import health
from apps.discovery.proxies.assignment import pool_config
from apps.discovery.proxies.urls import clean_proxy_url, effective_proxy_url, proxy_label, proxy_mode, xray_port

log = logging.getLogger("instagram.accounts")


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
        url = clean_proxy_url(account.proxy_url)
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

    info = health.inspect_proxy_egress(effective_proxy_url(account))
    ds = dict(account.device_settings or {})
    meta = dict(ds.get("proxy_meta") or {})
    prev = meta.get("last_ip")
    ok = info.get("status") in ("success", "direct")
    if ok:
        meta.update(last_ip=info.get("ip", ""), isp=info.get("isp", ""), country=info.get("country", ""),
                    country_code=info.get("country_code", ""), classification=info.get("classification", ""),
                    risk_score=info.get("risk_score", ""), checked_at=now_iso())
        ds["proxy_meta"] = meta
        account.device_settings = ds
        account.save(update_fields=["device_settings"])
        changed = bool(prev and prev != meta["last_ip"])
        msg = f"IP خروجی {meta['last_ip']} · {meta['country']} · {meta['isp']}" + (f" (قبلاً {prev})" if changed else "")
        log_event(account, "ip_checked", msg, {"ip": meta["last_ip"], "country": meta["country"], "isp": meta["isp"],
                                               "classification": meta["classification"], "previous": prev}, actor)
        return result_for(account, True, msg, extra={"ip": meta["last_ip"], "changed": changed})
    log_event(account, "ip_checked", "IP خروجی پیدا نشد (اتصال پروکسی/کانفیگ برقرار نشد)", None, actor)
    return result_for(account, False, "IP خروجی پیدا نشد؛ اتصال پروکسی/کانفیگ برقرار نشد.")
