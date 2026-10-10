"""Buyer-intent X search queries built from products (optionally from LLM-written search terms).

X joins plain words with AND, so one query per template ("دنبال X می‌گردم", "X می‌خوام", …) is both narrow and
wasteful. Instead every query is ``(term OR "multi word term" …) (intent word OR …) <operators>``: one search covers
all names of the product and all buying cues. Time windows (``since_time:``) are added by the worker per run.
"""
from __future__ import annotations

import os
import re
from typing import Any

from need_engine.text import norm, tokens

# kept for backward compatibility (imported by older code/tests); queries now use INTENT_WORDS
INTENT_TEMPLATES = (
    "دنبال {t} می‌گردم", "دنبال {t} میگردم", "دنبال {t} هستم", "{t} می‌خوام",
    "{t} میخوام", "قصد خرید {t} دارم", "کسی {t} سراغ داره", "{t} پیشنهاد بدید",
    "{t} کجا بخرم", "برای خرید {t} راهنمایی", "{t} چی بخرم", "ممنون میشم {t} معرفی کنید",
)
INTENT_WORDS = ("بخرم", "بگیرم", "میخوام", "می‌خوام", "پیشنهاد", "سراغ", "معرفی", "دنبال", "خوبه")
DEFAULT_NEGATIVES = ("تخفیف", "\"ارسال رایگان\"", "\"کد تخفیف\"", "\"فروش ویژه\"")
_FA = re.compile(r"[آ-ی]")
# marketing/filler words dropped from title n-grams (nobody searches "ایرفون بلوتوثی مدل")
TITLE_STOPWORDS = {"مدل", "فروشگاه", "جدید", "اورجینال", "اصل", "اصلی", "ویژه", "پرفروش", "بهترین", "فوق", "العاده", "کد", "سری", "طرح",
                   "خرید", "قیمت", "فروش", "ارزان", "ارزانترین", "تخفیف", "تخفیفی", "آنلاین"}
# one-word names too broad to search alone ("اشتراک" + "خوبه" matches half of Persian X)
GENERIC_SINGLE = {"اشتراک", "اکانت", "حساب", "محصول", "کالا", "لوازم", "خرید", "قیمت", "سرویس", "پکیج", "بسته"}
# brand/model queries still need a buying/opinion cue, otherwise "YouTube Premium" returns every mention
_LEADING_NOISE = {norm(w) for w in ("خرید", "قیمت", "فروش", "سفارش")}
PRODUCT_CUES = ("خوبه", "بخرم", "بگیرم", "پیشنهاد", "نظرتون", "تجربه", "ارزش")
_LATIN_TOKEN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9.\-]*$")
_TITLE_SPLIT = re.compile(r"\s+[-–—|]\s+|\s*[|،(]\s*")


def _env_bool(name: str, default: bool = False) -> bool:
    return os.getenv(name, str(default)).strip().lower() in {"1", "true", "yes", "on"}


def clean_term(value: Any) -> str:
    """A search term without X syntax: no quotes/parentheses/operators, no OR/AND, no leading '-', '#', '@'."""
    text = re.sub(r"[\"“”«»()\[\]{}:*]", " ", str(value or ""))
    words = [w.lstrip("-#@+") for w in text.split()]
    return " ".join(w for w in words if w and w.upper() not in {"OR", "AND"})


def _quote(term: str) -> str:
    return f'"{term}"' if " " in term else term


def clean_title(title: str) -> str:
    """First segment of a marketplace title: "آستین … Pro - فروشگاه پدارول" → "آستین … Pro"."""
    return (_TITLE_SPLIT.split(str(title or "").strip()) or [""])[0].strip()


def model_term(title: str) -> str:
    """Brand/model as people type it ("Airfly M7"): the first latin run of the cleaned title, at most 2 tokens."""
    best: list[str] = []
    run: list[str] = []
    for word in clean_title(title).split() + [""]:
        if word and _LATIN_TOKEN.match(word):
            run.append(word)
            continue
        if run and any(len(re.sub(r"[^A-Za-z]", "", w)) >= 3 for w in run) and (len(run) > len(best) or not best):
            best = run
        run = []
    if len(best) == 1 and not re.search(r"\d", best[0]):
        return ""            # a lone brand word ("Riversong") is too broad; brand + model only
    return " ".join(best[:2])


