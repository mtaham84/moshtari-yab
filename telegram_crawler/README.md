# کراولر و سیستم پایش لید گروه‌های تلگرام (Telegram Lead Crawler)

یک ابزار پایش خودکار برای شناسایی مشتریان بالقوه (Leads) در گروه‌های تلگرامی، استخراج عمیق کانتکست (اطلاعات کاربر، ۵ پیام قبل، و زنجیره کامل ریپلای‌ها)، و ذخیره‌سازی ساخت‌یافته در دیتابیس SQLite.

---

## قابلیت‌های کلیدی

1. **عضویت و اتصال خودکار با لینک**:
   - پشتیبانی از لینک‌های عمومی (`https://t.me/groupname` یا `@groupname`)
   - پشتیبانی از لینک‌های خصوصی/دعوت (`https://t.me/+hash` یا `https://t.me/joinchat/hash`)
   - مدیریت خطاهای تلگرام نظیر `UserAlreadyParticipantError` و `FloodWaitError`

2. **پایش دو مرحله‌ای (Hybrid)**:
   - **بک‌فیل (Backfill)**: اسکن پیام‌های گذشته گروه بر اساس محدودیت زمانی (مثلاً پیام‌های ۲۴ ساعت گذشته) و سقف تعداد (مثلاً ۲۰۰ پیام اخیر)
   - **مانیتورینگ زنده (Live Stream)**: گوش دادن پیوسته به پیام‌های جدید گروه به صورت لحظه‌ای با `events.NewMessage`

3. **ارزیابی پیام هدف (Target Detector)**:
   - تابع غیرهمگام `is_target_message(text, metadata)` با قابلیت نرمال‌سازی حروف فارسی و عربی
   - طراحی ماژولار جهت اتصال آسان به مدل‌های زبانی (LLM / Gemini / OpenAI) یا سرویس‌های هوش مصنوعی در آینده

4. **استخراج عمیق بافت پیام (Deep Context Extraction)**:
   - **اطلاعات فرستنده**: نام، نام خانوادگی، نام کاربری، شناسه عددی، شماره تماس، وضعیت پرمیوم
   - **کانتکست چت**: ۵ پیام پیش از پیام هدف در همان گروه جهت درک جریان گفتگو
   - **ترد ریپلای‌ها**:
     - ردیابی زنجیره پیام‌های والد (Ancestors) تا رسیدن به ریشه گفتگو
     - استخراج پاسخ‌های داده شده به این پیام (Child Replies)

5. **خروجی ساخت‌یافته و دیتابیس پایدار**:
   - مدل داده استاندارد مبتنی بر **Pydantic v2** (`LeadContext`)
   - ذخیره پایدار در دیتابیس SQLite با قابلیت WAL Mode برای بازدهی بالا
   - ثبت وضعیت اسکن هر گروه در جدول `group_monitors`

---

## پیش‌نیازها و راه‌اندازی

### ۱. فعال‌سازی محیط مجازی و نصب وابستگی‌ها
```bash
source .venv/bin/activate
pip install -r requirements.txt
```

### ۲. تنظیم متغیرهای محیطی
یک کپی از `.env.example` با نام `.env` بسازید:
```bash
cp .env.example .env
```
مقادیر `TG_API_ID` و `TG_API_HASH` را از [my.telegram.org](https://my.telegram.org) دریافت کرده و در فایل `.env` وارد کنید:
```ini
TG_API_ID=12345678
TG_API_HASH=abcdef0123456789abcdef0123456789
TG_SESSION=data/telegram_crawler_session
DB_PATH=data/leads.db

BACKFILL_HOURS=24
BACKFILL_LIMIT=200
CONTEXT_MSG_COUNT=5

TARGET_KEYWORDS="روغن موتور,خریدارم,دنبال,قیمت چنده,سراغ دارید"
```

---

## نحوه اجرا

### اجرای سریع با لینک گروه:
```bash
python -m telegram_crawler.main --link "https://t.me/+YourInviteHash"
```
یا برای یک گروه عمومی:
```bash
python -m telegram_crawler.main --link "https://t.me/group_username"
```

### پارامترهای اختیاری خط فرمان:
| آرگومان | پیش‌فرض | توضیحات |
|---|---|---|
| `--link` | ورودی دستی | لینک عمومی یا لینک دعوت خصوصی گروه |
| `--hours` | `24.0` | بررسی پیام‌های گذشته تا چند ساعت قبل |
| `--limit` | `200` | حداکثر تعداد پیام‌های گذشته برای اسکن |
| `--context-count` | `5` | تعداد پیام‌های قبل از پیام تارگت برای کانتکست |
| `--keywords` | کلیدواژه‌های `.env` | کلمات کلیدی دلخواه جدا شده با کاما |
| `--no-live` | خیر | فقط اسکن گذشته‌نگر و خروج (بدون گوش دادن زنده) |
| `--db-path` | `data/leads.db` | مسیر فایل دیتابیس SQLite |

---

## نحوه اتصال به سایر سیستم‌ها (Integration)

شما می‌توانید به راحتی تابع کال‌بک دلخواه خود را به `LeadMonitor` متصل کنید. مثال:

```python
from telegram_crawler.models import LeadContext
from telegram_crawler.monitor import LeadMonitor
from telegram_crawler.telegram_client import create_telegram_client

async def my_custom_handler(lead: LeadContext):
    # ارسال به بات تلگرام، پیامک، CRM یا وب‌هوک
    print(f"مشتری پیدا شد: {lead.user.display_name} - @{lead.user.username}")
    print(f"پیام: {lead.target_message.text}")
    # دریافت ساختار دیکشنری یا JSON
    lead_dict = lead.model_dump()
    # یا lead.model_dump_json()

async def run():
    client = create_telegram_client()
    await client.start()
    
    monitor = LeadMonitor(
        client=client,
        group_link="https://t.me/...",
        on_lead_detected=my_custom_handler
    )
    await monitor.run()
```

---

## اجرای تست‌های خودکار
```bash
pytest -v
```
تمام ۶ تست پوشش‌دهنده مدل‌ها، دیتابیس، تشخیص کلمات، پارسر لینک، استخراج کانتکست و پایپ‌لاین کلی را اجرا می‌کند.
