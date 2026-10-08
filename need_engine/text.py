"""Persian text normalisation, cheap message filter and a small BM25."""
from __future__ import annotations

import math
import re
import unicodedata
from collections import Counter

import numpy as np

_AR = str.maketrans({"ي": "ی", "ى": "ی", "ك": "ک", "ة": "ه", "ۀ": "ه", "أ": "ا", "إ": "ا", "ٱ": "ا"})
_DIG = str.maketrans("۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩", "01234567890123456789")
_STOP = set("و در به از که این آن با برای را تا یا هم می ها های یک است هست شد شده بود کنم کنید کن من تو ما شما او اون رو چی چه".split())
_ACKS = {"مرسی", "ممنون", "باشه", "اوکی", "ok", "okay", "آره", "نه", "سلام", "خوبی", "+1", "+", "😂", "👍", "🙏", "لایک", "دمت گرم", "عالی"}


def norm(s: str | None) -> str:
    s = unicodedata.normalize("NFKC", str(s or "")).translate(_AR).translate(_DIG)
    s = s.replace("\u200c", " ").lower()
    return re.sub(r"\s+", " ", s).strip()


def tokens(s: str | None) -> list[str]:
    return [t for t in re.findall(r"[\w]+", norm(s)) if len(t) > 1 and t not in _STOP]


def is_noise(text: str, min_chars: int = 2) -> bool:
    """Free pre-filter: messages that cannot carry a need (empty, emoji/punctuation only, bare acknowledgements)."""
    t = norm(text)
    if len(t) < min_chars:
        return True
    if not re.search(r"[\w]", t):           # only emoji / punctuation
        return True
    return t in _ACKS


class BM25:
    def __init__(self, docs: list[str], k1: float = 1.5, b: float = 0.75):
        self.docs = [Counter(tokens(d)) for d in docs]
        self.k1, self.b = k1, b
        self.len = np.array([sum(c.values()) for c in self.docs], dtype=np.float32)
        self.avg = float(self.len.mean()) if len(self.docs) else 1.0
        df = Counter(t for c in self.docs for t in c)
        n = len(docs)
        self.idf = {t: math.log(1 + (n - k + 0.5) / (k + 0.5)) for t, k in df.items()}

    def scores(self, query: str) -> np.ndarray:
        q = tokens(query)
        s = np.zeros(len(self.docs), dtype=np.float32)
        for i, c in enumerate(self.docs):
            sc = 0.0
            for t in q:
                f = c.get(t)
                if f:
                    sc += self.idf[t] * f * (self.k1 + 1) / (f + self.k1 * (1 - self.b + self.b * self.len[i] / (self.avg or 1)))
            s[i] = sc
        return s
