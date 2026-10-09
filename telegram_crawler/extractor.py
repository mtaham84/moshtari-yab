"""Turn Telethon objects into plain records for the archive (users, chats, messages).

Every getter is defensive: Telegram omits most fields depending on privacy settings, "min" entities and
layer versions, so missing values become ``None`` instead of exceptions.
"""

from __future__ import annotations

import base64
import json
from datetime import datetime
from typing import Any

from telethon import utils
from telethon.tl import types


# ── helpers ──────────────────────────────────────────────────────────────────
def _s(v: Any) -> str | None:
    return v.replace("\x00", "") if isinstance(v, str) and v else None


def _i(v: Any) -> int | None:
    return v if isinstance(v, int) and not isinstance(v, bool) else None


def _b(v: Any) -> bool:
    return v is True


def _dt(v: Any) -> datetime | None:
    return v if isinstance(v, datetime) else None


def _json_default(o: Any) -> Any:
    if isinstance(o, datetime):
        return o.isoformat()
    if isinstance(o, (bytes, bytearray)):
        return base64.b64encode(bytes(o)).decode()
    return str(o)


def to_json(obj: Any) -> str | None:
    """JSON text of a TL object (``to_dict``), safe for PostgreSQL jsonb (no NUL characters)."""
    if obj is None:
        return None
    try:
        data = obj.to_dict() if hasattr(obj, "to_dict") else obj
        return json.dumps(data, ensure_ascii=False, default=_json_default).replace("\\u0000", "")
    except Exception:
        return None


def peer_id(peer: Any) -> int | None:
    """Marked id of a Peer/User/Chat/Channel (users > 0, chats < 0, channels -100…)."""
    if peer is None:
        return None
    try:
        return utils.get_peer_id(peer)
    except Exception:
        return None


def display_name(entity: Any) -> str | None:
    if entity is None:
        return None
    if isinstance(entity, types.User):
        return " ".join(p for p in (_s(entity.first_name), _s(entity.last_name)) if p) or _s(entity.username)
    return _s(getattr(entity, "title", None)) or _s(getattr(entity, "username", None))


# ── users ────────────────────────────────────────────────────────────────────
def user_record(user: types.User, store_raw: bool = True) -> dict[str, Any]:
    usernames = [u.username for u in (getattr(user, "usernames", None) or []) if getattr(u, "active", False) and _s(u.username)]
    username = _s(user.username) or (usernames[0] if usernames else None)
    return {
        "user_id": user.id,
        "username": username,
        "usernames": usernames or ([username] if username else None),
        "first_name": _s(user.first_name),
        "last_name": _s(user.last_name),
        "phone": _s(user.phone),
        "lang_code": _s(user.lang_code),
        "is_bot": _b(user.bot),
        "is_premium": _b(user.premium),
        "is_verified": _b(user.verified),
        "is_scam": _b(user.scam),
        "is_fake": _b(user.fake),
        "is_deleted": _b(user.deleted),
        "raw": to_json(user) if store_raw else None,
    }


# ── chats ────────────────────────────────────────────────────────────────────
def chat_type(entity: Any) -> str:
    if isinstance(entity, (types.Channel, types.ChannelForbidden)):
        if getattr(entity, "gigagroup", False):
            return "gigagroup"
        return "supergroup" if getattr(entity, "megagroup", False) else "channel"
    return "group"


def chat_record(entity: Any, full: Any = None, store_raw: bool = True) -> dict[str, Any]:
    """``full`` is the ``ChannelFull``/``ChatFull`` object (``full_chat``) when it was fetched."""
    linked = _i(getattr(full, "linked_chat_id", None))
    return {
        "chat_id": peer_id(entity),
        "type": chat_type(entity),
        "title": _s(getattr(entity, "title", None)),
        "username": _s(getattr(entity, "username", None)),
        "about": _s(getattr(full, "about", None)),
        "members_count": _i(getattr(full, "participants_count", None)) or _i(getattr(entity, "participants_count", None)),
        "linked_chat_id": utils.get_peer_id(types.PeerChannel(linked)) if linked else None,
        "is_forum": _b(getattr(entity, "forum", None)),
        "is_verified": _b(getattr(entity, "verified", None)),
        "is_scam": _b(getattr(entity, "scam", None)),
        "raw": to_json(entity) if store_raw else None,
    }


# ── messages ─────────────────────────────────────────────────────────────────
_DOC_KINDS = (("sticker", "sticker"), ("voice", "voice"), ("video_note", "video_note"), ("gif", "gif"),
              ("video", "video"), ("audio", "audio"))


