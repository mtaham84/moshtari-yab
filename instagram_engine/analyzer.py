import re
import json
import logging
from typing import Optional, List, Dict, Any, Tuple
import urllib.request
import urllib.error
try:
    import httpx
except ImportError:
    httpx = None

from instagram_engine.config import settings

logger = logging.getLogger("instagram_engine.analyzer")

# ---------------------------------------------------------------------------
# Tier 1A: Persian Regex Patterns for Caption CTA Detection
# ---------------------------------------------------------------------------
PERSION_CTA_PATTERNS = [
    # کلمه X رو/را کامنت کنید/بذارید/بنویسید
    re.compile(r"کلمه(?:‌ی|ی|\s+)?\s*[«\"'(\[{]*([^\s\n،,«»\"'؛:!?]+)[»\"')\]}]*\s+(?:رو|را)\s+کامنت", re.IGNORECASE),
    re.compile(r"عبارت(?:‌ی|ی|\s+)?\s*[«\"'(\[{]*([^\s\n،,«»\"'؛:!?]+)[»\"')\]}]*\s+(?:رو|را)\s+کامنت", re.IGNORECASE),
    re.compile(r"عدد(?:‌ی|ی|\s+)?\s*[«\"'(\[{]*(\d+|[^\s\n،,«»\"'؛:!?]+)[»\"')\]}]*\s+(?:رو|را)\s+کامنت", re.IGNORECASE),
    # کامنت کنید X / کامنت کن X
    re.compile(r"کامنت\s+(?:کنید|کن|بذارید|بنویسید)\s+[«\"'(\[{]*([^\s\n،,«»\"'؛:!?]+)", re.IGNORECASE),
    re.compile(r"فقط\s+بنویس\s+[«\"'(\[{]*([^\s\n،,«»\"'؛:!?]+)", re.IGNORECASE),
    # برای دریافت قیمت/لینک کلمه X را بفرستید/کامنت کنید
    re.compile(r"(?:دریافت|ارسال|خرید|تخفیف|قیمت|لینک)\s+.*?(?:کلمه|عبارت)\s+[«\"'(\[{]*([^\s\n،,«»\"'؛:!?]+)", re.IGNORECASE),
]

PUNCTUATION_TO_STRIP = "«»\"'()[]{}.,;:!؟?،#@/\n\r\t "

# ---------------------------------------------------------------------------
# Tier 2: Persian High-Intent Buyer Keywords
# ---------------------------------------------------------------------------
BUYER_KEYWORDS = [
    "قیمت",
    "چنده",
    "چند",
    "موجوده",
    "موجود دارید",
    "سفارش",
    "ثبت سفارش",
    "شرایط",
    "ارسال",
    "هزینه ارسال",
    "دایرکت چک",
    "دایرکت پیام",
    "چک دایرکت",
    "لینک",
    "خرید",
    "نحوه خرید",
    "سایز",
    "قد",
    "رنگبندی",
    "آدرس حضوری",
]


def clean_trigger_word(raw: str) -> str:
    """Cleans punctuation, Persian quotes, and symbols from extracted trigger words."""
    cleaned = raw.strip(PUNCTUATION_TO_STRIP)
    return cleaned


def extract_caption_cta(caption: Optional[str]) -> Optional[str]:
    """
    Tier 1A: Uses deterministic Persian regex patterns to extract CTA trigger words
    that the post author asked users to comment (e.g., 'کلمه لباس رو کامنت کن').
    """
    if not caption or not caption.strip():
        return None

    for pattern in PERSION_CTA_PATTERNS:
        match = pattern.search(caption)
        if match:
            candidate = clean_trigger_word(match.group(1))
            if candidate and len(candidate) >= 2 and candidate not in ("کنید", "رو", "را", "کن", "پست"):
                logger.debug("Regex matched caption CTA trigger word: '%s'", candidate)
                return candidate

    return None


