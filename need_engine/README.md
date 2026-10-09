# need_engine — موتور تشخیص نیاز و تطبیق محصول

پیام‌هایی که کراولر در schema `crawler` می‌نویسد (با اطلاعات فرستنده و متن پیام والد) را **فقط می‌خواند**، نیازهای صریح و ضمنی افراد را با LLM استخراج می‌کند،
با محصولات کاتالوگ تطبیق می‌دهد و برای هر فرصت یک JSON طبق قرارداد Opportunity تولید می‌کند.

```
crawler ──(write)──► crawler.tg_messages ──(read-only)──► need_engine ──► need_engine.opportunities ──► sync → پنل
                                                             │
                                                             └── need_engine.*  (state، بردارها در pgvector، هزینه‌ها)
```

## تضمین‌ها
- به جدول‌های کراولر و پنل **هیچ چیزی نمی‌نویسد** (اتصال با `default_transaction_read_only`). تست، hash آرشیو را قبل و بعد چک می‌کند.
- همه‌ی state خودش (cursor، پیام‌های در انتظار، نیازها، کش LLM، هزینه‌ها، سهمیه) در schema جدای `NE_STATE_SCHEMA` (پیش‌فرض `need_engine`) است.
- بردارهای محصولات (`product_vectors`) و نیازها (`needs.summary_vec`، `need_vectors`) از نوع `vector` در pgvector ذخیره می‌شوند.
- هر فرصت در `need_engine.opportunities` منتشر می‌شود (`seq` با هر تغییر بزرگ‌تر می‌شود) و `python manage.py sync_opportunities [--follow]` آن را به مدل‌های پنل منتقل می‌کند (idempotent با `opportunity_id`).
- شناسه‌ی نیاز/فرصت شامل شناسه‌ی یکتای همین state است (`need_000001_<instance>`) تا با ریست state تداخل پیش نیاید.

## جریان کار
1. **ingest**: پیام‌های جدید را با cursor روی `tg_messages.id` می‌خواند (پیام‌های سرویسی و والدهایی که فقط برای context گرفته شده‌اند ورودی حساب نمی‌شوند) و در صف هر گروه می‌گذارد.
2. **trigger**: فقط بر اساس تعداد — هر بار که ۵۰ پیام جدید از یک گروه جمع شود همان ۵۰ پیام (با ۱۰ پیام قبلی به‌عنوان زمینه) تحلیل می‌شود؛ باقی‌مانده منتظر دسته‌ی بعدی می‌ماند. منتظر تمام شدن بحث نمی‌ماند. (`NE_MAX_WAIT_MINUTES` اختیاری است و پیش‌فرض خاموش.)
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
python -m need_engine stats                     # وضعیت state، پیام‌های تحلیل‌شده، هزینه کل و هزینه هر پیام
python -m need_engine demo --chats chats.jsonl --products products.jsonl --mock --out data/demo.jsonl   # بدون API
TEST_DATABASE_URL=postgresql://… python -m pytest need_engine/tests -q
```

## تنظیمات مهم (env)
| متغیر | پیش‌فرض | توضیح |
|---|---|---|
| `NE_DATABASE_URL` | از `POSTGRES_*` ساخته می‌شود | همان دیتابیس پنل |
| `NE_MESSAGES_SOURCE` / `NE_CRAWLER_SCHEMA` | `db` / `crawler` | یا `jsonl:path` برای تست و دمو |
| `NE_PRODUCTS_SOURCE` / `NE_PRODUCTS_TABLE` | `db` / `public.products_product` | محصولات فعال پنل؛ یا `jsonl:path` |
| `NE_STATE_SCHEMA` | `need_engine` | state، بردارها و جدول opportunities |
| `NE_OUTPUT_JSONL` | خالی | یک کپی اضافه از هر فرصت در فایل (برای دیباگ) |
| `NE_EXTRACT_MODEL` / `NE_VERIFY_MODEL` / `NE_REPLY_MODEL` | `gemini-3.5-flash-lite` | در صورت 503 مکرر: `gemini-2.5-flash-lite` |
| `NE_LLM_BASE_URL` | Gemini OpenAI-compatible | هر provider سازگار با OpenAI |
| `NE_EMBED_BACKEND` | `gemini` | `cloudflare` (bge-m3) یا `hash` (فقط تست) |
| `NE_SIM_FLOOR` | خودکار (gemini 0.55، bge-m3 0.40) | با بخش calibration نوت‌بوک تنظیم شود |
| `NE_TRIGGER_COUNT` / `NE_WINDOW_SIZE` | 50 / 50 | هر چند پیام یک بار تحلیل شود / پیام در هر فراخوانی |
| `stats --business <id>` | — | سهم یک فروشنده از هزینه: تطبیق و پاسخ محصولاتش + تحلیل گروه‌های اختصاصی‌اش (تقسیم مساوی بین صاحبان)؛ گروه عمومی = هزینه‌ی پلتفرم |
| `NE_SOURCE_ACCESS` / `NE_COMMUNITIES_TABLE` | `panel` / `public.discovery_monitoredcommunity` | قانون دسترسی قبل از هر LLM: منبع عمومی فعال → محصولات همه، فقط منبع اختصاصی → فقط محصولات صاحبانش، بدون منبع فعال → چت تحلیل نمی‌شود (`open` = بدون قانون) |
| `NE_MAX_WAIT_MINUTES` | 0 (خاموش) | اختیاری: پیام‌های کمتر از یک دسته بعد از این مدت تحلیل شوند |
| `NE_MIN_SCORE` | 15 | حداقل امتیاز (۰..۱۰۰) برای نگه داشتن یک محصول |
| `NE_USD_TO_TOMAN` | 100000 | برای محاسبه‌ی هزینه به تومان |

لیست کامل در `config.py` است؛ هر فیلد با `NE_<نام فیلد با حروف بزرگ>` قابل override است.

## کارهای باز
- منبع X (خروجی `workers/x_collector`) به‌عنوان ورودی دوم.
- gate ارزان (مدل خیلی ارزان یا Jev) قبل از استخراج برای کاهش هزینه به ~۱–۱.۵ تومان/پیام.
