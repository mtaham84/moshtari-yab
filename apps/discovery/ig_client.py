"""instagrapi clients for an account: bound to the account's proxy and its own phone profile, with network timeouts."""
from __future__ import annotations

import logging
from typing import Callable

from apps.discovery.device_emulator import account_device_settings, connect_saved_session
from apps.discovery.ig_state import apply_state, log_event
from apps.discovery.proxies.urls import effective_proxy_url

log = logging.getLogger("instagram.accounts")


def _client_class():
    from instagrapi import Client  # looked up at call time (tests patch instagrapi.Client)
    return Client


def mount_timeouts(cl, connect: float = 10, read: float = 35) -> None:
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
    """Nobody is there to type a code. Raise at once: for some challenge forms instagrapi would otherwise ask again
    for minutes before giving up."""
    from instagrapi.exceptions import ChallengeRequired
    raise ChallengeRequired("Instagram asks for a verification code: log the account in from the panel")


def _no_new_password(username):
    from instagrapi.exceptions import ChallengeRequired
    raise ChallengeRequired("Instagram asks for a password change: do it in the Instagram app, then save it here")


def new_client(account, *, code_handler: Callable | None = None, with_proxy: bool = True, **attrs):
    """The one way to build an instagrapi Client for an account: bound to the account's proxy (its own, its system
    config or the shared pool; never the server IP unless configured so).

    attrs are set on the client (delay_range, request_timeout, ...). with_proxy=False is for a caller that checks the
    proxy first and sets it itself. A challenge never waits for a keyboard: without code_handler it raises
    ChallengeRequired at once, so the account is marked CHALLENGED (the operator logs in from the panel with the code).
    """
    cl = _client_class()()
    for name, value in attrs.items():
        setattr(cl, name, value)
    proxy = effective_proxy_url(account) if with_proxy else ""
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
    mount_timeouts(cl)
    return cl


def save_session(account, cl, *, kind: str = "login_ok", message: str = "", actor: str = "",
                 activate: bool = True) -> None:
    """Stores the client's settings (session + same phone) and, by default, puts the account back to work."""
    fresh = cl.get_settings()
    account.session_json = fresh
    account.device_settings = account_device_settings(account, client_settings=fresh)
    fields = ["session_json", "device_settings"]
    if activate:
        fields += apply_state(account, "ACTIVE", "", message)
    account.save(update_fields=fields)
    if kind:
        log_event(account, kind, message, None, actor)
