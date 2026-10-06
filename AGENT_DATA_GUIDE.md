# راهنمای جامع داده‌ها و معماری بک‌اند ایجنت (Agent Data Guide)
> **مستندات دسترسی به پایگاه دانش، سلسله‌مراتب ۵ سطحی، ویژگی‌های داینامیک، سهمیه‌ها و اندپوینت‌های دریافت و ثبت سرنخ برای مهندسان هوش مصنوعی**  
> **پروژه:** مشتری‌یاب (Moshtari-Yab) | **مسابقه:** buildX

این سند تشریح می‌کند که مهندسان ایجنت و توسعه‌دهندگان پایپ‌لاین هوش مصنوعی چگونه به داده‌های محصولات، کلمات کلیدی، درخت‌واره ۵ سطحی و تنظیمات فروشنده دسترسی پیدا کنند و چگونه سرنخ‌های کشف‌شده را همراه با هزینه و توکن در پایگاه داده جنگو ثبت نمایند.

---

## ۱. روش‌های دسترسی به داده‌ها (Data Access Methods)

### الف) دسترسی درون‌برنامه‌ای با Python ORM (توصیه شده برای ورکرها و اسکریپت‌های پایتون)

در صورتی که اسکریپت خزنده‌ها یا گراف ایجنت درون محیط پایتون پروژه اجرا می‌شود:

```python
import os
import sys
import django

# راه‌اندازی جنگو
sys.path.append("/home/taha/HDD/customerweb")
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
django.setup()

from apps.businesses.models import Business
from apps.products.models import Product, Category
from apps.discovery.models import DiscoveredLead, ProcessedMessageHash, ProductDailyMetric
from apps.discovery.services import (
    get_agent_discovery_feed,
    check_account_message_cap,
    validate_message_in_product_domain
)

# ۱. دریافت کسب‌وکار
business = Business.objects.first()

# ۲. فراخوانی فید آماده و اولویت‌بندی شده
feed = get_agent_discovery_feed(
    business=business,
    min_priority=1,      # حداقل اولویت (۱ تا ۵)
    channel="TELEGRAM"   # فیلتر کانال: "TELEGRAM" یا "X" یا None
)

print(f"کسب‌وکار: {feed['business']['name']}")
for p in feed['products']:
    print(f"[{p['priority']['level']}] {p['name']} | کلمات کلیدی: {p['keywords']}")
```

---

### ب) دسترسی از طریق REST API (برای میکروسرویس‌ها و خزنده‌های مستقل)

#### ۱. دریافت خوراک کاتالوگ و کلمات کلیدی:
* **اندپوینت:** `GET /discovery/api/agent/feed/`
* **پارامترهای اختیاری Query:**
  * `min_priority`: حداقل سطح اولویت کالا (۱ تا ۵)
  * `limit`: حداکثر تعداد رکوردهای خروجی
  * `channel`: فیلتر کانال (`TELEGRAM` یا `X`)

**نمونه فراخوانی با cURL:**
```bash
curl -X GET "http://127.0.0.1:8000/discovery/api/agent/feed/?channel=X&min_priority=2"
```

#### ۲. ثبت سرنخ کشف‌شده در پایگاه داده جنگو:
* **اندپوینت:** `POST /discovery/api/leads/submit/`
* **فرمت درخواست:** `Content-Type: application/json`

**نمونه بدنه ارسالی (Payload):**
```json
{
  "business_id": 1,
  "channel": "X",
  "lead_handle": "@tech_buyer",
  "lead_display_name": "سارا تهرانی",
  "post_url": "https://x.com/tech_buyer/status/17891230491",
  "content_snippet": "بچه‌ها شلوار کارگو باکیفیت و دوخت تمیز از کجا بخرم؟",
  "product_id": 1,
  "intent_score": 88,
  "intent_reasoning": "تطبیق ویژگی‌های کتان بگ با نیاز فوری خریدار",
  "matched_branch": "پوشاک > زنانه > شلوار > کارگو",
  "outreach_mode": "COMMENT",
  "outreach_message": "سلام سارا گرامی، در پاسخ به پرسش شما درباره شلوار کارگو...",
  "direct_link_sent": "https://customerweb.ir/p/1",
  "tokens_used": 580,
  "cost_usd": 0.000365,
  "cost_toman": 26
}
```

**پاسخ استاندارد API:**
```json
{
  "status": "success",
  "lead_id": 15,
  "product_id": 1,
  "message_count_today": 1,
  "is_conversation_capped": false,
  "cost_toman": 26,
  "tokens_used": 580,
  "message": "سرنخ کشف‌شده با موفقیت در پایگاه داده ثبت و پیام به مشتری ارسال شد."
}
```

---

## ۲. ساختار و فیلدهای مدل‌های پایگاه داده (Data Models Dictionary)

### ۱. مدل سرنخ کشف‌شده (`DiscoveredLead` در `apps/discovery/models.py`)
این مدل تمام داده‌های مربوط به فرصت‌های فروش، مکالمات، هزینه‌ها و سهمیه‌ها را نگهداری می‌کند:

