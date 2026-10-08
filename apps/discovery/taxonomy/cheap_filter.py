from __future__ import annotations

import re
from typing import Optional
from .normalizer import normalize_persian_text

# Deterministic purchase and need signal patterns
BUYER_SIGNALS: list[str] = [
    "میخوام", "می خواهم", "می‌خوام", "میخام",
    "لازم دارم", "نیاز دارم", "نیازمند", "محتاجم",
    "دنبال", "می گردم", "می‌گردم", "میگردم",
    "میخرم", "خریدار", "قصد خرید", "خریدارم", "میخواستم بخرم", "خرید",
    "قیمت", "چنده", "نرخ", "هزینه", "تعرفه",
    "سراغ دارید", "سراغ داری", "پیشنهاد", "پیشنهادی", "پیشنهادتون",
    "کسی داره", "کسی سراغ داره", "موجود دارید", "موجوده", "موجود هست",
    "سفارش", "سفارش بدم", "ثبت نام", "تهیه کنم", "خریداری کنم",
    "کجا داره", "از کجا بخرم", "راهنمایی برای خرید", "چی پیشنهاد",
    "فروش دارید", "ارسال دارید", "امکان خرید",
]

# Obvious non-buyer / spam / chit-chat filter (negative signals when unaccompanied by need)
OBVIOUS_NON_BUYER_PATTERNS: list[re.Pattern] = [
    re.compile(r"^(سلام|درود|صبح بخیر|عصر بخیر|شب بخیر|مرسی|ممنون|تشکر|اوکی|باشه|خسته نباشید)$", re.IGNORECASE),
    re.compile(r"^(خوبید|چطورید|حال شما|احوال شما|فدات|قربانت)$", re.IGNORECASE),
]


class CheapNeedFilter:
    """
    Deterministic fast filter that checks whether a message contains
    purchase or need signals before spending LLM tokens.
    """

    def __init__(self, additional_signals: Optional[list[str]] = None):
        self.signals = list(BUYER_SIGNALS)
        if additional_signals:
            self.signals.extend(additional_signals)

    def evaluate(self, raw_message: str) -> tuple[bool, str]:
        """
        Evaluates a raw customer message.
        Returns:
            (is_potential_buyer: bool, reason: str)
        """
        if not raw_message or not raw_message.strip():
            return False, "EMPTY_MESSAGE"

        normalized = normalize_persian_text(raw_message)

        if len(normalized) < 3:
            return False, "MESSAGE_TOO_SHORT"

        # Check for pure pleasantries / zero intent
        for pat in OBVIOUS_NON_BUYER_PATTERNS:
            if pat.match(normalized):
                return False, "CHITCHAT_NO_BUYER_INTENT"

        # Check for presence of any purchase/need signals
        matched_signals = [sig for sig in self.signals if sig in normalized]
        if matched_signals:
            return True, f"MATCHED_SIGNALS: {', '.join(matched_signals[:3])}"

        # Additional regex patterns for price inquiries e.g. "قیمتش؟", "چند؟"
        if re.search(r"\b(چند|چنده|قیمت|تخفیف|موجود)\b", normalized):
            return True, "MATCHED_INQUIRY_PATTERN"

        return False, "REJECTED_NON_BUYER"


# Default singleton instance
default_cheap_filter = CheapNeedFilter()
