"""Telegram client management, entity resolution, and group join logic."""

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Any, Tuple
from telethon import TelegramClient
from telethon.errors import (
    ChannelPrivateError,
    ChannelsTooMuchError,
    InviteHashExpiredError,
    InviteHashInvalidError,
    InviteRequestSentError,
    UserAlreadyParticipantError,
    UserBannedInChannelError,
)
from telethon.tl.functions.channels import JoinChannelRequest
from telethon.tl.functions.messages import (
    CheckChatInviteRequest,
    ImportChatInviteRequest,
)
from telethon.tl.types import ChatInviteAlready, User

from telegram_crawler.config import settings
from telegram_crawler.models import GroupInfo
from telegram_crawler.ratelimit import FloodWaitTooLong, with_flood_retry

log = logging.getLogger("telegram_crawler.client")


class JoinError(RuntimeError):
    """A link that cannot be monitored. ``code`` is one of the panel error codes (see telegram_crawler.panel)."""

    def __init__(self, code: str, detail: str = "") -> None:
        super().__init__(f"{code}: {detail}" if detail else code)
        self.code = code


def proxy_options(url: str) -> dict[str, Any]:
    """TG_PROXY → extra TelegramClient kwargs. Empty → {} (direct connection)."""
    from urllib.parse import unquote, urlsplit

    if not url:
        return {}
    u = urlsplit(url)
    scheme = (u.scheme or "").lower()
    if not u.hostname or not u.port:
        raise ValueError(f"TG_PROXY needs host and port: {url!r}")
    if scheme in ("mtproxy", "mtproto"):
        from telethon import connection

        secret = unquote(u.username or "")
        if not secret:
            raise ValueError("TG_PROXY mtproxy:// needs the secret: mtproxy://<secret>@host:port")
        return {"connection": connection.ConnectionTcpMTProxyRandomizedIntermediate,
                "proxy": (u.hostname, u.port, secret)}
    kinds = {"socks5": "socks5", "socks5h": "socks5", "socks4": "socks4", "http": "http"}
    if scheme not in kinds:
        raise ValueError(f"unsupported TG_PROXY scheme {scheme!r} (use socks5, http or mtproxy)")
    proxy: dict[str, Any] = {"proxy_type": kinds[scheme], "addr": u.hostname, "port": u.port, "rdns": True}
    if u.username:
        proxy["username"], proxy["password"] = unquote(u.username), unquote(u.password or "")
    return {"proxy": proxy}


def create_telegram_client(
    session_path: str | None = None,
    api_id: int | None = None,
    api_hash: str | None = None,
) -> TelegramClient:
    """Create a Telethon TelegramClient instance."""
    s_path = session_path or settings.session_path
    app_id = api_id or settings.api_id
    app_hash = api_hash or settings.api_hash

    if not app_id or not app_hash:
        raise ValueError(
            "TG_API_ID and TG_API_HASH must be configured in .env or passed to create_telegram_client"
        )

    # Ensure parent directory of session file exists
    Path(s_path).parent.mkdir(parents=True, exist_ok=True)

    # Telethon sleeps automatically on FloodWait shorter than this threshold;
    # longer waits raise FloodWaitError and are handled by ratelimit.with_flood_retry.
    extra = proxy_options(settings.proxy)
    if extra:
        log.info("Connecting to Telegram through proxy %s", settings.proxy.split("@")[-1])
    return TelegramClient(s_path, app_id, app_hash, flood_sleep_threshold=settings.flood_sleep_threshold, **extra)


def parse_group_link(link: str) -> tuple[str, str]:
    """
    Parse a Telegram group link or username into (link_type, identifier).
    link_type can be:
      - 'invite': private invite link with hash (t.me/+hash or t.me/joinchat/hash)
      - 'public': public username or t.me/username
      - 'id': numeric chat id
    """
    clean = link.strip()

    # Private invite link: t.me/+hash or telegram.me/+hash or t.me/joinchat/hash
    invite_match = re.search(r"(?:t\.me|telegram\.me)/(?:\+|joinchat/)([a-zA-Z0-9_\-]+)", clean)
    if invite_match:
        return ("invite", invite_match.group(1))

    # Public link: t.me/username
    public_match = re.search(r"(?:t\.me|telegram\.me)/([a-zA-Z0-9_]{4,})/?$", clean)
    if public_match:
        return ("public", public_match.group(1))

    # Clean username: @username
    if clean.startswith("@"):
        return ("public", clean.lstrip("@"))

    # Numeric ID
    if clean.lstrip("-").isdigit():
        return ("id", clean)

    # Default to public identifier
    return ("public", clean)


async def join_and_resolve_group(
    client: TelegramClient,
    group_link: str,
) -> Tuple[Any, GroupInfo]:
    """
    Resolve group entity and join if not already a member.
    Returns (telethon_entity, GroupInfo).
    """
    link_type, identifier = parse_group_link(group_link)
    entity = None
    group_title = "Unknown Group"
    group_username = None

    if link_type == "invite":
        log.info("Processing private invite link with hash: %s", identifier)
        try:
            updates = await with_flood_retry(lambda: client(ImportChatInviteRequest(hash=identifier)), what="join(invite)")
            if hasattr(updates, "chats") and updates.chats:
                entity = updates.chats[0]
            else:
                entity = await client.get_entity(updates)
            log.info("Successfully joined private group via invite link.")
        except UserAlreadyParticipantError:
            log.info("Already a participant of the private group. Resolving entity...")
            check_res = await with_flood_retry(lambda: client(CheckChatInviteRequest(hash=identifier)), what="check_invite")
            if isinstance(check_res, ChatInviteAlready):
                entity = check_res.chat
            elif hasattr(check_res, "chat"):
                entity = check_res.chat
            else:
                raise RuntimeError("Could not resolve chat from invite hash while already participant.")
        except (InviteHashExpiredError, InviteHashInvalidError) as exc:
            raise JoinError("NO_ACCESS", f"invite link is invalid or expired: {exc}") from exc

    elif link_type in ("public", "id"):
        target_ref: Any = int(identifier) if link_type == "id" else identifier
        log.info("Resolving entity: %s", target_ref)
        entity = await with_flood_retry(lambda: client.get_entity(target_ref), what="get_entity")
        if isinstance(entity, User):
            raise JoinError("NOT_A_GROUP", f"{target_ref} is a user or bot")

        try:
            await with_flood_retry(lambda: client(JoinChannelRequest(entity)), what="join(public)")
            log.info("Joined public channel/group: %s", getattr(entity, "title", target_ref))
        except UserAlreadyParticipantError:
            log.info("Already a member of %s", getattr(entity, "title", target_ref))
        except (FloodWaitTooLong, ChannelPrivateError, ChannelsTooMuchError, InviteRequestSentError,
                UserBannedInChannelError):
            raise
        except Exception as exc:
            log.debug("JoinChannelRequest note (may already be in or not needed): %s", exc)

    if entity is None:
        raise ValueError(f"Could not resolve group entity for link: {group_link}")

    # Marked peer id: -100<id> for supergroups/channels (Channel objects), -<id> for legacy groups (Chat).
    from telethon import utils

    group_id = utils.get_peer_id(entity)
    is_supergroup = hasattr(entity, "megagroup") or hasattr(entity, "broadcast")

    group_title = getattr(entity, "title", str(group_id))
    group_username = getattr(entity, "username", None)

    info = GroupInfo(
        group_id=group_id,
        title=group_title,
        username=group_username,
        invite_link=group_link,
        is_supergroup=is_supergroup,
    )

    return entity, info