def core_terms(product: Any, card: Any = None) -> list[str]:
    """Names of the product people may type (Persian, 1–4 words), most specific first."""
    card = card if card is not None else getattr(product, "card", None)
    path = str(getattr(product, "category_path", "") or "")
    title = [t for t in tokens(clean_title(getattr(product, "title", ""))) if _FA.search(t) and t not in TITLE_STOPWORDS]
    candidates = [str(getattr(product, "product_type", "") or "")]
    candidates += list(getattr(card, "aliases", []) or [])
    candidates += [path.split("/")[-1].strip()] if path else []
    candidates += list(getattr(product, "category_keywords", []) or [])
    candidates += [" ".join(title[:size]) for size in (2, 3)]
    return _dedupe_terms(candidates, 1, 4)


def _dedupe_terms(values: list[Any], min_words: int, max_words: int) -> list[str]:
    out, seen = [], set()
    for value in values:
        words = clean_term(value).split()
        while words and norm(words[0]) in _LEADING_NOISE:   # "خرید اکانت نوشن" → "اکانت نوشن"
            words = words[1:]
        words = words[:max_words]
        term = " ".join(words)
        key = norm(term)
        if len(words) == 1 and norm(words[0]) in {norm(g) for g in GENERIC_SINGLE}:
            continue
        if min_words <= len(words) and _FA.search(term) and key and key not in seen:
            seen.add(key)
            out.append(term)
    return out


def operators() -> str:
    ops = os.getenv("X_QUERY_OPERATORS", "lang:fa").strip()
    if _env_bool("X_QUERY_NEGATIVE_OPERATORS"):
        extra = [x.strip() for x in os.getenv("X_QUERY_NEGATIVE_TERMS", "").split(",") if x.strip()]
        negatives = [f"-{_quote(clean_term(x))}" for x in extra] if extra else [f"-{x}" for x in DEFAULT_NEGATIVES]
        ops = " ".join([ops, *negatives]).strip()
    suffix = os.getenv("X_QUERY_SUFFIX", "").strip()
    return " ".join(x for x in (ops, suffix) if x)


def or_queries(terms: list[str], tail: str, max_chars: int) -> list[str]:
    """Pack terms into ``(a OR "b c" …) <tail>`` queries no longer than ``max_chars``."""
    out, group = [], []

    def render(items: list[str]) -> str:
        head = _quote(items[0]) if len(items) == 1 else "(" + " OR ".join(_quote(t) for t in items) + ")"
        return f"{head} {tail}".strip()

    for term in terms:
        if group and len(render(group + [term])) > max_chars:
            out.append(render(group))
            group = []
        if len(render([term])) <= max_chars:
            group.append(term)
    if group:
        out.append(render(group))
    return out


def generate_queries(product: Any, mode: str | None = None, terms: dict[str, Any] | None = None,
                     card: Any = None) -> list[dict[str, str]]:
    """``terms`` (optional, from the LLM): {"names": [...], "problems": [...], "model": str | None}."""
    mode = (mode or os.getenv("X_QUERY_MODE", "both")).strip().lower()
    if mode not in {"intent", "product", "both"}:
        mode = "both"
    max_chars = int(os.getenv("X_QUERY_MAX_CHARS", "400"))
    max_count = int(os.getenv("X_QUERY_MAX_PER_PRODUCT", "12"))
    if mode == "product":   # legacy: raw title / category / keywords
        legacy = [str(getattr(product, key, "") or "").strip() for key in ("title", "category_path")]
        legacy += [str(x).strip() for x in (getattr(product, "category_keywords", []) or [])]
        return [{"query": query, "kind": "product"} for query in legacy if len(query) >= 2]
    terms = terms or {}
    ops = operators()
    intent_tail = "(" + " OR ".join(INTENT_WORDS) + ") " + ops
    names = _dedupe_terms(list(terms.get("names") or []) + core_terms(product, card), 1, 4)
    values: list[tuple[str, str]] = [(q, "intent") for q in or_queries(names, intent_tail, max_chars)]
    problems = _dedupe_terms(list(terms.get("problems") or []), 2, 5)
    values += [(q, "problem") for q in or_queries(problems, ops, max_chars)]
    if mode == "both":
        model = clean_term(terms.get("model") or "") or model_term(getattr(product, "title", ""))
        if model and len(model) >= 3:
            values.append((f"{_quote(model)} ({' OR '.join(PRODUCT_CUES)}) {ops}".strip(), "product"))
    out, seen = [], set()
    for query, kind in values:
        query = " ".join(query.split())
        key = norm(query)
        if not query or query.startswith("-") or len(query) > max_chars or key in seen:
            continue
        seen.add(key)
        out.append({"query": query, "kind": kind})
        if len(out) >= max_count:
            break
    return out


