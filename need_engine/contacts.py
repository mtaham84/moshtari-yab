"""Extract only explicitly self-declared public contact channels; never fetch bio URLs."""
from __future__ import annotations

import re

from need_engine.schemas import ContactChannel
from need_engine.text import norm

_TG_URL = re.compile(r"(?:https?://)?(?:t\.me|telegram\.me|telegram\.dog)/([A-Za-z0-9_]{5,32})", re.I)
_IG_URL = re.compile(r"(?:https?://)?(?:instagram\.com|instagr\.am)/([A-Za-z0-9._]{1,30})", re.I)
_DOMAIN = re.compile(r"https?://[^\s]+|\b(?:[A-Za-z0-9-]+\.)+(?:com|net|org|ir|io|co|me|app|shop)(?:/[^\s]*)?", re.I)
_EMAIL = re.compile(r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b", re.I)
_PHONE = re.compile(r"(?<!\d)(?:\+?98|0)9\d{9}(?!\d)")


def extract_public_contacts(bio: str, extra_urls: list[str] | None = None,
                            types: tuple[str, ...] | list[str] = ("telegram", "instagram", "website")) -> list[ContactChannel]:
    allowed = set(types)
    raw = str(bio or "")[:500]
    normalized = norm(raw)
    found: list[ContactChannel] = []

    if "telegram" in allowed:
        for match in _TG_URL.finditer(raw):
            handle = match.group(1)
            if handle.casefold() not in {"joinchat", "addstickers", "share"} and not handle.isdigit():
                found.append(ContactChannel(type="telegram", value=handle, display=f"@{handle}", url=f"https://t.me/{handle}"))
        for match in re.finditer(r"(?:تلگرام|آیدی تلگرام|telegram|tg\s*:)[^\n]{0,25}?@?([A-Za-z_][A-Za-z0-9_]{4,31})", raw, re.I):
            handle = match.group(1)
            if handle.casefold() not in {"joinchat", "telegram", "official"}:
                found.append(ContactChannel(type="telegram", value=handle, display=f"@{handle}", url=f"https://t.me/{handle}"))

    if "instagram" in allowed:
        for match in _IG_URL.finditer(raw):
            handle = match.group(1).split("/")[0]
            if handle.casefold() not in {"p", "reel", "reels", "stories", "explore", "accounts"} and not handle.startswith(".") and not handle.endswith(".") and ".." not in handle:
                found.append(ContactChannel(type="instagram", value=handle, display=f"@{handle}", url=f"https://instagram.com/{handle}"))
        for match in re.finditer(r"(?:اینستا(?:گرام)?|\big\b|\binsta\b)[^\n]{0,20}?@([A-Za-z0-9._]{1,30})", raw, re.I):
            handle = match.group(1)
            if not handle.startswith(".") and not handle.endswith(".") and ".." not in handle:
                found.append(ContactChannel(type="instagram", value=handle, display=f"@{handle}", url=f"https://instagram.com/{handle}"))

    if "website" in allowed:
        for match in _DOMAIN.finditer(raw):
            if match.start() > 0 and raw[match.start() - 1] in "@.":
                continue
            url = match.group(0).rstrip(".,؛)")
            if re.search(r"(?:x\.com|twitter\.com)(?:/|$)", url, re.I):
                continue
            if "t.me/" in url.casefold() or "instagram.com/" in url.casefold() or "instagr.am/" in url.casefold():
                continue
            if not url.startswith("http"):
                url = "https://" + url
            found.append(ContactChannel(type="website", value=url, display=url, url=url))
    if "email" in allowed:
        for match in _EMAIL.finditer(raw):
            found.append(ContactChannel(type="email", value=match.group(0), display=match.group(0), url=f"mailto:{match.group(0)}"))
    if "phone" in allowed or "whatsapp" in allowed:
        for match in _PHONE.finditer(normalized):
            value = match.group(0)
            value = "+98" + value[1:] if value.startswith("0") else value
            kind = "whatsapp" if "whatsapp" in allowed and re.search(r"واتساپ|whatsapp", normalized) else "phone"
            if kind in allowed:
                found.append(ContactChannel(type=kind, value=value, display=value, url=f"https://wa.me/{value.lstrip('+')}" if kind == "whatsapp" else f"tel:{value}"))
    if extra_urls:
        found.extend(extract_public_contacts(" ".join(extra_urls), types=allowed))

    result, seen = [], set()
    for channel in found:
        key = (channel.type, channel.value.casefold())
        if key not in seen:
            seen.add(key)
            result.append(channel)
        if len(result) >= 5:
            break
    return result
