"""
Comment Moderation & Anti-Toxicity Filter for Instagram Scraper.
Detects and filters:
1. Profanity, vulgarities, and abusive Persian / English language.
2. Disses, mockery, insults, and attacks against the seller or post.
3. Spam, spam bots, follower/like sellers, and scam ads.
"""
import re
from typing import Tuple

# ---------------------------------------------------------------------------
# 1. Profanity & Vulgarities (فحش‌ها، الفاظ رکیک و توهین‌های خانوادگی/جنسی)
# ---------------------------------------------------------------------------
PROFANITY_PATTERNS = [
    r"\bحرومزاده\b", r"\bحرامزاده\b", r"\bبی[\s‌]?شرف\b", r"\bبیشرف\b",
    r"\bبی[\s‌]?ناموس\b", r"\bبیناموس\b", r"\bپفیوز\b", r"\bدیوس\b", r"\bدیوث\b",
    r"\bاسکل\b", r"\bاوسکول\b", r"\bاسکول\b", r"\bعوضی\b", r"\bاحمق\b", r"\bابله\b",
    r"\bکودن\b", r"\bبیشعور\b", r"\bبی[\s‌]?شعور\b", r"\bنفهم\b",
    r"\bخفه[\s‌]?شو\b", r"\bگمشو\b", r"\bگم[\s‌]?شو\b", r"\bخفه\b",
    r"\bزر[\s‌]?نزن\b", r"\bزر[\s‌]?مفت\b", r"\bچرت[\s‌]?نگو\b", r"\bکس[\s‌]?نگو\b",
    r"\bکص[\s‌]?نگو\b", r"\bگوه[\s‌]?نخور\b", r"\bگه[\s‌]?نخور\b", r"\bریدی\b",
    r"\bریدین\b", r"\bکسشعر\b", r"\bکصشعر\b", r"\bکسشر\b", r"\bکصشر\b",
    r"\bکیر\b", r"\bکص\b", r"\bکس\b", r"\bجنده\b", r"\bکونی\b", r"\bمادر[\s‌]?خراب\b",
    r"\bمادر[\s‌]?جنده\b", r"\bخار[\s‌]?کسه\b", r"\bلاشخور\b", r"\bحرومخور\b",
    r"\bحروم[\s‌]?خور\b", r"\bپست[\s‌]?فطرت\b", r"\bنکبت\b",
]

# ---------------------------------------------------------------------------
# 2. Disses, Mockery & Sarcastic Attacks (دیس، تیکه‌ها و تمسخر فروشنده و پست)
# ---------------------------------------------------------------------------
DISS_PATTERNS = [
    # جونت درمیاد قیمت بگی / سنگاو بگی
    r"جونت[\s‌]?در[\s‌]?میاد",
    r"جونتون[\s‌]?در[\s‌]?میاد",
    r"مزخرف[\s‌]?بازی",
    r"مزخزف",
    r"مسخره[\s‌]?بازی",
    r"مسخره[\s‌]?ها",
    r"دلقک[\s‌]?بازی",
    r"جمع[\s‌]?کنید",
    r"جمع[\s‌]?کن[\s‌]?بابا",
    r"گول[\s‌]?نخورید",
    r"کلاهبردار",
    r"کلاهبرداری",
    r"دروغگو",
    r"دروغه",
    r"دروغ[\s‌]?محض",
    r"چرت[\s‌]?و[\s‌]?پرت",
    r"شر[\s‌]?و[\s‌]?ور",
    r"شروور",
    r"جیب[\s‌]?مردم",
    r"پول[\s‌]?مردم[\s‌]?خوری",
    r"خاک[\s‌]?تو[\s‌]?سرت",
    r"خاک[\s‌]?بر[\s‌]?سرت",
    r"خجالت[\s‌]?بکش",
    r"خجالت[\s‌]?بکشید",
    r"مفت[\s‌]?نمی‌ارزه",
    r"مفت[\s‌]?نمیارزه",
    r"آت[\s‌]?و[\s‌]?آشغال",
    r"اتوآشغال",
    r"بدردنخور",
    r"به[\s‌]?درد[\s‌]?نخور",
]

