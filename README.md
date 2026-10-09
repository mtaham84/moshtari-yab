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
| جمع‌آوری X (آزمایشی) | `workers/x_collector/` | read-only، خروجی JSONL — هنوز به need_engine وصل نیست |

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
python -m telegram_crawler.main --links-file groups.txt      # ۱) آرشیو پیام‌ها
python -m need_engine run                                     # ۲) تحلیل و تولید فرصت‌ها
python manage.py sync_opportunities --follow                  # ۳) ورود فرصت‌ها به پنل
python manage.py runserver                                    # ۴) پنل
```

دموی آفلاین بدون API و تلگرام (state در یک schema موقت، خروجی JSONL):

```bash
python -m need_engine demo --chats chats.jsonl --products products.jsonl --mock --out data/demo.jsonl
```

## اجرا با Docker

```bash
cp .env.example .env     # حتماً: POSTGRES_PASSWORD، DJANGO_SECRET_KEY، NE_LLM_API_KEY، TG_API_ID، TG_API_HASH
cp groups.example.txt groups.txt   # لینک گروه‌های تلگرام، هر خط یکی

docker compose up -d --build                        # postgres + پنل (gunicorn) + sync + need_engine
docker compose --profile crawler run --rm crawler   # فقط بار اول: ورود به تلگرام (شماره + کد)، بعد Ctrl+C
docker compose --profile crawler up -d crawler      # کراولر در پس‌زمینه
docker compose exec web python manage.py createsuperuser
docker compose logs -f engine sync
```

- یک image برای همه‌ی سرویس‌ها؛ نقش با `command` تعیین می‌شود (`web`، `sync`، `engine`، `crawler`، `x`).
- دیتابیس: `pgvector/pgvector:pg16`؛ همه‌ی داده‌ها (پنل، آرشیو تلگرام، state و بردارهای موتور، فرصت‌ها) در volume `pgdata`.
  `appdata` فقط session تلگرام را نگه می‌دارد و `media` عکس محصولات را.
- `web` موقع بالا آمدن خودش `migrate` می‌زند؛ با `DJANGO_SEED_DEMO=true` داده‌ی نمونه هم ساخته می‌شود.
- need_engine پیام‌ها و محصولات را با اتصال read-only می‌خواند؛ تا کراولر داده‌ای ننوشته باشد کاری انجام نمی‌دهد.
- X collector آزمایشی است: `docker compose --profile x up -d x`.
- پشت دامنه و HTTPS: `DJANGO_DEBUG=False`، `DJANGO_ALLOWED_HOSTS`، `DJANGO_CSRF_TRUSTED_ORIGINS` و `DJANGO_BEHIND_HTTPS_PROXY=true`.

## هزینه و کیفیت
- هزینه‌ی هر فراخوانی LLM/embedding در `need_engine.costs` ثبت می‌شود؛ `python -m need_engine stats` مجموع هزینه، تعداد پیام تحلیل‌شده و **هزینه‌ی هر پیام** را نشان می‌دهد و داشبورد همین عدد را می‌خواند.
- هر فرصت امتیاز تطبیق محصول، شواهد (پیام‌های اصلی)، نیاز استخراج‌شده و هزینه‌ی خودش را دارد.
- وقتی فرد بگوید نیازش برطرف شده یا TTL تمام شود، وضعیت فرصت در پنل به «نیاز برطرف شد» / «منقضی شده» تغییر می‌کند (مگر فروشنده قبلاً با او تماس گرفته باشد).
- کلیک و سفارش از لینک `?ref=` به همان فرصت متصل می‌شود و سفارش، فرصت را «مشتری نهایی» می‌کند.

## تست

تست‌های ذخیره‌سازی روی PostgreSQL واقعی (با pgvector) و در schemaهای موقت اجرا می‌شوند:

```bash
docker compose run --rm web python -m pytest            # crawler + need_engine + x_collector
docker compose run --rm web python manage.py test apps  # پنل و sync
# بیرون از Docker: TEST_DATABASE_URL=postgresql://postgres:pass@127.0.0.1:5432/postgres python -m pytest
```

## X Collector (آزمایشی)
`python -m workers.x_collector --mock --once` بدون شبکه اجرا می‌شود. اجرای زنده با کوکی (حساب burner) ریسک
محدودیت/ban دارد و نگاشت CLI هنوز باید با نسخه‌ی نصب‌شده‌ی `twitter-cli` تطبیق داده شود؛ جزئیات در
`docs/X_COLLECTION_ANALYSIS.md`.
