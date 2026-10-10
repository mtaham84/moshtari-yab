# مشتری‌یاب (Moshtari-Yab)
> سامانه‌ی ایجنتیک کشف مشتری بالقوه در گروه‌های تلگرام و X — مسابقه buildX، مسئله ۱

مشتری‌یاب پیام‌های گروه‌های عمومی را آرشیو می‌کند، نیازهای **صریح و ضمنی** افراد را با LLM استخراج می‌کند،
آن‌ها را با محصولات فروشنده تطبیق می‌دهد و فرصت‌های فروش را همراه با شواهد، امتیاز کیفیت، پیش‌نویس پاسخ
و **هزینه‌ی واقعی تحلیل هر پیام (تومان)** در پنل فروشنده نشان می‌دهد.

## معماری (یک مسیر، یک دیتابیس PostgreSQL + pgvector)

```
telegram_crawler ──(write)──► crawler.tg_users / tg_chats / tg_messages
                                        │ (read-only)
Django products ──(read-only)──► need_engine ──► need_engine.* (state، بردارها با pgvector، هزینه‌ها)
                                        ▲
workers/x_collector → JSONL → x_ingest → crawler.x_posts ─┘ (NE_X_ENABLED=true)
                                        │
                                        └──► need_engine.opportunities
                                                   │
                          manage.py sync_opportunities
                                                   ▼
                 پنل Django: فرصت‌ها، داشبورد، هزینه هر پیام، کارت محصول /p/<id>/?ref=<opportunity>
```

| بخش | مسیر | نقش |
|---|---|---|
| کراولر تلگرام | `telegram_crawler/` | backfill + live؛ کاربر، چت، پیام و پیام والد را کامل ذخیره می‌کند (schema `crawler`) |
| موتور نیاز | `need_engine/` | تنها pipeline تحلیل؛ فقط در schema `need_engine` می‌نویسد، بقیه را read-only می‌خواند |
| پل به پنل | `apps/discovery/engine_bridge.py` + `sync_opportunities` | فرصت‌های منتشرشده را idempotent در مدل‌های Django می‌نویسد (به تفکیک کسب‌وکار) |
| پنل | `apps/` + `templates/` | ثبت‌نام/ورود، محصولات، فرصت‌ها، داشبورد با اعداد واقعی |
| جمع‌آوری X | `workers/x_collector/` + `x_ingest/` | worker فقط‌خواندنی، JSONL و upsert idempotent در `crawler.x_posts` |

## راه‌اندازی

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env          # POSTGRES_*، NE_LLM_API_KEY و TG_API_ID/TG_API_HASH را پر کنید
# PostgreSQL 16 با افزونه‌ی pgvector لازم است (ساده‌ترین راه: بخش Docker پایین)
python manage.py migrate
python manage.py seed_products   # اختیاری: دسته‌بندی‌ها + محصولات نمونه (seller@moshtariyab.com / demo123456)
```

چهار فرایند (هر کدام در یک ترمینال):

```bash
python -m telegram_crawler.main                              # ۱) آرشیو پیام‌های گروه‌هایی که در پنل («جوامع آنلاین») اضافه شده‌اند
python -m need_engine run                                     # ۲) تحلیل و تولید فرصت‌ها
python manage.py sync_opportunities --follow                  # ۳) ورود فرصت‌ها به پنل

