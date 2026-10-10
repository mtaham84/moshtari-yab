# اتصال X به need_engine — تحلیل و تصمیم‌ها

## کیفیت جست‌وجو، فیلتر و پاسخ‌های X

- استقرار پیشنهادی: ابتدا X_QUERY_MODE=both و NE_X_PREFILTER=shadow نگه دارید؛ پس از چند روز داده، تصمیم‌های فیلتر را دستی بررسی کنید و فقط با سنجش خطای منفی، NE_X_PREFILTER=on را فعال کنید. سپس می‌توان X_QUERY_MODE=intent را ارزیابی کرد.
- NE_X_BATCH_SIZE=8 تعداد فراخوانی استخراج را کم می‌کند؛ هزینه واقعی توکن و کیفیت باید روی دادهٔ واقعی سنجیده شود. هزینه batch برای پیام‌های ورودی مساوی تقسیم می‌شود.
- query operators، خروجی واقعی CLI، intent URL و شمارش کاراکتر X هنوز UNVERIFIED هستند. اجرای live همچنان NO-GO تا CLI/ToS، مجوز DB، هزینه و smoke test پنل تأیید شوند.
- این نسخه هنوز LLM-generated problem queries، dedupe بین پاسخ‌ها و گزارش false-negative کامل را ندارد؛ فیلترها heuristic هستند و نمی‌توانند خرید واقعی را تضمین کنند.

## مسیر فعلی و فرض‌های پلتفرم

مسیر تولید فعلی تلگرام این است: `telegram_crawler` جدول‌های `crawler.tg_*` را می‌نویسد؛ `SQLMessageSource` پیام‌های جدید را با `tg_messages.id` می‌خواند؛ `NeedEngine.ingest()` آن‌ها را در `need_engine.pending` می‌گذارد؛ windowing و استخراج، نیاز و فرصت را می‌سازند؛ `Store` خروجی را در `need_engine.opportunities` منتشر می‌کند؛ `sync_opportunities` از طریق `apps/discovery/engine_bridge.py` آن را به مدل‌های پنل می‌رساند. Source پل پیش‌فرضش را Telegram فرض می‌کرد و برای URL، شناسه و پروفایل `t.me` می‌ساخت. پنل همین حالا گزینه/برچسب X دارد، اما لینک کاربر در فهرست به‌صورت ثابت `t.me` بود.

در تلگرام «چت» یک مکالمه است: `chat_id` شناسهٔ گروه، `message_id` شناسهٔ پیام داخل آن، reply parent با همان chat و `recent` context ساخته می‌شود. X مکالمهٔ مشترک نیست؛ هر tweet مستقل و متعلق به نویسنده‌ای جداست. انتخاب: یک stream ورودی X با `chat_id` namespace‌شده، ولی context و reply-parent برای X خاموش؛ هر tweet فقط ورودی تازهٔ همان پنجره است. پرامپت X روشن می‌کند که این‌ها پست‌های مستقل کاربران‌اند و نباید از پست دیگران برای نتیجه‌گیری دربارهٔ فرد استفاده شود. حافظهٔ need همچنان به `(chat_id, author_id)` محدود است؛ بنابراین پست‌های یک نویسنده می‌توانند نیاز قبلی خودش را غنی کنند و کاربران مختلف با هم ادغام نمی‌شوند. `chat_id` stream به‌شکل `x:public` است و با شناسه‌های تلگرام تداخل نمی‌کند.

این از «یک چت برای هر tweet» کم‌هزینه‌تر است: روش انتخابی با window size 40 در حالت پرشدن حدود یک فراخوانی استخراج به ازای هر 40 پست دارد؛ روش یک‌چت‌برای‌هر-tweet تقریباً یک فراخوانی برای هر پست (تا 40 برابر فراخوانی ثابت بیشتر) مصرف می‌کند. متن توکن‌محور است، پس هزینهٔ توکنی دقیقاً خطی و ثابت نیست و فقط از ledger مصرف پس از اجرا قابل‌اندازه‌گیری است. سقف `NE_X_MAX_PER_RUN=200` هزینهٔ ورودی X را در هر اجرای موتور محدود می‌کند؛ سقف تعداد پیام به‌تنهایی سقف پولی قطعی نیست.

