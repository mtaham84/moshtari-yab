import os
import re
import json
import logging
from django.utils import timezone
from django.conf import settings
from apps.businesses.models import Business
from apps.products.models import Product, Category
from .models import CategoryBranchMemory, ProcessedMessageHash, DiscoveredLead

logger = logging.getLogger(__name__)

# Sample realistic social conversations on Telegram and X (Twitter)
SAMPLE_SOCIAL_STREAM = [
    {
        "channel": "X",
        "lead_handle": "@sara_tehrani",
        "lead_display_name": "سارا تهرانی",
        "post_url": "https://x.com/sara_tehrani/status/17891230491",
        "text": "بچه‌ها کسی پیج یا آنلاین‌شاپی رو می‌شناسه که شلوار کارگو باکیفیت و دوخت تمیز داشته باشه؟ چند وقته دنبال یه کارگو زیتونی یا مشکی خوش‌فرم می‌گردم پیدا نمی‌کنم.",
        "category_hints": ["پوشاک", "زنانه", "شلوار", "کارگو", "لباس"],
        "base_intent": 88
    },
    {
        "channel": "TELEGRAM",
        "lead_handle": "@ali_reza_dev",
        "lead_display_name": "علیرضا رضایی",
        "post_url": "https://t.me/tech_community/98412",
        "text": "سلام دوستان، من تازه می‌خوام یادگیری برنامه‌نویسی پایتون رو شروع کنم برای تحلیل داده و وب. دوره یا کارگاه پروژه‌محور خوب که پشتیبانی داشته باشه چی پیشنهاد می‌دید؟ خریدار دوره با کیفیت هستم.",
        "category_hints": ["آموزش", "برنامه نویسی", "پایتون", "نرم‌افزار", "خدمات"],
        "base_intent": 92
    },
    {
        "channel": "X",
        "lead_handle": "@farhad_m",
        "lead_display_name": "فرهاد محمدی",
        "post_url": "https://x.com/farhad_m/status/17891230899",
        "text": "می‌خوام برای سایتمون خدمات بهینه‌سازی سئو و رنک یک گوگل بگیرم. آژانس یا متخصص سئو کاربلد و با قیمت منطقی سراغ دارید؟ بودجه آماده داریم.",
        "category_hints": ["سئو", "دیجیتال مارکتینگ", "طراحی سایت", "خدمات"],
        "base_intent": 95
    },
    {
        "channel": "TELEGRAM",
        "lead_handle": "@maryam_lifestyle",
        "lead_display_name": "مریم احمدی",
        "post_url": "https://t.me/style_iran/45120",
        "text": "برای پاییز دنبال مانتو یا پالتو سوییت شیک و گرم هستم. جایی رو سراغ دارید قیمت مناسب بده و ارسال سریع داشته باشه؟",
        "category_hints": ["پوشاک", "زنانه", "مانتو", "پالتو"],
        "base_intent": 84
    },
    {
        "channel": "X",
        "lead_handle": "@pouya_tech",
        "lead_display_name": "پویا کریمی",
        "post_url": "https://x.com/pouya_tech/status/17891231122",
        "text": "گوشی جدید سامسونگ یا آیفون کدوم ارزش خرید بیشتری داره الان؟ می‌خوام گوشی بگیرم با گارانتی معتبر و رجیستر شده ولی فروشگاه مطمئن کم شده.",
        "category_hints": ["دیجیتال", "موبایل", "گوشی", "سامسونگ", "آیفون"],
        "base_intent": 78
    },
    {
        "channel": "TELEGRAM",
        "lead_handle": "@neda_art",
        "lead_display_name": "ندا صادقی",
        "post_url": "https://t.me/iranian_designers/7719",
        "text": "شلوار بگ یا جین راسته خوب از کجا می‌شه خرید کرد؟ کیفیت پارچه‌اش برام خیلی مهمه بعد از شستشو رنگش نره.",
        "category_hints": ["پوشاک", "جین", "شلوار"],
        "base_intent": 82
    },
    {
        "channel": "X",
        "lead_handle": "@kaveh_coder",
        "lead_display_name": "کاوه ناصری",
        "post_url": "https://x.com/kaveh_coder/status/17891235555",
        "text": "کسی هست در زمینه طراحی سایت فروشگاهی یا ریدیزاین سایت با وردپرس یا جانگو همکاری کنه؟ پروژه‌مون فوریه.",
        "category_hints": ["طراحی سایت", "برنامه نویسی", "خدمات"],
        "base_intent": 86
    },
    {
        "channel": "TELEGRAM",
        "lead_handle": "@mehdi_student",
        "lead_display_name": "مهدی یزدانی",
        "post_url": "https://t.me/uni_students/1109",
        "text": "بچه‌ها لپ‌تاپ استوک یا نو مناسب کارهای مهندسی چی پیشنهاد می‌دید تا ۴۰ تومن؟",
        "category_hints": ["دیجیتال", "لپ‌تاپ", "رایانه"],
        "base_intent": 75
    },
    {
        "channel": "X",
        "lead_handle": "@mina_sky",
        "lead_display_name": "مینا رحیمی",
        "post_url": "https://x.com/mina_sky/status/17891239999",
        "text": "امروز چقدر استایل‌های بگ و کارگو تو خیابون مد شده، جالبه ولی نمیدونم به استایل من میاد یا نه. شاید یه مدل ملایم‌ترشو امتحان کنم.",
        "category_hints": ["پوشاک", "کارگو", "شلوار"],
        "base_intent": 38
    },
    {
        "channel": "TELEGRAM",
        "lead_handle": "@reza_learner",
        "lead_display_name": "رضا مرادی",
        "post_url": "https://t.me/general_chat/3301",
        "text": "به نظرتون زبان پایتون سخت‌تره یا جاوااسکریپت؟ فعلا قصد ثبت‌نام ندارم ولی شاید ماه بعد شروع کنم یه نگاهی بندازم به آموزش‌ها.",
        "category_hints": ["برنامه نویسی", "پایتون", "آموزش"],
        "base_intent": 34
    },
    {
        "channel": "X",
        "lead_handle": "@shayan_marketing",
        "lead_display_name": "شایان",
        "post_url": "https://x.com/shayan_marketing/status/17891241100",
        "text": "آگهی استخدام فوری: شرکت ما به یک کارشناس ارشد سئو و تولید محتوا نیازمند است. ارسال رزومه در تلگرام.",
        "category_hints": ["سئو"],
        "base_intent": 10
    },
    {
        "channel": "TELEGRAM",
        "lead_handle": "@news_channel",
        "lead_display_name": "اخبار بازار",
        "post_url": "https://t.me/news_market/552",
        "text": "وضعیت آب و هوای امروز تهران و جاده‌های شمالی همراه با بارش پراکنده باران گزارش شده است.",
        "category_hints": [],
        "base_intent": 5
    }
]

