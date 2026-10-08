# مشتری‌یاب (Moshtari-Yab)
> سامانه‌ی ایجنتیک کشف مشتری بالقوه در گروه‌های تلگرام و X — مسابقه buildX، مسئله ۱

مشتری‌یاب پیام‌های گروه‌های عمومی را آرشیو می‌کند، نیازهای **صریح و ضمنی** افراد را با LLM استخراج می‌کند،
آن‌ها را با محصولات فروشنده تطبیق می‌دهد و فرصت‌های فروش را همراه با شواهد، امتیاز کیفیت، پیش‌نویس پاسخ
و **هزینه‌ی واقعی تحلیل هر پیام (تومان)** در پنل فروشنده نشان می‌دهد.

## معماری (یک مسیر واحد)

```
telegram_crawler ──► data/leads.db ──(read-only)──► need_engine ──► data/opportunities.jsonl
   (فقط آرشیو)          (messages)                    │  (LLM: استخراج → بازیابی → تأیید → پاسخ)
                                                        │
Django products ───────────(read-only)─────────────────┘
                                                                    │
                          manage.py sync_opportunities  ◄───────────┘
                                     │
                                     ▼
                    پنل Django: فرصت‌ها، داشبورد، هزینه هر پیام، کارت محصول /p/<id>/?ref=<opportunity>
```

| بخش | مسیر | نقش |
|---|---|---|
| کراولر تلگرام | `telegram_crawler/` | backfill + live، فقط ذخیره‌ی پیام‌ها (بدون تحلیل) |
| موتور نیاز | `need_engine/` | تنها pipeline تحلیل؛ state خصوصی در `data/need_engine_state.db` |
| پل به پنل | `apps/discovery/engine_bridge.py` + `sync_opportunities` | JSONL فرصت‌ها را idempotent در مدل‌های Django می‌نویسد (به تفکیک کسب‌وکار) |
| پنل | `apps/` + `templates/` | ثبت‌نام/ورود، محصولات، فرصت‌ها، داشبورد با اعداد واقعی |
| جمع‌آوری X (آزمایشی) | `workers/x_collector/` | read-only، خروجی JSONL — هنوز به need_engine وصل نیست |

## راه‌اندازی

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env          # NE_LLM_API_KEY و TG_API_ID/TG_API_HASH را پر کنید
python manage.py migrate
python manage.py seed_products   # اختیاری: دسته‌بندی‌ها + محصولات نمونه (seller@moshtariyab.com / demo123456)
```

چهار فرایند (هر کدام در یک ترمینال):

```bash
python -m telegram_crawler.main --links-file groups.txt      # ۱) آرشیو پیام‌ها
python -m need_engine run                                     # ۲) تحلیل و تولید فرصت‌ها
python manage.py sync_opportunities --follow                  # ۳) ورود فرصت‌ها به پنل
python manage.py runserver                                    # ۴) پنل
```

دموی آفلاین بدون API و تلگرام:

```bash
python -m need_engine demo --chats chats.jsonl --products products.jsonl --mock
python manage.py sync_opportunities
```

## هزینه و کیفیت
- هزینه‌ی هر فراخوانی LLM/embedding در state موتور ثبت می‌شود؛ `python -m need_engine stats` مجموع هزینه، تعداد پیام تحلیل‌شده و **هزینه‌ی هر پیام** را نشان می‌دهد و داشبورد همین عدد را می‌خواند.
- هر فرصت امتیاز تطبیق محصول، شواهد (پیام‌های اصلی)، نیاز استخراج‌شده و هزینه‌ی خودش را دارد.
- وقتی فرد بگوید نیازش برطرف شده یا TTL تمام شود، وضعیت فرصت در پنل به «نیاز برطرف شد» / «منقضی شده» تغییر می‌کند (مگر فروشنده قبلاً با او تماس گرفته باشد).
- کلیک و سفارش از لینک `?ref=` به همان فرصت متصل می‌شود و سفارش، فرصت را «مشتری نهایی» می‌کند.

## تست

```bash
python -m pytest            # telegram_crawler + need_engine + x_collector
python manage.py test apps  # پنل و پل sync
```

## X Collector (آزمایشی)
`python -m workers.x_collector --mock --once` بدون شبکه اجرا می‌شود. اجرای زنده با کوکی (حساب burner) ریسک
محدودیت/ban دارد و نگاشت CLI هنوز باید با نسخه‌ی نصب‌شده‌ی `twitter-cli` تطبیق داده شود؛ جزئیات در
`docs/X_COLLECTION_ANALYSIS.md`.
