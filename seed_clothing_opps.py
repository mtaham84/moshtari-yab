import os
import django
from datetime import timedelta

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
django.setup()

from django.utils import timezone
from apps.businesses.models import Business
from apps.products.models import Product, Category
from apps.discovery.models import Customer, Opportunity, OpportunityProductMatch, AIAnalysis

def seed_clothing():
    biz = Business.objects.filter(id=6).first()
    if not biz:
        print("Business 6 not found!")
        return

    # Delete any existing test opportunities for business 6 to ensure clean idempotency
    Opportunity.objects.filter(business=biz).delete()

    p49 = Product.objects.filter(id=49, business=biz).first()
    p50 = Product.objects.filter(id=50, business=biz).first()
    p51 = Product.objects.filter(id=51, business=biz).first()
    p52 = Product.objects.filter(id=52, business=biz).first()
    p53 = Product.objects.filter(id=53, business=biz).first()

    now = timezone.now()

    leads_data = [
        {
            "product": p49,
            "platform": "telegram",
            "msg_id": "tg_msg_9811204_1",
            "customer_name": "علی رضایی",
            "username": "alireza_rz88",
            "ext_id": "tg_9811204",
            "profile_url": "https://t.me/alireza_rz88",
            "text": "سلام وقت بخیر، پیراهن مردانه نخی آستین بلند ساده برای محیط کار چی موجود دارید؟ ترجیحا رنگ سرمه‌ای یا سفید که زیاد چروک نشه و خنک باشه. سایز L یا XL دارید؟",
            "intent_type": "buy_signal",
            "need": "خرید پیراهن مردانه نخی خنک و باکیفیت مناسب محیط کار و روزمره در رنگ‌های اداری (سرمه‌ای/سفید)",
            "intent_score": 0.95,
            "fit_score": 0.94,
            "suggested": "سلام و احترام! پیراهن نخی کلاسیک مردانه با الیاف ۱۰۰٪ پنبه طبیعی و دوخت تمیز صنعتی دقیقا مناسب محیط اداری و استفاده روزمره موجوده. پارچه‌اش تنفس‌پذیره و اتوپذیری عالی داره. رنگ‌های سرمه‌ای و سفید در تمامی سایزهای L و XL آماده ارسال فوری هستن. مایلید جدول سایزبندی دقیق براتون ارسال بشه؟",
            "hours_ago": 2,
        },
        {
            "product": p50,
            "platform": "x",
            "msg_id": "x_tweet_17849102",
            "customer_name": "Farhad Moradi",
            "username": "farhad_mrd",
            "ext_id": "x_17849102",
            "profile_url": "https://x.com/farhad_mrd",
            "text": "دنبال یه پیراهن آکسفورد مردانه ضخیم و تن‌خور اسلیم هستم که یقه دکمه‌دار باشه بشه با شلوار کتان کرم ست کرد. برند ایرانی یا تولیدی تمیز و خوش‌دوخت کسی سراغ داره؟",
            "intent_type": "recommendation_ask",
            "need": "خرید پیراهن آکسفورد مردانه یقه دکمه‌دار با پارچه ضخیم باکیفیت مناسب ست کردن با شلوار کتان",
            "intent_score": 0.92,
            "fit_score": 0.95,
            "suggested": "سلام آقا فرهاد، پیراهن آکسفورد مردانه با بافت باکیفیت پنبه ضخیم و یقه باتن‌دان دکمه‌دار دقیقا برای ست کژوال با شلوارهای کتان طراحی شده و قواره تن‌خورش فوق‌العاده شیکه. اگر مایلید تصویر تن‌پوش و لینک ثبت سفارش رو خدمتتون ارسال کنم.",
            "hours_ago": 5,
        },
        {
            "product": p52,
            "platform": "telegram",
            "msg_id": "tg_msg_6421099_1",
            "customer_name": "مریم تهرانی",
            "username": "maryam_teh_style",
            "ext_id": "tg_6421099",
            "profile_url": "https://t.me/maryam_teh_style",
            "text": "سلام خسته نباشید شومیز مجلسی حریر کرپ برای مهمونی می‌خواستم، ترجیحا آستین مچی پلیسه یا ساده و قد کار بالای ۷۵ باشه. سایز ۴۰ تا ۴۲ دارید؟ ارسال به تهران چقدر زمان می‌بره؟",
            "intent_type": "buy_signal",
            "need": "خرید شومیز زنانه مجلسی از جنس حریر با قد استاندارد و سایز ۴۰-۴۲ با ارسال فوری",
            "intent_score": 0.96,
            "fit_score": 0.96,
            "suggested": "سلام مریم عزیز! شومیز زنانه مجلسی حریر ما با آستین مچی و تن‌خور بسیار شیک مجلسی در سایزهای ۳۸ تا ۴۴ موجوده. قد کار ۷۸ سانته و پارچه‌اش دید نداره. برای تهران امکان ارسال فوری با پیک در همان روز وجود داره. بفرمایید کدام رنگ مد نظرتونه؟",
            "hours_ago": 8,
        },
        {
            "product": p51,
            "platform": "x",
            "msg_id": "x_tweet_28471920",
            "customer_name": "امیرحسین کیانی",
            "username": "amir_kiani_ir",
            "ext_id": "x_28471920",
            "profile_url": "https://x.com/amir_kiani_ir",
            "text": "هوا خنک شده برای پاییز پیراهن چهارخونه پشمی یا فوتر گرم با ترکیب رنگ زرشکی سرمه‌ای یا کرم قهوه‌ای می‌خوام، کجا جنس مرغوب داره؟",
            "intent_type": "recommendation_ask",
            "need": "خرید پیراهن پاییزه مردانه چهارخانه با پارچه گرم و ترکیب رنگ‌های فصلی",
            "intent_score": 0.89,
            "fit_score": 0.93,
            "suggested": "سلام امیرحسین عزیز! پیراهن چهارخانه پاییزه مردانه ما با پارچه ضخیم پاییزه گرم و ترکیب رنگ‌های بسیار جذاب زرشکی-سرمه‌ای و قهوه‌ای آماده تحویله. پارچه‌اش پرز نمیده و گرماژ عالی داره.",
            "hours_ago": 14,
        },
        {
            "product": p53,
            "platform": "telegram",
            "msg_id": "tg_msg_7718290_1",
            "customer_name": "سارا حسینی",
            "username": "sara_hoss_92",
            "ext_id": "tg_7718290",
            "profile_url": "https://t.me/sara_hoss_92",
            "text": "شومیز لینن زنانه نچرال و خنک می‌خوام، فری‌سایز تا ۴۶ جواب میده؟ آب‌رفت پارچه‌اش چطوره؟",
            "intent_type": "buy_signal",
            "need": "خرید شومیز زنانه لینن با قواره آزاد تا سایز ۴۶ بدون آبرفت",
            "intent_score": 0.91,
            "fit_score": 0.94,
            "suggested": "سلام سارا عزیز! شومیز لینن الیاف طبیعی با قواره آزاد مناسب تا سایز ۴۶ موجوده. پارچه پیش از دوخت آنزیم‌شور شده و آب‌رفت نداره و فوق‌العاده سبک و خنکه.",
            "hours_ago": 22,
        },
        {
            "product": p49,
            "platform": "x",
            "msg_id": "x_tweet_49201948",
            "customer_name": "نیما باقری",
            "username": "nima_bagheri",
            "ext_id": "x_49201948",
            "profile_url": "https://x.com/nima_bagheri",
            "text": "بچه‌ها دو تا پیراهن نخی کلاسیک برای اداره لازم دارم که جنسش خوب باشه و بعد دو بار شستن بور نشه. فروشگاه معتبر چی معرفی می‌کنید؟",
            "intent_type": "recommendation_ask",
            "need": "خرید پیراهن نخی کلاسیک بادوام با ثبات رنگ بالا برای استفاده روزانه کاری",
            "intent_score": 0.94,
            "fit_score": 0.95,
            "suggested": "سلام نیما جان! پیراهن مردانه نخی کلاسیک ما با رنگرزی ری‌اکتیو و تضمین ثبات رنگ پس از شست‌وشو تولید شده. دقیقا برای استفاده مداوم اداری طراحی شده. خوشحال میشیم کیفیت کار رو بررسی کنی.",
            "hours_ago": 30,
        },
    ]

    for item in leads_data:
        prod = item["product"]
        if not prod:
            continue
        cust, _ = Customer.objects.get_or_create(
            business=biz,
            source_platform=item["platform"],
            external_user_id=item["ext_id"],
            defaults={
                "name": item["customer_name"],
                "source_username": item["username"],
                "source_profile_url": item["profile_url"],
            }
        )

        posted_at = now - timedelta(hours=item["hours_ago"])
        cat = prod.category

        opp = Opportunity.objects.create(
            business=biz,
            customer=cust,
            category=cat,
            category_name_snapshot=cat.name if cat else "پوشاک",
            category_path_snapshot=cat.get_full_path() if cat else "پوشاک",
            source_platform=item["platform"],
            source_message_id=item["msg_id"],
            source_raw_message=item["text"],
            category_confidence=item["fit_score"],
            source_posted_at=posted_at,
            source_message_timestamp=posted_at,
            intent_type=item["intent_type"],
            status="NEW",
            trace_metadata={
                item["platform"]: {
                    "username": item["username"],
                    "url": item["profile_url"],
                }
            }
        )

        variants = {
            "variants": [
                {"tone": "friendly", "text": item["suggested"]},
                {"tone": "short", "text": f"سلام! کالای {prod.name} با بهترین کیفیت موجوده و آماده ارسال سریعه."},
                {"tone": "consultative", "text": f"سلام وقت بخیر، با توجه به نیاز شما برای {item['need']}، محصول {prod.name} رو با ضمانت کیفیت بهتون پیشنهاد می‌کنم."}
            ]
        }

        OpportunityProductMatch.objects.create(
            opportunity=opp,
            product=prod,
            rank=1,
            match_score=item["fit_score"],
            reply_variants=variants,
            recommendation_reason=f"تطابق نیاز مشتری با ویژگی‌های محصول {prod.name}"
        )

        AIAnalysis.objects.create(
            opportunity=opp,
            need=item["need"],
            intent_score=item["intent_score"],
            product_fit_score=item["fit_score"],
            confidence=0.90,
            cost_toman=150,
            suggested_reply=item["suggested"]
        )

    print(f"Successfully seeded {Opportunity.objects.filter(business=biz).count()} clothing opportunities for Business #6!")

if __name__ == "__main__":
    seed_clothing()
