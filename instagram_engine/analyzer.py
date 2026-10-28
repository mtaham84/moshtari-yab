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


from apps.discovery.comment_moderation import is_toxic_or_spam_comment, is_pure_emoji_or_symbols_only
from instagram_engine.post_filters import is_irrelevant_post_for_business, is_generic_compliment_comment, EMOJI_PATTERN

PERSIAN_COMMENT_STOPWORDS = {
    "و", "در", "به", "از", "که", "این", "رو", "را", "با", "برای", "است", "شد", "بود",
    "شدن", "کردن", "سلام", "من", "تو", "ما", "شما", "اون", "چون", "هم", "یک", "یه", "یا",
    "کنید", "لطفا", "لطفاً", "میشه", "دارید", "هست", "پست", "پیج"
}


def detect_repeated_comment_triggers(comments_text_list: List[str], min_count: int = 3) -> List[Tuple[str, int]]:
    """
    Detects short keywords repeatedly commented across a post's comments.
    When a creator instructs followers 'کلمه سنگ را کامنت کنید تا دایرکت ارسال شود',
    followers repeatedly post that single word or short phrase.
    """
    if not comments_text_list or len(comments_text_list) < 3:
        return []

    from collections import Counter
    freq = Counter()

    for raw in comments_text_list:
        if not raw or not raw.strip():
            continue
        clean = EMOJI_PATTERN.sub("", raw).strip(" .!،؟?~_-«»\"'#@*()[]{}<>^%$+=/\\").strip()
        if not clean:
            continue
        words = clean.split()
        if 1 <= len(words) <= 3:
            norm_clean = clean.lower()
            if len(norm_clean) >= 2 and norm_clean not in PERSIAN_COMMENT_STOPWORDS:
                freq[norm_clean] += 1
            elif norm_clean.isdigit():
                freq[norm_clean] += 1

            if len(words) > 1:
                for w in words:
                    w_norm = w.lower().strip(" .!،؟?~_-«»\"'#@*()[]{}<>^%$+=/\\")
                    if len(w_norm) >= 2 and w_norm not in PERSIAN_COMMENT_STOPWORDS:
                        freq[w_norm] += 1

    repeated = [(word, count) for word, count in freq.most_common(8) if count >= min_count]
    return repeated


# Luna GPT-6 Official Pricing ($ per 1M tokens)
GPT6_LUNA_INPUT_PRICE_PER_M = 0.10
GPT6_LUNA_OUTPUT_PRICE_PER_M = 0.50
GPT6_LUNA_CACHED_PRICE_PER_M = 0.01



