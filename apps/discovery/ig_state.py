"""Shared by every Instagram account module: account status, the account's timeline, results, input cleanup."""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from datetime import timedelta
from typing import Any

from django.db import transaction
from django.utils import timezone

from apps.discovery.models import InstagramAccountEvent

log = logging.getLogger("instagram.accounts")

_DIGITS = str.maketrans("۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩", "01234567890123456789")
_SECRET_KEYS = ("password", "sessionid", "code", "cookie", "authorization", "token")


def now_iso() -> str:
    return timezone.now().isoformat()


def parse_iso(value) -> Any:
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


def result_for(account, ok: bool, message: str, **kw) -> Result:
    return Result(ok, message, status=account.status, reason=account.status_reason or "", **kw)


def has_session(account) -> bool:
    sj = account.session_json if isinstance(account.session_json, dict) else {}
    return bool((sj.get("cookies") or {}).get("sessionid") or (sj.get("authorization_data") or {}).get("sessionid"))


def apply_state(account, status: str, reason: str = "", message: str = "", *, cooldown_minutes=None,
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
    fields = apply_state(account, status, reason, message, cooldown_minutes=cooldown_minutes, force=force)
    account.save(update_fields=list(dict.fromkeys([*fields, *extra_fields])))