# ---------------------------------------------------------------------------
# 3. Spam & Commercial Comment Promotion (تبلیغات هرزنامه، فالوور و درآمد)
# ---------------------------------------------------------------------------
SPAM_PATTERNS = [
    r"فالوور[\s‌]?(?:ارزان|رایگان|فوری|واقعی)",
    r"افزایش[\s‌]?(?:فالوور|لایک|ویو|کامنت|سین)",
    r"پیج[\s‌]?منو?[\s‌]?فالو",
    r"درآمد[\s‌]?دلاری",
    r"روزی[\s‌]?\d+[\s‌]?(?:دلار|میلیون)",
    r"فال[\s‌]?(?:تاروت|سرکتاب|قهوه|احضار)",
    r"سرکتاب",
    r"دعای[\s‌]?(?:محبت|رزق|ازدواج|بخت[\s‌]?گشایی)",
    r"طلسم",
    r"شارژ[\s‌]?رایگان",
    r"تبادل[\s‌]?(?:شات|استوری|پیج)",
    r"سکس[\s‌]?چت",
    r"صیغه",
    r"ماساژور[\s‌]?خانم",
]

COMPILED_PROFANITY = [re.compile(p, re.IGNORECASE) for p in PROFANITY_PATTERNS]
COMPILED_DISS = [re.compile(p, re.IGNORECASE) for p in DISS_PATTERNS]
COMPILED_SPAM = [re.compile(p, re.IGNORECASE) for p in SPAM_PATTERNS]


def is_toxic_or_spam_comment(text: str) -> Tuple[bool, str]:
    """
    Checks if a comment contains profanity, disses/mockery, or spam.
    Returns (is_rejected: bool, reason: str).
    - If True, the comment should be immediately rejected and NOT saved.
    - If False, the comment is clean and eligible for buyer intent evaluation.
    """
    if not text or not text.strip():
        return True, "متن کامنت خالی است"

    normalized = text.strip()

    # 1. Check Profanity
    for pattern in COMPILED_PROFANITY:
        if pattern.search(normalized):
            return True, f"حاوی الفاظ رکیک یا توهین ({pattern.pattern})"

    # 2. Check Disses & Sarcastic Attacks
    for pattern in COMPILED_DISS:
        if pattern.search(normalized):
            return True, f"دیس، تمسخر یا حمله به فروشنده ({pattern.pattern})"

    # 3. Check Spam & Ads
    for pattern in COMPILED_SPAM:
        if pattern.search(normalized):
            return True, f"هرزنامه، تبلیغ پیج یا ربات ({pattern.pattern})"

    # Length sanity check: comments with > 12 repetitive punctuation characters (e.g. "?????????????????")
    if re.search(r"([?!.،]){12,}", normalized):
        return True, "اسپم علائم نگارشی تکراری"

    return False, "Clean"


# ---------------------------------------------------------------------------
# 4. Pure Emojis, Symbols & Non-Substantive Content (کامنت‌های صرفاً ایموجی و علائم)
# ---------------------------------------------------------------------------
EMOJI_PATTERN = re.compile(
    "["
    "\U00010000-\U0010ffff"   # Supplementary planes (99% of modern emojis:,,,, etc.)
    "\u2600-\u27bf"           # Miscellaneous Symbols, Dingbats (,,, etc.)
    "\u2300-\u23ff"           # Miscellaneous Technical
    "\u2b50-\u2b55"           # Star and shape symbols
    "\u200d\ufe0e\ufe0f"       # Zero-width joiner & variation selectors
    "]+",
    flags=re.UNICODE
)

MENTION_OR_URL_PATTERN = re.compile(r"@[\w_.]+|https?://\S+|www\.\S+", flags=re.UNICODE)


def is_pure_emoji_or_symbols_only(text: str) -> Tuple[bool, str]:
    """
    Checks if a comment consists ONLY of emojis, punctuation, symbols, whitespace,
    or empty mentions (zero substantive Persian/Latin alphanumeric content).
    Filtering these immediately burns ZERO LLM tokens!
    """
    if not text or not text.strip():
        return True, "متن کامنت خالی است"

    # Strip mentions and URLs
    no_mentions = MENTION_OR_URL_PATTERN.sub("", text).strip()
    if not no_mentions:
        return True, "صرفاً منشن کاربر بدون متن"

    # Strip all emojis
    no_emojis = EMOJI_PATTERN.sub("", no_mentions).strip()
    if not no_emojis:
        return True, "صرفاً ایموجی بدون هیچ کلمه یا پیام متنی"

    # Strip punctuation, symbols, and whitespace (keep only genuine Persian/English letters and numbers)
    clean_alphanumeric = re.sub(r"[^آ-یa-zA-Z0-9۰-۹]", "", no_emojis).strip()
    if not clean_alphanumeric:
        return True, "صرفاً ایموجی و علائم نگارشی بدون محتوای متنی"

    return False, "Has text"


