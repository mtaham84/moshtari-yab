"""X reply draft validation and human-only intent links. Platform details are UNVERIFIED."""
from __future__ import annotations

import re
from urllib.parse import urlencode

URL_RE = re.compile(r"https?://\S+|www\.\S+", re.I)
PHONE_RE = re.compile(r"(?:\+?98|0)9\d{9}")
EMAIL_RE = re.compile(r"[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}")
UNVERIFIED_URL_WEIGHTS = {"url": 23, "emoji": 2, "default": 1}
FALLBACK = "اگر خواستید، چند گزینه متناسب با نیازتان معرفی کنم؟"


def sanitize_public_reply(text: str) -> str:
    text = URL_RE.sub("", text)
    text = PHONE_RE.sub("", text)
    text = EMAIL_RE.sub("", text)
    return text.replace("{{LINK}}", "").strip()


def x_weighted_length(text: str) -> int:
    text = URL_RE.sub("x" * UNVERIFIED_URL_WEIGHTS["url"], text)
    total = 0
    for char in text:
        if "\u200c" == char or "\u0600" <= char <= "\u06ff" or char.isdigit() or char.isalpha():
            total += 1
        elif ord(char) > 0xffff:
            total += UNVERIFIED_URL_WEIGHTS["emoji"]
        else:
            total += UNVERIFIED_URL_WEIGHTS["default"]
    return total


def fit_public_reply(text: str, limit: int) -> str:
    text = sanitize_public_reply(text)
    if not text:
        return FALLBACK
    if x_weighted_length(text) <= limit:
        return text
    sentences = re.split(r"(?<=[.!؟])\s+", text)
    fitted = ""
    for sentence in sentences:
        candidate = (fitted + " " + sentence).strip()
        if x_weighted_length(candidate) > limit:
            break
        fitted = candidate
    return fitted or FALLBACK


def build_x_intent_url(base_url: str, tweet_id: str | None, text: str) -> str | None:
    if not tweet_id:
        return None
    # UNVERIFIED: assumed X web intent URL shape; no server-side post is performed.
    return f"{base_url}?{urlencode({'in_reply_to': tweet_id, 'text': text})}"
