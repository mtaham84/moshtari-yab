"""Tenants (customers) of outreach: tenant ids and their legacy aliases, and each tenant's consultant name / pitch."""
from __future__ import annotations

import re

from django.conf import settings

from apps.discovery.models import InstagramEngineAccount, OutreachTenantProfile


TENANT_ALIASES = {"sarv": "tenant_4"}  # legacy tenant id of business 4


def canonical_tenant(tenant_id) -> str:
    tid = str(tenant_id or "").strip()
    return TENANT_ALIASES.get(tid, tid)


def tenant_key(business_or_id) -> str:
    """The canonical tenant id of a business ("tenant_<pk>")."""
    return f"tenant_{getattr(business_or_id, 'pk', business_or_id)}"


def tenant_business_id(tenant_id) -> int | None:
    """Business pk of a tenant id ("tenant_7" / legacy alias), None for unknown formats."""
    m = re.fullmatch(r"tenant_(\d+)", canonical_tenant(tenant_id))
    return int(m.group(1)) if m else None


def tenant_keys_for_business(business_or_id) -> list[str]:
    """Every tenant id whose rows belong to a business: the canonical one plus legacy aliases (e.g. "sarv")."""
    key = tenant_key(business_or_id)
    return [key] + sorted(alias for alias, target in TENANT_ALIASES.items() if target == key)


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


# Lowest-priority pitch per tenant (below its profile and legacy sender pitches).
# This used to be hard-coded as "if business.id == 4" in two views.
FALLBACK_TENANT_PITCHES = {
    "tenant_4": {
        "consultant_name": "مشاور دندان‌پزشکی",
        "pitch_template": (
            "درود بر شما \n"
            "بنده مشاور و پشتیبانی تخصصی دندان پزشکی هستم، متوجه شدم پیگیر امور زیبایی و سلامتی دندونتون هستید\n"
            "میتونید هر سوالی دارید از بنده بپرسید. \n"
            "اگر هم فکر میکنید موثر هست ، شماره تماستون رو بزارید برای ۱۰ دقیقه مشاوره تخصصی رایگان با شما تماس میگیرم"
        ),
    },
}


def tenant_ids(tenant_id) -> list[str]:
    """A tenant id plus every legacy alias of the same tenant ("sarv" <-> "tenant_4"), canonical first."""
    tid = canonical_tenant(tenant_id)
    return [tid] + sorted({str(tenant_id or "")} - {tid, ""} | {a for a, t in TENANT_ALIASES.items() if t == tid})


def outreach_tenants() -> list[str]:
    """Canonical tenants the DM dispatcher and reply sync serve (TEN-03).

    settings.OUTREACH_TENANTS (env OUTREACH_TENANTS="tenant_4,tenant_7") when set, otherwise every tenant that
    has an ACTIVE SENDER account."""
    configured = [str(t).strip() for t in getattr(settings, "OUTREACH_TENANTS", None) or [] if str(t).strip()]
    raw = configured or InstagramEngineAccount.objects.filter(role="SENDER", status="ACTIVE").exclude(
        tenant_id__isnull=True).exclude(tenant_id="").values_list("tenant_id", flat=True).distinct()
    return list(dict.fromkeys(canonical_tenant(t) for t in raw))


def outreach_tenant_ids() -> list[str]:
    """`outreach_tenants()` expanded with legacy aliases (for lead/sender filters)."""
    return [tid for tenant in outreach_tenants() for tid in tenant_ids(tenant)]


def _tenant_ids(tenant_id) -> set[str]:
    return set(tenant_ids(tenant_id))


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
    fallback = FALLBACK_TENANT_PITCHES.get(canonical_tenant(tenant_id), {})
    if not out["pitch_template"] and fallback.get("pitch_template"):
        out["pitch_template"] = fallback["pitch_template"]
        out["consultant_name"] = out["consultant_name"] or fallback.get("consultant_name", "")
        out["source"] = out["source"] or "fallback"
    return out
