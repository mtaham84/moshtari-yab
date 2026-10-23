"""Validate Instagram page references without contacting Instagram.

The panel and collectors use this module to agree on canonical handles.
"""
from __future__ import annotations

import re
import unicodedata
from urllib.parse import unquote, urlsplit


class PageRefError(ValueError):
    def __init__(self, reason: str):
        self.reason = reason
        super().__init__(reason)


_HANDLE = re.compile(r"^[a-z0-9._]{1,30}$")
_HOSTS = {"instagram.com", "www.instagram.com", "m.instagram.com", "instagr.am"}
_POST_HOSTS = {"instagram.com", "www.instagram.com", "m.instagram.com", "instagr.am"}
_POST_CODE = re.compile(r"^[A-Za-z0-9_-]{5,32}$")
_RESERVED = {
    "p", "reel", "reels", "tv", "stories", "explore", "accounts",
    "direct", "about", "legal", "developer", "web", "oauth", "challenge",
}


def parse_page_ref(text: str) -> str:
    """Return a validated lowercase Instagram handle from a handle or profile URL."""
    if not isinstance(text, str):
        raise PageRefError("invalid_type")
    if any(unicodedata.category(char) == "Cc" or char == "\x00" for char in text):
        raise PageRefError("control_character")
    value = text.strip()
    if not value:
        raise PageRefError("empty")
    if len(value) > 200:
        raise PageRefError("too_long")
    if any(char.isspace() for char in value):
        raise PageRefError("internal_whitespace")
    if not value.isascii():
        raise PageRefError("non_ascii")
    if "\\" in value:
        raise PageRefError("backslash")

    if value.startswith("@"):
        handle = value[1:]
    elif re.match(r"^[a-zA-Z][a-zA-Z0-9+.-]*:", value) or value.startswith("//"):
        try:
            parsed = urlsplit(value)
            port = parsed.port
        except ValueError as exc:
            raise PageRefError("invalid_url") from exc
        if parsed.scheme.lower() not in {"http", "https"}:
            raise PageRefError("invalid_scheme")
        if parsed.username is not None or parsed.password is not None:
            raise PageRefError("userinfo")
        if port is not None:
            raise PageRefError("port")
        if parsed.hostname is None or parsed.hostname.lower() not in _HOSTS:
            raise PageRefError("invalid_host")
        segments = parsed.path.split("/")[1:]
        if segments and segments[-1] == "":
            segments.pop()
        if segments and unquote(segments[0]).lower() in _RESERVED:
            raise PageRefError("reserved_path")
        if len(segments) != 1 or not segments[0]:
            raise PageRefError("invalid_path")
        raw_segment = segments[0]
        decoded = unquote(raw_segment)
        if decoded != raw_segment and ("/" in decoded or "." in decoded):
            raise PageRefError("encoded_path_separator")
        handle = decoded
    else:
        handle = value

    handle = handle.lower()
    if handle in _RESERVED:
        raise PageRefError("reserved_path")
    if not _HANDLE.fullmatch(handle):
        raise PageRefError("invalid_handle")
    if handle.startswith(".") or handle.endswith(".") or ".." in handle:
        raise PageRefError("invalid_handle")
    return handle


def parse_post_ref(text: str) -> str:
    """Return a validated Instagram post shortcode without making a request."""
    if not isinstance(text, str):
        raise PageRefError("invalid_type")
    if any(unicodedata.category(char) == "Cc" for char in text):
        raise PageRefError("control_character")
    value = text.strip()
    if not value:
        raise PageRefError("empty")
    if len(value) > 500:
        raise PageRefError("too_long")
    if any(char.isspace() for char in value):
        raise PageRefError("internal_whitespace")
    if "\\" in value:
        raise PageRefError("backslash")
    try:
        parsed = urlsplit(value)
        port = parsed.port
    except ValueError as exc:
        raise PageRefError("invalid_url") from exc
    if parsed.scheme.lower() not in {"http", "https"}:
        raise PageRefError("invalid_scheme")
    if parsed.username is not None or parsed.password is not None:
        raise PageRefError("userinfo")
    if port is not None:
        raise PageRefError("port")
    if parsed.hostname is None or parsed.hostname.lower() not in _POST_HOSTS:
        raise PageRefError("invalid_host")
    segments = parsed.path.split("/")[1:]
    if segments and segments[-1] == "":
        segments.pop()
    if len(segments) != 2 or segments[0].lower() not in {"p", "reel", "tv"}:
        raise PageRefError("invalid_path")
    shortcode = segments[1]
    if not _POST_CODE.fullmatch(shortcode):
        raise PageRefError("invalid_shortcode")
    return shortcode
