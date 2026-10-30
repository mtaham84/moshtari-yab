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
ALTERNATIVE_CUES = ("جایگزین", "\"به جای\"", "\"بهتر از\"", "\"سوییچ به\"", "\"کوچ به\"")
COMPLAINT_CUES = ("\"خسته شدم از\"", "افتضاحه", "\"کار نمیکنه\"", "\"دیگه نمیکشه\"", "پشیمونم", "\"کند شده\"")
ADVICE_CUES = ("\"کسی راهی بلده\"", "\"چی پیشنهاد میدین\"", "\"تجربه دارین\"", "\"راهکاری هست\"", "\"چیکار کنم\"", "\"راه حل\"")
DISSATISFACTION_CUES = ("\"خسته شدم از\"", "\"دیگه نمیکشه\"", "\"جواب نمیده\"", "\"فایده نداره\"", "افتضاحه", "\"کار نمیکنه\"", "پشیمونم")
KIND_WEIGHTS = {"intent": 1.0, "problem": 0.8, "alternative": 0.7, "complaint": 0.6, "product": 0.4}
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


# ── short phrase rule ─────────────────────────────────────────────────────────────────────────────────────────
MAX_PHRASE_WORDS = 3
_OPERATOR = re.compile(r"(?:^|\s)(?:lang|since_time|since|until|from|to|min_faves|min_retweets|filter):\S+", re.I)
_NEG = re.compile(r'(?:^|\s)-(?:"[^"]*"|\S+)')


def positive_word_count(query: str) -> int:
    """Words a person would type in ``query``: X operators, -negatives, quotes, parentheses and OR/AND are not counted."""
    text = _NEG.sub(" ", str(query or ""))
    text = _OPERATOR.sub(" ", text)
    text = re.sub(r"[\"“”«»()\[\]{}]", " ", text)
    return len([w for w in text.split() if w.upper() not in {"OR", "AND"} and re.search(r"\w", w)])


def is_short_query(query: str) -> bool:
    n = positive_word_count(query)
    return 1 <= n <= MAX_PHRASE_WORDS


# Purely generic filler words that have no topical product substance
GENERIC_FILLER = {norm(w) for w in ("کالا", "محصول", "محصولات", "خدمات", "سرویس", "فروشگاه", "مغازه",
                                    "وسیله", "وسایل", "چیز", "چیزی", "جنس", "برند", "کیفیت",
                                    "بهترین", "خوب", "جدید", "اصل", "اصلی", "ویژه", "تکی", "آنلاین", "اینترنتی")}
# Topic nouns that are valid when combined with other words (e.g. "لباس مردانه", "خرید لباس")
CATEGORY_NOUNS = {norm(w) for w in ("لباس", "پوشاک", "کفش", "کیف", "ساعت", "ابزار", "مد")}
BROAD_WORDS = GENERIC_FILLER | CATEGORY_NOUNS
AUDIENCE_WORDS = {norm(w) for w in ("مردانه", "زنانه", "بچگانه", "دخترانه", "پسرانه", "ورزشی")}
ACTION_WORDS = {norm(w) for w in ("خرید", "قیمت", "سفارش", "پیشنهاد", "معرفی", "تجربه", "کجا", "کدوم", "کدام", "دنبال", "میخوام",
                                   "می‌خوام", "نیاز", "فروش", "تهیه", "بخرم", "راهنمایی", "مشاوره", "نظر", "پیدا", "کردن")}
WEAK_QUERY_WORDS = {norm(w) for w in ("رایگان", "مفت", "نامناسب", "بدفرم", "بی‌کیفیت", "آشغال", "خرابکاری")}


def is_too_broad(words: list[str]) -> bool:
    """Reject phrases that describe no product or specific topic at all (e.g. lone 'لباس' or 'خرید آنلاین').
    Realistic buyer phrases like 'خرید لباس مردانه', 'پوشاک مردانه', 'لباس مردانه', 'پیراهن مردانه' are valid."""
    clean = [norm(w) for w in words]
    if not clean:
        return True
    if len(clean) == 1:
        return clean[0] in BROAD_WORDS | ACTION_WORDS | AUDIENCE_WORDS
    # Reject if all words are purely abstract filler + actions with no topic or category
    return all(w in GENERIC_FILLER | ACTION_WORDS for w in clean)