def _call_llm_completion(payload: dict, timeout: float = 20.0) -> Optional[str]:
    """Helper to perform OpenAI-compatible chat completion using httpx or stdlib urllib."""
    if not settings.llm_api_key:
        return None
    url = f"{settings.llm_api_base.rstrip('/')}/chat/completions"
    headers = {
        "Authorization": f"Bearer {settings.llm_api_key}",
        "Content-Type": "application/json",
    }
    try:
        if httpx is not None:
            with httpx.Client(timeout=timeout) as client:
                resp = client.post(url, headers=headers, json=payload)
                if resp.status_code == 200:
                    return resp.json()["choices"][0]["message"]["content"]
                logger.warning("LLM API error HTTP %d: %s", resp.status_code, resp.text[:200])
                return None
        else:
            req = urllib.request.Request(url, data=json.dumps(payload).encode("utf-8"), headers=headers)
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                res_json = json.loads(resp.read().decode("utf-8"))
                return res_json["choices"][0]["message"]["content"]
    except Exception as exc:
        logger.error("LLM call failed: %s", exc)
        return None


def llm_extract_caption_cta(caption: Optional[str]) -> Optional[str]:
    """
    Tier 1B: LLM fallback for complex or conversational Persian caption CTAs
    (e.g., author asks for an emoji, phrase, or indirect CTA not caught by regex).
    """
    if not caption or not caption.strip() or not settings.llm_api_key:
        return None

    prompt = (
        "شما تحلیل‌گر کپشن اینستاگرام فارسی هستید. وظیفه شما تشخیص این است که آیا نویسنده کپشن از مخاطبان "
        "خواسته کلمه، عبارت، عدد یا ایموجی خاصی را در کامنت بنویسند تا قیمت/لینک/اطلاعات برایشان دایرکت شود یا خیر.\n"
        "اگر کلمه یا عبارت خاصی مشخص شده، فقط و فقط همان کلمه یا عبارت را بدون هیچ توضیح اضافی برگردان.\n"
        "اگر هیچ درخواست کامنت خاصی وجود ندارد، دقیقاً کلمه 'NONE' را برگردان.\n\n"
        f"کپشن:\n{caption[:1500]}"
    )

    payload = {
        "model": settings.llm_model,
        "messages": [
            {"role": "system", "content": "You are a precise Persian text extractor. Return only the requested trigger or 'NONE'."},
            {"role": "user", "content": prompt},
        ],
        "temperature": 0.0,
        "max_tokens": 50,
    }

    raw = _call_llm_completion(payload, timeout=15.0)
    if raw:
        candidate = clean_trigger_word(raw.strip())
        if candidate and candidate.upper() != "NONE" and len(candidate) <= 30:
            logger.info("LLM extracted conversational caption CTA: '%s'", candidate)
            return candidate

    return None


def get_effective_caption_cta(caption: Optional[str]) -> Optional[str]:
    """
    Combined Tier 1A + Tier 1B: Deterministic Regex first, LLM fallback second.
    """
    cta = extract_caption_cta(caption)
    if cta:
        return cta
    return llm_extract_caption_cta(caption)


def is_buyer_comment(comment_text: str, cta_word: Optional[str] = None) -> Tuple[bool, Optional[str]]:
    """
    Tier 2: Direct Buyer Intent Filter.
    Returns (is_lead: bool, match_reason: Optional[str]).
    - True if the comment contains the extracted CTA word.
    - True if the comment contains high-intent Persian buying inquiry keywords.
    """
    if not comment_text or not comment_text.strip():
        return False, None

    normalized = comment_text.strip().lower()

    # 1. Match CTA word if available
    if cta_word and len(cta_word) >= 2:
        cta_norm = cta_word.lower()
        if cta_norm in normalized:
            return True, f"cta_match:{cta_word}"

    # 2. Match Direct Buying Intent Keywords
    for kw in BUYER_KEYWORDS:
        # Check whole word or substring match
        if kw in normalized:
            return True, f"keyword_match:{kw}"

    return False, None


