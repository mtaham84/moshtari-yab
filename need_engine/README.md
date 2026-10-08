# need_engine — موتور تشخیص نیاز و تطبیق محصول

پیام‌های خامی که کراولر در دیتابیس می‌نویسد را **فقط می‌خواند**، نیازهای صریح و ضمنی افراد را با LLM استخراج می‌کند،
با محصولات کاتالوگ تطبیق می‌دهد و برای هر فرصت یک JSON طبق قرارداد Opportunity تولید می‌کند.

```
crawler ──(write)──► messages DB ──(read-only)──► need_engine ──► data/opportunities.jsonl
                                                     │
                                                     └── data/need_engine_state.db  (state خصوصی موتور)
```

## تضمین‌ها
- به دیتابیس اصلی **هیچ چیزی نمی‌نویسد** (sqlite با `mode=ro`، Postgres با تراکنش read-only). تست هم hash فایل را چک می‌کند.
- همه‌ی state خودش (cursor، پیام‌های در انتظار، نیازها، بردارها، کش LLM، هزینه‌ها، سهمیه) در فایل جدای `NE_STATE_PATH` است.
- خروجی فعلاً فقط JSONL است؛ نوشتن در DB بعداً با یک sink جدید اضافه می‌شود (`NeedEngine(cfg, sink=callable)`).

## جریان کار
1. **ingest**: پیام‌های جدید را با cursor روی `id` می‌خواند و در صف هر گروه می‌گذارد.
2. **trigger**: یک گروه وقتی تحلیل می‌شود که ۴۰ پیام جدید جمع شود، یا ۱۰ دقیقه سکوت شود، یا قدیمی‌ترین پیام ۳۰ دقیقه منتظر باشد.
3. **پیش‌فیلتر رایگان**: استیکر/ایموجی/ربات/پیام‌های خیلی کوتاه حذف می‌شوند (بدون هزینه‌ی LLM).
4. **استخراج نیاز**: پنجره‌های ۴۰ پیامی + ۱۰ پیام قبلی به‌عنوان context (با نشانگر فاصله‌ی زمانی ⏸).
5. **حافظه‌ی فرد**: نیاز جدید با نیازهای قبلی همان فرد ادغام می‌شود؛ «گرفتمش/حل شد» فرصت باز را resolved می‌کند.
6. **بازیابی**: embedding + BM25 (RRF وزنی) با آستانه‌ی تطبیقی؛ محصولات با قیمت بیش از ۳ برابر بودجه حذف می‌شوند.
7. **تأیید**: LLM فقط چک‌لیست برمی‌گرداند (solves + الزامات met/unmet/unknown)؛ امتیاز را کد حساب می‌کند (بودجه، شهر، الزامات اجباری).
8. **خروجی**: Opportunity با شواهد، نیاز، محصولات منطبق، پیش‌نویس پاسخ و هزینه (تومان + تعداد فراخوانی). تغییر وضعیت (resolved/expired) هم با همان `opportunity_id` دوباره emit می‌شود.
9. **محصول جدید**: وقتی محصول اضافه/عوض شود، با نیازهای باز فعلی هم تطبیق داده می‌شود.

## اجرا
```bash
pip install -r requirements.txt
export NE_LLM_API_KEY=...                       # یا GEMINI_API_KEY
python -m need_engine run                       # حلقه‌ی دائمی (هر NE_POLL_SECONDS)
python -m need_engine run --once                # یک دور
python -m need_engine run --once --flush        # همه‌ی پیام‌های منتظر را بدون توجه به trigger تحلیل کن
python -m need_engine stats                     # وضعیت state و هزینه‌ها
python -m need_engine demo --chats chats.jsonl --products products.jsonl --mock   # آفلاین، بدون API
python -m pytest need_engine/tests -q
```

## تنظیمات مهم (env)
| متغیر | پیش‌فرض | توضیح |
|---|---|---|
| `NE_MESSAGES_DSN` | `sqlite:///data/leads.db` | بعد از مهاجرت: `postgresql://user:pass@host/db` |
| `NE_MESSAGES_TABLE` / `NE_GROUPS_TABLE` | `messages` / `group_monitors` | اگر نام جدول‌ها عوض شد |
| `NE_MESSAGES_COLUMNS` | (نگاشت پیش‌فرض schema.sql) | JSON برای نگاشت ستون‌ها اگر اسم ستون‌ها تغییر کرد |
| `NE_PRODUCTS_SOURCE` | `jsonl:data/products.jsonl` | یا `sql:postgresql://…` / `sql:sqlite:///db.sqlite3` (جدول `products_product`) |
| `NE_STATE_PATH` / `NE_OUTPUT_PATH` | `data/need_engine_state.db` / `data/opportunities.jsonl` | |
| `NE_EXTRACT_MODEL` / `NE_VERIFY_MODEL` / `NE_REPLY_MODEL` | `gemini-3.5-flash-lite` | در صورت 503 مکرر: `gemini-2.5-flash-lite` |
| `NE_LLM_BASE_URL` | Gemini OpenAI-compatible | هر provider سازگار با OpenAI |
| `NE_EMBED_BACKEND` | `gemini` | `cloudflare` (bge-m3) یا `hash` (فقط تست) |
| `NE_SIM_FLOOR` | خودکار (gemini 0.55، bge-m3 0.40) | با بخش calibration نوت‌بوک تنظیم شود |
| `NE_TRIGGER_COUNT` / `NE_SILENCE_MINUTES` / `NE_MAX_WAIT_MINUTES` | 40 / 10 / 30 | |
| `NE_MIN_SCORE` | 15 | حداقل امتیاز (۰..۱۰۰) برای نگه داشتن یک محصول |
| `NE_USD_TO_TOMAN` | 100000 | برای محاسبه‌ی هزینه به تومان |

لیست کامل در `config.py` است؛ هر فیلد با `NE_<نام فیلد با حروف بزرگ>` قابل override است.

## سوییچ به PostgreSQL
فقط `NE_MESSAGES_DSN` (و در صورت تغییر نام‌ها، `NE_MESSAGES_TABLE`/`NE_MESSAGES_COLUMNS`) را عوض کنید. شرط لازم: ستون `id` افزایشی باشد (cursor روی آن است).
اگر state از sqlite به Postgres منتقل می‌شود و idها از اول شروع می‌شوند، فایل state را پاک کنید (یا `stats` را ببینید) تا cursor ریست شود.

## کارهای باز
- sink دیتابیس (بعد از نهایی‌شدن مدل `DiscoveredLead.analysis` و endpoint ‏`api/leads/submit`).
- gate ارزان (مدل خیلی ارزان یا Jev) قبل از استخراج برای کاهش هزینه به ~۱–۱.۵ تومان/پیام.