def short_terms(values: Any, cap: int = 8, strip_noise: bool = False) -> list[str]:
    """1–3 word phrases only. Longer phrases are REJECTED (never truncated: a cut sentence is garbage), single
    generic words («اشتراک», «محصول») are rejected, duplicates (after normalisation) are dropped."""
    out, seen = [], set()
    generic = {norm(g) for g in GENERIC_SINGLE}
    for value in values if isinstance(values, (list, tuple)) else []:
        words = clean_term(value).split()
        while strip_noise and words and norm(words[0]) in _LEADING_NOISE and len(words) > 1:
            words = words[1:]   # product names: «خرید اکانت نوشن» → «اکانت نوشن»; buyer phrases keep «خرید …»
        if not 1 <= len(words) <= MAX_PHRASE_WORDS:
            continue
        term = " ".join(words)
        term = re.sub(r"دندان(?=\s|$)", "دندون", term)
        key = norm(term).casefold()
        if key in seen or len(term) < 3:
            continue
        if not (_FA.search(term) or any(len(re.sub(r"[^A-Za-z]", "", w)) >= 3 for w in words)):
            continue
        if len(words) == 1 and key in generic:
            continue   # a lone short word («سنگ») matches half of Persian X
        if is_too_broad(words) or any(norm(w) in WEAK_QUERY_WORDS for w in term.split()):
            continue
        seen.add(key)
        out.append(term)
        if len(out) >= cap:
            break
    return out


def phrase_candidates(product: Any, terms: dict[str, Any] | None, card: Any = None) -> list[tuple[str, str]]:
    """[(phrase, kind)] — every phrase 1–3 words. kind: intent (core topic names), problem, alternative, solution, product."""
    terms = terms or {}
    out: list[tuple[str, str]] = []
    names = short_terms(list(terms.get("names") or []) + core_terms(product, card), 4, strip_noise=True)
    out += [(p, "intent") for p in names]
    pain = short_terms(list(terms.get("pain_symptoms") or []) + list(terms.get("problems") or [])
                       + list(terms.get("dissatisfactions") or []) + list(terms.get("complaints") or []), 8)
    out += [(p, "problem") for p in pain]
    advice = short_terms(list(terms.get("advice_requests") or []) + list(terms.get("alternatives") or []), 8)
    out += [(p, "alternative") for p in advice]
    solutions = short_terms(list(terms.get("implicit_solutions") or []), 8)
    out += [(p, "solution") for p in solutions]
    model = clean_term(terms.get("model") or "") or model_term(getattr(product, "title", ""))
    if model and len(model) >= 3 and len(model.split()) <= MAX_PHRASE_WORDS:
        out.append((model, "product"))
    return out



def operators() -> str:
    ops = os.getenv("X_QUERY_OPERATORS", "lang:fa").strip()
    if _env_bool("X_QUERY_NEGATIVE_OPERATORS"):
        extra = [x.strip() for x in os.getenv("X_QUERY_NEGATIVE_TERMS", "").split(",") if x.strip()]
        negatives = [f"-{_quote(clean_term(x))}" for x in extra] if extra else [f"-{x}" for x in DEFAULT_NEGATIVES]
        ops = " ".join([ops, *negatives]).strip()
    suffix = os.getenv("X_QUERY_SUFFIX", "").strip()
    return " ".join(x for x in (ops, suffix) if x)


def or_queries(terms: list[str], tail: str, max_chars: int, max_terms: int | None = None) -> list[str]:
    """Pack terms into ``(a OR "b c" …) <tail>`` queries no longer than ``max_chars`` (and ``max_terms`` terms)."""
    out, group = [], []

    def render(items: list[str]) -> str:
        head = _quote(items[0]) if len(items) == 1 else "(" + " OR ".join(_quote(t) for t in items) + ")"
        return f"{head} {tail}".strip()

    for term in terms:
        if group and (len(render(group + [term])) > max_chars or (max_terms and len(group) >= max_terms)):
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
    per_format = int(os.getenv("X_QUERY_PER_FORMAT", "4"))
    if mode == "product":   # legacy: raw title / category / keywords
        legacy = [str(getattr(product, key, "") or "").strip() for key in ("title", "category_path")]
        legacy += [str(x).strip() for x in (getattr(product, "category_keywords", []) or [])]
        return [{"query": query, "kind": "product"} for query in legacy if len(query) >= 2]
    terms = terms or {}
    ops = operators()
    raw_candidates, seen = [], set()
    for phrase, kind in phrase_candidates(product, terms, card):
        query = f"{_quote(phrase)} {ops}".strip()      # ONE phrase (<= 3 words) per query, operators only as filters
        key = norm(query)
        if len(query) > max_chars or key in seen:
            continue
        seen.add(key)
        raw_candidates.append({"query": query, "kind": kind})

    # Same balanced three-format pick as product mode
    target_ref = f"{getattr(product, 'title', '')} {getattr(product, 'category_path', '')}"
    return balanced_pick(raw_candidates, target_ref, max_count, per_format)


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