# مسیر اختیاری X: queryها از محصولات فعال و دسته‌هایشان می‌آیند
python -m workers.x_collector --once                             # ۱) جمع‌آوری براساس محصولات فعال
python -m x_ingest --path data/x_collected/latest.jsonl         # ۲) ورود به crawler.x_posts
python -m need_engine run --once --flush                         # ۳) تحلیل (پس از فعال‌کردن NE_X_ENABLED=true)
python manage.py sync_opportunities                            # ۴) نمایش فرصت‌ها در پنل
python manage.py runserver                                    # ۴) پنل
```

دموی آفلاین بدون API و تلگرام (state در یک schema موقت، خروجی JSONL):

```bash
python -m need_engine demo --chats chats.jsonl --products products.jsonl --mock --out data/demo.jsonl
```

## پنل مدیریت و پرداخت به‌ازای مصرف (`/ops/`)

فقط کاربرهای staff وارد می‌شوند (`python manage.py createsuperuser`). بخش‌ها:

| صفحه | کار |
|---|---|
| داشبورد | هزینه‌ی واقعی مدل‌ها، درآمد، سود، مجموع اعتبار فروشنده‌ها، هزینه‌ی روزانه، تفکیک مدل/مرحله، **وضعیت سرویس‌ها** (DB، موتور، کراولر، sync، مدل‌ها) |
| هزینه‌ها | دفتر هر فراخوانی مدل (`need_engine.costs`) با فیلتر فروشنده/مدل/مرحله + جمع هر فروشنده |
| سرویس‌دهنده‌ها و مدل‌ها | افزودن/حذف سرویس‌دهنده (Base URL + API Key، سازگار با OpenAI)، تست اتصال، افزودن/حذف/غیرفعال‌کردن مدل، قیمت ورودی/خروجی ($ به‌ازای ۱M توکن)، نقش (استخراج / تطبیق / پاسخ)، اولویت و سقف‌های RPM/TPM/RPD. «ایمپورت از ‎.env» تنظیمات فعلی را منتقل می‌کند |
| کیف پول‌ها | موجودی هر فروشنده، شارژ / هدیه / بازگشت وجه / اصلاح دستی، تاریخچه‌ی تراکنش‌ها |
| گروه‌های تلگرام | همه‌ی منابع (عمومی و اختصاصی) با وضعیت کراولر، افزودن منبع عمومی، توقف/فعال‌سازی/حذف |
| تنظیمات پرداخت | نرخ دلار، درصد سود، کسر برای پاسخ کش‌شده، توقف تحلیل با اتمام اعتبار، حداقل اعتبار، اعتبار هدیه‌ی ثبت‌نام |

جریان پول: موتور هزینه‌ی واقعی هر فراخوانی را با قیمت مدل در پنل ثبت می‌کند → سرویس `sync` هر دور ردیف‌های جدید را جمع می‌زند و
`هزینه‌ی دلاری × نرخ دلار × (۱ + درصد سود)` را از کیف پول هر فروشنده کسر می‌کند (`manage.py charge_usage` هم همین کار را می‌کند).
فروشنده‌ای که اعتبارش تمام شود، تا شارژ بعدی تطبیق، پاسخ و تحلیل گروه‌های اختصاصی‌اش را نمی‌گیرد (چیزی هم از او کسر نمی‌شود).

**در انتظار پرداخت:** در این مدت کار او دور ریخته نمی‌شود، فقط در صف می‌ماند و بعد از شارژ خودکار (در اجرای بعدی موتور) پردازش می‌شود:
- پیام‌های گروه‌های اختصاصی‌اش جمع‌آوری می‌شوند ولی تحلیل نمی‌شوند؛
- نیازهایی که در گروه‌های عمومی/مشترک پیدا می‌شوند و محصولات او کاندید آن‌ها هستند، برایش در صف می‌مانند و بعد از شارژ فقط با محصولات او تطبیق داده می‌شوند؛
- محصول جدید/ویرایش‌شده‌اش کارت نمی‌گیرد تا شارژ شود، بعد کارت می‌گیرد و با نیازهای باز تطبیق می‌خورد.

چیزهای قدیمی‌تر از عمر نیاز (`NE_NEED_TTL_DAYS`) حذف می‌شوند. تعداد کارهای در صف در هدر پنل فروشنده، داشبورد و کیف پول‌های `/ops/` دیده می‌شود. `NE_RELEASE_BATCH` (پیش‌فرض ۲۰۰) = تعداد نیاز در صف که در هر اجرا برای هر فروشنده پردازش می‌شود.
موتور مدل‌ها/کلیدها/قیمت‌ها/موجودی‌ها را هر ۶۰ ثانیه از پنل می‌خواند؛ ری‌استارت لازم نیست. موجودی در هدر پنل فروشنده نمایش داده می‌شود.

> بعد از migrate همه‌ی فروشنده‌های فعلی کیف پول با موجودی صفر دارند و «توقف تحلیل با اتمام اعتبار» روشن است؛
> قبل از استقرار یا شارژشان کنید یا این گزینه را در تنظیمات پرداخت خاموش کنید.

## اجرا با Docker

```bash
cp .env.example .env     # حتماً: POSTGRES_PASSWORD، DJANGO_SECRET_KEY، NE_LLM_API_KEY، TG_API_ID، TG_API_HASH

