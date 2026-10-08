"""Stage 1 — free rule-based filter.

Drops only messages that can never be opportunities. It deliberately does NOT
require product keywords, so implicit needs ("my eyes hurt after work") reach
the LLM stages.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from analysis.schemas import SocialMessage
from analysis.text import URL_RE, letter_count, normalize, strip_urls

MIN_LETTERS = 10

_CHIT_CHAT = re.compile(
    r"^(?:سلام|درود|صبح بخیر|شب بخیر|عصر بخیر|مرسی|ممنون|ممنونم|متشکرم|مچکرم|خیلی ممنون|"
    r"باشه|اوکی|ok|okay|thanks|thank you|thx|hi|hello|لایک|عالی|دمت گرم|ایول|آره|اره|نه|"
    r"خوبی|چطوری|خواهش میکنم|حله|بله|خدافظ|خداحافظ|به همه|همگی|دوستان|بچه ها|عزیزان|رفقا|[\s!؟?.،,😂🤣😍❤️👍🙏🌹😊😁🔥]+)+$",
    re.IGNORECASE,
)

# Each pattern found adds 1 to the advertisement score; >= 2 means "seller ad".
_AD_PATTERNS = [
    r"فروش ویژه", r"تخفیف ویژه", r"تخفیف استثنایی", r"قیمت استثنایی", r"حراج", r"موجود شد", r"موجود است",
    r"ارسال (?:رایگان|به (?:سراسر|تمام نقاط))", r"جهت (?:سفارش|خرید|اطلاعات بیشتر)", r"سفارش در (?:دایرکت|پی ?وی)",
    r"(?:پیج|کانال|فروشگاه) ما", r"عضو (?:شوید|بشید)", r"تماس بگیرید", r"(?:پیام|دایرکت) بدید", r"عمده و تک",
    r"نمایندگی (?:رسمی|فروش)", r"اورجینال", r"0?9\d{9}", r"\+98\d{10}",
]
_AD_RE = [re.compile(p) for p in _AD_PATTERNS]


@dataclass
class PrefilterResult:
    passed: bool
    reason: str | None = None


def advertisement_score(text: str) -> int:
    norm = normalize(text)
    score = sum(1 for rx in _AD_RE if rx.search(norm))
    if norm.count("#") >= 3:
        score += 1
    if len(URL_RE.findall(text)) >= 2:
        score += 1
    return score


def prefilter(message: SocialMessage) -> PrefilterResult:
    text = message.text or ""
    if message.author.is_bot:
        return PrefilterResult(False, "bot_author")
    if letter_count(strip_urls(text)) < MIN_LETTERS:
        return PrefilterResult(False, "link_only" if URL_RE.search(text) else "too_short")
    if _CHIT_CHAT.match(normalize(text)):
        return PrefilterResult(False, "chit_chat")
    if advertisement_score(text) >= 2:
        return PrefilterResult(False, "advertisement")
    return PrefilterResult(True)