# ── per-product mode (NE_X_MODE=product): every query belongs to one seller product ────────────────────────────
# buying cues people write next to a product name; "موجود دارین" catches replies under competitors' ads
PRODUCT_MODE_CUES = INTENT_WORDS + ("موجود", "دارین", "از کجا", "تهیه", "کدوم")
# words of seller ads; a buyer rarely writes them
PRODUCT_MODE_NEGATIVES = ("تخفیف", "کد تخفیف", "فروش ویژه", "ارسال رایگان")
_LATIN_NAME = re.compile(r"[A-Za-z]{3,}")


def _name_terms(values: list[Any], cap: int) -> list[str]:
    """Product names for search: Persian or Latin («notion ai»), 1-3 words, no generic single words."""
    out, seen = [], set()
    generic = {norm(g) for g in GENERIC_SINGLE}
    for value in values:
        words = clean_term(value).split()
        while words and norm(words[0]) in _LEADING_NOISE:
            words = words[1:]
        words = words[:3]
        term = " ".join(words)
        key = norm(term).casefold()
        if not words or (len(words) == 1 and norm(words[0]) in generic) or key in seen:
            continue
        if not (_FA.search(term) or _LATIN_NAME.search(term)) or len(term) < 3:
            continue
        seen.add(key)
        out.append(term)
        if len(out) >= cap:
            break
    return out


def _short_terms(values: Any, cap: int, max_words: int = 3) -> list[str]:
    out = []
    for value in values if isinstance(values, (list, tuple)) else []:
        term = " ".join(clean_term(value).split()[:max_words])
        if term and term not in out:
            out.append(term)
    return out[:cap]


def product_queries(product: Any, terms: dict[str, Any] | None = None, card: Any = None) -> list[dict[str, Any]]:
    """Queries of ONE product: ``(names) (buying cues) -seller-ad-words lang:fa``, problem phrases and brand/model.
    Each query carries the product and its seller (the collector tags every post it finds with them)."""
    terms = terms or {}
    max_chars = int(os.getenv("X_QUERY_MAX_CHARS", "400"))
    max_count = int(os.getenv("X_QUERY_MAX_PER_PRODUCT_TARGETED", "4"))
    ops = os.getenv("X_QUERY_OPERATORS", "lang:fa").strip()
    llm_names = list(terms.get("names") or [])
    model = clean_term(terms.get("model") or "") or model_term(getattr(product, "title", ""))
    names = _name_terms(llm_names + ([model] if model else []) + core_terms(product, card), 8)
    cues = _short_terms(terms.get("cues"), 10) or list(PRODUCT_MODE_CUES)
    negatives = _short_terms(list(terms.get("negatives") or []) + list(PRODUCT_MODE_NEGATIVES), 6)
    neg_ops = " ".join(f"-{_quote(n)}" for n in negatives)
    tail = " ".join(x for x in ("(" + " OR ".join(_quote(c) for c in cues) + ")", neg_ops, ops) if x)
    values = [(q, "intent") for q in or_queries(names, tail, max_chars)]
    problems = _dedupe_terms(list(terms.get("problems") or []), 2, 5)
    values += [(q, "problem") for q in or_queries(problems, " ".join(x for x in (neg_ops, ops) if x), max_chars)]
    pid, bid = str(getattr(product, "product_id", "")), getattr(product, "business_id", None)
    out, seen = [], set()
    for query, kind in values:
        query = " ".join(query.split())
        key = norm(query)
        if not query or query.startswith("-") or len(query) > max_chars or key in seen:
            continue
        seen.add(key)
        out.append({"query": query, "kind": kind, "product_ids": [pid], "business_ids": [str(bid)] if bid else [],
                    "priority": float(getattr(product, "discovery_priority", 1) or 1)})
        if len(out) >= max_count:
            break
    return out
