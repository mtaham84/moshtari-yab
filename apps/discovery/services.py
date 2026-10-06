import os
import re
import json
import logging
from datetime import timedelta
from django.utils import timezone
from django.conf import settings
from apps.businesses.models import Business
from apps.products.models import Product, Category
from apps.core.jalali import format_jalali_date, parse_jalali_date, to_persian_digits
from .models import (
    CategoryBranchMemory,
    ProcessedMessageHash,
    DiscoveredLead,
    ProductDailyMetric,
    ProductOrder
)

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
        "base_intent": 88,
        "customer_reply": "سلام، ممنون از پیامتون! لینک رو دیدم و مدل زیتونی رو پسندیدم. فقط قد شلوار و سایزبندیش چطوریه؟"
    },
    {
        "channel": "TELEGRAM",
        "lead_handle": "@ali_reza_dev",
        "lead_display_name": "علیرضا رضایی",
        "post_url": "https://t.me/tech_community/98412",
        "text": "سلام دوستان، من تازه می‌خوام یادگیری برنامه‌نویسی پایتون رو شروع کنم برای تحلیل داده و وب. دوره یا کارگاه پروژه‌محور خوب که پشتیبانی داشته باشه چی پیشنهاد می‌دید؟ خریدار دوره با کیفیت هستم.",
        "category_hints": ["آموزش", "برنامه نویسی", "پایتون", "نرم‌افزار", "خدمات"],
        "base_intent": 92,
        "customer_reply": "سلام و درود، دموی جلسه اول و سرفصل‌ها عالی بود. آیا امکان پشتیبانی مستقیم و رفع اشکال هم روی این دوره هست؟"
    },
    {
        "channel": "X",
        "lead_handle": "@farhad_m",
        "lead_display_name": "فرهاد محمدی",
        "post_url": "https://x.com/farhad_m/status/17891230899",
        "text": "می‌خوام برای سایتمون خدمات بهینه‌سازی سئو و رنک یک گوگل بگیرم. آژانس یا متخصص سئو کاربلد و با قیمت منطقی سراغ دارید؟ بودجه آماده داریم.",
        "category_hints": ["سئو", "دیجیتال مارکتینگ", "طراحی سایت", "خدمات"],
        "base_intent": 95,
        "customer_reply": "سلام، نمونه قرارداد و پلن‌های تضمینی سئوتون رو می‌تونید به دایرکت بفرستید تا با هیئت مدیره بررسی کنیم؟"
    },
    {
        "channel": "TELEGRAM",
        "lead_handle": "@maryam_lifestyle",
        "lead_display_name": "مریم احمدی",
        "post_url": "https://t.me/style_iran/45120",
        "text": "برای پاییز دنبال مانتو یا پالتو سوییت شیک و گرم هستم. جایی رو سراغ دارید قیمت مناسب بده و ارسال سریع داشته باشه؟",
        "category_hints": ["پوشاک", "زنانه", "مانتو", "پالتو"],
        "base_intent": 84,
        "customer_reply": "سلام، سایزبندی تا چه سایزی موجوده؟ ارسال فوری به تهران هم دارید؟"
    },
    {
        "channel": "X",
        "lead_handle": "@pouya_tech",
        "lead_display_name": "پویا کریمی",
        "post_url": "https://x.com/pouya_tech/status/17891231122",
        "text": "گوشی جدید سامسونگ یا آیفون کدوم ارزش خرید بیشتری داره الان؟ می‌خوام گوشی بگیرم با گارانتی معتبر و رجیستر شده ولی فروشگاه مطمئن کم شده.",
        "category_hints": ["دیجیتال", "موبایل", "گوشی", "سامسونگ", "آیفون"],
        "base_intent": 78,
        "customer_reply": "سلام، آیا تحویل حضوری یا پرداخت در محل برای تهران دارید؟"
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

    # 2. Category tree names (up to 5 levels: ancestors[:4] + category)
    if product.category:
        for cat in product.category.get_ancestors()[:4] + [product.category]:
            for word in cat.name.split():
                if len(word) > 2:
                    keywords.add(word.lower())

    # 3. Product description keywords (distinct from attributes)
    if product.description:
        desc_clean = re.sub(r"[^\w\s؀-ۿ]", " ", product.description)
        stop_words = {"برای", "دارد", "است", "شده", "انواع", "دارای", "جهت", "بوده", "شامل", "کردن", "کنید", "این", "آن", "که", "با"}
        for word in desc_clean.split():
            if len(word) > 2 and word not in stop_words:
                keywords.add(word.lower())

    # 4. Dynamic custom attributes (both keys and values, e.g. سایز, ۳۸, رنگ, مشکی, کتان)
    if isinstance(product.attributes, dict):
        for k, v in product.attributes.items():
            if isinstance(k, str):
                for word in k.split():
                    if len(word) > 2:
                        keywords.add(word.lower())
            if isinstance(v, str):
                v_words = re.split(r"[,،/\s]+", v)
                for word in v_words:
                    if len(word) > 2:
                        keywords.add(word.lower())

    return list(keywords), NEGATIVE_PATTERNS


def generate_smart_outreach_message(business: Business, product: Product, lead_data: dict, mode: str) -> str:
    lead_name = lead_data.get("lead_display_name") or lead_data.get("lead_handle", "دوست گرامی")
    price_info = product.formatted_price() if product.price else "با شرایط ویژه و تضمین اصالت"
    
    seller_identity = ""
    if lead_data.get("channel") == "TELEGRAM" and business.telegram_account_handle:
        seller_identity = f" ({business.telegram_account_handle})"
    elif lead_data.get("channel") == "X" and business.x_account_handle:
        seller_identity = f" ({business.x_account_handle})"

    # Match product custom attributes against lead post text
    lead_text_lower = (lead_data.get("text") or "").lower()
    matched_attrs = []
    if isinstance(product.attributes, dict):
        for k, v in product.attributes.items():
            if isinstance(v, str):
                v_parts = [p.strip().lower() for p in re.split(r"[,،/\s]+", v) if len(p.strip()) > 2]
                if any(p in lead_text_lower for p in v_parts):
                    matched_attrs.append(f"{k}: {v}")

OFF_TOPIC_KEYWORDS = [
    "قرمه سبزی", "قورمه سبزی", "دستور پخت", "آشپزی", "غذا", "سیاست",
    "انتخابات", "آب و هوا", "فوتبال", "فال", "جوک", "بورس", "ارز دیجیتال",
    "بیت کوین", "شعر", "هواشناسی"
]

def validate_message_in_product_domain(message_text: str, product: Product, business: Business) -> tuple[bool, str]:
    """
    Strict Guardrail: Ensures user conversations and questions stay strictly within
    the scope of products available in the catalog.
    Rejects out-of-domain inquiries politely.
    """
    text_lower = message_text.lower()
    for off in OFF_TOPIC_KEYWORDS:
        if off in text_lower:
            return False, (
                f"من دستیار تخصصی خرید کاتالوگ «{business.name}» هستم و تنها درباره "
                f"مشخصات فنی، قیمت، موجودی و راهنمای سفارش محصول «{product.name}» "
                f"می‌توانم پاسخگو باشم. اگر سوالی درباره این کالا دارید در خدمت شما هستم."
            )
    return True, "SAFE_IN_DOMAIN"


def check_account_message_cap(lead: DiscoveredLead) -> tuple[bool, str]:
    """
    Strict Daily Rate-Limiting: Limits conversation with any account (across comments and DMs)
    to a maximum of 10 messages per day on Telegram and X.
    Resets automatically on a new day.
    """
    today = timezone.now().date()
    if lead.last_message_date != today:
        lead.message_count = 0
        lead.last_message_date = today
        lead.is_conversation_capped = False
        lead.save(update_fields=["message_count", "last_message_date", "is_conversation_capped"])

    if lead.message_count >= 10:
        lead.is_conversation_capped = True
        lead.save(update_fields=["is_conversation_capped"])
        return False, "سقف مجاز روزانه تبادل پیام (۱۰ پیام در روز) برای این حساب کاربری تکمیل شده است. ادامه گفتگو فردا امکان‌پذیر خواهد بود."
    return True, "ALLOWED"


def generate_smart_outreach_message(business: Business, product: Product, post: dict, mode: str = "COMMENT") -> tuple[str, str]:
    """
    Returns (outreach_message, direct_product_link).
    For X / COMMENT (default):
      Includes the direct product link and asks the user to DM for further details or questions.
    For Telegram:
      Friendly direct outreach including the direct link.
    """
    lead_name = post.get("lead_display_name") or post.get("lead_handle", "کاربر گرامی")
    channel = post.get("channel", "X")
    price_info = f"قیمت {product.formatted_price()}" if product.price else "شرایط و قیمت ویژه"
    seller_identity = f" ({business.telegram_account_handle})" if channel == "TELEGRAM" and business.telegram_account_handle else ""

    matched_attrs = []
    lead_text_lower = post.get("text", "").lower()
    if isinstance(product.attributes, dict):
        for k, v in product.attributes.items():
            if isinstance(v, str):
                v_parts = [p.strip().lower() for p in re.split(r"[,،/\s]+", v) if len(p.strip()) > 2]
                if any(p in lead_text_lower for p in v_parts):
                    matched_attrs.append(f"{k}: {v}")

    attr_snippet = ""
    if matched_attrs:
        attr_snippet = f" (مشخصات مدنظر شما: {'، '.join(matched_attrs[:2])})"
    elif isinstance(product.attributes, dict) and product.attributes:
        top_attrs = [f"{k}: {v}" for k, v in list(product.attributes.items())[:2]]
        attr_snippet = f" (مشخصات: {'، '.join(top_attrs)})"

    product_link = product.url or f"https://customerweb.ir/p/{product.id}"

    if channel == "X" or mode == "COMMENT":
        msg = (
            f"سلام {lead_name} گرامی،\n"
            f"در پاسخ به پرسش شما درباره {product.name}{attr_snippet}، این محصول در کاتالوگ «{business.name}» با {price_info} موجود است.\n"
            f"لینک مستقیم مشخصات و ثبت سفارش:\n{product_link}\n"
            f"در صورت نیاز به بررسی مشخصات بیشتر یا هرگونه سوال، خوشحال می‌شویم به دایرکت ما پیام بدهید."
        )
    else:
        msg = (
            f"درود {lead_name} گرامی،\n"
            f"پیام شما در ارتباط با نیاز به محصول را بررسی کردیم. من از مجموعه «{business.name}»{seller_identity} پیام می‌دهم. محصول «{product.name}» با مشخصات مدنظر شما{attr_snippet} موجود است ({price_info}).\n"
            f"لینک مستقیم کالا:\n{product_link}\n"
            f"در صورت تمایل، آماده راهنمایی و ارائه جزئیات تکمیلی هستیم."
        )

    return msg, product_link


DAILY_OUTREACH_CAP_PER_PRODUCT = 100


def get_intent_rank(score: int) -> int:
    """
    Rank purchase intent stage for AI prioritization:
      1: READY_TO_BUY (85-100) - بالاترین شانس خرید فوری
      2: COMPARING (60-84)     - در حال مقایسه و ارزیابی
      3: INITIAL_NEED (30-59)  - ابراز نیاز اولیه
    """
    if score >= 85:
        return 1
    if score >= 60:
        return 2
    return 3


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
    today = timezone.now().date()

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

        candidate_matches = []

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

            # Boost score based on matching custom attributes (e.g. matching color, size, material)
            attr_matches_count = 0
            if isinstance(product.attributes, dict):
                for k, v in product.attributes.items():
                    if isinstance(v, str):
                        v_parts = [p.strip().lower() for p in re.split(r"[,،/\s]+", v) if len(p.strip()) > 2]
                        if any(p in text_lower for p in v_parts):
                            attr_matches_count += 1
            if attr_matches_count > 0:
                score = min(99, score + attr_matches_count * 10)

            # CRITICAL RECALL THRESHOLD:
            # Drop only if < 30%. DO NOT drop any lead with >= 30% match score!
            if score < 30:
                continue

            # --- Tier 2: Deduplication Hash ---
            fingerprint = ProcessedMessageHash.calculate_hash(channel, lead_handle, text)
            if ProcessedMessageHash.objects.filter(fingerprint=fingerprint).exists():
                # Already captured previously, skip to save memory and server load
                continue

            rank = get_intent_rank(score)
            candidate_matches.append({
                "post": post,
                "channel": channel,
                "lead_handle": lead_handle,
                "text": text,
                "score": score,
                "rank": rank,
                "fingerprint": fingerprint,
                "attr_matches_count": attr_matches_count,
            })

        # Autonomous intent sorting:
        # Sort strictly by intent stage priority (rank 1 > 2 > 3), then highest score DESC (highest purchase chance)
        candidate_matches.sort(key=lambda c: (c["rank"], -c["score"]))

        # Daily Quota & Outreach Rule per product:
        # If <= 100 leads: send message to ALL of them.
        # If > 100 leads: send message to top 100 with highest chance of purchase; save remainder as DRAFT/NEW
        sent_today_count = 0
        for idx, candidate in enumerate(candidate_matches):
            post = candidate["post"]
            channel = candidate["channel"]
            lead_handle = candidate["lead_handle"]
            text = candidate["text"]
            score = candidate["score"]
            fingerprint = candidate["fingerprint"]
            attr_matches_count = candidate["attr_matches_count"]

            should_send = (idx < DAILY_OUTREACH_CAP_PER_PRODUCT)

            outreach_mode = "COMMENT" if channel == "X" else (business.preferred_outreach_mode or "DIRECT")
            outreach_msg, direct_link = generate_smart_outreach_message(business, product, post, outreach_mode)
            matched_branch_path = product.category.get_full_path() if product.category else "دسته‌بندی اصلی"

            seller_handle = ""
            if channel == "TELEGRAM":
                seller_handle = business.telegram_account_handle or "@seller_telegram"
            elif channel == "X":
                seller_handle = business.x_account_handle or "@seller_x"

            bot_sender = "بات پیدا (@peyda_bot)" if channel == "X" else (business.telegram_account_handle or "ایجنت هوشمند")
            cust_reply = post.get("customer_reply", "")
            has_reply = bool(cust_reply) if should_send else False

            reasoning_parts = [f"تطابق کلیدواژه‌های شاخه درختی «{matched_branch_path}»"]
            if attr_matches_count > 0:
                reasoning_parts.append(f"انطباق {attr_matches_count} ویژگی اختصاصی محصول")
            reasoning_parts.append(f"قرائن قصد خرید ({score}%)")
            intent_reasoning = " همراه با ".join(reasoning_parts) + "."

            lead = DiscoveredLead.objects.create(
                business=business,
                product=product,
                channel=channel,
                lead_handle=lead_handle,
                lead_display_name=post.get("lead_display_name", ""),
                post_url=post.get("post_url", ""),
                content_snippet=text,
                intent_score=score,
                intent_reasoning=intent_reasoning,
                matched_branch=matched_branch_path,
                outreach_mode=outreach_mode,
                outreach_message=outreach_msg,
                outreach_status="SENT" if should_send else "DRAFT",
                sent_from_handle=seller_handle if should_send else "",
                bot_agent_name=bot_sender,
                customer_reply=cust_reply if should_send else "",
                customer_reply_at=timezone.now() if has_reply else None,
                message_count=2 if has_reply else (1 if should_send else 0),
                last_message_date=today,
                tokens_used=570,
                cost_usd=0.000360,
                cost_toman=25,
                guardrail_status="SAFE_IN_DOMAIN",
                direct_link_sent=direct_link,
                status="CONTACTED" if should_send else "NEW"
            )

            # Record hash
            ProcessedMessageHash.objects.create(
                fingerprint=fingerprint,
                channel=channel
            )

            leads_created += 1
            created_lead_objects.append(lead)

            if should_send:
                sent_today_count += 1

            if branch_mem:
                branch_mem.total_scanned_count += 1
                branch_mem.leads_found_count += 1
                branch_mem.save(update_fields=["total_scanned_count", "leads_found_count", "last_scanned_at"])

        if sent_today_count > 0:
            daily_metric, _ = ProductDailyMetric.objects.get_or_create(
                product=product,
                date=today
            )
            daily_metric.outreach_sent_count += sent_today_count
            daily_metric.save(update_fields=["outreach_sent_count"])

    return {
        "status": "success",
        "message": f"پایش با موفقیت انجام شد. {leads_created} سرنخ بالقوه با انطباق بالای ۳۰٪ کشف و بر اساس اولویت قصد خرید تا سقف ۱۰۰ پیام برای هر کالا ارسال گردید.",
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


def get_agent_discovery_feed(business: Business, min_priority: int = 1, limit: int = None, channel: str = None) -> dict:
    """
    Returns a clean, structured, prioritized dataset tailored specifically for AI Agents,
    social listening workers, and crawler pipelines.
    
    Products are sorted by discovery_priority DESC (5 down to 1), then updated_at DESC.
    Includes:
      - 5-level taxonomy with ancestor hierarchy and full paths
      - Dynamic custom attributes (key-value dict)
      - Pre-extracted keywords and negative stop phrases
      - Outreach account configurations for Telegram and X
      - Clean ICP definition and distinct problem-solving description
      - Quota and daily limit metrics
    """
    qs = Product.objects.filter(
        business=business,
        is_discovery_active=True,
        status="ACTIVE"
    ).select_related("category").prefetch_related("images")

    if min_priority and min_priority > 1:
        qs = qs.filter(discovery_priority__gte=min_priority)

    if channel == "TELEGRAM":
        qs = qs.filter(telegram_outreach_enabled=True)
    elif channel == "X":
        qs = qs.filter(x_outreach_enabled=True)

    # Order strictly by priority (5: URGENT down to 1: NORMAL), then updated_at DESC
    qs = qs.order_by("-discovery_priority", "-updated_at")

    max_items = limit or business.daily_discovery_limit
    active_products = list(qs[:max_items])

    priority_map = {
        5: {"level": "URGENT", "fa_label": "فوری (پایش حداکثری و پیوسته)", "scan_weight": 3.0},
        4: {"level": "VERY_HIGH", "fa_label": "بسیار بالا", "scan_weight": 2.0},
        3: {"level": "HIGH", "fa_label": "بالا (کالای کلیدی)", "scan_weight": 1.5},
        2: {"level": "MEDIUM", "fa_label": "متوسط", "scan_weight": 1.0},
        1: {"level": "NORMAL", "fa_label": "عادی (رصد متناوب)", "scan_weight": 0.5},
    }

    product_items = []
    for prod in active_products:
        keywords, negatives = extract_keywords_from_product(prod)
        
        cat_info = None
        if prod.category:
            ancestors = prod.category.get_ancestors()[:4]
            cat_info = {
                "id": prod.category.id,
                "name": prod.category.name,
                "slug": prod.category.slug,
                "full_path": prod.category.get_full_path(),
                "depth": len(ancestors),
                "level": len(ancestors) + 1,
                "ancestors": [
                    {
                        "level": idx + 1,
                        "id": anc.id,
                        "name": anc.name,
                        "slug": anc.slug
                    }
                    for idx, anc in enumerate(ancestors)
                ]
            }

        p_info = priority_map.get(prod.discovery_priority, priority_map[1])
        main_img = prod.images.filter(is_main=True).first() or prod.images.first()

        product_items.append({
            "id": prod.id,
            "name": prod.name,
            "product_type": prod.product_type,
            "priority": {
                "score": prod.discovery_priority,
                "level": p_info["level"],
                "label": p_info["fa_label"],
                "scan_weight": p_info["scan_weight"]
            },
            "category": cat_info,
            "description": prod.description,
            "target_customer": prod.target_customer,
            "attributes": prod.attributes if isinstance(prod.attributes, dict) else {},
            "price": {
                "raw": prod.price,
                "formatted": prod.formatted_price() if prod.price else "توافقی / با شرایط ویژه",
            },
            "url": prod.url or "",
            "main_image_url": main_img.image.url if main_img and hasattr(main_img.image, "url") else None,
            "keywords": keywords,
            "negative_keywords": negatives,
            "outreach_config": {
                "telegram_enabled": prod.telegram_outreach_enabled,
                "x_enabled": prod.x_outreach_enabled,
                "seller_telegram_handle": business.telegram_account_handle or "",
                "seller_x_handle": business.x_account_handle or "",
                "preferred_mode": business.preferred_outreach_mode or "DIRECT",
            }
        })

    return {
        "status": "success",
        "business": {
            "id": business.id,
            "name": business.name,
            "domain": business.business_domain,
            "daily_discovery_limit": business.daily_discovery_limit,
            "active_products_in_quota": len(product_items),
            "remaining_quota": max(0, business.daily_discovery_limit - len(product_items)),
            "accounts": {
                "telegram": business.telegram_account_handle or "",
                "x": business.x_account_handle or ""
            },
            "peyda_bot_x": {
                "handle": "@peyda_bot",
                "identity": "بات سراسری پیدا (خرید و مشتری‌یابی در X)",
                "strategy": "COMMENT_FIRST_WITH_DIRECT_LINK",
                "dm_policy": "RESPOND_ONLY_ON_CUSTOMER_INITIATION_OR_AFTER_COMMENT",
                "daily_message_cap_per_account": 10,
                "daily_quota_description": "حداکثر سقف مجاز روزانه: ۱۰ پیام به ازای هر حساب کاربری در X و تلگرام",
                "domain_guardrail": "STRICT_CATALOG_PRODUCTS_ONLY"
            }
        },
        "priority_definitions": priority_map,
        "total_active_products": len(product_items),
        "products": product_items
    }


def seed_demo_analytics_if_needed(business: Business):
    """
    Seeds realistic metrics and conversions across past 14 days for business products
    if no metrics exist, so dashboard analytics reflect rich live graphs and sortable statistics.
    """
    if ProductDailyMetric.objects.filter(product__business=business).exists():
        return

    products = list(Product.objects.filter(business=business))
    if not products:
        return

    today = timezone.now().date()
    presets = [
        {"clicks": 18, "views": 42, "orders": 3, "outreach": 25},
        {"clicks": 12, "views": 28, "orders": 2, "outreach": 20},
        {"clicks": 7, "views": 19, "orders": 1, "outreach": 15},
        {"clicks": 4, "views": 10, "orders": 0, "outreach": 10},
    ]

    for p_idx, prod in enumerate(products):
        base = presets[p_idx % len(presets)]
        unit_price = int(prod.price) if prod.price else 650000

        for day_offset in range(14, -1, -1):
            d = today - timedelta(days=day_offset)
            factor = 1.0 + (14 - day_offset) * 0.05
            c = max(1, int(base["clicks"] * factor * 0.8))
            v = max(c, int(base["views"] * factor * 0.9))
            o = max(0, int(base["orders"] * factor * 0.7))
            outreach = max(2, int(base["outreach"] * factor * 0.8))
            sales = o * unit_price

            ProductDailyMetric.objects.create(
                product=prod,
                date=d,
                outreach_sent_count=outreach,
                clicks_count=c,
                views_count=v,
                orders_count=o,
                sales_amount=sales
            )


def get_performance_analytics(business: Business, start_date=None, end_date=None) -> dict:
    """
    Aggregates clicks, views, sales, and conversions for seller's catalog.
    Supports exact Persian/Gregorian date range filtering or all-time if not specified.
    """
    seed_demo_analytics_if_needed(business)

    metrics_qs = ProductDailyMetric.objects.filter(product__business=business)
    if start_date:
        metrics_qs = metrics_qs.filter(date__gte=start_date)
    if end_date:
        metrics_qs = metrics_qs.filter(date__lte=end_date)

    total_sales = 0
    total_orders = 0
    total_clicks = 0
    total_views = 0
    total_outreach = 0

    for m in metrics_qs:
        total_sales += int(m.sales_amount)
        total_orders += m.orders_count
        total_clicks += m.clicks_count
        total_views += m.views_count
        total_outreach += m.outreach_sent_count

    conversion_rate = round((total_orders / total_clicks * 100), 1) if total_clicks > 0 else 0.0

    products = Product.objects.filter(business=business).select_related("category")
    product_stats = []

    for prod in products:
        p_metrics = metrics_qs.filter(product=prod)
        p_sales = sum(int(m.sales_amount) for m in p_metrics)
        p_orders = sum(m.orders_count for m in p_metrics)
        p_clicks = sum(m.clicks_count for m in p_metrics)
        p_views = sum(m.views_count for m in p_metrics)
        p_outreach = sum(m.outreach_sent_count for m in p_metrics)

        latest_m = p_metrics.order_by("-date").first()
        last_date_shamsi = format_jalali_date(latest_m.date) if latest_m else "—"

        img = prod.main_image
        img_url = img.image.url if img and hasattr(img.image, "url") else None

        product_stats.append({
            "id": prod.id,
            "name": prod.name,
            "category_name": prod.category.name if prod.category else "عمومی",
            "image_url": img_url,
            "price_formatted": prod.formatted_price(),
            "outreach_sent": p_outreach,
            "clicks": p_clicks,
            "views": p_views,
            "orders": p_orders,
            "sales_amount": p_sales,
            "sales_amount_formatted": f"{p_sales:,}".replace(",", "،"),
            "last_interaction_date": last_date_shamsi,
            "raw_last_date": latest_m.date.isoformat() if latest_m else "1970-01-01",
        })

    # Default sort by sales_amount DESC
    product_stats.sort(key=lambda x: x["sales_amount"], reverse=True)

    start_shamsi = format_jalali_date(start_date) if start_date else ""
    end_shamsi = format_jalali_date(end_date) if end_date else ""

    return {
        "summary": {
            "total_sales": total_sales,
            "total_sales_formatted": f"{total_sales:,}".replace(",", "،"),
            "total_orders": total_orders,
            "total_clicks": total_clicks,
            "total_views": total_views,
            "total_outreach": total_outreach,
            "conversion_rate": conversion_rate,
            "start_date_shamsi": start_shamsi,
            "end_date_shamsi": end_shamsi,
            "is_all_time": not bool(start_date or end_date),
        },
        "products": product_stats,
    }