docker compose run --rm crawler login   # فقط بار اول: ورود به تلگرام (شماره + کد)؛ session در volume می‌ماند
docker compose up -d --build            # postgres + پنل + sync + need_engine + کراولر تلگرام
docker compose logs -f crawler engine sync
```

بعد از آن هیچ دستوری لازم نیست: در پنل → «جوامع آنلاین (تلگرام)» گروه را اضافه کنید.
کراولر هر `TG_PANEL_POLL_SECONDS` (پیش‌فرض ۳۰ ثانیه) جدول جوامع را می‌خواند، عضو گروه می‌شود، تاریخچه‌ی اخیر را آرشیو
می‌کند و پیام‌های زنده را می‌گیرد؛ وضعیت («در صف اتصال» / «در حال پایش» / «خطا در اتصال»)، تعداد اعضا و پیام‌های
رصدشده در همان صفحه نمایش داده می‌شود. «توقف پایش» پیام‌های آن گروه را کنار می‌گذارد و «فعال‌سازی» پیام‌های جاافتاده را
دوباره می‌گیرد. need_engine هر ۲۰ ثانیه پیام‌های جدید را برمی‌دارد و sync فرصت‌ها را وارد پنل می‌کند.
(فایل اختیاری `data/groups.txt` داخل volume هم مثل قبل خوانده می‌شود.)

- یک image برای همه‌ی سرویس‌ها؛ نقش با `command` تعیین می‌شود (`web`، `sync`، `engine`، `crawler`، `login`، `x`).
- دیتابیس: `pgvector/pgvector:pg16`؛ همه‌ی داده‌ها (پنل، آرشیو تلگرام، state و بردارهای موتور، فرصت‌ها) در volume `pgdata`.
  `appdata` فقط session تلگرام را نگه می‌دارد و `media` عکس محصولات را.
- `web` موقع بالا آمدن خودش `migrate` می‌زند؛ با `DJANGO_SEED_DEMO=true` داده‌ی نمونه هم ساخته می‌شود.
- need_engine پیام‌ها و محصولات را با اتصال read-only می‌خواند؛ تا کراولر داده‌ای ننوشته باشد کاری انجام نمی‌دهد.
- اتصال X به‌صورت پیش‌فرض خاموش است (`NE_X_ENABLED=false`). رعایت شرایط استفادهٔ X الزامی است؛ از حساب اختصاصی کم‌ریسک استفاده کنید. این pipeline فقط می‌خواند و هرگز پست، پاسخ، لایک، فالو یا DM نمی‌فرستد.
- فیلتر X برای کم‌کردن مشتری/دادهٔ ساختگی محافظه‌کار است: پست باید شناسهٔ پایدار نویسنده داشته باشد، متن خودش قصد خرید صریح را نشان دهد، شواهد از همان نویسنده باشد و محصول از مرحلهٔ تطبیق مستقل با حداقل امتیاز (`NE_X_MIN_MATCH_SCORE=65`) عبور کند. این کار خطای مثبت را کم می‌کند اما تضمین مشتری قطعی نمی‌دهد؛ نتیجه را پیش از تماس انسانی بررسی کنید.
- X collector آزمایشی است: `docker compose --profile x up -d x`.
- پشت دامنه و HTTPS: `DJANGO_DEBUG=False`، `DJANGO_ALLOWED_HOSTS`، `DJANGO_CSRF_TRUSTED_ORIGINS` و `DJANGO_BEHIND_HTTPS_PROXY=true`.

Query generation supports X_QUERY_MODE=product|intent|both (default both), a per-cycle budget, query length/count caps, and an optional suffix. Use --print-queries to review generated queries and --dry-run to inspect the next cycle without invoking the CLI. LLM-generated problem phrases are not implemented yet (X_QUERY_LLM=false).

For X analysis, NE_X_PREFILTER=shadow records decisions without dropping posts; switch to on only after reviewing need_engine x-prefilter-report. NE_X_BATCH_SIZE and NE_X_BATCH_MAX_CHARS bound independent-post batches. Reply variants are drafts only; Open on X uses an unverified web intent URL and never sends from the server. Finglish detection and false-negative auditing remain limitations.

## هزینه و کیفیت
- هزینه‌ی هر فراخوانی LLM/embedding در `need_engine.costs` ثبت می‌شود؛ `python -m need_engine stats` مجموع هزینه، تعداد پیام تحلیل‌شده و **هزینه‌ی هر پیام** را نشان می‌دهد و داشبورد همین عدد را می‌خواند.
- هر فرصت امتیاز تطبیق محصول، شواهد (پیام‌های اصلی)، نیاز استخراج‌شده و هزینه‌ی خودش را دارد.
- وقتی فرد بگوید نیازش برطرف شده یا TTL تمام شود، وضعیت فرصت در پنل به «نیاز برطرف شد» / «منقضی شده» تغییر می‌کند (مگر فروشنده قبلاً با او تماس گرفته باشد).
- لینک پیش‌نویس‌ها `<NE_PUBLIC_BASE_URL>/r/<محصول>/?ref=<فرصت>` است: کلیک ثبت می‌شود و کاربر به صفحه‌ی محصول در سایت خود فروشنده
  (فیلد «لینک صفحه محصول») می‌رود. محصول بدون لینک → پیش‌نویس بدون لینک.
- کارت عمومی `/p/<id>/?ref=` همچنان کار می‌کند و سفارش ثبت‌شده در آن، فرصت را «مشتری نهایی» می‌کند.

## ابزارهای فروشنده در پنل
- **سبک پیام** (تنظیمات): لحن، حداکثر جمله، ایموجی، امضا، لینک، دستورهای اضافه (۵۰۰ کاراکتر). «نمونه بساز» قبل از ذخیره با یکی از محصولات
  پیش‌نمایش می‌دهد و «دوباره بنویس» در صفحه‌ی هر فرصت متن تازه می‌نویسد. قوانین ثابت سیستم عوض نمی‌شوند (`need_engine/reply.py`).
- **پر کردن خودکار از لینک** (فرم محصول): اول JSON-LD / Open Graph صفحه، در صورت کمبود مدل زبانی؛ فقط فیلدهای خالی پر می‌شوند.
  درخواست به آدرس‌های داخلی/خصوصی سرور (و redirect به آن‌ها) رد می‌شود (`apps/products/autofill.py`).
- **«ایجنت محصول شما را این‌طور فهمیده»** (صفحه‌ی محصول): برداشت ایجنت قابل ویرایش است؛ نسخه‌ی فروشنده بدون فراخوانی مدل مستقیم
  در تطبیق استفاده می‌شود و «برگشت به نسخه‌ی ایجنت» آن را حذف می‌کند.
- این سه فراخوانی با همان کلاینت need_engine (rate limit، cache و دفتر هزینه) انجام و به حساب همان فروشنده نوشته می‌شوند.
  برای دمو/تست بدون API: `NE_PANEL_MOCK_LLM=true`.
- لیست فرصت‌ها هر ۲۰ ثانیه چک می‌کند و در صورت فرصت جدید نوار «N فرصت جدید» نشان می‌دهد.

## تست

تست‌های ذخیره‌سازی روی PostgreSQL واقعی (با pgvector) و در schemaهای موقت اجرا می‌شوند:

```bash
docker compose run --rm web python -m pytest            # crawler + need_engine + x_collector
docker compose run --rm web python manage.py test apps  # پنل و sync
# بیرون از Docker: TEST_DATABASE_URL=postgresql://postgres:pass@127.0.0.1:5432/postgres python -m pytest
```

## X Collector
اجرای mock بدون شبکه است. اجرای زنده queryها را از محصولات فعالِ علامت‌خورده برای پایش X می‌سازد: نام کالا، مسیر دسته‌بندی سراسری و keywordهای دسته. collector با `twitter search <query> -t Latest --exclude retweets --max N --json` می‌خواند، JSONL می‌سازد و loader آن را به `crawler.x_posts` می‌برد. برای تولید، نحو CLI را با `twitter --help` و `agent-reach doctor` بررسی کنید؛ اجرای X ممکن است محدودیت حساب/سرویس داشته باشد. `--queries` مسیر override دستی queryهاست.
