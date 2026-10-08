# مرحله تحویل ۱ — پایه ایجنت مشترک و تکمیل خزنده تلگرام

## خلاصه
- پکیج جدید `analysis/` (ایجنت مشترک، مستقل از منبع): قالب‌های داده، تنظیمات، صف ورودی مشترک (SQLite) و بارگذاری کاتالوگ.
- پوشه جدید `sources/`: مبدل تلگرام و مبدل فایل (JSONL/CSV برای X و داده دمو).
- خزنده تلگرام: ذخیره همه پیام‌ها، زمینه از آرشیو محلی، چند گروه، ادامه از جای قبلی، مدیریت FloodWait، اتصال جواب‌های دیررس، حذف شماره تلفن.

## معماری
```
خزنده تلگرام ─► sources/telegram_adapter ─┐
فایل JSONL/CSV ─► sources/file_adapter ───┼─► analysis/store (inbox) ─► [ایجنت — مرحله ۲]
                                           ┘
```
هر پیام با یک `uid` یکتا (مثلاً `telegram:<group_id>:<msg_id>` یا `x:<tweet_id>`) و زمینه‌اش (`previous` / `parent` / `reply`) در صف قرار می‌گیرد.

## فایل‌های جدید
| فایل | کار |
| --- | --- |
| `analysis/schemas.py` | `SocialMessage`، `ProductProfile`، `StageCost`، `TriageResult`، `DeepAnalysis`، `AgentVerdict` |
| `analysis/config.py` | تنظیمات از `.env` (مدل‌ها، قیمت‌ها، آستانه‌ها) |
| `analysis/store.py` | صف `inbox`، جدول `verdicts`، `runs`؛ حذف تکراری (uid + اثر انگشت متن هر نویسنده)، claim/release، پخش مجدد، آمار قیف |
| `analysis/catalog.py` | کاتالوگ از فایل JSON یا API جنگو (همان شکل `products[]`) |
| `analysis/text.py` | نرمال‌سازی فارسی/عربی، اثر انگشت متن |
| `sources/telegram_adapter.py` | `LeadContext` → `SocialMessage` + ساخت لینک درست پیام |
| `sources/file_adapter.py` | خواندن JSONL/CSV و ریختن در صف (`python -m sources.file_adapter`) |
| `telegram_crawler/ratelimit.py` | `with_flood_retry` و فاصله بین درخواست‌ها |
| `data/sample/catalog.json` | کاتالوگ نمونه (دوره پایتون، عینک بلوکنترل، اسپرسوساز) |

## تغییرات خزنده تلگرام
| مورد | قبل | الان |
| --- | --- | --- |
| فیلتر | فقط پیام‌های دارای کلمه کلیدی؛ بقیه دور ریخته می‌شد | `basic`: فقط خالی/کوتاه/بات حذف می‌شود؛ تصمیم با ایجنت. حالت قدیمی با `CRAWLER_FILTER_MODE=keyword` |
| ذخیره | فقط پیام‌های هدف | **همه پیام‌ها** در جدول `messages` |
| زمینه | برای هر پیام ۳+ درخواست به تلگرام | اول از آرشیو محلی؛ فقط در کمبود از تلگرام |
| بک‌فیل | جدیدترین به قدیمی‌ترین، پردازش همزمان | اول جمع‌آوری و آرشیو، بعد پردازش از قدیمی به جدید (زمینه و جواب‌ها محلی) |
| ادامه | هر بار از اول | از `last_scanned_msg_id` (غیرفعال با `--no-resume`) |
| گروه‌ها | یک گروه | چند `--link` یا `--links-file` |
| FloodWait | مدیریت نمی‌شد | خواب و تلاش دوباره؛ وقفه بیش از حد → توقف همان مرحله بدون کرش |
| جواب‌های بعدی (لایو) | همیشه خالی | جواب‌هایی که بعداً می‌رسند به پیام در صف اضافه می‌شوند |
| تکرار | ارسال دوباره به جنگو | هر پیام فقط یک بار وارد صف می‌شود |
| شماره تلفن | ذخیره و نمایش | جمع‌آوری نمی‌شود |
| لینک پیام | گروه‌های معمولی لینک غلط | سوپرگروه خصوصی `t.me/c/<id>/<msg>`، گروه معمولی بدون لینک |
| وبهوک جنگو | فعال با نمره ثابت ۸۸ | پیش‌فرض خاموش (نتایج ایجنت در مرحله ۳ به جنگو می‌رود) |

## اجرا
```bash
pip install -r requirements.txt
cp .env.example .env                       # و telegram_crawler/.env.example → telegram_crawler/.env
# TG_API_ID / TG_API_HASH را از my.telegram.org بگذارید (با اکانت جدا، نه اکانت شخصی)

python -m telegram_crawler.main --link https://t.me/group_a --link @group_b --hours 48 --limit 500
python -m telegram_crawler.main --links-file groups.txt --no-live --quiet

# ریختن داده X / دمو در همان صف
python -m sources.file_adapter tweets.jsonl --source x
```

## تست
```bash
python -m pytest -q        # 22 تست
```
تست‌ها با کلاینت ساختگی تلگرام اجرا می‌شوند. اتصال واقعی به تلگرام اجرا نشده و باید با اکانت شما بررسی شود.
