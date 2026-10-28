import re
from typing import Tuple, Optional

# Keywords that indicate a Real Estate / Property / Villa sales listing
REAL_ESTATE_LISTING_PATTERNS = [
    r"فروش\s+ویلا",
    r"خرید\s+ویلا",
    r"فروش\s+آپارتمان",
    r"خرید\s+آپارتمان",
    r"فروش\s+خانه",
    r"خرید\s+خانه",
    r"فروش\s+ملک",
    r"خرید\s+ملک",
    r"رهن\s+و\s+اجاره",
    r"اجاره\s+ویلا",
    r"اجاره\s+آپارتمان",
    r"مشاور\s+املاک",
    r"املاک\s+و\s+مستغلات",
    r"دپارتمان\s+املاک",
    r"کد\s+ملک",
    r"کد\s+فایل",
    r"متراژ\s+زمین",
    r"متراژ\s+بنا",
    r"سند\s+تک\s*برگ",
    r"پایان\s*کار",
    r"ویلا\s+دوبلکس",
    r"ویلا\s+تریپلکس",
    r"شهرکی\s+با\s+نگهبانی",
    r"منطقه\s+برند",
    r"خواب\s+مستر",
    r"پیش\s*پرداخت\s+کلید",
    r"قیمت\s+کل\s+\d+",
    r"فروش\s+فوری\s+ویلا",
    r"لوکیشن\s+عالی",
    r"ویلای\s+لاکچری",
    r"خونه\s+رویایی",
    r"طراحی\s+پلان",
    r"دکوراسیون\s+داخلی\s+منزل",
]

COMPILED_REAL_ESTATE_PATTERNS = [re.compile(p, re.IGNORECASE) for p in REAL_ESTATE_LISTING_PATTERNS]

# Positive keywords that indicate genuine stone/quarry/building material supply
STONE_MATERIAL_SALES_PATTERNS = [
    r"فروش\s+سنگ",
    r"سنگبری",
    r"کارخانه\s+سنگ",
    r"تولید\s+سنگ",
    r"انبار\s+سنگ",
    r"معدن\s+سنگ",
    r"سنگ\s+تراورتن",
    r"سنگ\s+مرمریت",
    r"سنگ\s+اسلب",
    r"سنگ\s+گرانیت",
    r"سنگ\s+لاشه",
    r"سنگ\s+پله",
    r"سنگ\s+کف",
    r"سنگ\s+نما",
    r"متری\s+\d+",
    r"قیمت\s+هر\s+متر",
    r"کوپ\s+سنگ",
    r"ارسال\s+از\s+اصفهان",
    r"سنگبری\s+محلات",
    r"صنایع\s+سنگ",
]

COMPILED_STONE_SALES_PATTERNS = [re.compile(p, re.IGNORECASE) for p in STONE_MATERIAL_SALES_PATTERNS]

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


def is_irrelevant_post_for_business(caption: str, business_domain: str = "") -> Tuple[bool, str]:
    """
    Checks if a post's content is fundamentally outside the business domain.
    For example: Real estate & villa selling listings for a stone/material business.
    Returns: (is_irrelevant: bool, reason: str)
    """
    if not caption or not caption.strip():
        return False, ""

    cap = caption.strip()

    # Domain: Stone & Building Materials (سنگ ساختمانی و مصالح)
    is_stone_business = any(k in business_domain for k in ["سنگ", "مصالح", "تراورتن", "مرمریت", "اسلب", "stone"])
    if is_stone_business:
        # Check if it has positive stone sales indicators first
        has_stone_sales = any(pattern.search(cap) for pattern in COMPILED_STONE_SALES_PATTERNS)
        
        # Check if it matches real estate listing
        has_real_estate = any(pattern.search(cap) for pattern in COMPILED_REAL_ESTATE_PATTERNS)

        if has_real_estate and not has_stone_sales:
            return True, "پست فروش املاک/ویلا یا آپارتمان است و ارتباطی به فروش سنگ ساختمانی و مصالح ندارد."

    return False, ""


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