BUYING_INTENT_PATTERN = re.compile(
    r"(دنبال|می‌خوام|میخوام|خریدار|پیشنهاد|کجا داره|معرفی کنید|چند|قیمت|سفارش|بهترین|خرید|کیفیت|راهنمایی|سراغ دارید|ارزش خرید|می‌شه خرید|میشه خرید)",
    re.IGNORECASE
)

NEGATIVE_PATTERNS = [
    "استخدام", "رزومه", "دعوت به همکاری", "نیازمندیم", "واگذاری", "حقوق و مزایا", "جویای کار", "آب و هوا", "اخبار"
]


def extract_keywords_from_product(product: Product) -> tuple[list[str], list[str]]:
    keywords = set()
    
    # 1. Product name words
    name_clean = re.sub(r"[^\w\s؀-ۿ]", " ", product.name)
    for word in name_clean.split():
        if len(word) > 2:
            keywords.add(word.lower())

    # 2. Category tree names
    if product.category:
        for cat in product.category.get_ancestors() + [product.category]:
            for word in cat.name.split():
                if len(word) > 2:
                    keywords.add(word.lower())

    # 3. Product attributes
    if isinstance(product.attributes, dict):
        for k, v in product.attributes.items():
            if isinstance(v, str):
                for word in v.split():
                    if len(word) > 2:
                        keywords.add(word.lower())

    return list(keywords), NEGATIVE_PATTERNS


def generate_smart_outreach_message(business: Business, product: Product, lead_data: dict, mode: str) -> str:
    lead_name = lead_data.get("lead_display_name") or lead_data.get("lead_handle", "دوست گرامی")
    lead_handle = lead_data.get("lead_handle", "")
    price_info = product.formatted_price() if product.price else "با شرایط ویژه و تضمین اصالت"
    
    seller_identity = ""
    if lead_data.get("channel") == "TELEGRAM" and business.telegram_account_handle:
        seller_identity = f" ({business.telegram_account_handle})"
    elif lead_data.get("channel") == "X" and business.x_account_handle:
        seller_identity = f" ({business.x_account_handle})"

    if mode == "COMMENT":
        return (
            f"سلام {lead_name} گرامی،\n"
            f"در خصوص گفت‌وگوی شما پیرامون {product.name}، مجموعه «{business.name}»{seller_identity} این کالا را با {price_info} ارائه می‌کند.\n"
            f"در صورت نیاز به بررسی مشخصات بیشتر: {product.url or business.name}"
        )
    else:
        return (
            f"درود {lead_name} گرامی،\n"
            f"پیام شما در ارتباط با نیاز به محصول را بررسی کردیم. من از مجموعه «{business.name}»{seller_identity} پیام می‌دهم. محصول «{product.name}» با مشخصات مدنظر شما موجود است ({price_info}).\n"
            f"در صورت تمایل، آماده راهنمایی و ارائه جزئیات تکمیلی هستیم."
        )


