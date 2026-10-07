"""Telegram client management, entity resolution, and group join logic."""

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Any, Tuple
from telethon import TelegramClient
from telethon.errors import (
    FloodWaitError,
    InviteHashExpiredError,
    InviteHashInvalidError,
    UserAlreadyParticipantError,
)
from telethon.tl.functions.channels import JoinChannelRequest
from telethon.tl.functions.messages import (
    CheckChatInviteRequest,
    ImportChatInviteRequest,
)
from telethon.tl.types import ChatInviteAlready

from telegram_crawler.config import settings
from telegram_crawler.models import GroupInfo

log = logging.getLogger("telegram_crawler.client")


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

    return TelegramClient(s_path, app_id, app_hash)


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
            updates = await client(ImportChatInviteRequest(hash=identifier))
            if hasattr(updates, "chats") and updates.chats:
                entity = updates.chats[0]
            else:
                entity = await client.get_entity(updates)
            log.info("Successfully joined private group via invite link.")
        except UserAlreadyParticipantError:
            log.info("Already a participant of the private group. Resolving entity...")
            check_res = await client(CheckChatInviteRequest(hash=identifier))
            if isinstance(check_res, ChatInviteAlready):
                entity = check_res.chat
            elif hasattr(check_res, "chat"):
                entity = check_res.chat
            else:
                raise RuntimeError("Could not resolve chat from invite hash while already participant.")
        except (InviteHashExpiredError, InviteHashInvalidError) as exc:
            raise ValueError(f"Invite link is invalid or expired: {exc}") from exc

    elif link_type in ("public", "id"):
        target_ref: Any = int(identifier) if link_type == "id" else identifier
        log.info("Resolving entity: %s", target_ref)
        entity = await client.get_entity(target_ref)

        try:
            await client(JoinChannelRequest(entity))
            log.info("Joined public channel/group: %s", getattr(entity, "title", target_ref))
        except UserAlreadyParticipantError:
            log.info("Already a member of %s", getattr(entity, "title", target_ref))
        except Exception as exc:
            log.debug("JoinChannelRequest note (may already be in or not needed): %s", exc)

    if entity is None:
        raise ValueError(f"Could not resolve group entity for link: {group_link}")

    group_id = entity.id
    # Format standard channel/supergroup ID with -100 prefix if needed
    if hasattr(entity, "broadcast") or hasattr(entity, "megagroup"):
        # Telethon entity.id is positive, but full chat_id is -100...
        pass

    group_title = getattr(entity, "title", str(group_id))
    group_username = getattr(entity, "username", None)

    info = GroupInfo(
        group_id=group_id,
        title=group_title,
        username=group_username,
        invite_link=group_link,
    )

    return entity, info