# kind -> prompt format. A: pain & frustration, B: advice seeking, C: demand / solution / direct intent
QUERY_FORMAT = {"problem": "A", "complaint": "A", "alternative": "B", "solution": "C", "intent": "C", "product": "C"}


def balanced_pick(candidates: list[dict[str, Any]], reference: str, total: int, per_format: int) -> list[dict[str, Any]]:
    """Up to ``per_format`` MMR-diverse queries from each of the three formats, topped up to ``total``.
    Strongly prioritizes 2-word queries."""
    from shared.mmr import select_by_mmr
    buckets: dict[str, list[dict[str, Any]]] = {"A": [], "B": [], "C": []}
    for item in candidates:
        buckets[QUERY_FORMAT.get(item.get("kind"), "C")].append(item)

    def pref_key(item: dict[str, Any]) -> int:
        n = positive_word_count(item.get("query", ""))
        return 0 if n == 2 else (1 if n == 3 else 2)

    for fmt in ("A", "B", "C"):
        buckets[fmt].sort(key=pref_key)

    def mmr(items: list[dict[str, Any]], k: int) -> list[dict[str, Any]]:
        sorted_items = sorted(items, key=pref_key)
        return select_by_mmr(sorted_items, lambda i: i["query"], reference_context=reference, top_k=k, lambda_param=0.65) if sorted_items and k > 0 else []

    picked: list[dict[str, Any]] = []
    for fmt in ("A", "B", "C"):
        picked += mmr(buckets[fmt], per_format)
    taken = {id(x) for x in picked}
    rest = [c for c in candidates if id(c) not in taken]
    rest.sort(key=pref_key)
    picked += mmr(rest, total - len(picked))
    return picked[:total]


def product_queries(product: Any, terms: dict[str, Any] | None = None, card: Any = None,
                    topic_context: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    """Queries of ONE product with MP-CABS Entity-Blind 4-angle queries + MMR diversification.
    Each query carries the product and its seller (the collector tags every post it finds with them)."""
    terms = terms or {}
    max_chars = int(os.getenv("X_QUERY_MAX_CHARS", "400"))
    max_count = min(10, max(1, int(os.getenv("X_QUERY_MAX_PER_FAMILY", os.getenv("X_QUERY_MAX_PER_PRODUCT_TARGETED", "10")))))
    per_format = min(10, max(1, int(os.getenv("X_QUERY_PER_FORMAT", "4"))))
    ops = os.getenv("X_QUERY_OPERATORS", "lang:fa").strip()
    llm_names = list(terms.get("names") or [])
    model = clean_term(terms.get("model") or "") or model_term(getattr(product, "title", ""))
    # Seller-ad exclusions can hide real buyer posts mentioning shipping or discounts.
    tail_ops = ops

    values: list[tuple[str, str]] = [(p, kind) for p, kind in phrase_candidates(product, terms, card)]
    if topic_context and topic_context.get("family") is not None:
        from workers.x_collector.families import family_discovery_terms
        values.extend(family_discovery_terms(topic_context["family"]))
    pid, bid = str(getattr(product, "product_id", "")), getattr(product, "business_id", None)

    # ProQE: queries evolved from confirmed buyers (already restricted to <= 3 words)
    try:
        from workers.x_collector.proqe import load_proqe_queries
        for pq in load_proqe_queries(pid):
            q_text = pq.get("query", "").strip()
            if q_text and is_short_query(q_text):
                values.append((q_text, pq.get("kind", "problem")))
    except Exception:
        pass

    raw_candidates, seen = [], set()
    for phrase, kind in values:
        phrase = " ".join(phrase.split())
        if is_too_broad(phrase.split()) or any(norm(w) in WEAK_QUERY_WORDS for w in phrase.split()):
            continue
        if topic_context:
            from workers.x_collector.families import family_phrase_allowed
            if not family_phrase_allowed(phrase, topic_context.get("anchor_tokens", set()),
                                         topic_context.get("other_family_nouns", set()),
                                         topic_context.get("topic_tokens")):
                continue
        query = f"{_quote(phrase)} {tail_ops}".strip()
        key = norm(query)
        if not query or query.startswith("-") or len(query) > max_chars or key in seen:
            continue
        seen.add(key)
        raw_candidates.append({
            "query": query, "kind": kind, "product_ids": [str(x) for x in (getattr(product, "member_product_ids", None) or [pid])],
            "business_ids": [str(bid)] if bid else [],
            "priority": float(getattr(product, "discovery_priority", 1) or 1)
        })

    # Keep enough pain, style/use-case and direct-topic phrases; MMR removes near-duplicates before the hard family cap.
    target_ref = f"{getattr(product, 'title', '')} {getattr(product, 'category_path', '')}"
    return balanced_pick(raw_candidates, target_ref, min(10, max(1, max_count)), per_format)
