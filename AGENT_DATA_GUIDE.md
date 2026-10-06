# راهنمای فنی داده‌های ایجنت کشف مشتری (Agent Data Guide)
> **مستندات دسترسی به پایگاه دانش، سلسله‌مراتب ۵ سطحی، ویژگی‌های دلخواه و سیستم اولویت‌بندی برای مهندسان هوش مصنوعی و توسعه‌دهندگان ایجنت**

این سند نحوه دسترسی تمیز، ساختاریافته و بدون ابهام به داده‌های محصولات، دسته‌بندی‌ها، ویژگی‌های فنی و تنظیمات اکانت‌های فروشنده را برای پیاده‌سازی پایپ‌لاین‌های هوش مصنوعی (AI Agents, Social Listeners, NLP Classifiers) تشریح می‌کند.

---

## ۱. روش‌های دسترسی به داده‌ها (Data Access Methods)

توسعه‌دهندگان ایجنت می‌توانند به دو شیوه کاملاً تمیز به این داده‌ها دسترسی پیدا کنند:

### الف) دسترسی مستقیم از طریق سرویس پایتون (Python Service - In-Process)
مناسب برای زمانی که کد ایجنت، ورکر Celery، یا اسکریپت پایتونی درون همان محیط یا با ایمپورت مدل‌های جنگو اجرا می‌شود:

```python
from apps.businesses.models import Business
from apps.discovery.services import get_agent_discovery_feed

business = Business.objects.get(id=1)

# دریافت تمام محصولات فعال پایش‌شده با اولویت‌بندی نزولی
feed = get_agent_discovery_feed(
    business=business,
    min_priority=1,      # حداقل اولویت (۱ تا ۵)
    limit=50,            # سقف تعداد کالاها در سهمیه روزانه
    channel="TELEGRAM"   # فیلتر بر اساس کانال: 'TELEGRAM' یا 'X' یا None
)

print(f"تعداد کالاهای فعال: {feed['total_active_products']}")
for item in feed['products']:
    print(f"[{item['priority']['level']}] {item['name']} - اولویت: {item['priority']['score']}")
```

---

### ب) دسترسی از طریق REST API (HTTP JSON Endpoint)
مناسب برای میکروسرویس‌های مستقل، کدهای خارج از پروژه (مانند ورکرهای Node.js، سرورهای FastAPI یا ایجنت‌های مستقر روی سرور مجزا):

- **مسیر (Endpoint):** `GET /discovery/api/agent/feed/`
- **احراز هویت:** Session Cookie (یا هدرهای احراز هویت در محیط پروداکشن)
- **پارامترهای پرکاربرد Query:**
  - `min_priority`: حداقل اولویت (مثلاً `?min_priority=3` برای دریافت کالاهای اولویت بالا)
  - `limit`: محدودسازی تعداد رکوردها (پیش‌فرض: سقف سهمیه روزانه فروشگاه)
  - `channel`: فیلتر کانال (`TELEGRAM` یا `X`)

**نمونه فراخوانی با cURL یا Python Requests:**
```bash
curl -X GET "http://127.0.0.1:8000/discovery/api/agent/feed/?min_priority=2&channel=TELEGRAM" \
     -H "Cookie: sessionid=YOUR_SESSION_ID"
```

---

## ۲. ساختار خروجی داده‌ها (JSON Contract & Schema)

خروجی داده‌ها به شکل استاندارد، منظم و بدون فیلدهای اضافه تحویل داده می‌شود:

```json
{
  "status": "success",
  "business": {
    "id": 1,
    "name": "بوتیک شیک‌پوش",
    "domain": "پوشاک و مد",
    "daily_discovery_limit": 50,
    "active_products_in_quota": 12,
    "remaining_quota": 38,
    "accounts": {
      "telegram": "@shikpoosh_bot",
      "x": "@shikpoosh_x"
    }
  },
  "priority_definitions": {
    "5": {"level": "URGENT", "fa_label": "فوری (پایش حداکثری و پیوسته)", "scan_weight": 3.0},
    "4": {"level": "VERY_HIGH", "fa_label": "بسیار بالا", "scan_weight": 2.0},
    "3": {"level": "HIGH", "fa_label": "بالا (کالای کلیدی)", "scan_weight": 1.5},
    "2": {"level": "MEDIUM", "fa_label": "متوسط", "scan_weight": 1.0},
    "1": {"level": "NORMAL", "fa_label": "عادی (رصد متناوب)", "scan_weight": 0.5}
  },
  "total_active_products": 1,
  "products": [
    {
      "id": 42,
      "name": "شلوار کارگو زنانه کتان بگ",
      "product_type": "PHYSICAL",
      "priority": {
        "score": 5,
        "level": "URGENT",
        "label": "فوری (پایش حداکثری و پیوسته)",
        "scan_weight": 3.0
      },
      "category": {
        "id": 18,
        "name": "کتان بگ",
        "slug": "cotton-baggy-3a1b2c",
        "full_path": "پوشاک > زنانه > شلوار > کارگو > کتان بگ",
        "depth": 4,
        "level": 5,
        "ancestors": [
          {"level": 1, "id": 1, "name": "پوشاک", "slug": "clothing"},
          {"level": 2, "id": 4, "name": "زنانه", "slug": "women"},
          {"level": 3, "id": 9, "name": "شلوار", "slug": "pants"},
          {"level": 4, "id": 14, "name": "کارگو", "slug": "cargo"}
        ]
      },
      "description": "شلوار کارگو شش جیب با پارچه کتان اعلا، دوخت صنعتی بدون آبرفت، مناسب استایل کژوال خیابانی.",
      "target_customer": "دختران و جوانان ۱۸ تا ۳۰ سال علاقمند به استایل استریت‌ویر و بگی",
      "attributes": {
        "سایز": "38, 40, 42",
        "رنگ": "مشکی، زیتونی، کرم",
        "جنس": "کتان پنبه ۱۰۰٪",
        "مدل": "کارگو ۶ جیب بگ",
        "گارانتی": "ضمانت ثبات رنگ و تعویض ۷ روزه"
      },
      "price": {
        "raw": 890000,
        "formatted": "۸۹۰،۰۰۰ تومان"
      },
      "url": "https://shikpoosh.ir/items/cargo-cotton",
      "main_image_url": "/media/products/cargo_front.jpg",
      "keywords": ["کارگو", "شلوار", "کتان", "بگ", "مشکی", "زیتونی", "پوشاک", "زنانه"],
      "negative_keywords": ["استخدام", "رزومه", "دعوت به همکاری", "نیازمندیم", "واگذاری", "آب و هوا"],
      "outreach_config": {
        "telegram_enabled": true,
        "x_enabled": true,
        "seller_telegram_handle": "@shikpoosh_bot",
        "seller_x_handle": "@shikpoosh_x",
        "preferred_mode": "DIRECT"
      }
    }
  ]
}
```

---

## ۳. نحوه زمان‌بندی و اعمال اولویت‌ها (Priority Scheduling Rules)

فیلد `priority.score` یک عدد صحیح بین ۱ تا ۵ است. سیستم بر اساس این امتیاز، به مهندسان ایجنت توصیه می‌کند که منابع اسکن و توکن را به شکل زیر مدیریت کنند:

| امتیاز (`score`) | سطح (`level`) | ضریب اسکن (`scan_weight`) | رفتار پیشنهادی ایجنت |
|:---:|:---:|:---:|:---|
| **۵** | **URGENT** | `3.0x` | **پایش بلادرنگ و دائمی**: اسکن در هر چرخه استریم پیام‌ها، کمترین تأخیر در رصد مکالمات، آماده‌باش کامل. |
| **۴** | **VERY_HIGH** | `2.0x` | **پایش متناوب پربسامد**: بررسی حداقل هر ۲ تا ۵ دقیقه یک‌بار در گروه‌ها و کانال‌های داغ. |
| **۳** | **HIGH** | `1.5x` | **کالای اصلی کاتالوگ**: رصد در فواصل زمانی ۱۰ دقیقه‌ای با اولویت استاندارد. |
| **۲** | **MEDIUM** | `1.0x` | **کالای فرعی**: رصد در ساعات پیک فعالیت جوامع آنلاین. |
| **۱** | **NORMAL** | `0.5x` | **رصد متناوب اقتصادی**: اسکن در ساعات کم‌ترافیک یا هنگامی که ظرفیت توکن آزاد است. |

**نحوه مرتب‌سازی:**
خروجی فید به صورت پیش‌فرض با ترتیب `-discovery_priority` و سپس `-updated_at` چیده می‌شود؛ به این معنا که مهم‌ترین و جدیدترین کالاهای فروشنده همواره در ابتدای آرایه `products` قرار دارند.

---

## ۴. سلسله‌مراتب دسته‌بندی تا ۵ سطح (5-Level Taxonomy)