def _call_llm_with_usage(payload: dict, timeout: float = 25.0) -> Dict[str, Any]:
    """Helper to perform OpenAI-compatible chat completion and extract token usage."""
    api_key = settings.llm_api_key
    api_base = settings.llm_api_base.rstrip("/")
    model_name = payload.get("model") or getattr(settings, "llm_model", "gpt-6-luna")

    # Dynamic lookup from database Provider / AIModel if configured
    try:
        from apps.billing.models import AIModel
        db_model = AIModel.objects.filter(is_active=True).order_by("priority").first()
        if db_model and db_model.provider and db_model.provider.is_active:
            api_key = db_model.provider.api_key or api_key
            api_base = db_model.provider.base_url.rstrip("/")
            model_name = db_model.name
    except Exception:
        pass

    if not api_key:
        return {"content": None, "tokens_in": 0, "tokens_out": 0, "cost_usd": 0.0}

    payload["model"] = model_name
    url = f"{api_base}/chat/completions"
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }

    try:
        raw_res = None
        if httpx is not None:
            with httpx.Client(timeout=timeout) as client:
                resp = client.post(url, headers=headers, json=payload)
                if resp.status_code == 200:
                    raw_res = resp.json()
                else:
                    logger.warning("LLM API error HTTP %d: %s", resp.status_code, resp.text[:200])
        else:
            req = urllib.request.Request(url, data=json.dumps(payload).encode("utf-8"), headers=headers)
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                raw_res = json.loads(resp.read().decode("utf-8"))

        if raw_res and "choices" in raw_res and raw_res["choices"]:
            content = raw_res["choices"][0]["message"]["content"]
            usage = raw_res.get("usage", {})
            tokens_in = usage.get("prompt_tokens") or (len(str(payload)) // 3)
            tokens_out = usage.get("completion_tokens") or (len(content) // 3)
            
            # Accurate token cost for Luna GPT-6 ($0.10 / $0.50 per 1M)
            cost_usd = (tokens_in * GPT6_LUNA_INPUT_PRICE_PER_M + tokens_out * GPT6_LUNA_OUTPUT_PRICE_PER_M) / 1_000_000.0

            return {
                "content": content,
                "tokens_in": tokens_in,
                "tokens_out": tokens_out,
                "cost_usd": round(cost_usd, 8),
                "model": model_name,
            }
    except Exception as exc:
        logger.error("LLM call failed: %s", exc)

    return {"content": None, "tokens_in": 0, "tokens_out": 0, "cost_usd": 0.0}


def _call_llm_completion(payload: dict, timeout: float = 20.0) -> Optional[str]:
    """Helper to perform OpenAI-compatible chat completion."""
    res = _call_llm_with_usage(payload, timeout=timeout)
    return res.get("content")


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
        "model": "gpt-6-luna",
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


def evaluate_comment_smart(
    comment_text: str,
    caption: str = "",
    cta_word: Optional[str] = None,
    product_context: str = "",
    repeated_triggers: Optional[List[Tuple[str, int]]] = None,
) -> Dict[str, Any]:
    """
    Intelligent Lead Qualification with Strict Domain Relevance & Token Optimization:
    1. Pure Emoji & Symbols Check: Rejects emoji-only, punctuation, or empty mentions (0 tokens).
    2. Moderation Check: Rejects toxic/diss/spam immediately (0 tokens).
    3. Generic Compliment Check: Rejects pure praise, compliments, nostalgia (0 tokens).
    4. Post-Level Domain Check: Rejects posts selling real estate/villas/houses for stone business (0 tokens).
    5. Direct CTA & Repeated Comment Trigger Check: If user repeated the post's CTA keyword to receive a DM, qualify directly with 0 tokens.
    6. Phase 1 Token-Optimized AI Evaluation (Luna GPT-6): Evaluates remaining candidate comments with post context and repeated trigger awareness.
    """
    if not comment_text or not comment_text.strip():
        return {"is_lead": False, "reason": "empty"}

    # 1. Reject pure emojis, symbols, whitespace, or empty mentions (0 tokens)
    is_emoji, emoji_reason = is_pure_emoji_or_symbols_only(comment_text)
    if is_emoji:
        return {"is_lead": False, "is_emoji": True, "reason": f"emoji_or_symbols_only:{emoji_reason}"}

    # 2. Moderation: Reject insults, disses, profanity, and spam (0 tokens)
    is_rejected, reject_reason = is_toxic_or_spam_comment(comment_text)
    if is_rejected:
        return {"is_lead": False, "is_toxic": True, "reason": reject_reason}

    # 3. Early heuristic: Reject pure compliments, praise, or nostalgic comments (0 tokens)
    if is_generic_compliment_comment(comment_text):
        return {"is_lead": False, "reason": "generic_compliment"}

    # 3.5 Semantic BGE-M3 Moderation: Catch subtle disses, mockery, profanity, spam & gibberish
    try:
        from apps.discovery.semantic_moderation import check_comment_semantic_moderation
        sem_res = check_comment_semantic_moderation(comment_text)
        if sem_res.get("is_rejected"):
            return {
                "is_lead": False,
                "is_toxic": True,
                "reason": f"bge_m3_semantic_moderation:{sem_res.get('reason')} (conf: {sem_res.get('confidence', 0):.2f})",
            }
    except Exception:
        pass

    # 4. Post-level Domain Check: Reject real estate/villa sales posts for stone/materials (0 tokens)
    if caption:
        is_irrel, irrel_reason = is_irrelevant_post_for_business(caption, product_context)
        if is_irrel:
            return {"is_lead": False, "reason": f"irrelevant_post:{irrel_reason}"}

    norm = comment_text.strip().lower()
    clean_comment = EMOJI_PATTERN.sub("", comment_text).strip(" .!،؟?~_-«»\"'#@*()[]{}<>^%$+=/\\")
    words = clean_comment.split()

    # 5. Tier 1: Direct CTA or Repeated Trigger Keyword match
    is_direct = False
    matched_kw = None

    # Check explicit caption CTA word (e.g. 'سنگ')
    if cta_word and len(cta_word) >= 2:
        if cta_word.lower() in clean_comment.lower() and len(words) <= 3:
            is_direct = True
            matched_kw = f"cta_trigger:{cta_word}"

    # Check statistically repeated keywords across this post's comments (users asking for DM)
    if not is_direct and repeated_triggers and len(words) <= 3:
        for trig_word, count in repeated_triggers:
            if trig_word.lower() in clean_comment.lower():
                is_direct = True
                matched_kw = f"repeated_trigger:{trig_word}"
                break

    # For generic words like 'قیمت' or 'چنده'
    if not is_direct and len(words) <= 3:
        has_stone_term = any(t in norm for t in ["سنگ", "تراورتن", "اسلب", "مرمریت", "گرانیت", "متری", "پله", "نما"])
        for kw in BUYER_KEYWORDS:
            if kw in norm:
                if has_stone_term or not any(k in product_context for k in ["سنگ", "stone"]):
                    is_direct = True
                    matched_kw = f"direct_kw:{kw}"
                    break

    if is_direct:
        return {
            "is_lead": True,
            "is_direct_cta": True,
            "intent_score": 0.95,
            "matched_cta": matched_kw,
            "need": f"درخواست دایرکت و استعلام ({matched_kw})",
            "why_selected": "ثبت کلمه کلیدی یا کال‌تواکشن تکرارشده برای ارسال دایرکت",
            "suggested_reply": None,
            "tokens_in": 0,
            "tokens_out": 0,
            "cost_usd": 0.0,
        }

    # 6. Tier 2: Token-Optimized Phase 1 AI Evaluation (Luna GPT-6)
    clean_caption = caption[:240].strip() if caption else "پست اینستاگرامی"
    biz_context = product_context or "فروش و تأمین سنگ‌های ساختمانی و نما"

    repeated_cue = ""
    if repeated_triggers:
        top_cues = ", ".join(f"«{w}»" for w, c in repeated_triggers[:4])
        repeated_cue = f"کلمات کلیدی پرتکرار مخاطبان در کامنت‌های این پست (درخواست دایرکت): {top_cues}\n"

    prompt = (
        f"زمینه کسب‌وکار: {biz_context}\n"
        f"خلاصه پست: {clean_caption}\n"
        f"{repeated_cue}"
        f"کامنت کاربر: «{clean_comment[:160]}»\n\n"
        "آیا این کاربر بر اساس صحبت‌هایش یا درخواست کلمه کلیدی، واقعاً خریدار یا متقاضی محصولات این کسب‌وکار است که منتظر پاسخ در دایرکت باشد؟\n"
        "قوانین مهم:\n"
        "۱. اگر پست یا کامنت صرفاً شوخی، تمجید، احوالپرسی، حواشی، بی‌ربط، یا فحاشی و دیس است -> is_customer: false.\n"
        f"۲. فقط زمانی is_customer: true است که کاربر تقاضا، سوال، استعلام قیمت، تعرفه، شرایط خرید/اقساط یا کلمه کلیدی دایرکت مرتبط با «{biz_context}» ارسال کرده باشد.\n"
        "پاسخ فقط فرمت JSON فشرده:\n"
        '{"is_customer": true, "intent_score": 0.85, "need": "خلاصه نیاز در ۳ کلمه", "suggested_reply": "متن کوتاه دایرکت در ۱-۲ جمله"}'
    )

    payload = {
        "model": "gpt-6-luna",
        "messages": [
            {"role": "system", "content": "You are a strict Persian lead qualification AI. Output valid compact JSON only."},
            {"role": "user", "content": prompt},
        ],
        "temperature": 0.1,
        "max_tokens": 110,
    }

    ai_res = _call_llm_with_usage(payload, timeout=20.0)
    raw_content = ai_res.get("content")
    tokens_in = ai_res.get("tokens_in", 0)
    tokens_out = ai_res.get("tokens_out", 0)
    cost_usd = ai_res.get("cost_usd", 0.0)

    if raw_content:
        import json as _json
        match = re.search(r"\{.*\}", raw_content, re.DOTALL)
        if match:
            try:
                data = _json.loads(match.group(0))
                is_cust = bool(data.get("is_customer", False))
                score = float(data.get("intent_score", 0.85))
                need = str(data.get("need", ""))[:120]
                reply = str(data.get("suggested_reply", ""))[:250]

                if is_cust and score >= 0.70:
                    return {
                        "is_lead": True,
                        "is_direct_cta": False,
                        "intent_score": score,
                        "matched_cta": "ai_qualified",
                        "need": need or f"نیاز ارزیابی‌شده هوشمند: {clean_comment[:50]}",
                        "why_selected": "تشخیص و تایید تطابق نیاز با زمینه کسب‌وکار توسط Luna GPT-6",
                        "suggested_reply": reply,
                        "tokens_in": tokens_in,
                        "tokens_out": tokens_out,
                        "cost_usd": cost_usd,
                    }
                else:
                    return {
                        "is_lead": False,
                        "is_toxic": False,
                        "reason": "Not a qualified buyer for this business domain",
                        "tokens_in": tokens_in,
                        "tokens_out": tokens_out,
                        "cost_usd": cost_usd,
                    }
            except Exception as pe:
                logger.warning("Failed to parse AI evaluation JSON: %s", pe)

    # Fallback if LLM unavailable or not configured:
    is_buyer, reason = is_buyer_comment(comment_text, cta_word=cta_word)
    if is_buyer:
        return {
            "is_lead": True,
            "is_direct_cta": True,
            "intent_score": 0.88,
            "matched_cta": reason,
            "need": f"استعلام ({reason})",
            "why_selected": "انطباق با کلمات کلیدی قصد خرید",
            "suggested_reply": None,
            "tokens_in": 0,
            "tokens_out": 0,
            "cost_usd": 0.0,
        }

    return {"is_lead": False, "reason": "unqualified"}