def media_info(msg: Any) -> tuple[str | None, dict[str, Any] | None]:
    media = getattr(msg, "media", None)
    if media is None:
        return None, None
    if isinstance(media, types.MessageMediaPhoto):
        kind = "photo"
    elif isinstance(media, types.MessageMediaDocument):
        kind = next((name for attr, name in _DOC_KINDS if getattr(msg, attr, None)), "document")
    elif isinstance(media, types.MessageMediaWebPage):
        kind = "webpage"
    elif isinstance(media, types.MessageMediaPoll):
        kind = "poll"
    elif isinstance(media, (types.MessageMediaGeo, types.MessageMediaGeoLive, types.MessageMediaVenue)):
        kind = "location"
    elif isinstance(media, types.MessageMediaContact):
        kind = "contact"
    else:
        kind = type(media).__name__.replace("MessageMedia", "").lower() or "other"

    info: dict[str, Any] = {}
    f = getattr(msg, "file", None) if kind not in {"webpage", "poll", "location", "contact"} else None
    if f is not None:
        for key in ("mime_type", "size", "name", "duration", "width", "height", "title", "performer", "emoji"):
            try:
                val = getattr(f, key, None)
            except Exception:
                val = None
            if isinstance(val, (str, int, float)) and not isinstance(val, bool):
                info[key] = val
    if kind == "webpage":
        wp = getattr(media, "webpage", None)
        for key in ("url", "site_name", "title", "description"):
            if _s(getattr(wp, key, None)):
                info[key] = _s(getattr(wp, key))
    elif kind == "poll":
        poll = getattr(media, "poll", None)
        q = getattr(poll, "question", None)
        info["question"] = _s(getattr(q, "text", q))
        info["answers"] = [_s(getattr(getattr(a, "text", None), "text", getattr(a, "text", None))) for a in (getattr(poll, "answers", None) or [])]
    elif kind == "contact":
        for key in ("first_name", "last_name", "phone_number", "user_id"):
            val = getattr(media, key, None)
            if isinstance(val, (str, int)) and val:
                info[key] = val
    elif kind == "location":
        geo = getattr(media, "geo", None)
        if isinstance(getattr(geo, "lat", None), float):
            info.update(lat=geo.lat, long=geo.long)
        if _s(getattr(media, "title", None)):
            info["title"] = _s(media.title)
    return kind, info or None


def entities_info(msg: Any) -> list[dict[str, Any]] | None:
    out = []
    for e in getattr(msg, "entities", None) or []:
        item: dict[str, Any] = {"type": type(e).__name__.replace("MessageEntity", ""), "offset": e.offset, "length": e.length}
        if _s(getattr(e, "url", None)):
            item["url"] = e.url
        if _i(getattr(e, "user_id", None)):
            item["user_id"] = e.user_id
        out.append(item)
    return out or None


def message_record(msg: Any, chat_id: int, *, is_context: bool = False, store_raw: bool = True) -> dict[str, Any]:
    """Archive row for a ``Message`` or ``MessageService``. Sender ids are resolved by the caller."""
    reply = getattr(msg, "reply_to", None)
    fwd = getattr(msg, "fwd_from", None)
    is_service = isinstance(msg, types.MessageService)
    action = getattr(msg, "action", None) if is_service else None
    media_type, media = (None, None) if is_service else media_info(msg)
    entities = None if is_service else entities_info(msg)
    from_peer = getattr(msg, "from_id", None)
    sender_user = from_peer.user_id if isinstance(from_peer, types.PeerUser) else None
    sender_chat = peer_id(from_peer) if isinstance(from_peer, (types.PeerChannel, types.PeerChat)) else None
    if from_peer is None and getattr(msg, "post", False):   # channel post: the channel itself is the sender
        sender_chat = chat_id
    return {
        "chat_id": chat_id,
        "message_id": msg.id,
        "sender_user_id": sender_user,
        "sender_chat_id": sender_chat,
        "date": msg.date,
        "edit_date": _dt(getattr(msg, "edit_date", None)),
        "text": _s(getattr(msg, "message", None)) or "",
        "entities": json.dumps(entities, ensure_ascii=False) if entities else None,
        "reply_to_msg_id": _i(getattr(reply, "reply_to_msg_id", None)),
        "reply_to_top_id": _i(getattr(reply, "reply_to_top_id", None)),
        "quote_text": _s(getattr(reply, "quote_text", None)),
        "fwd_from_id": peer_id(getattr(fwd, "from_id", None)),
        "fwd_from_name": _s(getattr(fwd, "from_name", None)),
        "fwd_date": _dt(getattr(fwd, "date", None)),
        "via_bot_id": _i(getattr(msg, "via_bot_id", None)),
        "post_author": _s(getattr(msg, "post_author", None)),
        "grouped_id": _i(getattr(msg, "grouped_id", None)),
        "media_type": media_type,
        "media": json.dumps(media, ensure_ascii=False) if media else None,
        "views": _i(getattr(msg, "views", None)),
        "forwards": _i(getattr(msg, "forwards", None)),
        "is_service": is_service,
        "service_action": type(action).__name__.replace("MessageAction", "") if action is not None else None,
        "is_pinned": _b(getattr(msg, "pinned", None)),
        "is_context": is_context,
        "raw": to_json(msg) if store_raw else None,
    }