| نام فیلد | نوع داده | توضیحات فنی |
| :--- | :--- | :--- |
| `business` | ForeignKey | کسب‌وکار مرتبط |
| `product` | ForeignKey | کالای منطبق از کاتالوگ فروشنده |
| `channel` | CharField | شبکه اجتماعی (`TELEGRAM` یا `X`) |
| `lead_handle` | CharField | شناسه کاربری فرد در شبکه اجتماعی (مانند `@user`) |
| `lead_display_name` | CharField | نام نمایشی کاربر |
| `post_url` | URLField | لینک مستقیم پست، توییت یا پیام |
| `content_snippet` | TextField | متن کامل پیام کاربر در شبکه اجتماعی |
| `intent_score` | SmallInt | درصد احتمال خرید (۳۰ تا ۱۰۰ درصد) |
| `intent_reasoning` | TextField | تحلیل و استدلال هوش مصنوعی در چرایی انطباق کالا |
| `matched_branch` | CharField | مسیر کامل شاخه دسته‌بندی منطبق |
| `outreach_mode` | CharField | شیوه ارتباط (`COMMENT` یا `DIRECT`) |
| `outreach_message` | TextField | متن پاسخ هوشمند ایجنت حاوی لینک مستقیم |
| `direct_link_sent` | CharField | لینک اختصاصی کارت دیجی‌کالایی محصول (`/p/<id>/`) |
| `message_count` | SmallInt | **تعداد پیام‌های تبادل‌شده امروز (سقف روزانه: ۱۰ پیام)** |
| `last_message_date` | DateField | **تاریخ آخرین تبادل پیام (جهت ریست خودکار در روز جدید)** |
| `is_conversation_capped` | BooleanField | **آیا سقف روزانه ۱۰ پیام برای این کاربر پر شده است؟** |
| `tokens_used` | IntegerField | **تعداد توکن‌های مصرفی جهت بررسی پیام (buildX)** |
| `cost_usd` | DecimalField | **هزینه دلاری بررسی پیام طبق تعرفه ابری** |
| `cost_toman` | IntegerField | **هزینه تومانی بررسی پیام (بر پایه نرخ ۷۰,۰۰۰ تومان)** |
| `guardrail_status` | CharField | وضعیت انطباق کانتکست (`SAFE_IN_DOMAIN` یا `OFF_TOPIC`) |
| `status` | CharField | وضعیت سرنخ (`NEW`, `CONTACTED`, `CONVERTED`, `IGNORED`) |

---

### ۲. مدل هش یکتای پیام (`ProcessedMessageHash` در `apps/discovery/models.py`)
برای جلوگیری از اتلاف منابع، توکن‌ها و هزینه‌ها:
$$\text{fingerprint} = \text{hashlib.sha256(f"{channel}:{lead\_handle}:{text}".encode()).hexdigest()}$$
اگر رکوردی با این `fingerprint` موجود باشد، پیام به مدل زبانی ارسال نمی‌شود (هزینه صفر).

---

### ۳. مدل کاتالوگ و ویژگی‌های داینامیک (`Product` و `Category` در `apps/products/models.py`)
* **دسته‌بندی درختی تا ۵ سطح:**
  متد `category.get_full_path()` مسیر کامل والد-فرزندی را تولید می‌کند (مانند `پوشاک > زنانه > شلوار > کارگو > کتان بگ`).
* **ویژگی‌های داینامیک کلید-مقدار (`product.attributes`):**
  یک دیکشنری باز JSON برای مشخصات فنی نامحدود (رنگ، سایز، جنس، پردازنده، گارانتی و...).
* **اولویت پایش ۵ سطحی (`product.discovery_priority`):**
  * `5`: فوری (URGENT - ضریب اسکن ۳.۰)
  * `4`: بسیار بالا (VERY_HIGH - ضریب اسکن ۲.۰)
  * `3`: بالا (HIGH - ضریب اسکن ۱.۵)
  * `2`: متوسط (MEDIUM - ضریب اسکن ۱.۰)
  * `1`: عادی (NORMAL - ضریب اسکن ۰.۵)

---

## ۳. سهمیه‌ها و محدودیت‌های اعمال‌شده (Quotas & Limits)

```
┌────────────────────────────────────────┬────────────────────────────────────────┐
│ عنوان سهمیه                            │ مقدار و نحوه اعمال                     │
├────────────────────────────────────────┼────────────────────────────────────────┤
│ سقف پیام روزانه به ازای هر حساب کاربری │ حداکثر ۱۰ پیام در روز (کامنت + دایرکت) │
│ ریست سقف پیام کاربر                    │ خودکار با تغییر تاریخ تقویمی           │
│ سقف ارسال پیام روزانه به ازای هر کالا  │ ۱۰۰ پیام در روز با اولویت شانس خرید    │
│ کف کیفیت سرنخ برای اقدام ایجنت         │ حداقل ۳۰ درصد انطباق نیت               │
│ سهمیه فعال‌سازی کالا در پایش روزانه   │ ۵۰ محصول به صورت همزمان                │
└────────────────────────────────────────┴────────────────────────────────────────┘
```

---

## ۴. اسکریپت‌های اجرایی در پوشه `scripts/crawlers/`

1. **`scripts/crawlers/telegram_listener.py`:**
   ورکر آماده شنود گروه‌های تلگرامی با پشتیبانی از فیلتر لغوی منفی، بررسی چکیده هش، تطبیق با کاتالوگ و ثبت خودکار در دیتابیس جنگو.
2. **`scripts/crawlers/x_listener.py`:**
   بات شبکه X با پیاده‌سازی کامل استراتژی کامنت‌اول، درج لینک کارت کالا، پاسخ مشروط در دایرکت و گاردریل امنیتی.
3. **`scripts/crawlers/mock_social_feed.py`:**
   اسکریپت تست و اجرای شبیه‌سازی زنده کل پایپ‌لاین برای نمایش در روز مسابقه.
