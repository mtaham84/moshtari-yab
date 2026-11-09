import re

# Generic Persian compliments & social chatter with ZERO buying intent
COMPLIMENT_WORD = r"(?:عالی|زیبا|شیک|قشنگ|خاص|جذاب|قشنگه|خوشگله|زیباست|محشره|معرکه|عالیه|خوبه|عالی[\s‌]?بود|بی‌نظیر|درجه[\s‌]?یک|قشنگ[\s‌]?بود)"

GENERIC_COMPLIMENTS_PATTERNS = [
    rf"^(?:خیلی\s+|بسیار\s+|فوق‌العاده\s+|واقعا\s+|چقد[ر]?\s+)?{COMPLIMENT_WORD}(?:\s*(?:و|,|،)\s*{COMPLIMENT_WORD})*(?:ست|ه|بود)?(?:\s+(?:داداش|عزیز|جان|مهندس|استاد|رفیق))?$",
    r"^(?:ماشا[ءالله|لا|لله]|خدا\s+قوت|دستخوش|دمتون\s+گرم|دمت\s+گرم|احسنت|آفرین|درود|خسته\s+نباشید|مبارک[ه]?|به\s+به|ایول|پرچمت\s+بالاست|موفق\s+باشید|خیرشو\s+ببینی|دست\s+مریزاد)(?:\s+(?:داداش|عزیز|جان|مهندس|استاد))?$",
    r"^(?:سلام|درود|وقت\s+بخیر|سلام\s+علیکم|روزتون\s+بخیر|شب\s+بخیر)$",
    r"^(?:حیف|حیف\s+شد|چه\s+جالب|یادش\s+بخیر|نوستالژی)\b.*",
]

COMPILED_COMPLIMENTS = [re.compile(p, re.IGNORECASE) for p in GENERIC_COMPLIMENTS_PATTERNS]


EMOJI_PATTERN = re.compile(
    "["
    "\U00010000-\U0010ffff"
    "\u2600-\u27bf"
    "\u2300-\u23ff"
    "\u2b50-\u2b55"
    "\u200d\ufe0e\ufe0f"
    "]+",
    flags=re.UNICODE
)


PRAISE_START_PATTERN = re.compile(
    r"^(?:ماشا[ءالله|لا|لله]|خدا\s+قوت|دستخوش|دمتون\s+گرم|دمت\s+گرم|احسنت|آفرین|درود|خسته\s+نباشید|مبارک[ه]?|به\s+به|ایول|پرچمت\s+بالاست|موفق\s+باشید|خیرشو\s+ببینی|دست\s+مریزاد)",
    re.IGNORECASE
)
BUY_CUES_PATTERN = re.compile(
    r"(?:قیمت|چند|خرید|سفارش|موجود|متری|دایرکت|ارسال|کاتالوگ|هزینه|تخفیف|شرایط|نحوه)",
    re.IGNORECASE
)


def is_generic_compliment_comment(comment_text: str) -> bool:
    """
    Identifies pure compliments, emoji-only, or nostalgic remarks that have
    ZERO buying intent (e.g. 'چقد زیبا😍👏', 'عالیه داداش', 'خسته نباشید مهندس').
    Filtering these early burns ZERO LLM tokens!
    """
    if not comment_text or not comment_text.strip():
        return True

    text = comment_text.strip()

    # Strip emojis and surrounding punctuation for clean match
    clean = EMOJI_PATTERN.sub("", text).strip(" .!،؟?~_-«»\"'#@*()[]{}<>^%$+=/\\").strip()
    if not clean:
        return True  # Was pure emoji/punctuation

    # Check praise starting expressions without buying cues (e.g. "ماشالله به این سلیقه", "خسته نباشید کارتون عالیه")
    if PRAISE_START_PATTERN.search(clean) and not BUY_CUES_PATTERN.search(clean):
        return True

    for pattern in COMPILED_COMPLIMENTS:
        if pattern.match(clean):
            return True

    return False


