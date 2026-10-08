"""Persian/Arabic text normalization helpers shared by the analysis package."""

from __future__ import annotations

import hashlib
import re

_ARABIC_TO_PERSIAN = str.maketrans({
    "ي": "ی", "ى": "ی", "ك": "ک", "ة": "ه", "ۀ": "ه", "أ": "ا", "إ": "ا", "ٱ": "ا",
})
_DIGITS = str.maketrans("۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩", "01234567890123456789")
_DIACRITICS = re.compile(r"[\u064B-\u065F\u0670\u0640]")  # harakat + tatweel
URL_RE = re.compile(r"(https?://\S+|www\.\S+|t\.me/\S+)", re.IGNORECASE)
MENTION_RE = re.compile(r"@\w+")
LETTER_RE = re.compile(r"[A-Za-z\u0600-\u06FF]")


def normalize(text: str | None) -> str:
    """Lower-case, unify Arabic/Persian letters and digits, collapse spaces."""
    if not text:
        return ""
    out = text.translate(_ARABIC_TO_PERSIAN).translate(_DIGITS)
    out = _DIACRITICS.sub("", out)
    out = out.replace("\u200c", " ").replace("\u200f", " ").replace("\u200e", " ")
    out = re.sub(r"\s+", " ", out)
    return out.lower().strip()


def letter_count(text: str) -> int:
    return len(LETTER_RE.findall(text or ""))


def strip_urls(text: str) -> str:
    return URL_RE.sub(" ", text or "")


def content_fingerprint(source: str, author_key: str | None, text: str) -> str:
    """Stable hash used to drop copy-pasted duplicates from the same author."""
    base = f"{source}|{author_key or ''}|{normalize(strip_urls(text))}"
    return hashlib.sha256(base.encode("utf-8")).hexdigest()


def truncate(text: str | None, limit: int) -> str:
    text = (text or "").strip()
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 1)].rstrip() + "…"
