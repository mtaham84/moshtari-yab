from __future__ import annotations

import re
import unicodedata

# Translation table for Arabic/Persian digits to ASCII digits
DIGIT_MAP = str.maketrans("۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩", "01234567890123456789")

# Arabic to Persian character mapping
CHAR_MAP = str.maketrans({
    "ي": "ی",
    "ى": "ی",
    "ك": "ک",
    "ة": "ه",
    "ؤ": "و",
    "إ": "ا",
    "أ": "ا",
    "آ": "ا",
    "ء": "",
})

# Arabic diacritics pattern (harakat, tanween, tashdeed)
DIACRITICS_PATTERN = re.compile(r"[\u064B-\u065F\u0670]")

# Punctuation to normalize to space
PUNCTUATION_PATTERN = re.compile(r"[؟!\?،,\.;:؛\"'«»\(\)\[\]\{\}\\\/\|\-_—\+=*&^%$#@~`]")

# Common Persian colloquial and spelling variants
VARIANTS = [
    (re.compile(r"\bمی\s*خو?استم\b"), "میخواستم"),
    (re.compile(r"\bمی\s*خام\b"), "میخوام"),
    (re.compile(r"\bمی\s*خواهم\b"), "میخواهم"),
    (re.compile(r"\bمی\s*خرم\b"), "میخرم"),
    (re.compile(r"\bنمی\s*دونم\b"), "نمیدونم"),
    (re.compile(r"\bنمی\s*دانم\b"), "نمیدانم"),
    (re.compile(r"\bمی\s*شه\b"), "میشه"),
    (re.compile(r"\bمی\s*شود\b"), "میشود"),
    (re.compile(r"\bچند\s*هست\b"), "چنده"),
    (re.compile(r"\bپروژه\s*محور\b"), "پروژه‌محور"),
    (re.compile(r"\bبرنامه\s*نویس(ی|ان)?\b"), r"برنامه‌نویس\1"),
    (re.compile(r"\bآنلاین\b"), "انلاین"),
]


def normalize_persian_text(text: str) -> str:
    """
    Normalizes Persian and Arabic text:
    - Normalizes Unicode NFC.
    - Replaces Arabic letters (ي, ك, ة, etc.) with Persian equivalents.
    - Replaces Persian and Arabic digits with ASCII digits.
    - Strips Arabic diacritics.
    - Normalizes Zero-Width Non-Joiner (ZWNJ / \\u200c).
    - Normalizes colloquial prefix/stem variants.
    - Cleans punctuation and excessive whitespace.
    - Lowercases Latin characters.
    """
    if not text:
        return ""

    # Unicode normalization
    t = unicodedata.normalize("NFC", str(text))

    # Strip diacritics
    t = DIACRITICS_PATTERN.sub("", t)

    # Translate Arabic characters to Persian
    t = t.translate(CHAR_MAP)

    # Translate Persian/Arabic digits to ASCII
    t = t.translate(DIGIT_MAP)

    # Lowercase Latin text
    t = t.lower()

    # Normalize spelling variants
    for pattern, replacement in VARIANTS:
        t = pattern.sub(replacement, t)

    # Clean punctuation to space
    t = PUNCTUATION_PATTERN.sub(" ", t)

    # Remove isolated or redundant ZWNJ
    t = re.sub(r"\s*\u200c\s*", " ", t)

    # Collapse multiple whitespace
    t = re.sub(r"\s+", " ", t).strip()

    return t


def extract_search_tokens(text: str) -> list[str]:
    """Tokenizes normalized text into informative search tokens, filtering short noise."""
    normalized = normalize_persian_text(text)
    tokens = [tok for tok in normalized.split() if len(tok) > 1]
    return tokens
