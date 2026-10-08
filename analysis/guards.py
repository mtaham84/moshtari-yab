"""Deterministic checks applied to every reply draft before it is shown to the seller."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from analysis.schemas import ProductProfile
from analysis.text import URL_RE

PLACEHOLDER = "{{PRODUCT_LINK}}"
_PHONE_RE = re.compile(r"(?:\+98|0)?9[\d۰-۹]{9}|[\d۰-۹]{3,4}[-\s][\d۰-۹]{3,4}[-\s][\d۰-۹]{3,4}")
_EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+\.[\w.]+")
_PERSIAN_RE = re.compile(r"[\u0600-\u06FF]")


@dataclass
class GuardResult:
    ok: bool
    text: str | None
    issues: list[str] = field(default_factory=list)


def tracked_url(url: str, ref: str) -> str:
    """Add ?src=agent&ref=<ref> so the product card counts agent clicks."""
    parts = urlsplit(url)
    query = dict(parse_qsl(parts.query))
    query.update({"src": "agent", "ref": ref})
    return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(query), parts.fragment))


def apply_guards(draft: str | None, product: ProductProfile, ref: str, max_chars: int = 600, expect_persian: bool = True) -> GuardResult:
    issues: list[str] = []
    text = (draft or "").strip()
    if not text:
        return GuardResult(False, None, ["empty_draft"])

    # Remove any URL the model wrote itself (only the catalog link is allowed).
    text_wo_placeholder = text.replace(PLACEHOLDER, "\x00")
    found_urls = URL_RE.findall(text_wo_placeholder)
    if found_urls:
        issues.append("foreign_link_removed")
        text_wo_placeholder = URL_RE.sub("", text_wo_placeholder)
    text = text_wo_placeholder.replace("\x00", PLACEHOLDER)

    if _EMAIL_RE.search(text) or _PHONE_RE.search(text):
        issues.append("contact_info_removed")
        text = _PHONE_RE.sub("", _EMAIL_RE.sub("", text))

    if expect_persian and not _PERSIAN_RE.search(text):
        return GuardResult(False, None, issues + ["not_persian"])

    count = text.count(PLACEHOLDER)
    if count == 0:
        issues.append("link_appended")
        text = f"{text.rstrip()}\n{PLACEHOLDER}"
    elif count > 1:
        issues.append("duplicate_link_removed")
        first = text.find(PLACEHOLDER) + len(PLACEHOLDER)
        text = text[:first] + text[first:].replace(PLACEHOLDER, "")

    body_len = len(text.replace(PLACEHOLDER, ""))
    if body_len > max_chars:
        issues.append("truncated")
        body = text.replace(PLACEHOLDER, "").strip()
        cut = body[:max_chars]
        stop = max(cut.rfind("."), cut.rfind("!"), cut.rfind("؟"), cut.rfind("\n"))
        body = cut[: stop + 1] if stop > max_chars // 2 else cut.rstrip() + "…"
        text = f"{body}\n{PLACEHOLDER}"

    text = re.sub(r"[ \t]{2,}", " ", text).strip()
    return GuardResult(True, text.replace(PLACEHOLDER, tracked_url(product.url, ref)), issues)