## Cursor، ذخیره‌سازی و دسترسی

Cursor تلگرام `fetch_cursor` دست‌نخورده می‌ماند؛ X از `fetch_cursor_x` جدا استفاده می‌کند. تغییر، cursor قبلی و صف‌های `pending`/`recent` تلگرام را reset، migrate یا replay نمی‌کند. تنها پیام‌های X به stream جدید اضافه می‌شوند. Loader یک جدول `crawler.x_posts` idempotent می‌سازد و upsert را بر `tweet_id` یکتا انجام می‌دهد؛ `row_id BIGSERIAL` ترتیب ورود و cursor مستقل منبع را می‌دهد. شناسه‌های X در `BIGINT` جا می‌شوند (Snowflakeهای فعلی زیر حد signed 64-bit هستند) و به‌صورت `message_id` عددی نگهداری می‌شوند؛ `chat_id` نیز prefix پلتفرم دارد.

Crawler صاحب نوشتن `crawler.x_posts` است؛ به نقش loader اجازهٔ نوشتن crawler schema/table و به نقش موتور فقط `SELECT` داده شود. موتور مانند Telegram به sourceها read-only متصل می‌ماند و فقط state در schema خودش را می‌نویسد. Loader بدون Django است، JSONL UTF-8 را خط‌به‌خط اعتبارسنجی می‌کند، خط خراب را با علت رد می‌کند و retry/upsert تکراری ردیف جدید نمی‌سازد. Worker `author_id` و `metadata.query` را ثبت می‌کند. Queryهای live از محصول‌های ACTIVE با discovery و X outreach روشن تولید می‌شوند: نام کالا، مسیر دسته‌بندی و keywordهای دسته، با اولویت `discovery_priority`; `--queries` برای override است و loop فهرست DB را در هر چرخه بازخوانی می‌کند. Category path/keywords در کارت و embedding محصول می‌آیند تا تطبیق نیاز پست با محصول/دسته دقیق‌تر شود؛ خروجی فرصت، شناسه و URL شاهد را به bridge پنل می‌دهد.

CLI طبق قرارداد ارائه‌شده به‌شکل `twitter search <query> -t Latest --exclude retweets --max N --json` ساخته می‌شود و queryهای آغازشونده با `-` رد می‌شوند. باید نصب واقعی CLI را با `twitter --help` و `agent-reach doctor` بررسی کرد.

## پنل، پرچم‌ها و اعتبارسنجی

`NE_X_ENABLED=false` به‌طور پیش‌فرض مسیر X و تغییر state مربوط به آن را خاموش نگه می‌دارد؛ `NE_X_MAX_PER_RUN=200` فقط وقتی X فعال است اعمال می‌شود. داده‌های Telegram و نمایش فعلی آن باید بدون تغییر بمانند. Opportunityهای X باید `platform=x`، پروفایل `https://x.com/<handle>` و URL شواهد tweet داشته باشند؛ متن tweet ورودی نامطمئن است و باید با autoescape پیش‌فرض template نمایش داده شود، نه `safe`. هیچ قابلیت post/reply/like/follow/DM در این اتصال وجود ندارد.

دستورهای بررسی در محیط توسعه: `pytest -q`، `python manage.py test apps` و end-to-end شامل collector mock، `python -m x_ingest`, `python -m need_engine run --once --flush --mock` و `python manage.py sync_opportunities`. baseline اعلام‌شده در درخواست: pytest شامل 50 تست پایه و Django apps. در این محیط `python -m pytest` با `No module named pytest` شکست خورد و `python manage.py test apps` با `psycopg2.OperationalError` چون PostgreSQL در `127.0.0.1:5432` در دسترس نیست؛ بنابراین baseline، تست‌های پس از تغییر و زنجیرهٔ DB end-to-end اینجا قابل تأیید نیستند. Docker نیز در تلاش قبلی در دسترس نبوده است.

**وضعیت اولیه: No-Go برای فعال‌سازی تولیدی تا نصب وابستگی‌های تست، اجرای تست‌ها با PostgreSQL/pgvector، اعطای دسترسی حداقلی DB و تأیید کامل زنجیرهٔ end-to-end.**