def llm_evaluate_batch(caption: str, comments: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    Tier 3: Local or Remote LLM Batch Comment Evaluator.
    Evaluates comments that did not match simple keyword/CTA filters (e.g. descriptive problem
    statements, questions, pain points like 'چشمم پای مانیتور خسته میشه').
    
    comments input structure: [{'id': str, 'text': str, 'username': str}, ...]
    Returns filtered list of comments determined to be qualified buyer leads.
    """
    if not comments or not settings.llm_api_key:
        return []

    # Limit batch to max 25 comments per call to fit context and keep latency low
    batch_slice = comments[:25]
    comments_formatted = "\n".join([f"[{i+1}] ID:{c.get('id')} User:@{c.get('username')}: {c.get('text')}" for i, c in enumerate(batch_slice)])

    prompt = (
        "شما متخصص ارزیابی قصد خرید و لید در اینستاگرام هستید. با توجه به کپشن پست زیر، کامنت‌هایی که "
        "نشان‌دهنده نیاز واقعی به محصول، سوال جدی، ابراز درد/مشکل مرتبط با محصول، یا تمایل به خرید هستند را "
        "شناسایی کن. کامنت‌های تشکر ساده، ایموجی خالی، یا تعریف غیرخریدارانه را رد کن.\n\n"
        f"کپشن پست:\n{caption[:800]}\n\n"
        f"کامنت‌ها:\n{comments_formatted}\n\n"
        "پاسخ را دقیقاً در قالب یک آرایه JSON از شناسه‌ها برگردان. مثال: [\"123\", \"456\"]"
    )

    payload = {
        "model": settings.llm_model,
        "messages": [
            {"role": "system", "content": "You are a precise JSON evaluator. Output valid JSON list of matching comment IDs only."},
            {"role": "user", "content": prompt},
        ],
        "temperature": 0.1,
    }

    raw_content = _call_llm_completion(payload, timeout=30.0)
    if raw_content:
        match = re.search(r"\[.*?\]", raw_content, re.DOTALL)
        if match:
            try:
                matched_ids = json.loads(match.group(0))
                matched_ids_set = {str(mid).strip() for mid in matched_ids}
                qualified = [c for c in batch_slice if str(c.get("id")) in matched_ids_set]
                logger.info("Tier 3 LLM evaluated %d comments, qualified %d leads.", len(batch_slice), len(qualified))
                return qualified
            except Exception as exc:
                logger.warning("Failed to parse LLM JSON batch evaluation: %s", exc)

    return []


# ---------------------------------------------------------------------------
# Strict Anti-Spam Guardrail Regexes for First Cold DM
# ---------------------------------------------------------------------------
URL_REGEX = re.compile(
    r"(?:https?:\/\/|www\.)[^\s]+|\b[a-zA-Z0-9.-]+\.(?:ir|com|net|org|co|me|shop|site|online|io|info|biz)\b",
    re.IGNORECASE
)
MENTION_REGEX = re.compile(r"@[\w.]+")
PHONE_REGEX = re.compile(r"(?:\+98|0098|0)?9\d{9}|\b0\d{2,3}[-\s]?\d{7,8}\b")


def validate_and_sanitize_dm(text: str) -> Tuple[bool, str]:
    """
    Strict Guardrail: Verifies that the first Cold DM contains ZERO links,
    ZERO @mentions, and ZERO phone numbers.
    Returns (is_valid: bool, cleaned_or_reason: str).
    """
    if not text or not text.strip():
        return False, "Empty message"

    if URL_REGEX.search(text):
        logger.warning("Rejected DM containing URL: '%s'", text)
        return False, "Contains URL link (forbidden in first Cold DM)"

    if MENTION_REGEX.search(text):
        logger.warning("Rejected DM containing Instagram @mention: '%s'", text)
        return False, "Contains @mention"

    if PHONE_REGEX.search(text):
        logger.warning("Rejected DM containing phone number: '%s'", text)
        return False, "Contains phone number"

    return True, text.strip()