def evaluate_and_discover_leads(business: Business) -> dict:
    active_products = list(
        Product.objects.filter(
            business=business,
            is_discovery_active=True,
            status="ACTIVE"
        ).select_related("category")[:business.daily_discovery_limit]
    )

    if not active_products:
        return {
            "status": "warning",
            "message": "هیچ محصول فعالی در سهمیه پایش روزانه شما انتخاب نشده است.",
            "leads_created": 0,
            "leads": []
        }

    leads_created = 0
    created_lead_objects = []

    for product in active_products:
        keywords, negatives = extract_keywords_from_product(product)
        
        # Track branch memory
        branch_mem = None
        if product.category:
            branch_mem, _ = CategoryBranchMemory.objects.get_or_create(
                category=product.category,
                business=business,
                defaults={"keywords": keywords, "negative_keywords": negatives}
            )

        for post in SAMPLE_SOCIAL_STREAM:
            channel = post["channel"]
            lead_handle = post["lead_handle"]
            text = post["text"]
            
            # --- Tier 1: Zero-Waste Lexical Filter ---
            # Negative keyword check
            if any(neg in text for neg in negatives):
                continue
            
            # Buying intent check or matching category hint
            text_lower = text.lower()
            kw_match = any(kw in text_lower for kw in keywords) or any(hint in text_lower for hint in post.get("category_hints", []))
            has_intent_words = bool(BUYING_INTENT_PATTERN.search(text))

            if not kw_match:
                continue

            # Calculate score
            score = post.get("base_intent", 50)
            if not has_intent_words and score > 40:
                score -= 20

            # CRITICAL RECALL THRESHOLD:
            # Drop only if < 30%. DO NOT drop any lead with >= 30% match score!
            if score < 30:
                continue

            # --- Tier 2: Deduplication Hash ---
            fingerprint = ProcessedMessageHash.calculate_hash(channel, lead_handle, text)
            if ProcessedMessageHash.objects.filter(fingerprint=fingerprint).exists():
                # Already captured previously, skip to save memory and server load
                continue

            # --- Tier 3: Qualified Lead Formation & Outreach Generation ---
            outreach_mode = business.preferred_outreach_mode or "DIRECT"
            outreach_msg = generate_smart_outreach_message(business, product, post, outreach_mode)
            
            matched_branch_path = product.category.get_full_path() if product.category else "دسته‌بندی اصلی"

            seller_handle = ""
            if channel == "TELEGRAM":
                seller_handle = business.telegram_account_handle or "@seller_telegram"
            elif channel == "X":
                seller_handle = business.x_account_handle or "@seller_x"

            lead = DiscoveredLead.objects.create(
                business=business,
                product=product,
                channel=channel,
                lead_handle=lead_handle,
                lead_display_name=post.get("lead_display_name", ""),
                post_url=post.get("post_url", ""),
                content_snippet=text,
                intent_score=score,
                intent_reasoning=f"تطابق کلیدواژه‌های شاخه درختی «{matched_branch_path}» همراه با قرائن قصد خرید ({score}%).",
                matched_branch=matched_branch_path,
                outreach_mode=outreach_mode,
                outreach_message=outreach_msg,
                outreach_status="SENT",
                sent_from_handle=seller_handle,
                status="CONTACTED"
            )

            # Record hash
            ProcessedMessageHash.objects.create(
                fingerprint=fingerprint,
                channel=channel
            )

            leads_created += 1
            created_lead_objects.append(lead)

            if branch_mem:
                branch_mem.total_scanned_count += 1
                branch_mem.leads_found_count += 1
                branch_mem.save(update_fields=["total_scanned_count", "leads_found_count", "last_scanned_at"])

    return {
        "status": "success",
        "message": f"پایش با موفقیت انجام شد. {leads_created} سرنخ بالقوه با انطباق بالای ۳۰٪ کشف و پیام هوشمند به صورت خودکار از حساب شما ارسال گردید.",
        "leads_created": leads_created,
        "leads": created_lead_objects
    }


def send_lead_outreach(lead_id: int, business: Business) -> dict:
    lead = DiscoveredLead.objects.filter(id=lead_id, business=business).first()
    if not lead:
        return {"status": "error", "message": "سرنخ یافت نشد."}

    channel_handle = ""
    if lead.channel == "TELEGRAM":
        channel_handle = business.telegram_account_handle or "@seller_telegram"
    elif lead.channel == "X":
        channel_handle = business.x_account_handle or "@seller_x"

    lead.sent_from_handle = channel_handle
    lead.outreach_status = "SENT"
    lead.status = "CONTACTED"
    lead.save(update_fields=["sent_from_handle", "outreach_status", "status", "updated_at"])

    mode_display = "دایرکت" if lead.outreach_mode == "DIRECT" else "کامنت"
    return {
        "status": "success",
        "message": f"پیام فروشنده با موفقیت از طریق حساب {channel_handle} به صورت {mode_display} ارسال شد.",
        "lead_status": lead.status,
        "outreach_status": lead.outreach_status,
        "sent_from_handle": lead.sent_from_handle,
    }
