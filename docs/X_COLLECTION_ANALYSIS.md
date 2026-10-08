# تحلیل مسیر جمع‌آوری X

## جریان فعلی و قرارداد داده

تحلیل مشترک در پوشه analysis از خزنده‌ها مستقل است. مسیر فایل sources/file_adapter.py است: iter_records() خط‌های JSONL/CSV را می‌خواند، record_to_message() آن‌ها را به analysis.schemas.SocialMessage (با Author و ContextMessage) تبدیل می‌کند و CLI در صورت فراخوانی AnalysisStore.enqueue_many() را اجرا می‌کند. فیلدهای لازم/اختیاری: id, text, source, author_id, author_handle, author_name, created_at, url, context, metadata. source در هر رکورد بر --source اولویت دارد؛ برای X باید خود JSONL مقدار source=x داشته باشد تا UID به x:<id> تبدیل شود. خطوط خالی و خطوط شروع‌شونده با // نادیده گرفته می‌شوند.

در مسیر مستقل buyer engine، buyer_engine/sources.py:XAdapter از X API v2 و bearer token استفاده می‌کند و SourceRecord(source, source_id, text, source_url, author_id, username, created_at, query_used, raw_data) برمی‌گرداند. این مسیر با SocialMessage یکی نیست؛ collector جدید عمداً مستقیماً آن را مصرف نمی‌کند و به‌جای آن JSONL را به file_adapter می‌دهد. scripts/crawlers/x_listener.py مسیر دیگری است: به Django/feed تکیه دارد و منطق ارسال پاسخ دارد؛ این تغییر به آن دست نمی‌زند.

وجود analysis/ و sources/file_adapter.py در این snapshot تأیید شد. تناقض اصلی تفاوت نام‌ها/قراردادهاست: SourceRecord.source_id/source_url/username در فایل به id/url/author_handle تبدیل می‌شود. worker این تبدیل را صریح انجام می‌دهد.

## Agent Reach و وضعیت تأیید

دسترسی شبکه برای خواندن README و docs/install.md مخزن بالادستی و اجرای twitter-cli --help در محیط پیاده‌سازی موجود نبود؛ Agent Reach نیز در محیط نصب‌شده تأیید نشد. بنابراین همه‌ی جزئیات اختصاصی CLI در این نسخه UNVERIFIED هستند: نام اجرایی twitter-cli، subcommand search، گزینه‌های --query, --limit, --json و کلیدهای احتمالی خروجی (tweets/results/data/items, id/tweet_id/rest_id, text/full_text/content, handle/name/timestamp). هیچ‌کدام را نباید دستور upstream قطعی تلقی کرد.

پیش از اتصال زنده، دستور و JSON واقعی را از agent-reach doctor و twitter-cli --help نصب محلی بررسی کنید و فقط workers/x_collector/cli_mapping.py را مطابق همان نسخه تنظیم کنید. worker command را با آرایه‌ی argument و shell=False اجرا می‌کند؛ cookieها به subprocess argument، log یا خروجی منتقل نمی‌شوند. دستور نصب upstream در README صرفاً بررسی/راه‌اندازی محیط است و نصب system با --system نیازمند اجازه‌ی صریح اپراتور است.

## نگاشت فیلد

| خروجی احتمالی CLI (UNVERIFIED) | JSONL worker | مقصد loader |
|---|---|---|
| id / tweet_id / rest_id | id | SocialMessage.uid = x:<id> |
| text / full_text / content | text | SocialMessage.text |
| username / screen_name / handle | author_handle (@...) | SocialMessage.author.handle |
| name / display_name | author_name | SocialMessage.author.display_name |
| created_at / timestamp / date | created_at | SocialMessage.created_at |
| raw ID + public handle | url = https://x.com/<handle>/status/<id> | SocialMessage.url |
| تمام فیلدهای اصلی پاسخ | metadata.raw_cli | SocialMessage.metadata |
| ثابت worker | source = x | بر مقدار --source مقدم است |

اگر handle عمومی در پاسخ نباشد، رکورد برای جلوگیری از ساخت URL ناقص کنار گذاشته می‌شود. هیچ هویت یا داده‌ی مفقودی جعل نمی‌شود.

## ریسک‌ها و کنترل‌ها

خواندن با session cookie ممکن است با شرایط X مغایر باشد و باعث invalidation نشست، محدودیت یا ban شود. cookieها secrets هستند: فقط از env امن استفاده کنید، در .gitignore داده‌ی جمع‌آوری‌شده و فایل session پوشش داده شده و هیچ‌گاه مقدارشان log نمی‌شود. از حساب burner اختصاصی استفاده کنید، سقف ۵۰ نتیجه برای query و ۵۰۰ نتیجه روزانه، jitter بین queryها، backoff نمایی و circuit breaker چهار خطای متوالی به‌طور پیش‌فرض فعال‌اند. این کنترل‌ها ریسک پلتفرم را حذف نمی‌کنند. تغییر CLI/JSON upstream شکننده است؛ خطای mapping باید صریحاً متوقف شود و اصلاح در یک لایه محدود بماند.

## انتخاب ساختاری و توصیه

worker در workers/x_collector/ قرار گرفته چون یک فرایند مستقل جمع‌آوری است، باید بدون Django import شود و خروجی پایدار فایل دارد؛ crawlerهای قدیمی اسکریپت‌های متصل به Django/feed هستند و قرار نیست منطق reply آن‌ها تغییر کند. Go مشروط برای آزمایش محلی با mock و fixture. No-go برای اجرای زنده تا زمان تأیید CLI محلی و بررسی مجازبودن روش cookie-based. برای production مسیر API رسمی X یا data provider دارای مجوز توصیه می‌شود.