دسته‌بندی‌ها به صورت منعطف از ۱ تا ۵ سطح تعریف می‌شوند:
- `level = 1`: سرشاخه اصلی (مثلاً `پوشاک`)
- `level = 2`: زیرشاخه (مثلاً `زنانه`)
- `level = 3`: رسته (مثلاً `شلوار`)
- `level = 4`: زیررسته تخصصی (مثلاً `کارگو`)
- `level = 5`: سطح نهایی (مثلاً `کتان بگ`)

**کاربرد برای ایجنت:**
- زنجیره `ancestors` برای تفکیک موضوعی در پایگاه برداری (Embedding Namespace) یا سیستم پرامپت مفید است.
- کلمات کلیدی تمام ۵ سطح به صورت خودکار در فیلد `keywords` قرار دارند تا جستجوی لغوی (Lexical Filter) هیچ شاخه‌ای را از دست ندهد.

---

## ۵. ویژگی‌های دلخواه محصول (`attributes` Dictionary)

فیلد `attributes` یک دیکشنری باز از خصوصیات فنی است (سایز، رنگ، جنس، مدل، گارانتی، ...).

**قوانین تطابق در ایجنت:**
1. **تطابق دقیق مقادیر (Value Matching):**
   اگر خریدار در پیام خود بگوید: *«شلوار مشکی کتان بگ می‌خوام»*، کلمات `مشکی` و `کتان` مستقیماً با مقادیر `attributes["رنگ"]` و `attributes["جنس"]` منطبق می‌شوند.
2. **افزایش امتیاز نیت خرید (+10% Boost):**
   به ازای هر ویژگی فنی منطبق، امتیاز اطمینان نیت خرید را ۱۰ درصد افزایش دهید (تا سقف ۹۹٪).
3. **درج در پیام ارتباطی (Outreach Message Synthesis):**
   در پیامی که به خریدار ارسال می‌شود، ویژگی‌های منطبق را ذکر کنید:
   > *«سلام، در خصوص نیاز شما به شلوار با مشخصات جنس: کتان، رنگ: مشکی، این کالا با تضمین کیفیت موجود است.»*

---

## ۶. تفکیک شرح محصول (`description`) از پرسونای خریدار (`target_customer`)

- **`description`**: تشریح مشکل‌گشایی، مزایا و کارایی محصول است. ایجنت باید از این متن برای پاسخ به سوالات مفهومی خریداران (مانند «آیا برای استفاده روزمره راحته؟») استفاده کند.
- **`target_customer`**: بیان پرسونای ایده‌آل (ICP) برای ایجنت است تا تطابق هویتی کاربر در شبکه اجتماعی (دانشجو، ورزشکار، برنامه‌نویس، بانوان و...) را با مشتری هدف بسنجد.

---

## ۷. قانون حیاتی خط قرمز ۳۰ درصد (Critical Recall Threshold)

> [!IMPORTANT]
> **قانون قطعی سیستم:** هر سرنخ خریدی که نمره قصد خرید آن **۳۰ درصد یا بیشتر** باشد ($\ge 30\%$)، تحت هیچ شرایطی نباید دور ریخته شود و باید به عنوان سرنخ معتبر ثبت گردد. پیام هوشمند نیز بلافاصله و بدون معطلی در وضعیت پیش‌نویس، از حساب متصل فروشنده (`seller_telegram_handle` یا `seller_x_handle`) ارسال می‌شود.

---

## ۸. نمونه کد عملیاتی اسکریپت مانیتورینگ ایجنت (Ready-to-Run)

```python
import time
from apps.businesses.models import Business
from apps.discovery.services import get_agent_discovery_feed
from apps.discovery.models import DiscoveredLead

def run_agent_discovery_worker(business_id: int):
    business = Business.objects.get(id=business_id)
    feed = get_agent_discovery_feed(business, min_priority=1)
    
    print(f"✅ فید ایجنت با موفقیت بارگذاری شد. {feed['total_active_products']} کالا در سهمیه.")

    for prod in feed["products"]:
        priority_score = prod["priority"]["score"]
        scan_weight = prod["priority"]["scan_weight"]
        
        # دسترسی ساده به تمام داده‌های مورد نیاز:
        prod_name = prod["name"]
        cat_path = prod["category"]["full_path"] if prod["category"] else "عمومی"
        attrs = prod["attributes"]
        keywords = prod["keywords"]
        outreach = prod["outreach_config"]
        
        print(f"🔍 در حال پایش برای «{prod_name}» در شاخه «{cat_path}» (اولویت: {priority_score})...")
        # فراخوانی ایجنت رصدگر شبکه‌ها (تلگرام / X)...

if __name__ == "__main__":
    run_agent_discovery_worker(business_id=1)
```
