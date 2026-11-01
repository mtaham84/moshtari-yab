# گزارش فنی بازبینی moshtari-yab

**حکم اجرایی:** کد ارزش نگه‌داشتن دارد، ولی قبل از عرضه عمومی یا افزایش مشتری، مسیرهای مدیریت اکانت، ارسال پیام، آپلود و صورت‌حساب باید سخت‌سازی شوند. بازنویسی یک‌باره پیشنهاد نمی‌شود؛ اول جلوی آسیب را بگیرید و سپس مرزهای سیستم را با تست تثبیت کنید.

- مخزن: https://github.com/mtaham84/moshtari-yab
- مبنا: commit `a6aa1fcb1f9c716e49d1b462989bfb636409b106`؛ گزارش درباره همین snapshot است، نه تغییرات پس از آن.
- تاریخ بررسی: ۲۰۲۶-۱۰-۳۱.
- تغییر در کد اصلی: **هیچ فایل tracked تغییر نکرده است**. تست‌های شاهد و ابزارهای اندازه‌گیری خارج از مخزن ساخته شدند.
- ۴۲ یافته مستقل: ۲ مورد P0 بازتولیدشده، ۱ مورد P0 مشروط به اجرای بات، ۱۲ مورد P1، ۲۳ مورد P2، ۴ مورد P3. این‌ها ۴۲ آسیب‌پذیری امنیتی نیستند؛ خطای عملکردی، ریسک طراحی و نگهداشت‌پذیری نیز جداگانه آمده‌اند.

## ۱. دامنه و میزان قطعیت

تمام **۴۸۲ فایل tracked** فهرست و برحسب نوع اسکن شدند. برای تمام فایل‌های پایتون AST، طول توابع، exceptionها و کپی‌های متنی بررسی شد. مسیرهای حساس اکانت، tenant، پیام‌رسانی، محصول، LLM، billing، storage، collector و templateها بازخوانی هدفمند شدند؛ شواهد دقیق در هر یافته آمده است. **این گزارش ادعای بازبینی معنایی خط‌به‌خطِ تمام ۴۸۲ فایل، خصوصاً assets و همه migrationها، ندارد.** ضمیمه فایل‌به‌فایل صریحاً عمق بررسی هر فایل را نشان می‌دهد. «بدون یافته مستقل» به معنای سالم‌بودن قطعی نیست.

سطوح قطعیت:

1. **بازتولیدشده:** یک تست ایزوله رفتار نامطلوب فعلی را نشان می‌دهد.
2. **تأیید ایستا:** مسیر مشخص در سورس دیده شده؛ exploit یا رخداد production لزوماً آزمایش نشده است.
3. **ریسک طراحی/همزمانی:** invariant مناسب وجود ندارد؛ وقوع race در production ادعا نشده است.
4. **مشروط:** به روشن‌بودن entrypoint یا نحوه استقرار وابسته است.

تست نفوذ مرورگری، benchmark بار، اتصال به حساب واقعی شبکه اجتماعی، صحت مدل در ترافیک واقعی و اسکن تمام تاریخچه git انجام نشده‌اند. اتصال PostgreSQL محلی با UTF-8 و pgvector فراهم شد. محیط اجرا Python 3.13 است، در حالی که CI از 3.12 استفاده می‌کند؛ موفقیت در این محیط جای بازاجرای CI رسمی را نمی‌گیرد.

## ۲. تصمیم‌های فوری

| اولویت | اقدام | معیار خروج |
|---|---|---|
| توقف انتشار | مهار R01 و R02 در مدیریت اکانت و follow-up | تست دو tenant و عدم ساخت Client برای درخواست ممنوع |
| توقف قابلیت بات، اگر فعال است | R03: allowlist fail-closed | هیچ پیام غیرمجاز به handler مدیریت نرسد |
| همین حالا | R04: انتقال و چرخش credential تونل | هیچ secret واقعی در کد؛ سابقه و credential قبلی بررسی شود |
| پیش از استفاده واقعی | R05–R07 و R09–R12 | اعتبارسنجی آپلود، وضعیت صحیح سفارش، API محدود و ارسال قابل‌اعتماد |
| پیش از افزایش مصرف | R14–R18 و R31–R34 | مصرف، سهمیه، billing و retry دارای invariant و reconciliation |

P0 یعنی در این گزارش مانع انتشار؛ P1 باید پیش از استفاده جدی رفع شود؛ P2 بدهی عملکرد/طراحی مهم؛ P3 عمدتاً نگهداشت‌پذیری است. اولویت عملی را با فعال‌بودن قابلیت و حساسیت اطلاعات تطبیق دهید.

## ۳. نتیجه تست و ابزارها

نتیجه اجرای نهایی pytest: **۳۸۷ موفق، ۲ شکست، ۵ skip**. پنج skip به نبود `TEST_DATABASE_URL` در fixtureهای خاص مربوط بودند. با تنظیم صریح آن، بازاجرای سایر تست‌ها **۳۹۲ موفق و ۲ deselected** داشت؛ همان دو شکست شناخته‌شده عمداً جدا شدند و suite سبز اعلام نمی‌شود. Django **۱۹۰ تست با ۳ شکست** داشت. جزئیات نام تست‌ها، علت و فرمان بازاجرا در `test-results.md` آمده است. دو شکست pytest شامل fixture قیمت ناسازگار و mock شبکه ناسازگار با implementation است؛ سه شکست Django اختلاف رفتار/انتظار در reward، UI و validation هستند، نه پنج آسیب‌پذیری امنیتی اثبات‌شده. شمار تست‌های pytest، Django و تست‌های شاهد را با هم جمع نکنید؛ discovery و دانه‌بندی آن‌ها متفاوت و بعضاً هم‌پوشان است.

### ۳.۱ یازده شاهد اختصاصی

هر ۱۱ تست شاهد موفق شدند؛ **موفقیت اینجا یعنی وجود باگ فعلی را نشان داده‌اند، نه سلامت سیستم**. assertionها عمداً رفتار بد موجود را انتظار دارند و برای regression پس از اصلاح باید برعکس شوند. تعامل Instagram و فراخوانی LLM در این مجموعه mock شده است. هیچ پیام واقعی ارسال نشد و این مجموعه درخواست پولی واقعی نداشت.

| شاهد | یافته |
|---|---|
| follow-up با لید و sender کسب‌وکار دیگر مجاز می‌شود | R01 |
| افزودن اکانت، tenant رکورد قبلی را عوض می‌کند | R02 |
| clear cooldown به import تعریف‌نشده می‌رسد | R09 |
| handler خطای add account به logger تعریف‌نشده می‌رسد | R09 |
| endpoint ناشناس category به LLM می‌رسد | R07 |
| fallback دسته‌بندی، دسته tenant دیگر را برمی‌گرداند | R13 |
| HTML به‌عنوان تصویر محصول ذخیره می‌شود | R05 |
| سفارش بدون پرداخت PAID ثبت می‌شود | R06 |
| برداشتن تیک discovery آن را غیرفعال نمی‌کند | R19 |
| سهمیه روزانه تمام‌شده جلوی failover سالم را می‌گیرد | R16 |
| dry-run لید ارسال‌نشده را DM_SENT می‌کند | R10 |

### ۳.۲ کنترل ایستا

- `manage.py check`: بدون issue؛ این ابزار IDOR و منطق billing/ارسال را اثبات نمی‌کند.
- Ruff با تنظیمات مستقل و ruleهای حداقلی CI (`E9,F63,F7,F82`): دو F821، هر دو در R09.
- Ruff فقط خانواده F: ۱۴۶ مورد، شامل ۹۸ F401، ۲۳ F841، ۱۸ F811، ۵ F541 و همان ۲ F821. این عدد شمار باگ بحرانی نیست؛ import/variable بلااستفاده و بازتعریف نیز در آن است.
- گزارش lint وابسته به تنظیمات پوشه والد مبنای شمارش قرار نگرفت؛ فایل‌های `ruff-ci.json` و `ruff-f.json` حاصل `--isolated` هستند.

## ۴. اندازه، پیچیدگی و میزان کد تکراری

اعداد زیر با اسکریپت ضمیمه روی همان commit محاسبه شده‌اند؛ پوشش تست یا درصد خطای محصول نیستند.

| شاخص | مقدار |
|---|---:|
| فایل tracked | 482 |
| فایل Python | 337 |
| خطوط فیزیکی Python، شامل تست و migration | 52,831 |
| Python غیرتست و غیرmigration | 221 فایل / 40,340 خط |
| فایل Python تست / migration | 53 / 63 |
| HTML / CSS / JavaScript | 44 / 5 / 2 |
| Python تولیدی بیشتر از 500 خط | 18 |
| توابع تولیدی بیشتر از 100 خط | 47 |
| except عمومی در Python تولیدی | 312 |

### تکرار متنی: پاسخ دقیق به «چقدر کپی شده؟»

با پنجره ۱۰ خط غیرخالی/غیرکامنت، پس از strip فاصله، کد Python تولیدی مقایسه شد؛ تست و migration حذف شدند. اجتماع بازه‌های فیزیکی منطبق در فایل‌های متفاوت **۸۵۸ خط در ۱۵ فایل** است: حدود **۲٫۱۳٪ از ۴۰٬۳۴۰ خط فیزیکی**. هر دو سمت کپی شمرده می‌شوند و فاصله‌ها/کامنت‌های بین خطوط معنادار داخل بازه قرار دارند. بنابراین این **شاخص پوشش clone متنی با این روش** است، نه «فقط ۲٫۱۳٪ منطق تکراری»، نه تعداد خطوط قابل‌حذف و نه نتیجه SonarQube. تغییر نام متغیر و بازنویسی مشابه را نمی‌بیند؛ تکرار دامنه‌ای مهم‌تر از این عدد است.

مقایسه AST بدنه تابع، بدون docstring، ۸ گروه برابر یافت؛ یک گروه صرفاً بدنه‌های abstract/Protocol است و تکرار business logic محسوب نشد. ۷ گروه باقیمانده در جدول زیر هستند.



| تابع/منطق | دو محل | تصمیم پیشنهادی |
|---|---|---|
| `is_irrelevant_post_for_business` | `apps/discovery/post_filters.py:94–117` [^https://github.com/mtaham84/moshtari-yab/blob/a6aa1fcb1f9c716e49d1b462989bfb636409b106/apps/discovery/post_filters.py#L94-L117]؛ `instagram_engine/post_filters.py:94–113` [^https://github.com/mtaham84/moshtari-yab/blob/a6aa1fcb1f9c716e49d1b462989bfb636409b106/instagram_engine/post_filters.py#L94-L113] | انتقال فقط پس از تثبیت contract و تست خروجی؛ helper خالص shared اولویت دارد |
| `is_generic_compliment_comment` | `apps/discovery/post_filters.py:130–154` [^https://github.com/mtaham84/moshtari-yab/blob/a6aa1fcb1f9c716e49d1b462989bfb636409b106/apps/discovery/post_filters.py#L130-L154]؛ `instagram_engine/post_filters.py:126–150` [^https://github.com/mtaham84/moshtari-yab/blob/a6aa1fcb1f9c716e49d1b462989bfb636409b106/instagram_engine/post_filters.py#L126-L150] | انتقال فقط پس از تثبیت contract و تست خروجی؛ helper خالص shared اولویت دارد |
| `normalize_link` | `apps/discovery/sources.py:40–45` [^https://github.com/mtaham84/moshtari-yab/blob/a6aa1fcb1f9c716e49d1b462989bfb636409b106/apps/discovery/sources.py#L40-L45]؛ `telegram_crawler/panel.py:67–72` [^https://github.com/mtaham84/moshtari-yab/blob/a6aa1fcb1f9c716e49d1b462989bfb636409b106/telegram_crawler/panel.py#L67-L72] | انتقال فقط پس از تثبیت contract و تست خروجی؛ helper خالص shared اولویت دارد |
| `_instagram_list` | `apps/discovery/views.py:58–74` [^https://github.com/mtaham84/moshtari-yab/blob/a6aa1fcb1f9c716e49d1b462989bfb636409b106/apps/discovery/views.py#L58-L74]؛ `apps/products/views.py:17–33` [^https://github.com/mtaham84/moshtari-yab/blob/a6aa1fcb1f9c716e49d1b462989bfb636409b106/apps/products/views.py#L17-L33] | انتقال فقط پس از تثبیت contract و تست خروجی؛ helper خالص shared اولویت دارد |
| `_bounded_post_int` | `apps/discovery/views.py:77–82` [^https://github.com/mtaham84/moshtari-yab/blob/a6aa1fcb1f9c716e49d1b462989bfb636409b106/apps/discovery/views.py#L77-L82]؛ `apps/products/views.py:36–41` [^https://github.com/mtaham84/moshtari-yab/blob/a6aa1fcb1f9c716e49d1b462989bfb636409b106/apps/products/views.py#L36-L41] | انتقال فقط پس از تثبیت contract و تست خروجی؛ helper خالص shared اولویت دارد |
| `cooldown_until` | `shared/collector_kit/seen.py:218–229` [^https://github.com/mtaham84/moshtari-yab/blob/a6aa1fcb1f9c716e49d1b462989bfb636409b106/shared/collector_kit/seen.py#L218-L229]؛ `shared/collector_kit/state_store.py:159–170` [^https://github.com/mtaham84/moshtari-yab/blob/a6aa1fcb1f9c716e49d1b462989bfb636409b106/shared/collector_kit/state_store.py#L159-L170] | انتقال فقط پس از تثبیت contract و تست خروجی؛ helper خالص shared اولویت دارد |
| `parse_ss_url` | `xray/multi_proxy_manager.py:294–319` [^https://github.com/mtaham84/moshtari-yab/blob/a6aa1fcb1f9c716e49d1b462989bfb636409b106/xray/multi_proxy_manager.py#L294-L319]؛ `xray/proxy_switcher.py:222–248` [^https://github.com/mtaham84/moshtari-yab/blob/a6aa1fcb1f9c716e49d1b462989bfb636409b106/xray/proxy_switcher.py#L222-L248] | انتقال فقط پس از تثبیت contract و تست خروجی؛ helper خالص shared اولویت دارد |


### تکرار معنایی که در درصد دیده نمی‌شود

- سه مسیر اجرای LLM با قیمت‌گذاری و ثبت هزینه متفاوت: R14.
- Django و SQLAlchemy با مالکیت مبهم schema و مدل‌های مشابه: R25.
- سیاست ارسال و follow-up متفاوت: R11 و R12.
- دو state store با مسئولیت‌های نزدیک؛ تنها برابر بودن یک تابع دلیل ادغام کل store نیست.

برای helper خالص، استخراج shared کم‌خطر است؛ برای دو ORM یا سه LLM client، صرفاً حذف خطوط کافی نیست و باید مالک policy، transaction و lifecycle تعیین شود.

### تمرکز پیچیدگی

Radon cyclomatic complexity، شاخص تعداد مسیرهای تصمیم است، نه اثبات خطا. عدد کلاس با متد اشتباه گرفته نشده و رکورد تکراری متدها حذف شده است.

| محل | بازه | CC |
|---|---|---:|
| `workers/ig_collector/worker.py::_run_locked` | 418–1063 | 228 |
| `workers/x_collector/worker.py::_run_locked` | 274–476 | 90 |
| `apps/discovery/services.py::sync_instagram_lead_to_opportunity` | 103–300 | 85 |
| `apps/discovery/views.py::opportunity_export_view` | 399–577 | 85 |
| `apps/products/services.py::suggest_category_for_product` | 127–352 | 83 |
| `apps/discovery/engine_bridge.py::import_opportunity` | 70–197 | 79 |
| `apps/discovery/management/commands/run_instagram_scraper.py::_inspect_family_medias` | 1045–1250 | 73 |
| `telegram_crawler/retention.py::purge_retained_data` | 373–660 | 73 |
| `instagram_discovery/analyzer.py::analyze_post` | 323–464 | 70 |
| `scripts/config_inventory.py::collect_reads` | 75–150 | 67 |
| `apps/billing/manage_views.py::manage_dashboard` | 74–477 | 60 |
| `apps/discovery/management/commands/run_instagram_scraper.py::_scrape_target` | 1362–1695 | 59 |


اولین refactor پیشنهادی برای worker بزرگ: جداکردن انتخاب کار، رزرو کار، تماس شبکه، ثبت نتیجه، retry و accounting. تقسیم تصادفی فایل به چند فایل کوچک، بدون تغییر مرزها، پیچیدگی را فقط جابه‌جا می‌کند.

## ۵. یافته‌ها، با شواهد و معیار اصلاح


### R01 — ارسال follow-up با لید و فرستنده متعلق به کسب‌وکار دیگر

**شدت:** P0 · **قطعیت:** بازتولیدشده

**شاهد:** `apps/accounts/views.py:599–625` [^https://github.com/mtaham84/moshtari-yab/blob/a6aa1fcb1f9c716e49d1b462989bfb636409b106/apps/accounts/views.py#L599-L625]؛ `apps/discovery/outreach_service.py:444–520` [^https://github.com/mtaham84/moshtari-yab/blob/a6aa1fcb1f9c716e49d1b462989bfb636409b106/apps/discovery/outreach_service.py#L444-L520]

**ایراد:** وجود business در view بررسی می‌شود اما شناسه کسب‌وکار به سرویس منتقل نمی‌شود. سرویس لید را فقط با pk و فرستنده را فقط با username و role می‌گیرد.

**اثر:** کاربر عادی دارای کسب‌وکار می‌تواند با شناسه لید و نام اکانت دیگران، از طرف آن‌ها پیام بفرستد و وضعیت مشتری آن‌ها را تغییر دهد. login_required مانع IDOR نیست.

**اصلاح پیشنهادی:** امضای سرویس business و actor اجباری داشته باشد؛ لید و sender هر دو در QuerySet متعلق به همان tenant واکشی شوند؛ سیاست استفاده از اکانت اشتراکی جدا و صریح باشد. تست A/B و رد شدن درخواست پیش از ساخت Client.

**تست/معیار پذیرش:** test_cross_tenant_followup_is_currently_allowed


### R02 — تصاحب رکورد اکانت دیگر از مسیر افزودن اکانت

**شدت:** P0 · **قطعیت:** بازتولیدشده

**شاهد:** `apps/accounts/views.py:416–544` [^https://github.com/mtaham84/moshtari-yab/blob/a6aa1fcb1f9c716e49d1b462989bfb636409b106/apps/accounts/views.py#L416-L544]

**ایراد:** اکانت موجود با username__iexact و بدون فیلتر مالکیت پیدا می‌شود؛ سپس tenant_id و role عوض می‌شود. هویت برگشتی login_by_sessionid نیز با username ارسالی تطبیق داده نمی‌شود.

**اثر:** مالکیت رکورد و دسترسی به حساب شبکه اجتماعی به tenant دیگری منتقل می‌شود. حتی یک سشن متعلق به حساب متفاوت نباید مجوز انتقال رکورد بدهد.

**اصلاح پیشنهادی:** اگر username در tenant دیگری موجود است 403/409 بدهید. انتقال مالکیت فقط جریان مستقل مدیریتی با audit. شناسه کاربر حاصل از سشن با اکانت هدف تطبیق داده شود.

**تست/معیار پذیرش:** test_add_account_can_reassign_foreign_account


### R03 — بات مدیریت پروکسی هیچ allowlist مدیریتی ندارد

**شدت:** P0 مشروط به اجرای بات · **قطعیت:** تأیید ایستا

**شاهد:** `workers/telegram_config_bot.py:320–377` [^https://github.com/mtaham84/moshtari-yab/blob/a6aa1fcb1f9c716e49d1b462989bfb636409b106/workers/telegram_config_bot.py#L320-L377]

**ایراد:** run_bot پیام هر chat را به status/accounts/prune و دریافت پروکسی هدایت می‌کند؛ کنترل msg.from.id، chat.id یا نقش مدیر وجود ندارد.

**اثر:** اگر بات مستقر و برای دیگران قابل پیام باشد، افراد غیرمدیر می‌توانند وضعیت زیرساخت را ببینند، کانفیگ جدید وارد کنند و پاکسازی را تحریک کنند. داشتن BOT_TOKEN در env به معنای احراز هویت فرستنده نیست.

**اصلاح پیشنهادی:** allowlist عددی و fail-closed برای user و chat، محدودیت چت خصوصی/گروه مجاز، نرخ درخواست و audit. قبل از هر handler و قبل از پذیرش کانفیگ کنترل شود.

**تست/معیار پذیرش:** تست پیام از user غیرمجاز؛ هیچ handler فراخوانی نشود.


### R04 — رمز تونل پشتیبان به صورت literal در مخزن

**شدت:** P1 فوری · **قطعیت:** تأیید ایستا؛ اعتبار فعلی نامعلوم

**شاهد:** `xray/multi_proxy_manager.py:48–62` [^https://github.com/mtaham84/moshtari-yab/blob/a6aa1fcb1f9c716e49d1b462989bfb636409b106/xray/multi_proxy_manager.py#L48-L62]؛ `xray/multi_proxy_manager.py:499–509` [^https://github.com/mtaham84/moshtari-yab/blob/a6aa1fcb1f9c716e49d1b462989bfb636409b106/xray/multi_proxy_manager.py#L499-L509]

**ایراد:** DEFAULT_BACKUP_TUNNEL شامل فیلد password با مقدار غیرخالی ثابت است و هنگام نبود پروکسی فعال وارد کانفیگ می‌شود. مقدار در گزارش تکرار نشده است.

**اثر:** این مقدار باید افشاشده فرض شود؛ حذف از HEAD تاریخچه و cloneهای قبلی را پاک نمی‌کند. فعال بودن رمز یا سوءاستفاده از آن بررسی نشده است.

**اصلاح پیشنهادی:** اگر واقعی است فوراً rotate/revoke؛ secret فقط از محل امن؛ secret scanning و هماهنگی پاکسازی history. در صورت حذف fallback، رفتار قطع اتصال روشن و امن باشد.

**تست/معیار پذیرش:** تست عدم وجود literal غیرنمونه در تنظیمات؛ بررسی تاریخچه جداگانه لازم است.


### R05 — ImageField بدون اعتبارسنجی، فایل HTML را ذخیره می‌کند

**شدت:** P1 · **قطعیت:** بازتولید پذیرش فایل؛ پیامد مرورگری وابسته به استقرار

**شاهد:** `apps/products/views.py:137–219` [^https://github.com/mtaham84/moshtari-yab/blob/a6aa1fcb1f9c716e49d1b462989bfb636409b106/apps/products/views.py#L137-L219]؛ `apps/products/views.py:237–336` [^https://github.com/mtaham84/moshtari-yab/blob/a6aa1fcb1f9c716e49d1b462989bfb636409b106/apps/products/views.py#L237-L336]؛ `apps/products/models.py:273–279` [^https://github.com/mtaham84/moshtari-yab/blob/a6aa1fcb1f9c716e49d1b462989bfb636409b106/apps/products/models.py#L273-L279]؛ `config/urls.py:27–30` [^https://github.com/mtaham84/moshtari-yab/blob/a6aa1fcb1f9c716e49d1b462989bfb636409b106/config/urls.py#L27-L30]

**ایراد:** view فایل‌های request.FILES را مستقیم با objects.create ذخیره می‌کند. ImageField در Model.save محتوا را مثل ModelForm اعتبارسنجی نمی‌کند. یک فایل review-proof.html با content-type متن پذیرفته و ذخیره شد.

**اثر:** با سرو رسانه روی همان origin و تشخیص HTML، امکان میزبانی محتوای فعال و same-origin XSS مطرح است؛ همچنین ریسک مصرف دیسک و فایل غیرتصویر. exploit مرورگری اجرا نشده است.

**اصلاح پیشنهادی:** Form/ImageField واقعی + decode/re-encode امن Pillow، سقف ابعاد/بایت/تعداد، پسوند تولیدشده، origin مستقل media بدون کوکی و Content-Disposition مناسب. صرف accept=image/* در HTML کافی نیست.

**تست/معیار پذیرش:** test_html_file_is_accepted_as_product_image


### R06 — سفارش بدون هیچ پرداختی PAID و فرصت CONVERTED می‌شود

**شدت:** P1 · **قطعیت:** بازتولیدشده

**شاهد:** `apps/products/views.py:546–619` [^https://github.com/mtaham84/moshtari-yab/blob/a6aa1fcb1f9c716e49d1b462989bfb636409b106/apps/products/views.py#L546-L619]

**ایراد:** در POST کارت عمومی، ProductOrder با status=PAID ساخته می‌شود؛ تأیید درگاه، receipt یا وضعیت پرداخت جدا دیده نمی‌شود. quantity سقف تجاری ندارد.

**اثر:** فروش و درآمد گزارش‌شده قابل جعل است. اگر تحویل یا عملیات بعدی به PAID تکیه کند، خطر مالی مستقیم ایجاد می‌شود. این کد پول واقعی منتقل نمی‌کند؛ معنای وضعیت را جعل می‌کند.

**اصلاح پیشنهادی:** PENDING اولیه؛ transition به PAID فقط بعد از verification سمت سرور، مبلغ/واحد پول و شناسه پرداخت یکتا و idempotency. خرید بدون درگاه باید UNPAID/MANUAL باشد.

**تست/معیار پذیرش:** test_order_marked_paid_without_payment


### R07 — API عمومی پیشنهاد دسته می‌تواند LLM پولی را فعال کند

**شدت:** P1 · **قطعیت:** بازتولیدشده

**شاهد:** `apps/products/views.py:452–470` [^https://github.com/mtaham84/moshtari-yab/blob/a6aa1fcb1f9c716e49d1b462989bfb636409b106/apps/products/views.py#L452-L470]؛ `apps/products/services.py:303–350` [^https://github.com/mtaham84/moshtari-yab/blob/a6aa1fcb1f9c716e49d1b462989bfb636409b106/apps/products/services.py#L303-L350]

**ایراد:** api_suggest_category نه login_required دارد نه POST اجباری؛ در عدم تطبیق heuristic، chat_json فراخوانی می‌شود. فراخوانی business_id هم ندارد.

**اثر:** کاربر ناشناس می‌تواند هزینه پلتفرم و اشغال worker بسازد. برای کاربر واردشده نیز GET ممکن است auto-adopt انجام دهد؛ GET عملیات خواندنی باقی نمی‌ماند.

**اصلاح پیشنهادی:** پیشنهاد ارزان محلی را از تولید پولی جدا کنید؛ POST + CSRF + authentication، سهمیه tenant و IP، budget reservation، محدودیت طول ورودی و عدم mutation در GET.

**تست/معیار پذیرش:** test_anonymous_category_api_calls_paid_llm


### R08 — اسرار شبکه اجتماعی رمزنگاری‌نشده در DB و فایل پشتیبان

**شدت:** P1 · **قطعیت:** تأیید ایستا

**شاهد:** `apps/discovery/models.py:1040–1053` [^https://github.com/mtaham84/moshtari-yab/blob/a6aa1fcb1f9c716e49d1b462989bfb636409b106/apps/discovery/models.py#L1040-L1053]؛ `apps/discovery/models.py:1410–1414` [^https://github.com/mtaham84/moshtari-yab/blob/a6aa1fcb1f9c716e49d1b462989bfb636409b106/apps/discovery/models.py#L1410-L1414]؛ `apps/discovery/accounts_pool_file.py:22–74` [^https://github.com/mtaham84/moshtari-yab/blob/a6aa1fcb1f9c716e49d1b462989bfb636409b106/apps/discovery/accounts_pool_file.py#L22-L74]؛ `instagram_engine/models.py:56–69` [^https://github.com/mtaham84/moshtari-yab/blob/a6aa1fcb1f9c716e49d1b462989bfb636409b106/instagram_engine/models.py#L56-L69]

**ایراد:** password/auth_token/ct0 و session_json فیلدهای معمولی‌اند. snapshot همین مقادیر را در JSON و history می‌نویسد. رمزنگاری Fernet برای Provider وجود دارد، اما این اسرار را پوشش نمی‌دهد.

**اثر:** دسترسی به dump یا backup به دسترسی حساب‌های واقعی تبدیل می‌شود. chmod 0600 کنترل دسترسی است نه encryption at rest.

**اصلاح پیشنهادی:** مدل تهدید، encryption با keyring/rotation، محدودسازی دسترسی backup و مدت نگهداری، عدم نمایش در فرم/لاگ و حذف رمز وقتی سشن کافی است. در داده‌ای که باید بازیابی شود hashing جایگزین encryption نیست.

**تست/معیار پذیرش:** تست dump و snapshot نباید plaintext حساس بدهد؛ تست rotation و restore.


### R09 — دو NameError قطعی در viewهای اکانت

**شدت:** P1 · **قطعیت:** بازتولیدشده + Ruff

**شاهد:** `apps/accounts/views.py:532–532` [^https://github.com/mtaham84/moshtari-yab/blob/a6aa1fcb1f9c716e49d1b462989bfb636409b106/apps/accounts/views.py#L532-L532]؛ `apps/accounts/views.py:747–753` [^https://github.com/mtaham84/moshtari-yab/blob/a6aa1fcb1f9c716e49d1b462989bfb636409b106/apps/accounts/views.py#L747-L753]

**ایراد:** logger در ماژول تعریف/وارد نشده است. InstagramEngineAccount در clear_outreach_cooldown_view وارد نشده؛ import محلی توابع دیگر برای آن قابل استفاده نیست.

**اثر:** خطای شبکه هنگام افزودن اکانت با NameError پوشانده می‌شود؛ پاک‌کردن cooldown خطای 500 می‌دهد. این دو مورد با همان انتخاب قوانین CI نیز شناسایی می‌شوند.

**اصلاح پیشنهادی:** logger ماژولی تعریف شود؛ import مدل در scope معتبر؛ regression دو مسیر، CI اجباری branch protection.

**تست/معیار پذیرش:** test_clear_cooldown_raises_missing_import و test_add_account_error_handler_raises_missing_logger


### R10 — dry-run داده واقعی را به DM_SENT تغییر می‌دهد

**شدت:** P1 · **قطعیت:** بازتولیدشده

**شاهد:** `apps/discovery/management/commands/run_instagram_outreach.py:374–391` [^https://github.com/mtaham84/moshtari-yab/blob/a6aa1fcb1f9c716e49d1b462989bfb636409b106/apps/discovery/management/commands/run_instagram_outreach.py#L374-L391]

**ایراد:** شاخه dry_run وضعیت لید، متن/زمان ارسال و شمارنده‌های روزانه/ساعتی را ذخیره می‌کند. پیش از آن sync پاسخ‌ها و reset شمارنده نیز اجرا می‌شوند.

**اثر:** لیدهای ارسال‌نشده از صف واقعی خارج می‌شوند و سهمیه مصرف می‌شود؛ اپراتور در بررسی ظاهراً بی‌اثر، داده عملیاتی را خراب می‌کند.

**اصلاح پیشنهادی:** dry-run فقط plan برگرداند؛ Client و همه writeها غیرفعال؛ در صورت نیاز شبیه‌سازی ماندگار، sandbox/namespace جدا و نام flag متفاوت.

**تست/معیار پذیرش:** test_dry_run_currently_marks_unsent_lead_as_sent


### R11 — ارسال DM پیش از claim پایدار و بدون محافظت در برابر دو worker

**شدت:** P1 · **قطعیت:** ریسک همزمانی مستند در کد

**شاهد:** `apps/discovery/management/commands/run_instagram_outreach.py:295–304` [^https://github.com/mtaham84/moshtari-yab/blob/a6aa1fcb1f9c716e49d1b462989bfb636409b106/apps/discovery/management/commands/run_instagram_outreach.py#L295-L304]؛ `apps/discovery/management/commands/run_instagram_outreach.py:421–449` [^https://github.com/mtaham84/moshtari-yab/blob/a6aa1fcb1f9c716e49d1b462989bfb636409b106/apps/discovery/management/commands/run_instagram_outreach.py#L421-L449]؛ `instagram_engine/outreach_worker.py:210–291` [^https://github.com/mtaham84/moshtari-yab/blob/a6aa1fcb1f9c716e49d1b462989bfb636409b106/instagram_engine/outreach_worker.py#L210-L291]

**ایراد:** لید NEW انتخاب و در حافظه نگه داشته می‌شود؛ API بیرونی اجرا و بعد transaction.atomic برای update باز می‌شود. select_for_update یا compare-and-set قبل از ارسال وجود ندارد.

**اثر:** دو worker هم‌زمان می‌توانند یک لید را ارسال کنند. crash پس از موفقیت API و پیش از commit نیز ارسال را دوباره واجد شرایط می‌کند. atomic بعد از send تضمین exactly-once نیست.

**اصلاح پیشنهادی:** claim با lease و SKIP LOCKED/CAS، outbox با request_id، SENDING/SENT/UNKNOWN، reconciliation پس از timeout. اگر API idempotency ندارد exactly-once مطلق وعده ندهید.

**تست/معیار پذیرش:** تست دو worker و fault injection پس از پاسخ API و قبل commit.


### R12 — مسیر follow-up محدودیت‌های فرستنده را دور می‌زند

**شدت:** P1 · **قطعیت:** تأیید ایستا؛ ارسال mock در R01

**شاهد:** `apps/discovery/outreach_service.py:459–520` [^https://github.com/mtaham84/moshtari-yab/blob/a6aa1fcb1f9c716e49d1b462989bfb636409b106/apps/discovery/outreach_service.py#L459-L520]

**ایراد:** انتخاب sender فقط بر username و role است؛ status، cooldown، daily_limit، hourly_limit و ساعات کاری کنترل نمی‌شوند. daily_action_count با read-modify-write زیاد می‌شود و hourly count افزایش نمی‌یابد.

**اثر:** اکانت COOLDOWN/CHALLENGED می‌تواند از مسیر دستی استفاده شود؛ محدودیت ایمنی میان UI و worker متفاوت است و شمارنده در رقابت از دست می‌رود.

**اصلاح پیشنهادی:** تمام ارسال‌ها از SenderPolicy واحد با reservation اتمیک سهمیه و وضعیت عبور کنند؛ override فقط مدیر با علت و audit.

**تست/معیار پذیرش:** تست sender مسدود و سقف روزانه/ساعتی و رقابت همزمان.


### R13 — نشت دسته خصوصی میان tenantها

**شدت:** P1 · **قطعیت:** بازتولید fallback؛ endpoint تأیید ایستا

**شاهد:** `apps/products/services.py:176–182` [^https://github.com/mtaham84/moshtari-yab/blob/a6aa1fcb1f9c716e49d1b462989bfb636409b106/apps/products/services.py#L176-L182]؛ `apps/products/views.py:466–470` [^https://github.com/mtaham84/moshtari-yab/blob/a6aa1fcb1f9c716e49d1b462989bfb636409b106/apps/products/views.py#L466-L470]

**ایراد:** اگر دسته خود کسب‌وکار و دسته سیستم خالی باشد، fallback همه دسته‌های فعال همه کسب‌وکارها را می‌خواند. api_category_attributes نیز دسته را تنها با id بدون بررسی public/owner برمی‌گرداند.

**اثر:** نام/مسیر/ویژگی‌های دسته کسب‌وکار دیگر می‌تواند در پیشنهاد یا پاسخ مستقیم افشا شود.

**اصلاح پیشنهادی:** تمام branchها شرط business=actor.business OR business IS NULL داشته باشند. endpoint ویژگی‌ها هم سیاست یکسان بخواهد.

**تست/معیار پذیرش:** test_category_fallback_can_return_foreign_business_category


### R14 — سه مسیر LLM با صورتحساب و قیمت‌گذاری متفاوت

**شدت:** P1 · **قطعیت:** تأیید ایستا

**شاهد:** `need_engine/llm.py:126–315` [^https://github.com/mtaham84/moshtari-yab/blob/a6aa1fcb1f9c716e49d1b462989bfb636409b106/need_engine/llm.py#L126-L315]؛ `apps/core/llm.py:33–126` [^https://github.com/mtaham84/moshtari-yab/blob/a6aa1fcb1f9c716e49d1b462989bfb636409b106/apps/core/llm.py#L33-L126]؛ `instagram_engine/analyzer.py:130–212` [^https://github.com/mtaham84/moshtari-yab/blob/a6aa1fcb1f9c716e49d1b462989bfb636409b106/instagram_engine/analyzer.py#L130-L212]

**ایراد:** core.chat_json موفقیت را با cost_usd=0 در LLMUsageLog می‌نویسد؛ fallback آن بدون همین ثبت هم برمی‌گردد. instagram_engine برای تمام candidateها از قیمت ثابت Luna استفاده می‌کند. این‌ها از need_engine.costs و مسیر debit واحد عبور نمی‌کنند.

**اثر:** هزینه واقعی provider، هزینه داشبورد و مبلغ قابل‌کسر سازگار نیست؛ تغییر provider قیمت را لزوماً اصلاح نمی‌کند.

**اصلاح پیشنهادی:** یک LLM gateway با مدل هویتی provider+model، usage و invoice واقعی، ledger واحد و سیاست روشن هزینه پلتفرم/فروشنده. fallback و parse retry هم هزینه ثبت کنند.

**تست/معیار پذیرش:** تست قرارداد برای هر caller: usage واقعی ثبت و payer مشخص شود.


### R15 — RPM/TPM فقط محلی است و panel برای هر درخواست limiter تازه می‌سازد

**شدت:** P1 · **قطعیت:** تأیید ایستا

**شاهد:** `need_engine/llm.py:60–102` [^https://github.com/mtaham84/moshtari-yab/blob/a6aa1fcb1f9c716e49d1b462989bfb636409b106/need_engine/llm.py#L60-L102]؛ `need_engine/llm.py:129–134` [^https://github.com/mtaham84/moshtari-yab/blob/a6aa1fcb1f9c716e49d1b462989bfb636409b106/need_engine/llm.py#L129-L134]؛ `apps/core/agent.py:40–64` [^https://github.com/mtaham84/moshtari-yab/blob/a6aa1fcb1f9c716e49d1b462989bfb636409b106/apps/core/agent.py#L40-L64]

**ایراد:** RateLimiter.win/cool در حافظه instance است. panel_llm در هر ورود LLMClient جدید می‌سازد. RPD در DB ثبت می‌شود اما check و increment اتمیک واحد نیستند.

**اثر:** چند gunicorn worker، چند process و حتی درخواست‌های پیاپی پنل سقف دقیقه را هماهنگ رعایت نمی‌کنند؛ 429 و هزینه/تاخیر نامتوازن.

**اصلاح پیشنهادی:** limiter مشترک با کلید provider+credential+model، رزرو اتمیک quota در DB/Redis و TTL؛ سقف per-tenant جدا.

**تست/معیار پذیرش:** تست چند process و race در مرز quota.


### R16 — اتمام quota روزانه جلوی failover به مدل سالم را می‌گیرد

**شدت:** P2 · **قطعیت:** بازتولیدشده

**شاهد:** `need_engine/llm.py:80–89` [^https://github.com/mtaham84/moshtari-yab/blob/a6aa1fcb1f9c716e49d1b462989bfb636409b106/need_engine/llm.py#L80-L89]؛ `need_engine/llm.py:223–249` [^https://github.com/mtaham84/moshtari-yab/blob/a6aa1fcb1f9c716e49d1b462989bfb636409b106/need_engine/llm.py#L223-L249]

**ایراد:** QuotaExhausted از acquire و on_429 بیرون می‌آید و در حلقه candidateها catch نمی‌شود. در نتیجه candidate بعدی امتحان نمی‌شود.

**اثر:** با تمام‌شدن سهمیه مدل اول، fallback تبلیغ‌شده کار نمی‌کند حتی اگر provider دوم ظرفیت داشته باشد.

**اصلاح پیشنهادی:** QuotaExhausted را در سطح candidate مدیریت و cooldown روزانه را به همان مدل محدود کنید؛ اگر همه تمام شده‌اند خطای نهایی روشن بدهید.

**تست/معیار پذیرش:** test_daily_quota_prevents_healthy_model_failover


### R17 — هویت مدل در registry با schema دیتابیس ناسازگار است

**شدت:** P2 · **قطعیت:** تأیید ایستا

**شاهد:** `apps/billing/models.py:63–68` [^https://github.com/mtaham84/moshtari-yab/blob/a6aa1fcb1f9c716e49d1b462989bfb636409b106/apps/billing/models.py#L63-L68]؛ `need_engine/registry.py:59–62` [^https://github.com/mtaham84/moshtari-yab/blob/a6aa1fcb1f9c716e49d1b462989bfb636409b106/need_engine/registry.py#L59-L62]؛ `need_engine/registry.py:126–140` [^https://github.com/mtaham84/moshtari-yab/blob/a6aa1fcb1f9c716e49d1b462989bfb636409b106/need_engine/registry.py#L126-L140]

**ایراد:** دیتابیس یکتایی provider+name دارد اما snap.models فقط name را کلید می‌زند و setdefault نام تکراری provider دوم را حذف می‌کند؛ models_by_role نیز بر نام deduplicate می‌کند.

**اثر:** failover میان دو provider با یک نام مدل ناممکن یا اشتباه می‌شود؛ قیمت/endpoint به provider برنده وابسته می‌ماند.

**اصلاح پیشنهادی:** شناسه مدل مستقل یا tuple(provider_id, model_name) در registry، cache، quota و ledger.

**تست/معیار پذیرش:** دو provider با یک نام و اعتبار متفاوت بسازید؛ هر دو باید مستقل قابل انتخاب باشند.


### R18 — Embedder پس از اتمام سهمیه هرگز روز بعد بازیابی نمی‌شود

**شدت:** P2 · **قطعیت:** تأیید ایستا

**شاهد:** `need_engine/embeddings.py:20–45` [^https://github.com/mtaham84/moshtari-yab/blob/a6aa1fcb1f9c716e49d1b462989bfb636409b106/need_engine/embeddings.py#L20-L45]

**ایراد:** self.exhausted یک bool دائمی instance است؛ date/reset یا retry time ندارد.

**اثر:** process طولانی پس از یک quota error تا restart بدون embedding می‌ماند و کیفیت matching تغییر می‌کند.

**اصلاح پیشنهادی:** exhausted_until بر پایه زمان reset provider، health probe محدود و metric degraded_mode.

**تست/معیار پذیرش:** زمان را به روز بعد ببرید؛ embedding باید دوباره تلاش شود.


### R19 — checkbox اینستاگرام در ویرایش خاموش نمی‌شود

**شدت:** P2 · **قطعیت:** بازتولیدشده

**شاهد:** `apps/products/views.py:264–270` [^https://github.com/mtaham84/moshtari-yab/blob/a6aa1fcb1f9c716e49d1b462989bfb636409b106/apps/products/views.py#L264-L270]؛ `templates/products/product_form.html:1–1068` [^https://github.com/mtaham84/moshtari-yab/blob/a6aa1fcb1f9c716e49d1b462989bfb636409b106/templates/products/product_form.html#L1-L1068]

**ایراد:** ویرایش instagram_discovery_enabled فقط وقتی کلید POST وجود داشته باشد انجام می‌شود؛ checkbox تیک‌نخورده استاندارد اصلاً ارسال نمی‌شود.

**اثر:** کاربر گزینه را خاموش می‌کند اما پایش و مصرف احتمالی ادامه دارد.

**اصلاح پیشنهادی:** فرم Django یا hidden marker برای تفاوت PATCH و فرم کامل؛ در فرم کامل نبود checkbox یعنی False.

**تست/معیار پذیرش:** test_unchecking_instagram_discovery_does_not_disable_it


### R20 — اعتبارسنجی add/edit محصول یکسان نیست و choices اجرا نمی‌شوند

**شدت:** P2 · **قطعیت:** تأیید ایستا

**شاهد:** `apps/products/views.py:102–184` [^https://github.com/mtaham84/moshtari-yab/blob/a6aa1fcb1f9c716e49d1b462989bfb636409b106/apps/products/views.py#L102-L184]؛ `apps/products/views.py:250–315` [^https://github.com/mtaham84/moshtari-yab/blob/a6aa1fcb1f9c716e49d1b462989bfb636409b106/apps/products/views.py#L250-L315]؛ `apps/products/models.py:129–224` [^https://github.com/mtaham84/moshtari-yab/blob/a6aa1fcb1f9c716e49d1b462989bfb636409b106/apps/products/models.py#L129-L224]

**ایراد:** add نام و توضیح الزامی دارد؛ edit این الزام را بررسی نمی‌کند. status/product_type/discovery_priority دستی دریافت و Model.save بدون full_clean اجرا می‌شود. price با int(float(...)) parse می‌شود.

**اثر:** داده نامعتبر، اولویت خارج از ۱ تا ۵، قیمت گرد/بی‌صدا None و رفتار متفاوت CRUD. choices به‌تنهایی constraint دیتابیس نیست.

**اصلاح پیشنهادی:** ProductForm/DTO مشترک، Decimal برای قیمت، error صریح به‌جای تبدیل ورودی بد به None، DB constraint برای invariantها.

**تست/معیار پذیرش:** تست فرم کامل/ناقص، قیمت فارسی و اعشاری و حد مرزی، priority=65536 و enum ناشناخته.


### R21 — متریک عمومی read-modify-write است و سفارش idempotency ندارد

**شدت:** P2 · **قطعیت:** تأیید ایستا

**شاهد:** `apps/products/views.py:553–611` [^https://github.com/mtaham84/moshtari-yab/blob/a6aa1fcb1f9c716e49d1b462989bfb636409b106/apps/products/views.py#L553-L611]؛ `apps/discovery/models.py:1–169` [^https://github.com/mtaham84/moshtari-yab/blob/a6aa1fcb1f9c716e49d1b462989bfb636409b106/apps/discovery/models.py#L1-L169]

**ایراد:** views_count/clicks_count/orders_count/sales_amount در object زیاد و save می‌شود؛ با product_click_view که از F استفاده می‌کند متفاوت است. سفارش و متریک و conversion در atomic واحد نیستند.

**اثر:** lost update زیر بار، دوباره‌شماری با retry، سفارش تکراری و گزارش ناقص پس از خطای میانی.

**اصلاح پیشنهادی:** event/receipt یکتا، transaction برای عملیات داخلی، F برای counters؛ بازسازی متریک از source of truth و فیلتر bot/refresh در صورت نیاز.

**تست/معیار پذیرش:** تست درخواست موازی و retry یک checkout با idempotency key واحد.


### R22 — سفارش عمومی محصول غیرفعال را هم می‌پذیرد

**شدت:** P2 · **قطعیت:** تأیید ایستا

**شاهد:** `apps/products/views.py:546–552` [^https://github.com/mtaham84/moshtari-yab/blob/a6aa1fcb1f9c716e49d1b462989bfb636409b106/apps/products/views.py#L546-L552]؛ `apps/products/views.py:397–405` [^https://github.com/mtaham84/moshtari-yab/blob/a6aa1fcb1f9c716e49d1b462989bfb636409b106/apps/products/views.py#L397-L405]

**ایراد:** public_product_card و redirect محصول را فقط با pk می‌گیرند؛ وضعیت ACTIVE یا مجاز بودن public sale شرط نیست. shop برعکس ACTIVE را فیلتر می‌کند.

**اثر:** خاموش‌کردن محصول در پنل لزوماً لینک قبلی را از نمایش/ثبت سفارش خارج نمی‌کند.

**اصلاح پیشنهادی:** سیاست visibility و sellability را صریح و مشترک کنید؛ اگر inactive فقط discovery است، نام/معنا و فیلد فروش جدا داشته باشد.

**تست/معیار پذیرش:** تست لینک مستقیم محصول غیرفعال؛ انتظار محصولی باید توسط مالک روشن شود.


### R23 — فایل pitch مشترک بدون مالکیت، قفل و نوشتن اتمیک

**شدت:** P2 · **قطعیت:** تأیید ایستا

**شاهد:** `apps/accounts/views.py:373–410` [^https://github.com/mtaham84/moshtari-yab/blob/a6aa1fcb1f9c716e49d1b462989bfb636409b106/apps/accounts/views.py#L373-L410]؛ `apps/accounts/views.py:710–731` [^https://github.com/mtaham84/moshtari-yab/blob/a6aa1fcb1f9c716e49d1b462989bfb636409b106/apps/accounts/views.py#L710-L731]؛ `apps/discovery/management/commands/run_instagram_outreach.py:70–82` [^https://github.com/mtaham84/moshtari-yab/blob/a6aa1fcb1f9c716e49d1b462989bfb636409b106/apps/discovery/management/commands/run_instagram_outreach.py#L70-L82]

**ایراد:** save_pitch هر username را می‌پذیرد و sender آن کسب‌وکار را بررسی نمی‌کند. همه روی data/sarv_outreach_config.json read-modify-write می‌کنند؛ write_text فاقد lock/atomic replace است. loader هم pitches mergeشده را در حالت فایل موجود برنمی‌گرداند و cfg خام را می‌دهد.

**اثر:** تغییر متن اکانت tenant دیگر، lost update، JSON نیمه‌نوشته و رفتار ناهماهنگ defaultها.

**اصلاح پیشنهادی:** انتقال pitch به مدل tenant-scoped با FK sender، version و audit. تا آن زمان قفل تمام read-modify-write و replace اتمیک؛ merge را واقعاً در cfg ذخیره کنید.

**تست/معیار پذیرش:** تست دو writer همزمان و username خارجی و فایل ناقص.


### R24 — threadهای daemon برای کار تجاری پایدار نیستند

**شدت:** P2 · **قطعیت:** تأیید ایستا

**شاهد:** `apps/products/taxonomy.py:338–359` [^https://github.com/mtaham84/moshtari-yab/blob/a6aa1fcb1f9c716e49d1b462989bfb636409b106/apps/products/taxonomy.py#L338-L359]

**ایراد:** maybe_organize بعد از شمردن محصولات thread daemon راه می‌اندازد؛ deduplication/lock کسب‌وکار و job پایدار ندارد.

**اثر:** با restart worker کار گم می‌شود؛ چند درخواست همزمان چند LLM job و دسته‌های رقیب می‌سازند.

**اصلاح پیشنهادی:** job table/outbox یا صف پایدار؛ job key کسب‌وکار+catalog_version، lease، retry و وضعیت قابل مشاهده. نیاز فوری به microservice نیست.

**تست/معیار پذیرش:** تست restart/تکرار enqueue و همزمانی پنج محصول.


### R25 — مدل‌های موازی Django و SQLAlchemy روی جدول یکسان واگرا شده‌اند

**شدت:** P2 · **قطعیت:** تأیید ایستا

**شاهد:** `instagram_engine/models.py:51–90` [^https://github.com/mtaham84/moshtari-yab/blob/a6aa1fcb1f9c716e49d1b462989bfb636409b106/instagram_engine/models.py#L51-L90]؛ `apps/discovery/models.py:1023–1066` [^https://github.com/mtaham84/moshtari-yab/blob/a6aa1fcb1f9c716e49d1b462989bfb636409b106/apps/discovery/models.py#L1023-L1066]؛ `instagram_engine/models.py:101–139` [^https://github.com/mtaham84/moshtari-yab/blob/a6aa1fcb1f9c716e49d1b462989bfb636409b106/instagram_engine/models.py#L101-L139]؛ `apps/discovery/models.py:1174–1214` [^https://github.com/mtaham84/moshtari-yab/blob/a6aa1fcb1f9c716e49d1b462989bfb636409b106/apps/discovery/models.py#L1174-L1214]

**ایراد:** هر دو ig_accounts و leads را تعریف می‌کنند، اما ستون‌ها و statusها و defaultهای آن‌ها یکسان نیستند؛ مدل Django REPLIED/FOLLOWUP و limitهای اضافه دارد. init_db مستقل نیز وجود دارد.

**اثر:** schema ownership مبهم است؛ worker قدیمی با schema پنل می‌تواند insert ناقص/constraint error بدهد یا قواعد جدید را نادیده بگیرد. رخداد واقعی در production مشاهده نشده است.

**اصلاح پیشنهادی:** یک مالک migration و مدل domain؛ مسیر legacy را یا بازنشسته یا adapter فقط خواندنی کنید؛ contract test در برابر schema migrateشده.

**تست/معیار پذیرش:** schema diff خودکار و smoke test اجرای هر entrypoint در image رسمی.


### R26 — وابستگی‌ها قفل نیستند و image ریشه کل مسیر legacy را پوشش نمی‌دهد

**شدت:** P2 · **قطعیت:** تأیید ایستا

**شاهد:** `requirements.txt:1–26` [^https://github.com/mtaham84/moshtari-yab/blob/a6aa1fcb1f9c716e49d1b462989bfb636409b106/requirements.txt#L1-L26]؛ `instagram_engine/requirements.txt:1–22` [^https://github.com/mtaham84/moshtari-yab/blob/a6aa1fcb1f9c716e49d1b462989bfb636409b106/instagram_engine/requirements.txt#L1-L22]؛ `Dockerfile:1–34` [^https://github.com/mtaham84/moshtari-yab/blob/a6aa1fcb1f9c716e49d1b462989bfb636409b106/Dockerfile#L1-L34]

**ایراد:** اکثر dependencyها lower bound باز دارند؛ Django بازه ۵.۲ تا قبل ۷ و instagrapi بدون سقف است. SQLAlchemy و psycopg2 مسیر legacy در requirements اصلی نیستند.

**اثر:** build امروز و فردا رفتار متفاوت دارد؛ اجرای CLI legacy در image ریشه ممکن است import failure بدهد. محیط بررسی هم Python 3.13 بود و عین CI 3.12 نیست.

**اصلاح پیشنهادی:** lock/constraints قابل بازتولید، محدوده Python و ماتریس تست مشخص؛ extra جدا برای entrypointهای واقعاً پشتیبانی‌شده، SBOM و audit وابستگی.

**تست/معیار پذیرش:** build پاک image و smoke test همه commandهای پشتیبانی‌شده؛ نصب دقیق نسخه‌های lock.


### R27 — پوشش تست پیش‌فرض برخی مسیرها را حذف کرده است

**شدت:** P2 · **قطعیت:** تأیید ایستا + تست

**شاهد:** `pytest.ini:1–7` [^https://github.com/mtaham84/moshtari-yab/blob/a6aa1fcb1f9c716e49d1b462989bfb636409b106/pytest.ini#L1-L7]؛ `.github/workflows/ci.yml:40–67` [^https://github.com/mtaham84/moshtari-yab/blob/a6aa1fcb1f9c716e49d1b462989bfb636409b106/.github/workflows/ci.yml#L40-L67]؛ `need_engine/tests/test_golden_eval.py:48–55` [^https://github.com/mtaham84/moshtari-yab/blob/a6aa1fcb1f9c716e49d1b462989bfb636409b106/need_engine/tests/test_golden_eval.py#L48-L55]؛ `need_engine/eval/golden.py:70–92` [^https://github.com/mtaham84/moshtari-yab/blob/a6aa1fcb1f9c716e49d1b462989bfb636409b106/need_engine/eval/golden.py#L70-L92]؛ `need_engine/tests/test_registry.py:99–130` [^https://github.com/mtaham84/moshtari-yab/blob/a6aa1fcb1f9c716e49d1b462989bfb636409b106/need_engine/tests/test_registry.py#L99-L130]؛ `need_engine/llm.py:152–168` [^https://github.com/mtaham84/moshtari-yab/blob/a6aa1fcb1f9c716e49d1b462989bfb636409b106/need_engine/llm.py#L152-L168]

**ایراد:** pytest.ini مسیر instagram_engine/tests و tests ریشه و اسکریپت test_dedup_and_semantic.py را ندارد. Django app tests جدا اجرا می‌شوند که نکته مثبت است. تست golden قیمت Gemini می‌دهد ولی مدل پیش‌فرض Luna است و estimate صفر می‌شود. تست registry نیز urllib.request.urlopen را mock می‌کند اما implementation از build_opener().open استفاده می‌کند؛ در نتیجه fixture از شبکه ایزوله نیست و به b.example می‌رود. این شکست اثبات خرابی endpoint واقعی نیست.

**اثر:** عدد pass به معنای پوشش همه زیرسیستم‌ها نیست؛ تست هزینه از config فعلی جا مانده است. اسکریپت ریشه هنگام import دیتابیس واقعی می‌خواند و unit test پاک نیست.

**اصلاح پیشنهادی:** تعیین taxonomy تست‌ها، collect-only gate، تست‌های tenant isolation/ارسال/dry-run و failure injection. تست قیمت model و prices را هماهنگ و صفر ناشناخته را خطا/هشدار کنید.

**تست/معیار پذیرش:** نتیجه اجرای واقعی در بخش تست‌ها آمده است؛ شکست‌ها با محیط تفکیک شده‌اند.


### R28 — احراز هویت دستی و ثبت‌نام demo در مسیر اصلی

**شدت:** P2 · **قطعیت:** تأیید ایستا

**شاهد:** `apps/accounts/views.py:15–91` [^https://github.com/mtaham84/moshtari-yab/blob/a6aa1fcb1f9c716e49d1b462989bfb636409b106/apps/accounts/views.py#L15-L91]؛ `apps/accounts/views.py:94–151` [^https://github.com/mtaham84/moshtari-yab/blob/a6aa1fcb1f9c716e49d1b462989bfb636409b106/apps/accounts/views.py#L94-L151]؛ `config/settings.py:1–212` [^https://github.com/mtaham84/moshtari-yab/blob/a6aa1fcb1f9c716e49d1b462989bfb636409b106/config/settings.py#L1-L212]

**ایراد:** signup بدون verification فعال کاربر را login می‌کند؛ فقط طول پسورد و بررسی بسیار ساده ایمیل دارد؛ password strip می‌شود. User و Business در تراکنش واحد ساخته نمی‌شوند. login به‌جای authenticate مستقیماً check_password می‌کند و throttle در این مسیر/تنظیمات بررسی‌شده دیده نشد.

**اثر:** سیاست password/backendهای امنیتی متمرکز قابل اتکا نیست، race ثبت‌نام می‌تواند 500 بدهد، حساب بدون Business باقی بماند و abuse آسان‌تر شود. زیرساخت reverse proxy بررسی نشده است.

**اصلاح پیشنهادی:** Form و validate_email/validate_password، authenticate، transaction و مدیریت IntegrityError، یکتایی case-insensitive ایمیل در DB، throttle و verification متناسب با محصول؛ رمز را strip نکنید.

**تست/معیار پذیرش:** تست پسورد با فاصله، account inactive، rate limit و دو signup همزمان.


### R29 — خروجی نامطمئن در innerHTML قالب‌ها

**شدت:** P2 · **قطعیت:** تأیید ایستا

**شاهد:** `templates/accounts/dashboard.html:333–362` [^https://github.com/mtaham84/moshtari-yab/blob/a6aa1fcb1f9c716e49d1b462989bfb636409b106/templates/accounts/dashboard.html#L333-L362]؛ `templates/accounts/outreach.html:731–742` [^https://github.com/mtaham84/moshtari-yab/blob/a6aa1fcb1f9c716e49d1b462989bfb636409b106/templates/accounts/outreach.html#L731-L742]؛ `templates/ops/manage_dashboard.html:2885–2891` [^https://github.com/mtaham84/moshtari-yab/blob/a6aa1fcb1f9c716e49d1b462989bfb636409b106/templates/ops/manage_dashboard.html#L2885-L2891]؛ `templates/products/public_product_card.html:324–343` [^https://github.com/mtaham84/moshtari-yab/blob/a6aa1fcb1f9c716e49d1b462989bfb636409b106/templates/products/public_product_card.html#L324-L343]

**ایراد:** نام محصول/دسته مستقیماً در template string قرار می‌گیرد. title و badge خروجی LLM escape نمی‌شوند؛ در پنل مدیریت p.text هم خام و داخل onclick جایگذاری می‌شود. customer_name پاسخ سفارش در HTML تزریق می‌شود.

**اثر:** مسیرهای DOM/stored/reflected XSS بالقوه با دامنه اثر متفاوت؛ همه innerHTMLها آسیب‌پذیر نیستند و برخی داده‌ها درست escape شده‌اند. exploit مرورگری انجام نشده است.

**اصلاح پیشنهادی:** textContent و createElement، حذف inline onclick و تزریق رشته، json_script صحیح و CSP لایه دفاعی. تست payload در نام محصول/LLM/سفارش.

**تست/معیار پذیرش:** تست مرورگری مستقل لازم؛ سورس تا sink برای موارد فوق بررسی شده است.


### R30 — json_script داخل attribute HTML استفاده شده است

**شدت:** P2 · **قطعیت:** تأیید ایستا

**شاهد:** `templates/products/product_form.html:210–220` [^https://github.com/mtaham84/moshtari-yab/blob/a6aa1fcb1f9c716e49d1b462989bfb636409b106/templates/products/product_form.html#L210-L220]

**ایراد:** data-attrs تک‌نقل‌قولی خروجی json_script را دریافت می‌کند؛ خروجی این filter یک script tag کامل است نه مقدار مناسب attribute. safe این قرارداد را اصلاح نمی‌کند.

**اثر:** DOM و parsing داده attributes می‌تواند خراب شود؛ داده‌هایی با apostrophe/متن خاص مرز attribute را مختل می‌کنند.

**اصلاح پیشنهادی:** یک json_script مستقل با id بیرون از option و map بر اساس category_id؛ از JSON encode دستی در attribute پرهیز شود.

**تست/معیار پذیرش:** قالب را با quote و apostrophe و متن Unicode رندر و DOM آن را parse کنید.


### R31 — cursor عددی صورت‌حساب تضمین ترتیب commit نیست

**شدت:** P2 · **قطعیت:** ریسک همزمانی/طراحی

**شاهد:** `apps/billing/services.py:114–153` [^https://github.com/mtaham84/moshtari-yab/blob/a6aa1fcb1f9c716e49d1b462989bfb636409b106/apps/billing/services.py#L114-L153]؛ `need_engine/store.py:355–371` [^https://github.com/mtaham84/moshtari-yab/blob/a6aa1fcb1f9c716e49d1b462989bfb636409b106/need_engine/store.py#L355-L371]

**ایراد:** MAX(id) به‌عنوان high-water mark و grace زمانی استفاده می‌شود؛ ترتیب sequence الزاماً ترتیب commit نیست. پس از advance، cost دیر commitشده با id کوچک‌تر دیده نمی‌شود. add_cost نیز برای چند payer insertهای جداگانه autocommit دارد.

**اثر:** احتمال هزینه ثبت‌شده اما کسرنشده یا ledger چندپرداخت‌کننده ناقص در قطع اتصال. grace احتمال را کم می‌کند اما invariant ریاضی ایجاد نمی‌کند.

**اصلاح پیشنهادی:** claim/processed marker یا charge mapping یکتا به cost_id و پردازش unbilled rows؛ settlement و reconciliation به‌جای اتکا به زمان grace.

**تست/معیار پذیرش:** تست دو connection با commit خارج از ترتیب و crash میان payerها؛ end-to-end این رقابت اجرا نشده است.


### R32 — هزینه مصرف‌شده بیش از موجودی بی‌صدا clip و نهایی می‌شود

**شدت:** P2 · **قطعیت:** تأیید ایستا

**شاهد:** `apps/billing/services.py:41–65` [^https://github.com/mtaham84/moshtari-yab/blob/a6aa1fcb1f9c716e49d1b462989bfb636409b106/apps/billing/services.py#L41-L65]؛ `apps/billing/services.py:138–152` [^https://github.com/mtaham84/moshtari-yab/blob/a6aa1fcb1f9c716e49d1b462989bfb636409b106/apps/billing/services.py#L138-L152]؛ `need_engine/registry.py:146–158` [^https://github.com/mtaham84/moshtari-yab/blob/a6aa1fcb1f9c716e49d1b462989bfb636409b106/need_engine/registry.py#L146-L158]

**ایراد:** موجودی در snapshot خوانده می‌شود، مصرف قبل از settle رخ می‌دهد و debit تا موجودی clip می‌شود. cursor از همه هزینه‌ها عبور می‌کند حتی اگر charge_usage هیچ تراکنشی نسازد.

**اثر:** هزینه provider پرداخت می‌شود اما مابه‌التفاوت در بدهی/صورت‌حساب پیگیری نمی‌شود. ممکن است سیاست عمدی عدم بدهکارکردن باشد، اما باید explicit write-off باشد نه پنهان‌شدن هزینه.

**اصلاح پیشنهادی:** رزرو پیش از مصرف، settle پس از usage، write-off ledger شفاف و سقف hard spend؛ رفتار cached و enforce_balance مستند و تست شود.

**تست/معیار پذیرش:** تست چند مصرف همزمان با موجودی نزدیک صفر؛ مجموع cost=charged+reserved+writeoff.


### R33 — DDL در مسیر ساخت Store هر درخواست پنل

**شدت:** P2 · **قطعیت:** تأیید ایستا

**شاهد:** `need_engine/store.py:29–67` [^https://github.com/mtaham84/moshtari-yab/blob/a6aa1fcb1f9c716e49d1b462989bfb636409b106/need_engine/store.py#L29-L67]؛ `need_engine/store.py:78–89` [^https://github.com/mtaham84/moshtari-yab/blob/a6aa1fcb1f9c716e49d1b462989bfb636409b106/need_engine/store.py#L78-L89]؛ `apps/core/agent.py:40–64` [^https://github.com/mtaham84/moshtari-yab/blob/a6aa1fcb1f9c716e49d1b462989bfb636409b106/apps/core/agent.py#L40-L64]

**ایراد:** Store(...create=True) کل schema CREATE/ALTER را اجرا می‌کند؛ پنل هر بار Store تازه می‌سازد.

**اثر:** تاخیر و lock/catalog contention و نیاز runtime role به مجوز DDL/extension؛ تست/استقرار هم به‌سختی از runtime جدا می‌شود.

**اصلاح پیشنهادی:** migration/ bootstrap مستقل برای schema موتور؛ runtime با حداقل مجوز؛ pool اتصال و init یک‌بار خارج از request.

**تست/معیار پذیرش:** load test پنل با SQL trace؛ تعداد DDL در request باید صفر باشد.


### R34 — retry خودکار نوشتن پس از قطع اتصال نتیجه نامعلوم را تکرار می‌کند

**شدت:** P2 · **قطعیت:** تأیید ایستا

**شاهد:** `need_engine/store.py:97–106` [^https://github.com/mtaham84/moshtari-yab/blob/a6aa1fcb1f9c716e49d1b462989bfb636409b106/need_engine/store.py#L97-L106]

**ایراد:** _run روی OperationalError دوباره همان fn را اجرا می‌کند؛ برای INSERT هزینه بدون کلید idempotency معلوم نیست query اول در سرور commit شده یا نه.

**اثر:** در قطع پاسخ پس از commit، cost می‌تواند دوباره ثبت شود. این الگو برای خواندن امن‌تر از نوشتن است.

**اصلاح پیشنهادی:** تفکیک retry read/write؛ operation_id یکتا و upsert معتبر؛ وضعیت ambiguous commit را به reconciliation بسپارید.

**تست/معیار پذیرش:** fault injection قطع اتصال پس از commit؛ یک عملیات باید یک ledger item داشته باشد.


### R35 — قفل و invariant تصویر اصلی در دیتابیس وجود ندارد

**شدت:** P2 · **قطعیت:** تأیید ایستا

**شاهد:** `apps/products/models.py:295–313` [^https://github.com/mtaham84/moshtari-yab/blob/a6aa1fcb1f9c716e49d1b462989bfb636409b106/apps/products/models.py#L295-L313]؛ `apps/products/views.py:420–434` [^https://github.com/mtaham84/moshtari-yab/blob/a6aa1fcb1f9c716e49d1b462989bfb636409b106/apps/products/views.py#L420-L434]

**ایراد:** خاموش‌کردن تصویرهای قبلی و save تصویر جدید transaction/unique constraint مشترک ندارد. حذف آخرین main و انتخاب جایگزین نیز چندمرحله‌ای است.

**اثر:** در درخواست‌های موازی صفر یا چند main محتمل است؛ حذف فایل از QuerySet.delete/cascade لزوماً متد instance.delete را اجرا نمی‌کند و orphan media می‌ماند.

**اصلاح پیشنهادی:** UniqueConstraint مشروط product وقتی is_main=True، transaction با قفل product، cleanup فایل بعد از commit و sweeper امن.

**تست/معیار پذیرش:** تست دو set-main همزمان، cascade محصول و rollback حذف.


### R36 — N+1 در دسته‌بندی و واکشی درخت

**شدت:** P2 · **قطعیت:** تأیید ایستا

**شاهد:** `apps/products/services.py:190–204` [^https://github.com/mtaham84/moshtari-yab/blob/a6aa1fcb1f9c716e49d1b462989bfb636409b106/apps/products/services.py#L190-L204]؛ `apps/products/models.py:111–123` [^https://github.com/mtaham84/moshtari-yab/blob/a6aa1fcb1f9c716e49d1b462989bfb636409b106/apps/products/models.py#L111-L123]؛ `apps/accounts/shop.py:27–35` [^https://github.com/mtaham84/moshtari-yab/blob/a6aa1fcb1f9c716e49d1b462989bfb636409b106/apps/accounts/shop.py#L27-L35]

**ایراد:** get_full_path/get_ancestors زنجیره parent را طی می‌کند؛ select_related(parent) تنها یک سطح را بارگذاری می‌کند و scoring برای هر دسته انجام می‌شود. shop با children.all در حلقه درخت را می‌گردد.

**اثر:** با رشد دسته‌ها تعداد query و latency API پیشنهاد بالا می‌رود؛ ریسک هزینه CPU/DB در endpoint عمومی بیشتر می‌شود.

**اصلاح پیشنهادی:** full_path ذخیره‌شده معتبر یا map درخت در یک query، batch/prefetch، تست assertNumQueries و benchmark.

**تست/معیار پذیرش:** تست درخت ۵ سطحی با تعداد دسته رو به رشد.


### R37 — category invariant فقط در clean کامل است ولی save آن را اجرا نمی‌کند

**شدت:** P2 · **قطعیت:** تأیید ایستا

**شاهد:** `apps/products/models.py:74–123` [^https://github.com/mtaham84/moshtari-yab/blob/a6aa1fcb1f9c716e49d1b462989bfb636409b106/apps/products/models.py#L74-L123]؛ `apps/products/taxonomy.py:225–244` [^https://github.com/mtaham84/moshtari-yab/blob/a6aa1fcb1f9c716e49d1b462989bfb636409b106/apps/products/taxonomy.py#L225-L244]

**ایراد:** save خودارجاعی و depth را چک می‌کند اما clean چرخه ancestor را بررسی می‌کند و save آن را صدا نمی‌زند. get_ancestors بدون visited تا parent=None می‌رود.

**اثر:** اگر از admin/import/update مسیر دورزننده ساخته شود، چرخه می‌تواند حلقه بی‌نهایت و خرابی full_path بدهد. taxonomy helper برخی مسیرها را بهتر پوشش می‌دهد؛ نقص به همه مسیرها تعمیم داده نشده است.

**اصلاح پیشنهادی:** مالک واحد تغییر درخت، visited set دفاعی، validation مالکیت والد و refresh فرزندان؛ آزمون حلقه دوگرهی و update_fields.

**تست/معیار پذیرش:** تست programmatic save و bulk update؛ نیاز به تصمیم constraint/trigger متناسب با DB.


### R38 — استقرار مخصوص مشتری و بازتولیدپذیری ضعیف compose

**شدت:** P2 · **قطعیت:** تأیید ایستا

**شاهد:** `docker-compose.yml:11–34` [^https://github.com/mtaham84/moshtari-yab/blob/a6aa1fcb1f9c716e49d1b462989bfb636409b106/docker-compose.yml#L11-L34]؛ `docker-compose.yml:139–167` [^https://github.com/mtaham84/moshtari-yab/blob/a6aa1fcb1f9c716e49d1b462989bfb636409b106/docker-compose.yml#L139-L167]؛ `docker/entrypoint.sh:40–45` [^https://github.com/mtaham84/moshtari-yab/blob/a6aa1fcb1f9c716e49d1b462989bfb636409b106/docker/entrypoint.sh#L40-L45]

**ایراد:** ig-outreach به tenant_4 hardcode است؛ alias sarv و نام/پیام دندان‌پزشکی در مسیر عمومی کد وجود دارد. کد از bind mount می‌آید اما staticfiles در زمان build تولید شده است. sync یک process پس‌زمینه را بدون supervisor اجرا می‌کند.

**اثر:** tenant جدید فراموش می‌شود؛ image tag نسخه واقعی کد را تضمین نمی‌کند؛ static/template ناهمخوان و مرگ sync_x_status ممکن است با container زنده پنهان بماند.

**اصلاح پیشنهادی:** config tenant-scoped، profile تولید immutable بدون bind mount، image digest و migration job مستقل، سرویس مستقل sync_x_status و healthcheck برای هر worker.

**تست/معیار پذیرش:** smoke deployment برای tenant تازه و restart هر زیرفرآیند.


### R39 — ابر‌فایل‌ها و توابع با پیچیدگی بسیار بالا

**شدت:** P3 · **قطعیت:** اندازه‌گیری ایستا

**شاهد:** `workers/ig_collector/worker.py:418–1063` [^https://github.com/mtaham84/moshtari-yab/blob/a6aa1fcb1f9c716e49d1b462989bfb636409b106/workers/ig_collector/worker.py#L418-L1063]؛ `apps/billing/manage_views.py:74–477` [^https://github.com/mtaham84/moshtari-yab/blob/a6aa1fcb1f9c716e49d1b462989bfb636409b106/apps/billing/manage_views.py#L74-L477]؛ `apps/discovery/management/commands/run_instagram_scraper.py:1–1695` [^https://github.com/mtaham84/moshtari-yab/blob/a6aa1fcb1f9c716e49d1b462989bfb636409b106/apps/discovery/management/commands/run_instagram_scraper.py#L1-L1695]

**ایراد:** Radon برای _run_locked اینستاگرام CC=228، برای X CC=90 و برای چند تابع دامنه ۷۰ تا ۸۵ گزارش می‌کند. CC درصد کیفیت نیست؛ تعداد مسیرهای تصمیم است.

**اثر:** تغییر کوچک روی flowهای متعدد اثر می‌گذارد و تست رگرسیون ناقص می‌ماند؛ 646 خط در یک تابع بار شناختی بالا دارد.

**اصلاح پیشنهادی:** پس از تثبیت باگ‌ها، use-caseهای کوچک validate/plan/fetch/evaluate/persist/report و state machine صریح؛ refactor رفتاری در PRهای کوچک با characterization tests.

**تست/معیار پذیرش:** رفتار قبل/بعد روی fixtureهای فعلی یکسان بماند؛ complexity gate افزایشی نه تعطیل‌کردن CI روی همه بدهی قدیمی.


### R40 — تکرار متنی و منطق موازی؛ درصد کلی جایگزین تحلیل دامنه نیست

**شدت:** P3 · **قطعیت:** اندازه‌گیری ایستا

**شاهد:** `apps/discovery/post_filters.py:1–156` [^https://github.com/mtaham84/moshtari-yab/blob/a6aa1fcb1f9c716e49d1b462989bfb636409b106/apps/discovery/post_filters.py#L1-L156]؛ `instagram_engine/post_filters.py:1–152` [^https://github.com/mtaham84/moshtari-yab/blob/a6aa1fcb1f9c716e49d1b462989bfb636409b106/instagram_engine/post_filters.py#L1-L152]؛ `xray/multi_proxy_manager.py:127–319` [^https://github.com/mtaham84/moshtari-yab/blob/a6aa1fcb1f9c716e49d1b462989bfb636409b106/xray/multi_proxy_manager.py#L127-L319]؛ `xray/proxy_switcher.py:45–248` [^https://github.com/mtaham84/moshtari-yab/blob/a6aa1fcb1f9c716e49d1b462989bfb636409b106/xray/proxy_switcher.py#L45-L248]

**ایراد:** دو post_filters تقریباً جز کامنت/docstring یکسان‌اند؛ parserهای xray و helper فرم نیز کپی‌اند. اما تکرار مهم‌تر سه gateway مدل، دو ORM و چند sender policy است که clone متنی کامل نیست.

**اثر:** اصلاح باگ در یک نسخه به نسخه دیگر نمی‌رسد؛ abstraction شتابزده نیز ممکن است مسئولیت‌های متفاوت را غلط یکی کند.

**اصلاح پیشنهادی:** پاکسازی cloneهای محض به shared؛ تصمیم مالکیت برای منطق هم‌پوشان؛ Protocol و adapter مشابه را خودکار duplicate business logic تلقی نکنید.

**تست/معیار پذیرش:** گروه‌های clone و روش اندازه‌گیری در بخش مستقل و JSON ضمیمه آمده است.


### R41 — بلعیدن exception و پیام موفقیت کاذب

**شدت:** P3 · **قطعیت:** تأیید ایستا

**شاهد:** `apps/products/views.py:211–219` [^https://github.com/mtaham84/moshtari-yab/blob/a6aa1fcb1f9c716e49d1b462989bfb636409b106/apps/products/views.py#L211-L219]؛ `apps/products/views.py:329–338` [^https://github.com/mtaham84/moshtari-yab/blob/a6aa1fcb1f9c716e49d1b462989bfb636409b106/apps/products/views.py#L329-L338]؛ `apps/core/llm.py:90–102` [^https://github.com/mtaham84/moshtari-yab/blob/a6aa1fcb1f9c716e49d1b462989bfb636409b106/apps/core/llm.py#L90-L102]؛ `apps/discovery/management/commands/sync_opportunities.py:36–56` [^https://github.com/mtaham84/moshtari-yab/blob/a6aa1fcb1f9c716e49d1b462989bfb636409b106/apps/discovery/management/commands/sync_opportunities.py#L36-L56]

**ایراد:** در چند مسیر جزئیات خطا حذف می‌شود و کاربر موفقیت کلی می‌بیند؛ ذخیره تصویر و ثبت usage از نمونه‌هاست. در مقابل برخی swallowها برای heartbeat/best-effort آگاهانه و موجه‌اند.

**اثر:** داده ناقص و هزینه نامعلوم بدون signal؛ عیب‌یابی وابسته به تکرار دستی. یک payload خراب نیز import حلقه فرصت را متوقف می‌کند.

**اصلاح پیشنهادی:** خطای domain نوع‌دار، logging ساختاری با correlation/tenant و redaction، partial result صریح، dead-letter برای poison payload و alert برای ledger failure.

**تست/معیار پذیرش:** تست خرابی storage/DB/model و مشاهده پیام/metric مناسب؛ به‌جای حذف همه exceptها طبقه‌بندی کنید.


### R42 — مستندات معماری و CI enforcement عقب‌تر از وضعیت فعلی‌اند

**شدت:** P3 · **قطعیت:** تأیید ایستا

**شاهد:** `docs/architecture/CLEANUP_CANDIDATES.md:1–46` [^https://github.com/mtaham84/moshtari-yab/blob/a6aa1fcb1f9c716e49d1b462989bfb636409b106/docs/architecture/CLEANUP_CANDIDATES.md#L1-L46]؛ `docs/architecture/BOUNDARIES.md:1–38` [^https://github.com/mtaham84/moshtari-yab/blob/a6aa1fcb1f9c716e49d1b462989bfb636409b106/docs/architecture/BOUNDARIES.md#L1-L38]؛ `.github/workflows/ci.yml:54–67` [^https://github.com/mtaham84/moshtari-yab/blob/a6aa1fcb1f9c716e49d1b462989bfb636409b106/.github/workflows/ci.yml#L54-L67]

**ایراد:** مستند cleanup برخی اندازه‌ها و تعداد ۲۸۵ فایل را ذکر می‌کند، درحالی‌که این snapshot ۴۸۲ فایل دارد. BOUNDARIES از tests/test_architecture.py به‌عنوان باید نوشته شود یاد می‌کند، ولی چنین تستی در فایل‌های tracked نیست.

**اثر:** خواننده ممکن است proposal را با guard اجرایی اشتباه بگیرد و refactor بر فرض‌های قدیمی انجام دهد.

**اصلاح پیشنهادی:** تفکیک current/proposed، تولید خودکار inventory/import graph، تست boundary واقعی و allowlist موقتِ قابل پیگیری.

**تست/معیار پذیرش:** وجود test و failure آن برای import ممنوع عمدی باید در CI بررسی شود.



## ۶. معماری پیشنهادی؛ بدون بازنویسی فراگیر

### مرزهای لازم

1. **Tenant context در مرز سرویس:** actor و business ورودی الزامی عملیات مدیریتی باشند. login و انتخاب business در view جای scoping در سرویس را نمی‌گیرد. دسترسی shared account به‌عنوان policy صریح تعریف شود.
2. **ارسال پیام به شکل state machine:** `NEW → CLAIMED → SENDING → SENT / FAILED / UNKNOWN`. claim با lease و transaction کوتاه انجام شود؛ تماس شبکه خارج از transaction طولانی. نتیجه مبهم را بدون بررسی دوباره ارسال نکنید. برای سرویس خارجی بدون idempotency تضمین exactly-once ندهید.
3. **یک gateway LLM:** هویت مدل شامل provider و model؛ خروجی یکسان برای usage، نرخ، قیمت و خطا؛ reservation/settlement و quota با state مشترک بین processها. تفاوت معنای cache hit و هزینه قابل‌صورتحساب صریح باشد.
4. **یک مالک schema:** Django migrations یا migration مستقل رسمی؛ نه چند ORM که هر کدام جدول مشترک را «درست» فرض کنند. legacy تا زمان contract test و مسیر جایگزین حذف نشود.
5. **کار پایدار:** thread داخل request با صف durable و idempotent job جایگزین شود. transaction، retry، وضعیت و monitor داشته باشد.
6. **رابط وب امن:** فرم‌های add/edit یک schema اعتبارسنجی مشترک داشته باشند؛ validation آپلود مستقل از نام فیلد ImageField باشد؛ داده به DOM با textContent برسد.

### تست‌های محافظ ضروری

- ماتریس tenant A/B برای هر endpoint دارای object id، username، product/category/lead/account؛ مالک، غیرمالک، ناشناس، operator مجاز و shared policy.
- dry-run: snapshot دیتابیس قبل و بعد باید برای stateهای عملیاتی و quota برابر بماند؛ auditِ «شبیه‌سازی» جدا و مجاز باشد.
- دو worker همزمان روی یک lead؛ timeout پس از پذیرش درخواست خارجی؛ crash بین send و commit؛ recovery از lease منقضی.
- LLM: تمام‌شدن RPD فقط یک مدل، quota همزمان دو process، providerهای هم‌نام، قیمت ناشناخته، cache و tokenهای واقعی.
- billing: commit خارج از ترتیب، قطع اتصال بعد از commit، صفرشدن موجودی حین مصرف، تطبیق cost و charge/write-off.
- upload: MIME، decode واقعی تصویر، HTML/SVG فعال، فایل بزرگ، فایل خراب، تغییر نام پسوند؛ ذخیره در origin جدا و header امن.
- سفارش: create فقط `PENDING`؛ پرداخت و تبدیل فقط با منبع معتبر؛ webhook تکراری و درخواست موازی شمارش را دوباره افزایش ندهد.
- CI: lint حداقلی، هر دو runner تست، DB UTF-8+pgvector، collect-only برای همه زیرسیستم‌های فعال؛ تست محیط-dependent از unit جدا باشد.

## ۷. نقشه اصلاح مرحله‌ای

این زمان‌بندی ترتیب کار است، نه تعهد زمانی؛ ظرفیت تیم، استقرار فعال و نیاز محصول تعیین‌کننده‌اند.

### فاز صفر — مهار آسیب

- قابلیت‌های R01–R03 را محدود یا موقتاً خاموش کنید؛ tenant scoping و allowlist را با تست تثبیت کنید.
- credential موجود در سورس را معتبر فرض نکنید؛ اعتبار را بررسی و در صورت واقعی‌بودن rotate/revoke کنید؛ مقدار آن در گزارش بازنشر نشده است.
- دو NameError، dry-run و upload را اصلاح کنید؛ تا آن زمان از اعتماد به این مسیرها خودداری کنید.
- endpoint هزینه‌ساز ناشناس را ببندید؛ وضعیت PAID بدون پرداخت حذف شود.

### فاز یک — صحت رفتار

- sender policy واحد، claim و recovery؛ اصلاح tenant isolation و همه routeهای مشابه، نه فقط نمونه‌های گزارش.
- schema مشترک فرم add/edit؛ transaction و uniqueness تصاویر؛ validation و DOM امن.
- تست‌های فعلی را به رفتار مورد توافق همگام کنید؛ صرفاً assertion را برای سبزشدن حذف نکنید.

### فاز دو — هزینه و معماری

- gateway و هویت مدل واحد؛ quota مشترک و atomic؛ settlement قابل reconciliation.
- روشن‌کردن مالک schema و وضعیت legacy؛ jobهای durable.
- مرزهای import را در تست enforce کنید؛ مستند معماری به‌تنهایی مانع cycle نیست.

### فاز سه — تمیزی و کارایی

- cloneهای خالص را منتقل کنید؛ parserهای مشترک با golden fixtures.
- workerهای پیچیده را به state machine و سرویس‌های کوچک آزمون‌پذیر بشکنید.
- با query-count test، N+1ها را برطرف کنید؛ benchmark پیش/پس از اصلاح و سپس تصمیم درباره cache.
- dependencyها را lock و imageها را immutable کنید؛ worker health و supervision را جدا تعریف کنید.

## ۸. بخش‌هایی که نباید بی‌دلیل خراب یا بازنویسی شوند

نقد سخت به معنی بی‌ارزش‌بودن کل پروژه نیست. در همین snapshot دفاع‌های خوبی وجود دارد:



- `apps/products/autofill.py` برای URL بیرونی بررسی IP عمومی، DNS و اتصال مقید، redirect و محدودیت اندازه/زمان دارد؛ گزارش حاضر ادعای SSRF عمومی درباره این مسیر ندارد. [^https://github.com/mtaham84/moshtari-yab/blob/a6aa1fcb1f9c716e49d1b462989bfb636409b106/apps/products/autofill.py]

- اعمال تراکنش کیف پول از transaction و قفل ردیف استفاده می‌کند؛ ریسک‌های R31/R32 را نباید به ادعای «هیچ قفلی در billing نیست» تعمیم داد. [^https://github.com/mtaham84/moshtari-yab/blob/a6aa1fcb1f9c716e49d1b462989bfb636409b106/apps/billing/services.py]

- کلیدهای provider با Fernet و امکان rotation نگهداری می‌شوند؛ این نقطه قوت، plaintext بودن credentialهای اجتماعی را حل نمی‌کند. [^https://github.com/mtaham84/moshtari-yab/blob/a6aa1fcb1f9c716e49d1b462989bfb636409b106/apps/billing/models.py]

- کتابخانه JSONL مشترک برای نوشتن ایمن، قفل و جایگزینی اتمیک طراحی شده است. [^https://github.com/mtaham84/moshtari-yab/blob/a6aa1fcb1f9c716e49d1b462989bfb636409b106/shared/collector_kit/jsonl.py]

- بخش مدیریت سشن Instagram میان خطای شبکه و مشکل login/challenge تفاوت می‌گذارد؛ قفل و ذخیره سشن ایمن دارد. [^https://github.com/mtaham84/moshtari-yab/blob/a6aa1fcb1f9c716e49d1b462989bfb636409b106/apps/discovery/ig_session.py]

- CI، تست‌های PostgreSQL و تست‌های دامنه در پروژه وجود دارند؛ مشکل «نبود مطلق تست» نیست، شکاف سناریوها و همگام‌نبودن بخشی از آن‌هاست. [^https://github.com/mtaham84/moshtari-yab/blob/a6aa1fcb1f9c716e49d1b462989bfb636409b106/.github/workflows/ci.yml]


## ۹. جمع‌بندی حرفه‌ای

نشانه‌های توسعه سریع و یکپارچه‌نشده وجود دارد: مسئولیت‌های زیاد در چند تابع، کپی helperها، مدل‌های موازی، policyهای متفاوت و تست‌هایی که عقب افتاده‌اند. اما از روی سورس نمی‌توان با قطعیت گفت چه مقدار را AI نوشته یا هر مشکل ناشی از «وایب‌کدینگ» است.

**بزرگ‌ترین مشکل صرفاً تکرار کد نیست؛ عدم یکپارچگی قراردادهاست:** یک مسیر مالکیت را چک می‌کند و دیگری نه، یک مسیر quota را رعایت می‌کند و follow-up نه، یک client هزینه را دقیق ثبت می‌کند و دیگری صفر، dry-run به معنای «عدم ارسال» هست ولی «عدم تغییر state» نیست. اول این قراردادها باید مشترک و قابل‌آزمون شوند، بعد پاک‌سازی ظاهری معنا پیدا می‌کند.

ضمیمه `file-by-file-fa.md` برای تک‌تک ۴۸۲ فایل، نوع، اندازه، سنجه‌های قابل‌اندازه‌گیری، یافته‌های مرتبط و محدودیت بازبینی را دارد. برای واگذاری کار به برنامه‌نویس، از شناسه Rxx استفاده کنید؛ هر کدام شواهد، اثر، پیشنهاد و معیار تست مستقل دارد.


---

# نتیجه اجرای تست‌ها و حدود استنباط

## محیط

Python 3.13.15، PostgreSQL 15 محلی با UTF-8 و pgvector v0.8.2؛ CI پروژه Python 3.12 و PostgreSQL 16 دارد. Django 6.1.2، pytest 9.2.0، psycopg 3.3.6، pydantic 2.14.2، instagrapi 3.2.2، twitter-cli 0.8.5، Ruff 0.17.3، Radon 6.0.1. این نتیجه برای snapshot ثبت‌شده و این dependency resolution است؛ requirements قفل نشده‌اند.

## نتیجه‌ها

| اجرا | نتیجه | تفسیر |
|---|---|---|
| pytest پیش‌فرض؛ پوشه موقت مستقل | 387 passed / 2 failed / 5 skipped؛ 9 subtests passed | 5 skip به علت نبود TEST_DATABASE_URL، هرچند POSTGRES_* برای fixtureهای دیگر تنظیم بود |
| بازاجرای تست‌ها با TEST_DATABASE_URL صریح و کنارگذاشتن همان دو شکست شناخته‌شده | 392 passed / 2 deselected؛ 9 subtests passed، صفر skip | فقط تأیید باقی تست‌هاست؛ این اجرا را suite کاملاً سبز معرفی نمی‌کنیم |
| Django: manage.py test apps | 190 اجرا / 3 شکست؛ 327.480 ثانیه | شکست‌های زیر؛ باقی بدون شکست گزارش شدند |
| 11 تست شاهد اختصاصی | 11 passed؛ 0.889 ثانیه | نشان‌دهنده وجود رفتار بد مورد انتظار در assertionها، نه صحت سیستم |
| instagram_engine/tests و tests ریشه، اجرای اضافی مجزا | 6 passed / 1 failed | شکست semantic moderation به embedding/محیط وابسته است |
| manage.py check | بدون issue | check عمومی فریم‌ورک، نه اثبات امنیت/صحت business logic |

### دو شکست pytest

1. `need_engine/tests/test_golden_eval.py::test_live_cost_estimate_uses_rendered_input_and_configured_prices`: انتظار estimate>0، مقدار 0.0. fixture قیمت Gemini می‌دهد ولی مدل پیش‌فرض تنظیمات Luna است. مدل و قیمت تست همگام نیستند؛ صفرشدن قیمت ناشناخته نیز باید در محصول تصمیم صریح داشته باشد.
2. `need_engine/tests/test_registry.py::test_llm_client_uses_panel_model_endpoint_and_price`: تست urlopen را mock می‌کند اما implementation از opener.open استفاده می‌کند؛ به دامنه مثال می‌رود و DNS fail می‌شود. این یک مشکل ایزولاسیون fixture/تغییر contract تست است، نه شاهد خرابی API واقعی. هیچ پاسخ پولی موفق از provider در این تست رخ نداد.

### سه شکست Django

1. `apps.discovery.test_instagram_family_reply.InstagramFamilyDatabaseTests.test_source_reward_and_empty_run_penalty_are_persisted`: alpha انتظار 3.0، مقدار واقعی 1.6.
2. `apps.discovery.tests.PanelViewTests.test_instagram_report_ui_allows_operator_labels_after_jev`: عبارت مورد انتظار UI در HTML نیست.
3. `apps.discovery.test_instagram_family_reply.InstagramPureTests.test_plan_rejects_handles_and_urls_and_requires_minimum_phrases`: ValueError مورد انتظار ایجاد نمی‌شود.

در این سه مورد، اختلاف تست/رفتار قطعاً وجود دارد؛ اینکه implementation باید عوض شود یا spec تست، نیازمند تصمیم مالک محصول است. گزارش آن‌ها را سه آسیب‌پذیری اثبات‌شده جا نزده است.

### اجرای اضافی

`tests/test_political_semantic_filter.py::test_pure_political_tweets_are_rejected` روی خروجی False/confidence=0 شکست خورد. embedding بیرونی/داده آماده برای این مسیر تضمین نشده بود؛ این نتیجه را اثبات کیفیت پایین classifier در production نمی‌دانیم. اسکریپت `test_dedup_and_semantic.py` هنگام import کار دیتابیس انجام می‌دهد و در این ارزیابی unit test مستقل به شمار نرفت.

### خطاهای محیطی حذف‌شده از نتیجه نهایی

خروجی اجرای اولیه روی cluster با encoding نامناسب و خطای پوشه موقت مشترک دو اجرای همزمان مبنای قضاوت محصول نیست. cluster UTF-8 و basetemp مستقل برای اجرای نهایی استفاده شد. لاگ‌های ناقص/آلوده محیطی در بسته شواهد نهایی قرار نگرفته‌اند.

## فرمان‌های بازاجرا

این فرمان‌ها فقط روی دیتابیس تست disposable با مجوز CREATE DATABASE/SCHEMA اجرا شوند؛ هرگز روی دیتابیس production اجرا نکنید. pgvector باید نصب و در دسترس باشد. مقادیر اتصال را برای محیط خود تنظیم کنید.

```bash
cd /path/to/moshtari-yab
export DJANGO_DEBUG=true
export NE_PANEL_MOCK_LLM=true
# TEST_DATABASE_URL و POSTGRES_* را برای DB تست خود تنظیم کنید.
export FIELD_ENCRYPTION_KEY="$(python -c 'from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())')"
python -m pytest --basetemp=/tmp/moshtari-review-pytest -q -rs
python manage.py test apps --noinput
ruff check --isolated --select E9,F63,F7,F82 .
ruff check --isolated --select F .
radon cc -j -s . > complexity.json
# regression_tests.py در پوشه جداگانه قرار دارد:
PYTHONPATH=/path/to/evidence:/path/to/moshtari-yab python manage.py test regression_tests --noinput
```

برای اجرای 7 تست اضافه باید تنظیمات Django و محیط DB نیز آماده باشند. تست‌های شاهد به‌عمد رفتار فعلی نامطلوب را انتظار دارند؛ بعد از رفع باگ باید assertionها به رفتار امن برگردند. هیچ credential واقعی در فایل تست شاهد قرار ندارد؛ مقدارهای dummy/mock-only مصنوعی‌اند.


---

# ضمیمه فایل‌به‌فایل — moshtari-yab

مبنا: `a6aa1fcb1f9c716e49d1b462989bfb636409b106`. دقیقاً یک ردیف برای هر یک از 482 فایل tracked. ترتیب الفبایی مسیرهاست.

**راهنمای عمق:** «شاهد هدفمند» یعنی بخشی از فایل برای یافته مشخص بازخوانی شده، نه اینکه تمام خطوط آن عاری از اشکال است. «ساختاری» یعنی AST/متریک/clone برای Python، نه ممیزی کامل منطق. فایل‌های binary/assets فقط فهرست شده‌اند؛ تعداد خط برای آن‌ها ارائه نمی‌شود. migrationها در اجرای Django تست در ایجاد DB به کار رفته‌اند، ولی صحت rollback و مهاجرت داده تاریخی ممیزی نشده است. نبود Rxx در یک ردیف ادعای بی‌نقص‌بودن نیست.

CC = بیشترین cyclomatic complexity قابل‌استخراج توسط Radon در فایل. clone = اجتماع خطوط فیزیکی داخل پنجره‌های برابر؛ شمار خطوط قابل‌حذف نیست. function>100 شاخص نگهداشت‌پذیری است، نه اثبات باگ. ۴۲ یافته در گزارش اصلی تشریح شده‌اند.

## فایل‌های ریشه

| فایل و منبع | نوع / دامنه | سنجه | بررسی و نتیجه |
|---|---|---|---|
| `.dockerignore` [^https://github.com/mtaham84/moshtari-yab/blob/a6aa1fcb1f9c716e49d1b462989bfb636409b106/.dockerignore] | متن تنظیمات | 32 خط | فهرست/بازرسی متنی در دامنه ابزار؛ تأیید معنایی کامل انجام نشده.  |
| `.editorconfig` [^https://github.com/mtaham84/moshtari-yab/blob/a6aa1fcb1f9c716e49d1b462989bfb636409b106/.editorconfig] | متن تنظیمات | 6 خط | فهرست/بازرسی متنی در دامنه ابزار؛ تأیید معنایی کامل انجام نشده.  |
| `.env.example` [^https://github.com/mtaham84/moshtari-yab/blob/a6aa1fcb1f9c716e49d1b462989bfb636409b106/.env.example] | متن .example | 260 خط | فهرست/بازرسی متنی در دامنه ابزار؛ تأیید معنایی کامل انجام نشده.  |
| `.gitattributes` [^https://github.com/mtaham84/moshtari-yab/blob/a6aa1fcb1f9c716e49d1b462989bfb636409b106/.gitattributes] | متن تنظیمات | 8 خط | فهرست/بازرسی متنی در دامنه ابزار؛ تأیید معنایی کامل انجام نشده.  |

## .github

| فایل و منبع | نوع / دامنه | سنجه | بررسی و نتیجه |
|---|---|---|---|
| `.github/workflows/ci.yml` [^https://github.com/mtaham84/moshtari-yab/blob/a6aa1fcb1f9c716e49d1b462989bfb636409b106/.github/workflows/ci.yml] | متن .yml | 67 خط | شاهد هدفمند: R27, R42؛ شرح و اصلاح در گزارش اصلی. |

## فایل‌های ریشه

| فایل و منبع | نوع / دامنه | سنجه | بررسی و نتیجه |
|---|---|---|---|
| `.gitignore` [^https://github.com/mtaham84/moshtari-yab/blob/a6aa1fcb1f9c716e49d1b462989bfb636409b106/.gitignore] | متن تنظیمات | 58 خط | فهرست/بازرسی متنی در دامنه ابزار؛ تأیید معنایی کامل انجام نشده.  |
| `Dockerfile` [^https://github.com/mtaham84/moshtari-yab/blob/a6aa1fcb1f9c716e49d1b462989bfb636409b106/Dockerfile] | متن تنظیمات | 35 خط | شاهد هدفمند: R26؛ شرح و اصلاح در گزارش اصلی. |
| `README.md` [^https://github.com/mtaham84/moshtari-yab/blob/a6aa1fcb1f9c716e49d1b462989bfb636409b106/README.md] | متن .md | 179 خط | فهرست/بازرسی متنی در دامنه ابزار؛ تأیید معنایی کامل انجام نشده.  |

## apps

| فایل و منبع | نوع / دامنه | سنجه | بررسی و نتیجه |
|---|---|---|---|
| `apps/__init__.py` [^https://github.com/mtaham84/moshtari-yab/blob/a6aa1fcb1f9c716e49d1b462989bfb636409b106/apps/__init__.py] | Python | 1 خط | اسکن ساختاری؛ یافته مستقل ثبت نشده.  |
| `apps/accounts/__init__.py` [^https://github.com/mtaham84/moshtari-yab/blob/a6aa1fcb1f9c716e49d1b462989bfb636409b106/apps/accounts/__init__.py] | Python | 0 خط | اسکن ساختاری؛ یافته مستقل ثبت نشده.  |
| `apps/accounts/apps.py` [^https://github.com/mtaham84/moshtari-yab/blob/a6aa1fcb1f9c716e49d1b462989bfb636409b106/apps/accounts/apps.py] | Python | 6 خط؛ max CC=1 | اسکن ساختاری؛ یافته مستقل ثبت نشده.  |
| `apps/accounts/emails.py` [^https://github.com/mtaham84/moshtari-yab/blob/a6aa1fcb1f9c716e49d1b462989bfb636409b106/apps/accounts/emails.py] | Python | 110 خط؛ max CC=5؛ except عمومی=1؛ تابع>100=1 | اسکن ساختاری؛ یافته مستقل ثبت نشده. مرز مسئولیت `send_verification_email` در 7–110 بررسی تکمیلی شود. |
| `apps/accounts/middleware.py` [^https://github.com/mtaham84/moshtari-yab/blob/a6aa1fcb1f9c716e49d1b462989bfb636409b106/apps/accounts/middleware.py] | Python | 23 خط؛ max CC=9 | اسکن ساختاری؛ یافته مستقل ثبت نشده. توابع نمونه برای پیگیری: `__init__`, `__call__`. |
| `apps/accounts/migrations/0001_initial.py` [^https://github.com/mtaham84/moshtari-yab/blob/a6aa1fcb1f9c716e49d1b462989bfb636409b106/apps/accounts/migrations/0001_initial.py] | Python؛ migration | 34 خط؛ max CC=1 | اسکن ساختاری؛ یافته مستقل ثبت نشده. rollback و ارتقای داده واقعی نیازمند تست مستقل است. |
| `apps/accounts/migrations/__init__.py` [^https://github.com/mtaham84/moshtari-yab/blob/a6aa1fcb1f9c716e49d1b462989bfb636409b106/apps/accounts/migrations/__init__.py] | Python؛ migration | 0 خط | اسکن ساختاری؛ یافته مستقل ثبت نشده. rollback و ارتقای داده واقعی نیازمند تست مستقل است. |
| `apps/accounts/models.py` [^https://github.com/mtaham84/moshtari-yab/blob/a6aa1fcb1f9c716e49d1b462989bfb636409b106/apps/accounts/models.py] | Python | 58 خط؛ max CC=3 | اسکن ساختاری؛ یافته مستقل ثبت نشده. توابع نمونه برای پیگیری: `is_expired`, `is_blocked`. |
| `apps/accounts/onboarding.py` [^https://github.com/mtaham84/moshtari-yab/blob/a6aa1fcb1f9c716e49d1b462989bfb636409b106/apps/accounts/onboarding.py] | Python | 162 خط؛ max CC=13؛ except عمومی=1 | اسکن ساختاری؛ یافته مستقل ثبت نشده. بررسی تکمیلی: خطای قابل‌بازیابی از خطای داده/مجوز تفکیک شود. |
| `apps/accounts/shop.py` [^https://github.com/mtaham84/moshtari-yab/blob/a6aa1fcb1f9c716e49d1b462989bfb636409b106/apps/accounts/shop.py] | Python | 74 خط؛ max CC=11 | شاهد هدفمند: R36؛ شرح و اصلاح در گزارش اصلی. |
| `apps/accounts/tests.py` [^https://github.com/mtaham84/moshtari-yab/blob/a6aa1fcb1f9c716e49d1b462989bfb636409b106/apps/accounts/tests.py] | Python؛ تست/اسکریپت آزمون | 138 خط؛ max CC=2 | اسکن ساختاری؛ یافته مستقل ثبت نشده. نتیجه runnerها در test-results.md؛ پوشش این فایل از نام آن استنباط نمی‌شود. |
| `apps/accounts/urls.py` [^https://github.com/mtaham84/moshtari-yab/blob/a6aa1fcb1f9c716e49d1b462989bfb636409b106/apps/accounts/urls.py] | Python | 31 خط | اسکن ساختاری؛ یافته مستقل ثبت نشده.  |
| `apps/accounts/views.py` [^https://github.com/mtaham84/moshtari-yab/blob/a6aa1fcb1f9c716e49d1b462989bfb636409b106/apps/accounts/views.py] | Python | 773 خط؛ max CC=29؛ except عمومی=6؛ تابع>100=2 | شاهد هدفمند: R01, R02, R09, R23, R28؛ شرح و اصلاح در گزارش اصلی. |
| `apps/billing/__init__.py` [^https://github.com/mtaham84/moshtari-yab/blob/a6aa1fcb1f9c716e49d1b462989bfb636409b106/apps/billing/__init__.py] | Python | 0 خط | اسکن ساختاری؛ یافته مستقل ثبت نشده.  |
| `apps/billing/admin.py` [^https://github.com/mtaham84/moshtari-yab/blob/a6aa1fcb1f9c716e49d1b462989bfb636409b106/apps/billing/admin.py] | Python | 39 خط؛ max CC=2 | اسکن ساختاری؛ یافته مستقل ثبت نشده. توابع نمونه برای پیگیری: `has_change_permission`. |
| `apps/billing/apps.py` [^https://github.com/mtaham84/moshtari-yab/blob/a6aa1fcb1f9c716e49d1b462989bfb636409b106/apps/billing/apps.py] | Python | 10 خط؛ max CC=2 | اسکن ساختاری؛ یافته مستقل ثبت نشده. توابع نمونه برای پیگیری: `ready`. |
| `apps/billing/context_processors.py` [^https://github.com/mtaham84/moshtari-yab/blob/a6aa1fcb1f9c716e49d1b462989bfb636409b106/apps/billing/context_processors.py] | Python | 20 خط؛ max CC=8 | اسکن ساختاری؛ یافته مستقل ثبت نشده. توابع نمونه برای پیگیری: `wallet`. |
| `apps/billing/fields.py` [^https://github.com/mtaham84/moshtari-yab/blob/a6aa1fcb1f9c716e49d1b462989bfb636409b106/apps/billing/fields.py] | Python | 30 خط؛ max CC=3 | اسکن ساختاری؛ یافته مستقل ثبت نشده. توابع نمونه برای پیگیری: `from_db_value`, `to_python`. |
| `apps/billing/forms.py` [^https://github.com/mtaham84/moshtari-yab/blob/a6aa1fcb1f9c716e49d1b462989bfb636409b106/apps/billing/forms.py] | Python | 76 خط؛ max CC=8 | اسکن ساختاری؛ یافته مستقل ثبت نشده. توابع نمونه برای پیگیری: `__init__`, `clean_api_key`. |
| `apps/billing/manage_urls.py` [^https://github.com/mtaham84/moshtari-yab/blob/a6aa1fcb1f9c716e49d1b462989bfb636409b106/apps/billing/manage_urls.py] | Python | 23 خط | اسکن ساختاری؛ یافته مستقل ثبت نشده.  |
| `apps/billing/manage_views.py` [^https://github.com/mtaham84/moshtari-yab/blob/a6aa1fcb1f9c716e49d1b462989bfb636409b106/apps/billing/manage_views.py] | Python | 1913 خط؛ max CC=60؛ except عمومی=34؛ تابع>100=7 | شاهد هدفمند: R39؛ شرح و اصلاح در گزارش اصلی. |
| `apps/billing/management/__init__.py` [^https://github.com/mtaham84/moshtari-yab/blob/a6aa1fcb1f9c716e49d1b462989bfb636409b106/apps/billing/management/__init__.py] | Python | 0 خط | اسکن ساختاری؛ یافته مستقل ثبت نشده.  |
| `apps/billing/management/commands/__init__.py` [^https://github.com/mtaham84/moshtari-yab/blob/a6aa1fcb1f9c716e49d1b462989bfb636409b106/apps/billing/management/commands/__init__.py] | Python | 0 خط | اسکن ساختاری؛ یافته مستقل ثبت نشده.  |
| `apps/billing/management/commands/charge_usage.py` [^https://github.com/mtaham84/moshtari-yab/blob/a6aa1fcb1f9c716e49d1b462989bfb636409b106/apps/billing/management/commands/charge_usage.py] | Python | 27 خط؛ max CC=4 | اسکن ساختاری؛ یافته مستقل ثبت نشده. توابع نمونه برای پیگیری: `add_arguments`, `handle`. |
| `apps/billing/management/commands/rotate_provider_keys.py` [^https://github.com/mtaham84/moshtari-yab/blob/a6aa1fcb1f9c716e49d1b462989bfb636409b106/apps/billing/management/commands/rotate_provider_keys.py] | Python | 29 خط؛ max CC=6 | اسکن ساختاری؛ یافته مستقل ثبت نشده. توابع نمونه برای پیگیری: `add_arguments`, `handle`. |
| `apps/billing/migrations/0001_initial.py` [^https://github.com/mtaham84/moshtari-yab/blob/a6aa1fcb1f9c716e49d1b462989bfb636409b106/apps/billing/migrations/0001_initial.py] | Python؛ migration | 115 خط؛ max CC=1 | اسکن ساختاری؛ یافته مستقل ثبت نشده. rollback و ارتقای داده واقعی نیازمند تست مستقل است. |
| `apps/billing/migrations/0002_initial_data.py` [^https://github.com/mtaham84/moshtari-yab/blob/a6aa1fcb1f9c716e49d1b462989bfb636409b106/apps/billing/migrations/0002_initial_data.py] | Python؛ migration | 24 خط؛ max CC=5؛ except عمومی=1 | اسکن ساختاری؛ یافته مستقل ثبت نشده. rollback و ارتقای داده واقعی نیازمند تست مستقل است. |
| `apps/billing/migrations/0003_encrypt_provider_api_keys.py` [^https://github.com/mtaham84/moshtari-yab/blob/a6aa1fcb1f9c716e49d1b462989bfb636409b106/apps/billing/migrations/0003_encrypt_provider_api_keys.py] | Python؛ migration | 42 خط؛ max CC=4 | اسکن ساختاری؛ یافته مستقل ثبت نشده. rollback و ارتقای داده واقعی نیازمند تست مستقل است. |
| `apps/billing/migrations/__init__.py` [^https://github.com/mtaham84/moshtari-yab/blob/a6aa1fcb1f9c716e49d1b462989bfb636409b106/apps/billing/migrations/__init__.py] | Python؛ migration | 0 خط | اسکن ساختاری؛ یافته مستقل ثبت نشده. rollback و ارتقای داده واقعی نیازمند تست مستقل است. |
| `apps/billing/models.py` [^https://github.com/mtaham84/moshtari-yab/blob/a6aa1fcb1f9c716e49d1b462989bfb636409b106/apps/billing/models.py] | Python | 147 خط؛ max CC=4 | شاهد هدفمند: R17؛ شرح و اصلاح در گزارش اصلی. |
| `apps/billing/services.py` [^https://github.com/mtaham84/moshtari-yab/blob/a6aa1fcb1f9c716e49d1b462989bfb636409b106/apps/billing/services.py] | Python | 795 خط؛ max CC=56؛ except عمومی=10 | شاهد هدفمند: R31, R32؛ شرح و اصلاح در گزارش اصلی. |
| `apps/billing/signals.py` [^https://github.com/mtaham84/moshtari-yab/blob/a6aa1fcb1f9c716e49d1b462989bfb636409b106/apps/billing/signals.py] | Python | 11 خط؛ max CC=2 | اسکن ساختاری؛ یافته مستقل ثبت نشده. توابع نمونه برای پیگیری: `create_wallet`. |
| `apps/billing/templatetags/__init__.py` [^https://github.com/mtaham84/moshtari-yab/blob/a6aa1fcb1f9c716e49d1b462989bfb636409b106/apps/billing/templatetags/__init__.py] | Python | 0 خط | اسکن ساختاری؛ یافته مستقل ثبت نشده.  |
| `apps/billing/templatetags/ops.py` [^https://github.com/mtaham84/moshtari-yab/blob/a6aa1fcb1f9c716e49d1b462989bfb636409b106/apps/billing/templatetags/ops.py] | Python | 96 خط؛ max CC=8؛ except عمومی=2 | اسکن ساختاری؛ یافته مستقل ثبت نشده. بررسی تکمیلی: خطای قابل‌بازیابی از خطای داده/مجوز تفکیک شود. |
| `apps/billing/test_jalali_filter.py` [^https://github.com/mtaham84/moshtari-yab/blob/a6aa1fcb1f9c716e49d1b462989bfb636409b106/apps/billing/test_jalali_filter.py] | Python؛ تست/اسکریپت آزمون | 19 خط؛ max CC=3 | اسکن ساختاری؛ یافته مستقل ثبت نشده. نتیجه runnerها در test-results.md؛ پوشش این فایل از نام آن استنباط نمی‌شود. |
| `apps/billing/test_manage_sessions.py` [^https://github.com/mtaham84/moshtari-yab/blob/a6aa1fcb1f9c716e49d1b462989bfb636409b106/apps/billing/test_manage_sessions.py] | Python؛ تست/اسکریپت آزمون | 466 خط؛ max CC=3 | اسکن ساختاری؛ یافته مستقل ثبت نشده. نتیجه runnerها در test-results.md؛ پوشش این فایل از نام آن استنباط نمی‌شود. |
| `apps/billing/tests.py` [^https://github.com/mtaham84/moshtari-yab/blob/a6aa1fcb1f9c716e49d1b462989bfb636409b106/apps/billing/tests.py] | Python؛ تست/اسکریپت آزمون | 221 خط؛ max CC=3 | اسکن ساختاری؛ یافته مستقل ثبت نشده. نتیجه runnerها در test-results.md؛ پوشش این فایل از نام آن استنباط نمی‌شود. |
| `apps/billing/urls.py` [^https://github.com/mtaham84/moshtari-yab/blob/a6aa1fcb1f9c716e49d1b462989bfb636409b106/apps/billing/urls.py] | Python | 30 خط | اسکن ساختاری؛ یافته مستقل ثبت نشده.  |
| `apps/billing/views.py` [^https://github.com/mtaham84/moshtari-yab/blob/a6aa1fcb1f9c716e49d1b462989bfb636409b106/apps/billing/views.py] | Python | 358 خط؛ max CC=14 | اسکن ساختاری؛ یافته مستقل ثبت نشده. توابع نمونه برای پیگیری: `staff_required`, `_next`. |
| `apps/businesses/__init__.py` [^https://github.com/mtaham84/moshtari-yab/blob/a6aa1fcb1f9c716e49d1b462989bfb636409b106/apps/businesses/__init__.py] | Python | 0 خط | اسکن ساختاری؛ یافته مستقل ثبت نشده.  |
| `apps/businesses/apps.py` [^https://github.com/mtaham84/moshtari-yab/blob/a6aa1fcb1f9c716e49d1b462989bfb636409b106/apps/businesses/apps.py] | Python | 6 خط؛ max CC=1 | اسکن ساختاری؛ یافته مستقل ثبت نشده.  |
| `apps/businesses/migrations/0001_initial.py` [^https://github.com/mtaham84/moshtari-yab/blob/a6aa1fcb1f9c716e49d1b462989bfb636409b106/apps/businesses/migrations/0001_initial.py] | Python؛ migration | 36 خط؛ max CC=1 | اسکن ساختاری؛ یافته مستقل ثبت نشده. rollback و ارتقای داده واقعی نیازمند تست مستقل است. |
| `apps/businesses/migrations/0002_business_daily_discovery_limit_and_more.py` [^https://github.com/mtaham84/moshtari-yab/blob/a6aa1fcb1f9c716e49d1b462989bfb636409b106/apps/businesses/migrations/0002_business_daily_discovery_limit_and_more.py] | Python؛ migration | 43 خط؛ max CC=1 | اسکن ساختاری؛ یافته مستقل ثبت نشده. rollback و ارتقای داده واقعی نیازمند تست مستقل است. |
| `apps/businesses/migrations/0003_alter_business_daily_discovery_limit.py` [^https://github.com/mtaham84/moshtari-yab/blob/a6aa1fcb1f9c716e49d1b462989bfb636409b106/apps/businesses/migrations/0003_alter_business_daily_discovery_limit.py] | Python؛ migration | 18 خط؛ max CC=1 | اسکن ساختاری؛ یافته مستقل ثبت نشده. rollback و ارتقای داده واقعی نیازمند تست مستقل است. |
| `apps/businesses/migrations/0004_business_target_locations_business_target_max_age_and_more.py` [^https://github.com/mtaham84/moshtari-yab/blob/a6aa1fcb1f9c716e49d1b462989bfb636409b106/apps/businesses/migrations/0004_business_target_locations_business_target_max_age_and_more.py] | Python؛ migration | 28 خط؛ max CC=1 | اسکن ساختاری؛ یافته مستقل ثبت نشده. rollback و ارتقای داده واقعی نیازمند تست مستقل است. |
| `apps/businesses/migrations/0005_business_catalog_version_business_taxonomy_version.py` [^https://github.com/mtaham84/moshtari-yab/blob/a6aa1fcb1f9c716e49d1b462989bfb636409b106/apps/businesses/migrations/0005_business_catalog_version_business_taxonomy_version.py] | Python؛ migration | 23 خط؛ max CC=1 | اسکن ساختاری؛ یافته مستقل ثبت نشده. rollback و ارتقای داده واقعی نیازمند تست مستقل است. |
| `apps/businesses/migrations/0006_engine_cleanup.py` [^https://github.com/mtaham84/moshtari-yab/blob/a6aa1fcb1f9c716e49d1b462989bfb636409b106/apps/businesses/migrations/0006_engine_cleanup.py] | Python؛ migration | 21 خط؛ max CC=1 | اسکن ساختاری؛ یافته مستقل ثبت نشده. rollback و ارتقای داده واقعی نیازمند تست مستقل است. |
| `apps/businesses/migrations/0007_message_style.py` [^https://github.com/mtaham84/moshtari-yab/blob/a6aa1fcb1f9c716e49d1b462989bfb636409b106/apps/businesses/migrations/0007_message_style.py] | Python؛ migration | 33 خط؛ max CC=1 | اسکن ساختاری؛ یافته مستقل ثبت نشده. rollback و ارتقای داده واقعی نیازمند تست مستقل است. |
| `apps/businesses/migrations/0008_business_cover_business_logo_and_more.py` [^https://github.com/mtaham84/moshtari-yab/blob/a6aa1fcb1f9c716e49d1b462989bfb636409b106/apps/businesses/migrations/0008_business_cover_business_logo_and_more.py] | Python؛ migration | 76 خط؛ max CC=1 | اسکن ساختاری؛ یافته مستقل ثبت نشده. rollback و ارتقای داده واقعی نیازمند تست مستقل است. |
| `apps/businesses/migrations/0009_seed_business_types.py` [^https://github.com/mtaham84/moshtari-yab/blob/a6aa1fcb1f9c716e49d1b462989bfb636409b106/apps/businesses/migrations/0009_seed_business_types.py] | Python؛ migration | 42 خط؛ max CC=2 | اسکن ساختاری؛ یافته مستقل ثبت نشده. rollback و ارتقای داده واقعی نیازمند تست مستقل است. |
| `apps/businesses/migrations/__init__.py` [^https://github.com/mtaham84/moshtari-yab/blob/a6aa1fcb1f9c716e49d1b462989bfb636409b106/apps/businesses/migrations/__init__.py] | Python؛ migration | 0 خط | اسکن ساختاری؛ یافته مستقل ثبت نشده. rollback و ارتقای داده واقعی نیازمند تست مستقل است. |
| `apps/businesses/models.py` [^https://github.com/mtaham84/moshtari-yab/blob/a6aa1fcb1f9c716e49d1b462989bfb636409b106/apps/businesses/models.py] | Python | 174 خط؛ max CC=3 | اسکن ساختاری؛ یافته مستقل ثبت نشده. توابع نمونه برای پیگیری: `__str__`, `active_discovery_products_count`. |
| `apps/core/__init__.py` [^https://github.com/mtaham84/moshtari-yab/blob/a6aa1fcb1f9c716e49d1b462989bfb636409b106/apps/core/__init__.py] | Python | 1 خط | اسکن ساختاری؛ یافته مستقل ثبت نشده.  |
| `apps/core/admin.py` [^https://github.com/mtaham84/moshtari-yab/blob/a6aa1fcb1f9c716e49d1b462989bfb636409b106/apps/core/admin.py] | Python | 15 خط؛ max CC=2 | اسکن ساختاری؛ یافته مستقل ثبت نشده. توابع نمونه برای پیگیری: `has_add_permission`, `has_delete_permission`. |
| `apps/core/agent.py` [^https://github.com/mtaham84/moshtari-yab/blob/a6aa1fcb1f9c716e49d1b462989bfb636409b106/apps/core/agent.py] | Python | 137 خط؛ max CC=13 | شاهد هدفمند: R15, R33؛ شرح و اصلاح در گزارش اصلی. |
| `apps/core/apps.py` [^https://github.com/mtaham84/moshtari-yab/blob/a6aa1fcb1f9c716e49d1b462989bfb636409b106/apps/core/apps.py] | Python | 7 خط؛ max CC=1 | اسکن ساختاری؛ یافته مستقل ثبت نشده.  |
| `apps/core/context_processors.py` [^https://github.com/mtaham84/moshtari-yab/blob/a6aa1fcb1f9c716e49d1b462989bfb636409b106/apps/core/context_processors.py] | Python | 23 خط؛ max CC=6؛ except عمومی=2 | اسکن ساختاری؛ یافته مستقل ثبت نشده. بررسی تکمیلی: خطای قابل‌بازیابی از خطای داده/مجوز تفکیک شود. |
| `apps/core/jalali.py` [^https://github.com/mtaham84/moshtari-yab/blob/a6aa1fcb1f9c716e49d1b462989bfb636409b106/apps/core/jalali.py] | Python | 120 خط؛ max CC=11؛ except عمومی=1 | اسکن ساختاری؛ یافته مستقل ثبت نشده. بررسی تکمیلی: خطای قابل‌بازیابی از خطای داده/مجوز تفکیک شود. |
| `apps/core/llm.py` [^https://github.com/mtaham84/moshtari-yab/blob/a6aa1fcb1f9c716e49d1b462989bfb636409b106/apps/core/llm.py] | Python | 126 خط؛ max CC=28؛ except عمومی=3 | شاهد هدفمند: R14, R41؛ شرح و اصلاح در گزارش اصلی. |
| `apps/core/management/__init__.py` [^https://github.com/mtaham84/moshtari-yab/blob/a6aa1fcb1f9c716e49d1b462989bfb636409b106/apps/core/management/__init__.py] | Python | 1 خط | اسکن ساختاری؛ یافته مستقل ثبت نشده.  |
| `apps/core/management/commands/__init__.py` [^https://github.com/mtaham84/moshtari-yab/blob/a6aa1fcb1f9c716e49d1b462989bfb636409b106/apps/core/management/commands/__init__.py] | Python | 1 خط | اسکن ساختاری؛ یافته مستقل ثبت نشده.  |
| `apps/core/management/commands/ig_ingest.py` [^https://github.com/mtaham84/moshtari-yab/blob/a6aa1fcb1f9c716e49d1b462989bfb636409b106/apps/core/management/commands/ig_ingest.py] | Python | 238 خط؛ max CC=46؛ تابع>100=1 | اسکن ساختاری؛ یافته مستقل ثبت نشده. مرز مسئولیت `handle` در 30–157 بررسی تکمیلی شود. |
| `apps/core/management/commands/ig_sync_seeds.py` [^https://github.com/mtaham84/moshtari-yab/blob/a6aa1fcb1f9c716e49d1b462989bfb636409b106/apps/core/management/commands/ig_sync_seeds.py] | Python | 221 خط؛ max CC=18 | اسکن ساختاری؛ یافته مستقل ثبت نشده. توابع نمونه برای پیگیری: `_canonical`, `_hashtag_seeds`. |
| `apps/core/management/commands/purge_retained_data.py` [^https://github.com/mtaham84/moshtari-yab/blob/a6aa1fcb1f9c716e49d1b462989bfb636409b106/apps/core/management/commands/purge_retained_data.py] | Python | 72 خط؛ max CC=13؛ except عمومی=1 | اسکن ساختاری؛ یافته مستقل ثبت نشده. بررسی تکمیلی: خطای قابل‌بازیابی از خطای داده/مجوز تفکیک شود. |
| `apps/core/management/commands/seed_demo_businesses.py` [^https://github.com/mtaham84/moshtari-yab/blob/a6aa1fcb1f9c716e49d1b462989bfb636409b106/apps/core/management/commands/seed_demo_businesses.py] | Python | 806 خط؛ max CC=11؛ تابع>100=1 | اسکن ساختاری؛ یافته مستقل ثبت نشده. مرز مسئولیت `handle` در 35–805 بررسی تکمیلی شود. |
| `apps/core/migrations/0001_siteappearance.py` [^https://github.com/mtaham84/moshtari-yab/blob/a6aa1fcb1f9c716e49d1b462989bfb636409b106/apps/core/migrations/0001_siteappearance.py] | Python؛ migration | 21 خط؛ max CC=1 | اسکن ساختاری؛ یافته مستقل ثبت نشده. rollback و ارتقای داده واقعی نیازمند تست مستقل است. |
| `apps/core/migrations/0002_llmusagelog.py` [^https://github.com/mtaham84/moshtari-yab/blob/a6aa1fcb1f9c716e49d1b462989bfb636409b106/apps/core/migrations/0002_llmusagelog.py] | Python؛ migration | 30 خط؛ max CC=1 | اسکن ساختاری؛ یافته مستقل ثبت نشده. rollback و ارتقای داده واقعی نیازمند تست مستقل است. |
| `apps/core/migrations/__init__.py` [^https://github.com/mtaham84/moshtari-yab/blob/a6aa1fcb1f9c716e49d1b462989bfb636409b106/apps/core/migrations/__init__.py] | Python؛ migration | 0 خط | اسکن ساختاری؛ یافته مستقل ثبت نشده. rollback و ارتقای داده واقعی نیازمند تست مستقل است. |
| `apps/core/models.py` [^https://github.com/mtaham84/moshtari-yab/blob/a6aa1fcb1f9c716e49d1b462989bfb636409b106/apps/core/models.py] | Python | 47 خط؛ max CC=2 | اسکن ساختاری؛ یافته مستقل ثبت نشده. توابع نمونه برای پیگیری: `__str__`, `save`. |
| `apps/core/test_seed_demo_businesses.py` [^https://github.com/mtaham84/moshtari-yab/blob/a6aa1fcb1f9c716e49d1b462989bfb636409b106/apps/core/test_seed_demo_businesses.py] | Python؛ تست/اسکریپت آزمون | 70 خط؛ max CC=4 | اسکن ساختاری؛ یافته مستقل ثبت نشده. نتیجه runnerها در test-results.md؛ پوشش این فایل از نام آن استنباط نمی‌شود. |
| `apps/core/tests.py` [^https://github.com/mtaham84/moshtari-yab/blob/a6aa1fcb1f9c716e49d1b462989bfb636409b106/apps/core/tests.py] | Python؛ تست/اسکریپت آزمون | 62 خط؛ max CC=2 | اسکن ساختاری؛ یافته مستقل ثبت نشده. نتیجه runnerها در test-results.md؛ پوشش این فایل از نام آن استنباط نمی‌شود. |
| `apps/core/tests_agent.py` [^https://github.com/mtaham84/moshtari-yab/blob/a6aa1fcb1f9c716e49d1b462989bfb636409b106/apps/core/tests_agent.py] | Python؛ تست/اسکریپت آزمون | 244 خط؛ max CC=3 | اسکن ساختاری؛ یافته مستقل ثبت نشده. نتیجه runnerها در test-results.md؛ پوشش این فایل از نام آن استنباط نمی‌شود. |
| `apps/core/urls.py` [^https://github.com/mtaham84/moshtari-yab/blob/a6aa1fcb1f9c716e49d1b462989bfb636409b106/apps/core/urls.py] | Python | 10 خط | اسکن ساختاری؛ یافته مستقل ثبت نشده.  |
| `apps/core/views.py` [^https://github.com/mtaham84/moshtari-yab/blob/a6aa1fcb1f9c716e49d1b462989bfb636409b106/apps/core/views.py] | Python | 5 خط؛ max CC=1 | اسکن ساختاری؛ یافته مستقل ثبت نشده. توابع نمونه برای پیگیری: `landing`. |
| `apps/discovery/__init__.py` [^https://github.com/mtaham84/moshtari-yab/blob/a6aa1fcb1f9c716e49d1b462989bfb636409b106/apps/discovery/__init__.py] | Python | 0 خط | اسکن ساختاری؛ یافته مستقل ثبت نشده.  |
| `apps/discovery/accounts_pool_file.py` [^https://github.com/mtaham84/moshtari-yab/blob/a6aa1fcb1f9c716e49d1b462989bfb636409b106/apps/discovery/accounts_pool_file.py] | Python | 113 خط؛ max CC=14؛ except عمومی=1 | شاهد هدفمند: R08؛ شرح و اصلاح در گزارش اصلی. |
| `apps/discovery/admin.py` [^https://github.com/mtaham84/moshtari-yab/blob/a6aa1fcb1f9c716e49d1b462989bfb636409b106/apps/discovery/admin.py] | Python | 25 خط؛ max CC=2 | اسکن ساختاری؛ یافته مستقل ثبت نشده. توابع نمونه برای پیگیری: `activate`, `deactivate`. |
| `apps/discovery/apps.py` [^https://github.com/mtaham84/moshtari-yab/blob/a6aa1fcb1f9c716e49d1b462989bfb636409b106/apps/discovery/apps.py] | Python | 24 خط؛ max CC=5 | اسکن ساختاری؛ یافته مستقل ثبت نشده. توابع نمونه برای پیگیری: `ready`, `_sync`. |
| `apps/discovery/comment_moderation.py` [^https://github.com/mtaham84/moshtari-yab/blob/a6aa1fcb1f9c716e49d1b462989bfb636409b106/apps/discovery/comment_moderation.py] | Python | 167 خط؛ max CC=10 | اسکن ساختاری؛ یافته مستقل ثبت نشده. توابع نمونه برای پیگیری: `is_toxic_or_spam_comment`, `is_pure_emoji_or_symbols_only`. |
| `apps/discovery/context_processors.py` [^https://github.com/mtaham84/moshtari-yab/blob/a6aa1fcb1f9c716e49d1b462989bfb636409b106/apps/discovery/context_processors.py] | Python | 5 خط؛ max CC=1 | اسکن ساختاری؛ یافته مستقل ثبت نشده. توابع نمونه برای پیگیری: `x_panel`. |
| `apps/discovery/device_emulator.py` [^https://github.com/mtaham84/moshtari-yab/blob/a6aa1fcb1f9c716e49d1b462989bfb636409b106/apps/discovery/device_emulator.py] | Python | 198 خط؛ max CC=8؛ except عمومی=3 | اسکن ساختاری؛ یافته مستقل ثبت نشده. بررسی تکمیلی: خطای قابل‌بازیابی از خطای داده/مجوز تفکیک شود. |
| `apps/discovery/engine_bridge.py` [^https://github.com/mtaham84/moshtari-yab/blob/a6aa1fcb1f9c716e49d1b462989bfb636409b106/apps/discovery/engine_bridge.py] | Python | 365 خط؛ max CC=79؛ except عمومی=1؛ تابع>100=1 | اسکن ساختاری؛ یافته مستقل ثبت نشده. مرز مسئولیت `import_opportunity` در 70–197 بررسی تکمیلی شود. |
| `apps/discovery/funnel.py` [^https://github.com/mtaham84/moshtari-yab/blob/a6aa1fcb1f9c716e49d1b462989bfb636409b106/apps/discovery/funnel.py] | Python | 178 خط؛ max CC=28 | اسکن ساختاری؛ یافته مستقل ثبت نشده. توابع نمونه برای پیگیری: `_schema`, `_table_exists`. |
| `apps/discovery/ig_query_scoring.py` [^https://github.com/mtaham84/moshtari-yab/blob/a6aa1fcb1f9c716e49d1b462989bfb636409b106/apps/discovery/ig_query_scoring.py] | Python | 65 خط؛ max CC=6؛ except عمومی=1 | اسکن ساختاری؛ یافته مستقل ثبت نشده. بررسی تکمیلی: خطای قابل‌بازیابی از خطای داده/مجوز تفکیک شود. |
| `apps/discovery/ig_session.py` [^https://github.com/mtaham84/moshtari-yab/blob/a6aa1fcb1f9c716e49d1b462989bfb636409b106/apps/discovery/ig_session.py] | Python | 162 خط؛ max CC=8؛ except عمومی=2 | اسکن ساختاری؛ یافته مستقل ثبت نشده. بررسی تکمیلی: خطای قابل‌بازیابی از خطای داده/مجوز تفکیک شود. |
| `apps/discovery/instagram_families.py` [^https://github.com/mtaham84/moshtari-yab/blob/a6aa1fcb1f9c716e49d1b462989bfb636409b106/apps/discovery/instagram_families.py] | Python | 409 خط؛ max CC=39؛ except عمومی=1؛ تابع>100=1 | اسکن ساختاری؛ یافته مستقل ثبت نشده. مرز مسئولیت `expand_queries_from_top_sources` در 278–381 بررسی تکمیلی شود. |
| `apps/discovery/management/__init__.py` [^https://github.com/mtaham84/moshtari-yab/blob/a6aa1fcb1f9c716e49d1b462989bfb636409b106/apps/discovery/management/__init__.py] | Python | 0 خط | اسکن ساختاری؛ یافته مستقل ثبت نشده.  |
| `apps/discovery/management/commands/__init__.py` [^https://github.com/mtaham84/moshtari-yab/blob/a6aa1fcb1f9c716e49d1b462989bfb636409b106/apps/discovery/management/commands/__init__.py] | Python | 0 خط | اسکن ساختاری؛ یافته مستقل ثبت نشده.  |
| `apps/discovery/management/commands/accounts_pool.py` [^https://github.com/mtaham84/moshtari-yab/blob/a6aa1fcb1f9c716e49d1b462989bfb636409b106/apps/discovery/management/commands/accounts_pool.py] | Python | 23 خط؛ max CC=4 | اسکن ساختاری؛ یافته مستقل ثبت نشده. توابع نمونه برای پیگیری: `add_arguments`, `handle`. |
| `apps/discovery/management/commands/purge_x_contacts.py` [^https://github.com/mtaham84/moshtari-yab/blob/a6aa1fcb1f9c716e49d1b462989bfb636409b106/apps/discovery/management/commands/purge_x_contacts.py] | Python | 19 خط؛ max CC=2 | اسکن ساختاری؛ یافته مستقل ثبت نشده. توابع نمونه برای پیگیری: `add_arguments`, `handle`. |
| `apps/discovery/management/commands/purge_x_queries.py` [^https://github.com/mtaham84/moshtari-yab/blob/a6aa1fcb1f9c716e49d1b462989bfb636409b106/apps/discovery/management/commands/purge_x_queries.py] | Python | 83 خط؛ max CC=18؛ except عمومی=1 | اسکن ساختاری؛ یافته مستقل ثبت نشده. بررسی تکمیلی: خطای قابل‌بازیابی از خطای داده/مجوز تفکیک شود. |
| `apps/discovery/management/commands/run_instagram_outreach.py` [^https://github.com/mtaham84/moshtari-yab/blob/a6aa1fcb1f9c716e49d1b462989bfb636409b106/apps/discovery/management/commands/run_instagram_outreach.py] | Python | 541 خط؛ max CC=52؛ except عمومی=7؛ تابع>100=2 | شاهد هدفمند: R10, R11, R23؛ شرح و اصلاح در گزارش اصلی. |
| `apps/discovery/management/commands/run_instagram_scraper.py` [^https://github.com/mtaham84/moshtari-yab/blob/a6aa1fcb1f9c716e49d1b462989bfb636409b106/apps/discovery/management/commands/run_instagram_scraper.py] | Python | 1695 خط؛ max CC=73؛ except عمومی=40؛ تابع>100=6 | شاهد هدفمند: R39؛ شرح و اصلاح در گزارش اصلی. |
| `apps/discovery/management/commands/seed_global_sources.py` [^https://github.com/mtaham84/moshtari-yab/blob/a6aa1fcb1f9c716e49d1b462989bfb636409b106/apps/discovery/management/commands/seed_global_sources.py] | Python | 47 خط؛ max CC=9 | اسکن ساختاری؛ یافته مستقل ثبت نشده. توابع نمونه برای پیگیری: `add_arguments`, `handle`. |
| `apps/discovery/management/commands/sync_opportunities.py` [^https://github.com/mtaham84/moshtari-yab/blob/a6aa1fcb1f9c716e49d1b462989bfb636409b106/apps/discovery/management/commands/sync_opportunities.py] | Python | 59 خط؛ max CC=12؛ except عمومی=1 | شاهد هدفمند: R41؛ شرح و اصلاح در گزارش اصلی. |
| `apps/discovery/management/commands/sync_x_status.py` [^https://github.com/mtaham84/moshtari-yab/blob/a6aa1fcb1f9c716e49d1b462989bfb636409b106/apps/discovery/management/commands/sync_x_status.py] | Python | 22 خط؛ max CC=3 | اسکن ساختاری؛ یافته مستقل ثبت نشده. توابع نمونه برای پیگیری: `add_arguments`, `handle`. |
| `apps/discovery/migrations/0001_initial.py` [^https://github.com/mtaham84/moshtari-yab/blob/a6aa1fcb1f9c716e49d1b462989bfb636409b106/apps/discovery/migrations/0001_initial.py] | Python؛ migration | 75 خط؛ max CC=1 | اسکن ساختاری؛ یافته مستقل ثبت نشده. rollback و ارتقای داده واقعی نیازمند تست مستقل است. |
| `apps/discovery/migrations/0002_discoveredlead_bot_agent_name_and_more.py` [^https://github.com/mtaham84/moshtari-yab/blob/a6aa1fcb1f9c716e49d1b462989bfb636409b106/apps/discovery/migrations/0002_discoveredlead_bot_agent_name_and_more.py] | Python؛ migration | 68 خط؛ max CC=1 | اسکن ساختاری؛ یافته مستقل ثبت نشده. rollback و ارتقای داده واقعی نیازمند تست مستقل است. |
| `apps/discovery/migrations/0003_productorder_productdailymetric.py` [^https://github.com/mtaham84/moshtari-yab/blob/a6aa1fcb1f9c716e49d1b462989bfb636409b106/apps/discovery/migrations/0003_productorder_productdailymetric.py] | Python؛ migration | 56 خط؛ max CC=1 | اسکن ساختاری؛ یافته مستقل ثبت نشده. rollback و ارتقای داده واقعی نیازمند تست مستقل است. |
| `apps/discovery/migrations/0004_discoveredlead_cost_toman_discoveredlead_cost_usd_and_more.py` [^https://github.com/mtaham84/moshtari-yab/blob/a6aa1fcb1f9c716e49d1b462989bfb636409b106/apps/discovery/migrations/0004_discoveredlead_cost_toman_discoveredlead_cost_usd_and_more.py] | Python؛ migration | 44 خط؛ max CC=1 | اسکن ساختاری؛ یافته مستقل ثبت نشده. rollback و ارتقای داده واقعی نیازمند تست مستقل است. |
| `apps/discovery/migrations/0005_customer_opportunity_evidence_aianalysis_and_more.py` [^https://github.com/mtaham84/moshtari-yab/blob/a6aa1fcb1f9c716e49d1b462989bfb636409b106/apps/discovery/migrations/0005_customer_opportunity_evidence_aianalysis_and_more.py] | Python؛ migration | 115 خط؛ max CC=1 | اسکن ساختاری؛ یافته مستقل ثبت نشده. rollback و ارتقای داده واقعی نیازمند تست مستقل است. |
| `apps/discovery/migrations/0006_opportunity_category_confidence_and_more.py` [^https://github.com/mtaham84/moshtari-yab/blob/a6aa1fcb1f9c716e49d1b462989bfb636409b106/apps/discovery/migrations/0006_opportunity_category_confidence_and_more.py] | Python؛ migration | 43 خط؛ max CC=1 | اسکن ساختاری؛ یافته مستقل ثبت نشده. rollback و ارتقای داده واقعی نیازمند تست مستقل است. |
| `apps/discovery/migrations/0007_monitoredcommunity.py` [^https://github.com/mtaham84/moshtari-yab/blob/a6aa1fcb1f9c716e49d1b462989bfb636409b106/apps/discovery/migrations/0007_monitoredcommunity.py] | Python؛ migration | 40 خط؛ max CC=1 | اسکن ساختاری؛ یافته مستقل ثبت نشده. rollback و ارتقای داده واقعی نیازمند تست مستقل است. |
| `apps/discovery/migrations/0008_engine_cleanup.py` [^https://github.com/mtaham84/moshtari-yab/blob/a6aa1fcb1f9c716e49d1b462989bfb636409b106/apps/discovery/migrations/0008_engine_cleanup.py] | Python؛ migration | 79 خط؛ max CC=2 | اسکن ساختاری؛ یافته مستقل ثبت نشده. rollback و ارتقای داده واقعی نیازمند تست مستقل است. |
| `apps/discovery/migrations/0009_engine_sync_cursor.py` [^https://github.com/mtaham84/moshtari-yab/blob/a6aa1fcb1f9c716e49d1b462989bfb636409b106/apps/discovery/migrations/0009_engine_sync_cursor.py] | Python؛ migration | 22 خط؛ max CC=1 | اسکن ساختاری؛ یافته مستقل ثبت نشده. rollback و ارتقای داده واقعی نیازمند تست مستقل است. |
| `apps/discovery/migrations/0010_monitoredcommunity_crawler_sync.py` [^https://github.com/mtaham84/moshtari-yab/blob/a6aa1fcb1f9c716e49d1b462989bfb636409b106/apps/discovery/migrations/0010_monitoredcommunity_crawler_sync.py] | Python؛ migration | 28 خط؛ max CC=1 | اسکن ساختاری؛ یافته مستقل ثبت نشده. rollback و ارتقای داده واقعی نیازمند تست مستقل است. |
| `apps/discovery/migrations/0011_community_scope.py` [^https://github.com/mtaham84/moshtari-yab/blob/a6aa1fcb1f9c716e49d1b462989bfb636409b106/apps/discovery/migrations/0011_community_scope.py] | Python؛ migration | 53 خط؛ max CC=2 | اسکن ساختاری؛ یافته مستقل ثبت نشده. rollback و ارتقای داده واقعی نیازمند تست مستقل است. |
| `apps/discovery/migrations/0011_opportunityproductmatch_reply_variants.py` [^https://github.com/mtaham84/moshtari-yab/blob/a6aa1fcb1f9c716e49d1b462989bfb636409b106/apps/discovery/migrations/0011_opportunityproductmatch_reply_variants.py] | Python؛ migration | 11 خط؛ max CC=1 | اسکن ساختاری؛ یافته مستقل ثبت نشده. rollback و ارتقای داده واقعی نیازمند تست مستقل است. |
| `apps/discovery/migrations/0012_x_growth_fields.py` [^https://github.com/mtaham84/moshtari-yab/blob/a6aa1fcb1f9c716e49d1b462989bfb636409b106/apps/discovery/migrations/0012_x_growth_fields.py] | Python؛ migration | 71 خط؛ max CC=4 | اسکن ساختاری؛ یافته مستقل ثبت نشده. rollback و ارتقای داده واقعی نیازمند تست مستقل است. |
| `apps/discovery/migrations/0013_merge_x_need_engine_and_main.py` [^https://github.com/mtaham84/moshtari-yab/blob/a6aa1fcb1f9c716e49d1b462989bfb636409b106/apps/discovery/migrations/0013_merge_x_need_engine_and_main.py] | Python؛ migration | 12 خط؛ max CC=1 | اسکن ساختاری؛ یافته مستقل ثبت نشده. rollback و ارتقای داده واقعی نیازمند تست مستقل است. |
| `apps/discovery/migrations/0014_opportunity_seller_feedback.py` [^https://github.com/mtaham84/moshtari-yab/blob/a6aa1fcb1f9c716e49d1b462989bfb636409b106/apps/discovery/migrations/0014_opportunity_seller_feedback.py] | Python؛ migration | 55 خط؛ max CC=7 | اسکن ساختاری؛ یافته مستقل ثبت نشده. rollback و ارتقای داده واقعی نیازمند تست مستقل است. |
| `apps/discovery/migrations/0015_instagrampage.py` [^https://github.com/mtaham84/moshtari-yab/blob/a6aa1fcb1f9c716e49d1b462989bfb636409b106/apps/discovery/migrations/0015_instagrampage.py] | Python؛ migration | 34 خط؛ max CC=1 | اسکن ساختاری؛ یافته مستقل ثبت نشده. rollback و ارتقای داده واقعی نیازمند تست مستقل است. |
| `apps/discovery/migrations/0016_instagram_collection_state.py` [^https://github.com/mtaham84/moshtari-yab/blob/a6aa1fcb1f9c716e49d1b462989bfb636409b106/apps/discovery/migrations/0016_instagram_collection_state.py] | Python؛ migration | 27 خط؛ max CC=1 | اسکن ساختاری؛ یافته مستقل ثبت نشده. rollback و ارتقای داده واقعی نیازمند تست مستقل است. |
| `apps/discovery/migrations/0017_instagram_category_targets.py` [^https://github.com/mtaham84/moshtari-yab/blob/a6aa1fcb1f9c716e49d1b462989bfb636409b106/apps/discovery/migrations/0017_instagram_category_targets.py] | Python؛ migration | 32 خط؛ max CC=1 | اسکن ساختاری؛ یافته مستقل ثبت نشده. rollback و ارتقای داده واقعی نیازمند تست مستقل است. |
| `apps/discovery/migrations/0018_instagramengineaccount_instagramenginelead.py` [^https://github.com/mtaham84/moshtari-yab/blob/a6aa1fcb1f9c716e49d1b462989bfb636409b106/apps/discovery/migrations/0018_instagramengineaccount_instagramenginelead.py] | Python؛ migration | 64 خط؛ max CC=1 | اسکن ساختاری؛ یافته مستقل ثبت نشده. rollback و ارتقای داده واقعی نیازمند تست مستقل است. |
| `apps/discovery/migrations/0019_instagramcrawledpost.py` [^https://github.com/mtaham84/moshtari-yab/blob/a6aa1fcb1f9c716e49d1b462989bfb636409b106/apps/discovery/migrations/0019_instagramcrawledpost.py] | Python؛ migration | 32 خط؛ max CC=1 | اسکن ساختاری؛ یافته مستقل ثبت نشده. rollback و ارتقای داده واقعی نیازمند تست مستقل است. |
| `apps/discovery/migrations/0020_instagramcrawlexecution.py` [^https://github.com/mtaham84/moshtari-yab/blob/a6aa1fcb1f9c716e49d1b462989bfb636409b106/apps/discovery/migrations/0020_instagramcrawlexecution.py] | Python؛ migration | 42 خط؛ max CC=1 | اسکن ساختاری؛ یافته مستقل ثبت نشده. rollback و ارتقای داده واقعی نیازمند تست مستقل است. |
| `apps/discovery/migrations/0021_opportunity_intent_type.py` [^https://github.com/mtaham84/moshtari-yab/blob/a6aa1fcb1f9c716e49d1b462989bfb636409b106/apps/discovery/migrations/0021_opportunity_intent_type.py] | Python؛ migration | 29 خط؛ max CC=1 | اسکن ساختاری؛ یافته مستقل ثبت نشده. rollback و ارتقای داده واقعی نیازمند تست مستقل است. |
| `apps/discovery/migrations/0022_xcrawledpost.py` [^https://github.com/mtaham84/moshtari-yab/blob/a6aa1fcb1f9c716e49d1b462989bfb636409b106/apps/discovery/migrations/0022_xcrawledpost.py] | Python؛ migration | 33 خط؛ max CC=1 | اسکن ساختاری؛ یافته مستقل ثبت نشده. rollback و ارتقای داده واقعی نیازمند تست مستقل است. |
| `apps/discovery/migrations/0023_twitterengineaccount.py` [^https://github.com/mtaham84/moshtari-yab/blob/a6aa1fcb1f9c716e49d1b462989bfb636409b106/apps/discovery/migrations/0023_twitterengineaccount.py] | Python؛ migration | 50 خط؛ max CC=1 | اسکن ساختاری؛ یافته مستقل ثبت نشده. rollback و ارتقای داده واقعی نیازمند تست مستقل است. |
| `apps/discovery/migrations/0024_instagramengineaccount_status_message.py` [^https://github.com/mtaham84/moshtari-yab/blob/a6aa1fcb1f9c716e49d1b462989bfb636409b106/apps/discovery/migrations/0024_instagramengineaccount_status_message.py] | Python؛ migration | 16 خط؛ max CC=1 | اسکن ساختاری؛ یافته مستقل ثبت نشده. rollback و ارتقای داده واقعی نیازمند تست مستقل است. |
| `apps/discovery/migrations/0025_proxyconfig.py` [^https://github.com/mtaham84/moshtari-yab/blob/a6aa1fcb1f9c716e49d1b462989bfb636409b106/apps/discovery/migrations/0025_proxyconfig.py] | Python؛ migration | 38 خط؛ max CC=1 | اسکن ساختاری؛ یافته مستقل ثبت نشده. rollback و ارتقای داده واقعی نیازمند تست مستقل است. |
| `apps/discovery/migrations/0026_instagram_family_plans_replies_and_cache.py` [^https://github.com/mtaham84/moshtari-yab/blob/a6aa1fcb1f9c716e49d1b462989bfb636409b106/apps/discovery/migrations/0026_instagram_family_plans_replies_and_cache.py] | Python؛ migration | 93 خط؛ max CC=1 | اسکن ساختاری؛ یافته مستقل ثبت نشده. rollback و ارتقای داده واقعی نیازمند تست مستقل است. |
| `apps/discovery/migrations/0027_instagram_lead_public_signals.py` [^https://github.com/mtaham84/moshtari-yab/blob/a6aa1fcb1f9c716e49d1b462989bfb636409b106/apps/discovery/migrations/0027_instagram_lead_public_signals.py] | Python؛ migration | 28 خط؛ max CC=1 | اسکن ساختاری؛ یافته مستقل ثبت نشده. rollback و ارتقای داده واقعی نیازمند تست مستقل است. |
| `apps/discovery/migrations/0028_reply_history_and_sent_state.py` [^https://github.com/mtaham84/moshtari-yab/blob/a6aa1fcb1f9c716e49d1b462989bfb636409b106/apps/discovery/migrations/0028_reply_history_and_sent_state.py] | Python؛ migration | 28 خط؛ max CC=1 | اسکن ساختاری؛ یافته مستقل ثبت نشده. rollback و ارتقای داده واقعی نیازمند تست مستقل است. |
| `apps/discovery/migrations/0029_instagram_comment_cache_marker.py` [^https://github.com/mtaham84/moshtari-yab/blob/a6aa1fcb1f9c716e49d1b462989bfb636409b106/apps/discovery/migrations/0029_instagram_comment_cache_marker.py] | Python؛ migration | 18 خط؛ max CC=1 | اسکن ساختاری؛ یافته مستقل ثبت نشده. rollback و ارتقای داده واقعی نیازمند تست مستقل است. |
| `apps/discovery/migrations/0030_instagram_request_budget_counts.py` [^https://github.com/mtaham84/moshtari-yab/blob/a6aa1fcb1f9c716e49d1b462989bfb636409b106/apps/discovery/migrations/0030_instagram_request_budget_counts.py] | Python؛ migration | 18 خط؛ max CC=1 | اسکن ساختاری؛ یافته مستقل ثبت نشده. rollback و ارتقای داده واقعی نیازمند تست مستقل است. |
| `apps/discovery/migrations/0031_instagram_outreach_tiers_and_replies.py` [^https://github.com/mtaham84/moshtari-yab/blob/a6aa1fcb1f9c716e49d1b462989bfb636409b106/apps/discovery/migrations/0031_instagram_outreach_tiers_and_replies.py] | Python؛ migration | 103 خط؛ max CC=1 | اسکن ساختاری؛ یافته مستقل ثبت نشده. rollback و ارتقای داده واقعی نیازمند تست مستقل است. |
| `apps/discovery/migrations/__init__.py` [^https://github.com/mtaham84/moshtari-yab/blob/a6aa1fcb1f9c716e49d1b462989bfb636409b106/apps/discovery/migrations/__init__.py] | Python؛ migration | 0 خط | اسکن ساختاری؛ یافته مستقل ثبت نشده. rollback و ارتقای داده واقعی نیازمند تست مستقل است. |
| `apps/discovery/models.py` [^https://github.com/mtaham84/moshtari-yab/blob/a6aa1fcb1f9c716e49d1b462989bfb636409b106/apps/discovery/models.py] | Python | 1486 خط؛ max CC=35 | شاهد هدفمند: R08, R21, R25؛ شرح و اصلاح در گزارش اصلی. |
| `apps/discovery/outreach_service.py` [^https://github.com/mtaham84/moshtari-yab/blob/a6aa1fcb1f9c716e49d1b462989bfb636409b106/apps/discovery/outreach_service.py] | Python | 523 خط؛ max CC=35؛ except عمومی=6؛ تابع>100=1 | شاهد هدفمند: R01, R12؛ شرح و اصلاح در گزارش اصلی. |
| `apps/discovery/post_filters.py` [^https://github.com/mtaham84/moshtari-yab/blob/a6aa1fcb1f9c716e49d1b462989bfb636409b106/apps/discovery/post_filters.py] | Python | 156 خط؛ max CC=9؛ clone=153 | شاهد هدفمند: R40؛ شرح و اصلاح در گزارش اصلی. |
| `apps/discovery/retention.py` [^https://github.com/mtaham84/moshtari-yab/blob/a6aa1fcb1f9c716e49d1b462989bfb636409b106/apps/discovery/retention.py] | Python | 16 خط؛ max CC=1 | اسکن ساختاری؛ یافته مستقل ثبت نشده. توابع نمونه برای پیگیری: `customer_ids_to_anonymize`. |
| `apps/discovery/semantic_moderation.py` [^https://github.com/mtaham84/moshtari-yab/blob/a6aa1fcb1f9c716e49d1b462989bfb636409b106/apps/discovery/semantic_moderation.py] | Python | 286 خط؛ max CC=33؛ except عمومی=5 | اسکن ساختاری؛ یافته مستقل ثبت نشده. بررسی تکمیلی: خطای قابل‌بازیابی از خطای داده/مجوز تفکیک شود. |
| `apps/discovery/services.py` [^https://github.com/mtaham84/moshtari-yab/blob/a6aa1fcb1f9c716e49d1b462989bfb636409b106/apps/discovery/services.py] | Python | 491 خط؛ max CC=85؛ except عمومی=3؛ تابع>100=1 | اسکن ساختاری؛ یافته مستقل ثبت نشده. مرز مسئولیت `sync_instagram_lead_to_opportunity` در 103–300 بررسی تکمیلی شود. |
| `apps/discovery/sources.py` [^https://github.com/mtaham84/moshtari-yab/blob/a6aa1fcb1f9c716e49d1b462989bfb636409b106/apps/discovery/sources.py] | Python | 69 خط؛ max CC=5 | اسکن ساختاری؛ یافته مستقل ثبت نشده. توابع نمونه برای پیگیری: `max_private_sources`, `normalize_link`. |
| `apps/discovery/templatetags/__init__.py` [^https://github.com/mtaham84/moshtari-yab/blob/a6aa1fcb1f9c716e49d1b462989bfb636409b106/apps/discovery/templatetags/__init__.py] | Python | 0 خط | اسکن ساختاری؛ یافته مستقل ثبت نشده.  |
| `apps/discovery/templatetags/x_tags.py` [^https://github.com/mtaham84/moshtari-yab/blob/a6aa1fcb1f9c716e49d1b462989bfb636409b106/apps/discovery/templatetags/x_tags.py] | Python | 20 خط؛ max CC=5 | اسکن ساختاری؛ یافته مستقل ثبت نشده. توابع نمونه برای پیگیری: `ago`. |
| `apps/discovery/test_accounts_pool_file.py` [^https://github.com/mtaham84/moshtari-yab/blob/a6aa1fcb1f9c716e49d1b462989bfb636409b106/apps/discovery/test_accounts_pool_file.py] | Python؛ تست/اسکریپت آزمون | 59 خط؛ max CC=3 | اسکن ساختاری؛ یافته مستقل ثبت نشده. نتیجه runnerها در test-results.md؛ پوشش این فایل از نام آن استنباط نمی‌شود. |
| `apps/discovery/test_ig_session.py` [^https://github.com/mtaham84/moshtari-yab/blob/a6aa1fcb1f9c716e49d1b462989bfb636409b106/apps/discovery/test_ig_session.py] | Python؛ تست/اسکریپت آزمون | 105 خط؛ max CC=11 | اسکن ساختاری؛ یافته مستقل ثبت نشده. نتیجه runnerها در test-results.md؛ پوشش این فایل از نام آن استنباط نمی‌شود. |
| `apps/discovery/test_instagram_family_reply.py` [^https://github.com/mtaham84/moshtari-yab/blob/a6aa1fcb1f9c716e49d1b462989bfb636409b106/apps/discovery/test_instagram_family_reply.py] | Python؛ تست/اسکریپت آزمون | 329 خط؛ max CC=3 | اسکن ساختاری؛ یافته مستقل ثبت نشده. نتیجه runnerها در test-results.md؛ پوشش این فایل از نام آن استنباط نمی‌شود. |
| `apps/discovery/test_sources.py` [^https://github.com/mtaham84/moshtari-yab/blob/a6aa1fcb1f9c716e49d1b462989bfb636409b106/apps/discovery/test_sources.py] | Python؛ تست/اسکریپت آزمون | 110 خط؛ max CC=2 | اسکن ساختاری؛ یافته مستقل ثبت نشده. نتیجه runnerها در test-results.md؛ پوشش این فایل از نام آن استنباط نمی‌شود. |
| `apps/discovery/test_x_query_cleanup.py` [^https://github.com/mtaham84/moshtari-yab/blob/a6aa1fcb1f9c716e49d1b462989bfb636409b106/apps/discovery/test_x_query_cleanup.py] | Python؛ تست/اسکریپت آزمون | 30 خط؛ max CC=2 | اسکن ساختاری؛ یافته مستقل ثبت نشده. نتیجه runnerها در test-results.md؛ پوشش این فایل از نام آن استنباط نمی‌شود. |
| `apps/discovery/tests.py` [^https://github.com/mtaham84/moshtari-yab/blob/a6aa1fcb1f9c716e49d1b462989bfb636409b106/apps/discovery/tests.py] | Python؛ تست/اسکریپت آزمون | 1181 خط؛ max CC=8 | اسکن ساختاری؛ یافته مستقل ثبت نشده. نتیجه runnerها در test-results.md؛ پوشش این فایل از نام آن استنباط نمی‌شود. |
| `apps/discovery/tests_funnel.py` [^https://github.com/mtaham84/moshtari-yab/blob/a6aa1fcb1f9c716e49d1b462989bfb636409b106/apps/discovery/tests_funnel.py] | Python؛ تست/اسکریپت آزمون | 140 خط؛ max CC=18 | اسکن ساختاری؛ یافته مستقل ثبت نشده. نتیجه runnerها در test-results.md؛ پوشش این فایل از نام آن استنباط نمی‌شود. |
| `apps/discovery/urls.py` [^https://github.com/mtaham84/moshtari-yab/blob/a6aa1fcb1f9c716e49d1b462989bfb636409b106/apps/discovery/urls.py] | Python | 28 خط | اسکن ساختاری؛ یافته مستقل ثبت نشده.  |
| `apps/discovery/views.py` [^https://github.com/mtaham84/moshtari-yab/blob/a6aa1fcb1f9c716e49d1b462989bfb636409b106/apps/discovery/views.py] | Python | 1486 خط؛ max CC=85؛ except عمومی=5؛ clone=16؛ تابع>100=5 | اسکن ساختاری؛ یافته مستقل ثبت نشده. مرز مسئولیت `opportunity_export_view` در 399–577 بررسی تکمیلی شود. |
| `apps/discovery/x_activity.py` [^https://github.com/mtaham84/moshtari-yab/blob/a6aa1fcb1f9c716e49d1b462989bfb636409b106/apps/discovery/x_activity.py] | Python | 241 خط؛ max CC=31؛ except عمومی=3 | اسکن ساختاری؛ یافته مستقل ثبت نشده. بررسی تکمیلی: خطای قابل‌بازیابی از خطای داده/مجوز تفکیک شود. |
| `apps/products/__init__.py` [^https://github.com/mtaham84/moshtari-yab/blob/a6aa1fcb1f9c716e49d1b462989bfb636409b106/apps/products/__init__.py] | Python | 0 خط | اسکن ساختاری؛ یافته مستقل ثبت نشده.  |
| `apps/products/agent_card.py` [^https://github.com/mtaham84/moshtari-yab/blob/a6aa1fcb1f9c716e49d1b462989bfb636409b106/apps/products/agent_card.py] | Python | 61 خط؛ max CC=7 | اسکن ساختاری؛ یافته مستقل ثبت نشده. توابع نمونه برای پیگیری: `engine_row`, `card_state`. |
| `apps/products/apps.py` [^https://github.com/mtaham84/moshtari-yab/blob/a6aa1fcb1f9c716e49d1b462989bfb636409b106/apps/products/apps.py] | Python | 6 خط؛ max CC=1 | اسکن ساختاری؛ یافته مستقل ثبت نشده.  |
| `apps/products/autofill.py` [^https://github.com/mtaham84/moshtari-yab/blob/a6aa1fcb1f9c716e49d1b462989bfb636409b106/apps/products/autofill.py] | Python | 286 خط؛ max CC=33؛ except عمومی=2 | اسکن ساختاری؛ یافته مستقل ثبت نشده. بررسی تکمیلی: خطای قابل‌بازیابی از خطای داده/مجوز تفکیک شود. |
| `apps/products/management/__init__.py` [^https://github.com/mtaham84/moshtari-yab/blob/a6aa1fcb1f9c716e49d1b462989bfb636409b106/apps/products/management/__init__.py] | Python | 0 خط | اسکن ساختاری؛ یافته مستقل ثبت نشده.  |
| `apps/products/management/commands/__init__.py` [^https://github.com/mtaham84/moshtari-yab/blob/a6aa1fcb1f9c716e49d1b462989bfb636409b106/apps/products/management/commands/__init__.py] | Python | 0 خط | اسکن ساختاری؛ یافته مستقل ثبت نشده.  |
| `apps/products/management/commands/seed_products.py` [^https://github.com/mtaham84/moshtari-yab/blob/a6aa1fcb1f9c716e49d1b462989bfb636409b106/apps/products/management/commands/seed_products.py] | Python | 253 خط؛ max CC=3؛ تابع>100=1 | اسکن ساختاری؛ یافته مستقل ثبت نشده. مرز مسئولیت `handle` در 11–253 بررسی تکمیلی شود. |
| `apps/products/migrations/0001_initial.py` [^https://github.com/mtaham84/moshtari-yab/blob/a6aa1fcb1f9c716e49d1b462989bfb636409b106/apps/products/migrations/0001_initial.py] | Python؛ migration | 58 خط؛ max CC=1 | اسکن ساختاری؛ یافته مستقل ثبت نشده. rollback و ارتقای داده واقعی نیازمند تست مستقل است. |
| `apps/products/migrations/0002_productimage.py` [^https://github.com/mtaham84/moshtari-yab/blob/a6aa1fcb1f9c716e49d1b462989bfb636409b106/apps/products/migrations/0002_productimage.py] | Python؛ migration | 30 خط؛ max CC=1 | اسکن ساختاری؛ یافته مستقل ثبت نشده. rollback و ارتقای داده واقعی نیازمند تست مستقل است. |
| `apps/products/migrations/0003_category_business_product_discovery_priority_and_more.py` [^https://github.com/mtaham84/moshtari-yab/blob/a6aa1fcb1f9c716e49d1b462989bfb636409b106/apps/products/migrations/0003_category_business_product_discovery_priority_and_more.py] | Python؛ migration | 45 خط؛ max CC=1 | اسکن ساختاری؛ یافته مستقل ثبت نشده. rollback و ارتقای داده واقعی نیازمند تست مستقل است. |
| `apps/products/migrations/0004_category_depth_category_embedding_and_more.py` [^https://github.com/mtaham84/moshtari-yab/blob/a6aa1fcb1f9c716e49d1b462989bfb636409b106/apps/products/migrations/0004_category_depth_category_embedding_and_more.py] | Python؛ migration | 53 خط؛ max CC=1 | اسکن ساختاری؛ یافته مستقل ثبت نشده. rollback و ارتقای داده واقعی نیازمند تست مستقل است. |
| `apps/products/migrations/0005_engine_cleanup.py` [^https://github.com/mtaham84/moshtari-yab/blob/a6aa1fcb1f9c716e49d1b462989bfb636409b106/apps/products/migrations/0005_engine_cleanup.py] | Python؛ migration | 29 خط؛ max CC=1 | اسکن ساختاری؛ یافته مستقل ثبت نشده. rollback و ارتقای داده واقعی نیازمند تست مستقل است. |
| `apps/products/migrations/0006_product_agent_card_override.py` [^https://github.com/mtaham84/moshtari-yab/blob/a6aa1fcb1f9c716e49d1b462989bfb636409b106/apps/products/migrations/0006_product_agent_card_override.py] | Python؛ migration | 18 خط؛ max CC=1 | اسکن ساختاری؛ یافته مستقل ثبت نشده. rollback و ارتقای داده واقعی نیازمند تست مستقل است. |
| `apps/products/migrations/0007_product_x_search_enabled.py` [^https://github.com/mtaham84/moshtari-yab/blob/a6aa1fcb1f9c716e49d1b462989bfb636409b106/apps/products/migrations/0007_product_x_search_enabled.py] | Python؛ migration | 18 خط؛ max CC=1 | اسکن ساختاری؛ یافته مستقل ثبت نشده. rollback و ارتقای داده واقعی نیازمند تست مستقل است. |
| `apps/products/migrations/0008_alter_product_url.py` [^https://github.com/mtaham84/moshtari-yab/blob/a6aa1fcb1f9c716e49d1b462989bfb636409b106/apps/products/migrations/0008_alter_product_url.py] | Python؛ migration | 18 خط؛ max CC=1 | اسکن ساختاری؛ یافته مستقل ثبت نشده. rollback و ارتقای داده واقعی نیازمند تست مستقل است. |
| `apps/products/migrations/0009_product_instagram_targets.py` [^https://github.com/mtaham84/moshtari-yab/blob/a6aa1fcb1f9c716e49d1b462989bfb636409b106/apps/products/migrations/0009_product_instagram_targets.py] | Python؛ migration | 13 خط؛ max CC=1 | اسکن ساختاری؛ یافته مستقل ثبت نشده. rollback و ارتقای داده واقعی نیازمند تست مستقل است. |
| `apps/products/migrations/0010_category_origin_category_source.py` [^https://github.com/mtaham84/moshtari-yab/blob/a6aa1fcb1f9c716e49d1b462989bfb636409b106/apps/products/migrations/0010_category_origin_category_source.py] | Python؛ migration | 24 خط؛ max CC=1 | اسکن ساختاری؛ یافته مستقل ثبت نشده. rollback و ارتقای داده واقعی نیازمند تست مستقل است. |
| `apps/products/migrations/__init__.py` [^https://github.com/mtaham84/moshtari-yab/blob/a6aa1fcb1f9c716e49d1b462989bfb636409b106/apps/products/migrations/__init__.py] | Python؛ migration | 0 خط | اسکن ساختاری؛ یافته مستقل ثبت نشده. rollback و ارتقای داده واقعی نیازمند تست مستقل است. |
| `apps/products/models.py` [^https://github.com/mtaham84/moshtari-yab/blob/a6aa1fcb1f9c716e49d1b462989bfb636409b106/apps/products/models.py] | Python | 319 خط؛ max CC=11؛ except عمومی=1 | شاهد هدفمند: R05, R20, R35, R36, R37؛ شرح و اصلاح در گزارش اصلی. |
| `apps/products/services.py` [^https://github.com/mtaham84/moshtari-yab/blob/a6aa1fcb1f9c716e49d1b462989bfb636409b106/apps/products/services.py] | Python | 352 خط؛ max CC=83؛ except عمومی=3؛ تابع>100=1 | شاهد هدفمند: R07, R13, R36؛ شرح و اصلاح در گزارش اصلی. |
| `apps/products/taxonomy.py` [^https://github.com/mtaham84/moshtari-yab/blob/a6aa1fcb1f9c716e49d1b462989bfb636409b106/apps/products/taxonomy.py] | Python | 359 خط؛ max CC=27؛ except عمومی=2 | شاهد هدفمند: R24, R37؛ شرح و اصلاح در گزارش اصلی. |
| `apps/products/tests.py` [^https://github.com/mtaham84/moshtari-yab/blob/a6aa1fcb1f9c716e49d1b462989bfb636409b106/apps/products/tests.py] | Python؛ تست/اسکریپت آزمون | 734 خط؛ max CC=2 | اسکن ساختاری؛ یافته مستقل ثبت نشده. نتیجه runnerها در test-results.md؛ پوشش این فایل از نام آن استنباط نمی‌شود. |
| `apps/products/urls.py` [^https://github.com/mtaham84/moshtari-yab/blob/a6aa1fcb1f9c716e49d1b462989bfb636409b106/apps/products/urls.py] | Python | 22 خط | اسکن ساختاری؛ یافته مستقل ثبت نشده.  |
| `apps/products/views.py` [^https://github.com/mtaham84/moshtari-yab/blob/a6aa1fcb1f9c716e49d1b462989bfb636409b106/apps/products/views.py] | Python | 651 خط؛ max CC=26؛ except عمومی=5؛ clone=16؛ تابع>100=2 | شاهد هدفمند: R05, R06, R07, R13, R19, R20, R21, R22, R35, R41؛ شرح و اصلاح در گزارش اصلی. |

## config

| فایل و منبع | نوع / دامنه | سنجه | بررسی و نتیجه |
|---|---|---|---|
| `config/__init__.py` [^https://github.com/mtaham84/moshtari-yab/blob/a6aa1fcb1f9c716e49d1b462989bfb636409b106/config/__init__.py] | Python | 1 خط | اسکن ساختاری؛ یافته مستقل ثبت نشده.  |
| `config/asgi.py` [^https://github.com/mtaham84/moshtari-yab/blob/a6aa1fcb1f9c716e49d1b462989bfb636409b106/config/asgi.py] | Python | 7 خط | اسکن ساختاری؛ یافته مستقل ثبت نشده.  |
| `config/settings.py` [^https://github.com/mtaham84/moshtari-yab/blob/a6aa1fcb1f9c716e49d1b462989bfb636409b106/config/settings.py] | Python | 212 خط | شاهد هدفمند: R28؛ شرح و اصلاح در گزارش اصلی. |
| `config/urls.py` [^https://github.com/mtaham84/moshtari-yab/blob/a6aa1fcb1f9c716e49d1b462989bfb636409b106/config/urls.py] | Python | 30 خط | شاهد هدفمند: R05؛ شرح و اصلاح در گزارش اصلی. |
| `config/wsgi.py` [^https://github.com/mtaham84/moshtari-yab/blob/a6aa1fcb1f9c716e49d1b462989bfb636409b106/config/wsgi.py] | Python | 7 خط | اسکن ساختاری؛ یافته مستقل ثبت نشده.  |

## فایل‌های ریشه

| فایل و منبع | نوع / دامنه | سنجه | بررسی و نتیجه |
|---|---|---|---|
| `conftest.py` [^https://github.com/mtaham84/moshtari-yab/blob/a6aa1fcb1f9c716e49d1b462989bfb636409b106/conftest.py] | Python؛ تست/اسکریپت آزمون | 55 خط؛ max CC=4؛ except عمومی=1 | اسکن ساختاری؛ یافته مستقل ثبت نشده. نتیجه runnerها در test-results.md؛ پوشش این فایل از نام آن استنباط نمی‌شود. |

## data

| فایل و منبع | نوع / دامنه | سنجه | بررسی و نتیجه |
|---|---|---|---|
| `data/golden/need_golden.jsonl` [^https://github.com/mtaham84/moshtari-yab/blob/a6aa1fcb1f9c716e49d1b462989bfb636409b106/data/golden/need_golden.jsonl] | متن .jsonl | 30 خط | فهرست/بازرسی متنی در دامنه ابزار؛ تأیید معنایی کامل انجام نشده.  |
| `data/ig_lexicon_fa.json` [^https://github.com/mtaham84/moshtari-yab/blob/a6aa1fcb1f9c716e49d1b462989bfb636409b106/data/ig_lexicon_fa.json] | متن .json | 121 خط | فهرست/بازرسی متنی در دامنه ابزار؛ تأیید معنایی کامل انجام نشده.  |
| `data/ig_seeds.example.json` [^https://github.com/mtaham84/moshtari-yab/blob/a6aa1fcb1f9c716e49d1b462989bfb636409b106/data/ig_seeds.example.json] | متن .json | 5 خط | فهرست/بازرسی متنی در دامنه ابزار؛ تأیید معنایی کامل انجام نشده.  |
| `data/sarv_outreach_config.json` [^https://github.com/mtaham84/moshtari-yab/blob/a6aa1fcb1f9c716e49d1b462989bfb636409b106/data/sarv_outreach_config.json] | متن .json | 57 خط | فهرست/بازرسی متنی در دامنه ابزار؛ تأیید معنایی کامل انجام نشده.  |

## فایل‌های ریشه

| فایل و منبع | نوع / دامنه | سنجه | بررسی و نتیجه |
|---|---|---|---|
| `docker-compose.yml` [^https://github.com/mtaham84/moshtari-yab/blob/a6aa1fcb1f9c716e49d1b462989bfb636409b106/docker-compose.yml] | متن .yml | 204 خط | شاهد هدفمند: R38؛ شرح و اصلاح در گزارش اصلی. |

## docker

| فایل و منبع | نوع / دامنه | سنجه | بررسی و نتیجه |
|---|---|---|---|
| `docker/entrypoint.sh` [^https://github.com/mtaham84/moshtari-yab/blob/a6aa1fcb1f9c716e49d1b462989bfb636409b106/docker/entrypoint.sh] | متن .sh | 76 خط | شاهد هدفمند: R38؛ شرح و اصلاح در گزارش اصلی. |
| `docker/global_sources.example.txt` [^https://github.com/mtaham84/moshtari-yab/blob/a6aa1fcb1f9c716e49d1b462989bfb636409b106/docker/global_sources.example.txt] | متن .txt | 4 خط | فهرست/بازرسی متنی در دامنه ابزار؛ تأیید معنایی کامل انجام نشده.  |
| `docker/patch_twitter_cli.py` [^https://github.com/mtaham84/moshtari-yab/blob/a6aa1fcb1f9c716e49d1b462989bfb636409b106/docker/patch_twitter_cli.py] | Python | 89 خط | اسکن ساختاری؛ یافته مستقل ثبت نشده.  |

## docs

| فایل و منبع | نوع / دامنه | سنجه | بررسی و نتیجه |
|---|---|---|---|
| `docs/IG_COLLECTION_ANALYSIS.md` [^https://github.com/mtaham84/moshtari-yab/blob/a6aa1fcb1f9c716e49d1b462989bfb636409b106/docs/IG_COLLECTION_ANALYSIS.md] | متن .md | 33 خط | فهرست/بازرسی متنی در دامنه ابزار؛ تأیید معنایی کامل انجام نشده.  |
| `docs/INSTAGRAM_SESSIONS.md` [^https://github.com/mtaham84/moshtari-yab/blob/a6aa1fcb1f9c716e49d1b462989bfb636409b106/docs/INSTAGRAM_SESSIONS.md] | متن .md | 24 خط | فهرست/بازرسی متنی در دامنه ابزار؛ تأیید معنایی کامل انجام نشده.  |
| `docs/SECURITY_CLEANUP.md` [^https://github.com/mtaham84/moshtari-yab/blob/a6aa1fcb1f9c716e49d1b462989bfb636409b106/docs/SECURITY_CLEANUP.md] | متن .md | 19 خط | فهرست/بازرسی متنی در دامنه ابزار؛ تأیید معنایی کامل انجام نشده.  |
| `docs/X_COLLECTION_ANALYSIS.md` [^https://github.com/mtaham84/moshtari-yab/blob/a6aa1fcb1f9c716e49d1b462989bfb636409b106/docs/X_COLLECTION_ANALYSIS.md] | متن .md | 28 خط | فهرست/بازرسی متنی در دامنه ابزار؛ تأیید معنایی کامل انجام نشده.  |
| `docs/X_GROWTH_PREFLIGHT.md` [^https://github.com/mtaham84/moshtari-yab/blob/a6aa1fcb1f9c716e49d1b462989bfb636409b106/docs/X_GROWTH_PREFLIGHT.md] | متن .md | 42 خط | فهرست/بازرسی متنی در دامنه ابزار؛ تأیید معنایی کامل انجام نشده.  |
| `docs/X_INTENT_PREFLIGHT.md` [^https://github.com/mtaham84/moshtari-yab/blob/a6aa1fcb1f9c716e49d1b462989bfb636409b106/docs/X_INTENT_PREFLIGHT.md] | متن .md | 37 خط | فهرست/بازرسی متنی در دامنه ابزار؛ تأیید معنایی کامل انجام نشده.  |
| `docs/X_NEED_ENGINE_ANALYSIS.md` [^https://github.com/mtaham84/moshtari-yab/blob/a6aa1fcb1f9c716e49d1b462989bfb636409b106/docs/X_NEED_ENGINE_ANALYSIS.md] | متن .md | 32 خط | فهرست/بازرسی متنی در دامنه ابزار؛ تأیید معنایی کامل انجام نشده.  |
| `docs/architecture/BOUNDARIES.md` [^https://github.com/mtaham84/moshtari-yab/blob/a6aa1fcb1f9c716e49d1b462989bfb636409b106/docs/architecture/BOUNDARIES.md] | متن .md | 38 خط | شاهد هدفمند: R42؛ شرح و اصلاح در گزارش اصلی. |
| `docs/architecture/CLEANUP_CANDIDATES.md` [^https://github.com/mtaham84/moshtari-yab/blob/a6aa1fcb1f9c716e49d1b462989bfb636409b106/docs/architecture/CLEANUP_CANDIDATES.md] | متن .md | 46 خط | شاهد هدفمند: R42؛ شرح و اصلاح در گزارش اصلی. |
| `docs/architecture/CONFIG.md` [^https://github.com/mtaham84/moshtari-yab/blob/a6aa1fcb1f9c716e49d1b462989bfb636409b106/docs/architecture/CONFIG.md] | متن .md | 254 خط | فهرست/بازرسی متنی در دامنه ابزار؛ تأیید معنایی کامل انجام نشده.  |
| `docs/architecture/DATA_FLOW.md` [^https://github.com/mtaham84/moshtari-yab/blob/a6aa1fcb1f9c716e49d1b462989bfb636409b106/docs/architecture/DATA_FLOW.md] | متن .md | 28 خط | فهرست/بازرسی متنی در دامنه ابزار؛ تأیید معنایی کامل انجام نشده.  |
| `docs/architecture/OVERVIEW.md` [^https://github.com/mtaham84/moshtari-yab/blob/a6aa1fcb1f9c716e49d1b462989bfb636409b106/docs/architecture/OVERVIEW.md] | متن .md | 18 خط | فهرست/بازرسی متنی در دامنه ابزار؛ تأیید معنایی کامل انجام نشده.  |
| `docs/architecture/QUESTIONS.md` [^https://github.com/mtaham84/moshtari-yab/blob/a6aa1fcb1f9c716e49d1b462989bfb636409b106/docs/architecture/QUESTIONS.md] | متن .md | 24 خط | فهرست/بازرسی متنی در دامنه ابزار؛ تأیید معنایی کامل انجام نشده.  |
| `docs/architecture/dependency-graph.md` [^https://github.com/mtaham84/moshtari-yab/blob/a6aa1fcb1f9c716e49d1b462989bfb636409b106/docs/architecture/dependency-graph.md] | متن .md | 37 خط | فهرست/بازرسی متنی در دامنه ابزار؛ تأیید معنایی کامل انجام نشده.  |
| `docs/business_plan_pages/bp_page-1.png` [^https://github.com/mtaham84/moshtari-yab/blob/a6aa1fcb1f9c716e49d1b462989bfb636409b106/docs/business_plan_pages/bp_page-1.png] | asset / باینری | 1,116,250 بایت | فقط فهرست دارایی؛ محتوا، مجوز و metadata ممیزی نشده.  |
| `docs/business_plan_pages/bp_page-2.png` [^https://github.com/mtaham84/moshtari-yab/blob/a6aa1fcb1f9c716e49d1b462989bfb636409b106/docs/business_plan_pages/bp_page-2.png] | asset / باینری | 262,332 بایت | فقط فهرست دارایی؛ محتوا، مجوز و metadata ممیزی نشده.  |
| `docs/business_plan_pages/bp_page-3.png` [^https://github.com/mtaham84/moshtari-yab/blob/a6aa1fcb1f9c716e49d1b462989bfb636409b106/docs/business_plan_pages/bp_page-3.png] | asset / باینری | 969,507 بایت | فقط فهرست دارایی؛ محتوا، مجوز و metadata ممیزی نشده.  |
| `docs/business_plan_pages/bp_page-4.png` [^https://github.com/mtaham84/moshtari-yab/blob/a6aa1fcb1f9c716e49d1b462989bfb636409b106/docs/business_plan_pages/bp_page-4.png] | asset / باینری | 71,267 بایت | فقط فهرست دارایی؛ محتوا، مجوز و metadata ممیزی نشده.  |
| `docs/business_plan_pages/bp_page-5.png` [^https://github.com/mtaham84/moshtari-yab/blob/a6aa1fcb1f9c716e49d1b462989bfb636409b106/docs/business_plan_pages/bp_page-5.png] | asset / باینری | 261,422 بایت | فقط فهرست دارایی؛ محتوا، مجوز و metadata ممیزی نشده.  |
| `docs/business_plan_pages/bp_page-6.png` [^https://github.com/mtaham84/moshtari-yab/blob/a6aa1fcb1f9c716e49d1b462989bfb636409b106/docs/business_plan_pages/bp_page-6.png] | asset / باینری | 190,088 بایت | فقط فهرست دارایی؛ محتوا، مجوز و metadata ممیزی نشده.  |
| `docs/index.html` [^https://github.com/mtaham84/moshtari-yab/blob/a6aa1fcb1f9c716e49d1b462989bfb636409b106/docs/index.html] | متن .html | 170 خط | فهرست/بازرسی متنی در دامنه ابزار؛ تأیید معنایی کامل انجام نشده.  |
| `docs/peyda_business_plan.pdf` [^https://github.com/mtaham84/moshtari-yab/blob/a6aa1fcb1f9c716e49d1b462989bfb636409b106/docs/peyda_business_plan.pdf] | asset / باینری | 2,124,761 بایت | فقط فهرست دارایی؛ محتوا، مجوز و metadata ممیزی نشده.  |
| `docs/peyda_pitch_deck.pdf` [^https://github.com/mtaham84/moshtari-yab/blob/a6aa1fcb1f9c716e49d1b462989bfb636409b106/docs/peyda_pitch_deck.pdf] | asset / باینری | 3,235,092 بایت | فقط فهرست دارایی؛ محتوا، مجوز و metadata ممیزی نشده.  |
| `docs/peyda_technical_documentation.pdf` [^https://github.com/mtaham84/moshtari-yab/blob/a6aa1fcb1f9c716e49d1b462989bfb636409b106/docs/peyda_technical_documentation.pdf] | asset / باینری | 2,248,274 بایت | فقط فهرست دارایی؛ محتوا، مجوز و metadata ممیزی نشده.  |
| `docs/slides/slide-01.png` [^https://github.com/mtaham84/moshtari-yab/blob/a6aa1fcb1f9c716e49d1b462989bfb636409b106/docs/slides/slide-01.png] | asset / باینری | 1,073,802 بایت | فقط فهرست دارایی؛ محتوا، مجوز و metadata ممیزی نشده.  |
| `docs/slides/slide-02.png` [^https://github.com/mtaham84/moshtari-yab/blob/a6aa1fcb1f9c716e49d1b462989bfb636409b106/docs/slides/slide-02.png] | asset / باینری | 186,038 بایت | فقط فهرست دارایی؛ محتوا، مجوز و metadata ممیزی نشده.  |
| `docs/slides/slide-03.png` [^https://github.com/mtaham84/moshtari-yab/blob/a6aa1fcb1f9c716e49d1b462989bfb636409b106/docs/slides/slide-03.png] | asset / باینری | 162,973 بایت | فقط فهرست دارایی؛ محتوا، مجوز و metadata ممیزی نشده.  |
| `docs/slides/slide-04.png` [^https://github.com/mtaham84/moshtari-yab/blob/a6aa1fcb1f9c716e49d1b462989bfb636409b106/docs/slides/slide-04.png] | asset / باینری | 168,243 بایت | فقط فهرست دارایی؛ محتوا، مجوز و metadata ممیزی نشده.  |
| `docs/slides/slide-05.png` [^https://github.com/mtaham84/moshtari-yab/blob/a6aa1fcb1f9c716e49d1b462989bfb636409b106/docs/slides/slide-05.png] | asset / باینری | 748,424 بایت | فقط فهرست دارایی؛ محتوا، مجوز و metadata ممیزی نشده.  |
| `docs/slides/slide-06.png` [^https://github.com/mtaham84/moshtari-yab/blob/a6aa1fcb1f9c716e49d1b462989bfb636409b106/docs/slides/slide-06.png] | asset / باینری | 204,681 بایت | فقط فهرست دارایی؛ محتوا، مجوز و metadata ممیزی نشده.  |
| `docs/slides/slide-07.png` [^https://github.com/mtaham84/moshtari-yab/blob/a6aa1fcb1f9c716e49d1b462989bfb636409b106/docs/slides/slide-07.png] | asset / باینری | 297,123 بایت | فقط فهرست دارایی؛ محتوا، مجوز و metadata ممیزی نشده.  |
| `docs/slides/slide-08.png` [^https://github.com/mtaham84/moshtari-yab/blob/a6aa1fcb1f9c716e49d1b462989bfb636409b106/docs/slides/slide-08.png] | asset / باینری | 172,136 بایت | فقط فهرست دارایی؛ محتوا، مجوز و metadata ممیزی نشده.  |
| `docs/slides/slide-09.png` [^https://github.com/mtaham84/moshtari-yab/blob/a6aa1fcb1f9c716e49d1b462989bfb636409b106/docs/slides/slide-09.png] | asset / باینری | 178,770 بایت | فقط فهرست دارایی؛ محتوا، مجوز و metadata ممیزی نشده.  |
| `docs/slides/slide-10.png` [^https://github.com/mtaham84/moshtari-yab/blob/a6aa1fcb1f9c716e49d1b462989bfb636409b106/docs/slides/slide-10.png] | asset / باینری | 168,237 بایت | فقط فهرست دارایی؛ محتوا، مجوز و metadata ممیزی نشده.  |
| `docs/slides/slide-11.png` [^https://github.com/mtaham84/moshtari-yab/blob/a6aa1fcb1f9c716e49d1b462989bfb636409b106/docs/slides/slide-11.png] | asset / باینری | 172,952 بایت | فقط فهرست دارایی؛ محتوا، مجوز و metadata ممیزی نشده.  |
| `docs/slides/slide-12.png` [^https://github.com/mtaham84/moshtari-yab/blob/a6aa1fcb1f9c716e49d1b462989bfb636409b106/docs/slides/slide-12.png] | asset / باینری | 141,050 بایت | فقط فهرست دارایی؛ محتوا، مجوز و metadata ممیزی نشده.  |
| `docs/slides/slide-13.png` [^https://github.com/mtaham84/moshtari-yab/blob/a6aa1fcb1f9c716e49d1b462989bfb636409b106/docs/slides/slide-13.png] | asset / باینری | 198,286 بایت | فقط فهرست دارایی؛ محتوا، مجوز و metadata ممیزی نشده.  |
| `docs/slides/slide-14.png` [^https://github.com/mtaham84/moshtari-yab/blob/a6aa1fcb1f9c716e49d1b462989bfb636409b106/docs/slides/slide-14.png] | asset / باینری | 158,478 بایت | فقط فهرست دارایی؛ محتوا، مجوز و metadata ممیزی نشده.  |
| `docs/slides/slide-15.png` [^https://github.com/mtaham84/moshtari-yab/blob/a6aa1fcb1f9c716e49d1b462989bfb636409b106/docs/slides/slide-15.png] | asset / باینری | 229,132 بایت | فقط فهرست دارایی؛ محتوا، مجوز و metadata ممیزی نشده.  |
| `docs/tech_doc_pages/tech_page-01.png` [^https://github.com/mtaham84/moshtari-yab/blob/a6aa1fcb1f9c716e49d1b462989bfb636409b106/docs/tech_doc_pages/tech_page-01.png] | asset / باینری | 587,379 بایت | فقط فهرست دارایی؛ محتوا، مجوز و metadata ممیزی نشده.  |
| `docs/tech_doc_pages/tech_page-02.png` [^https://github.com/mtaham84/moshtari-yab/blob/a6aa1fcb1f9c716e49d1b462989bfb636409b106/docs/tech_doc_pages/tech_page-02.png] | asset / باینری | 224,394 بایت | فقط فهرست دارایی؛ محتوا، مجوز و metadata ممیزی نشده.  |
| `docs/tech_doc_pages/tech_page-03.png` [^https://github.com/mtaham84/moshtari-yab/blob/a6aa1fcb1f9c716e49d1b462989bfb636409b106/docs/tech_doc_pages/tech_page-03.png] | asset / باینری | 219,415 بایت | فقط فهرست دارایی؛ محتوا، مجوز و metadata ممیزی نشده.  |
| `docs/tech_doc_pages/tech_page-04.png` [^https://github.com/mtaham84/moshtari-yab/blob/a6aa1fcb1f9c716e49d1b462989bfb636409b106/docs/tech_doc_pages/tech_page-04.png] | asset / باینری | 193,358 بایت | فقط فهرست دارایی؛ محتوا، مجوز و metadata ممیزی نشده.  |
| `docs/tech_doc_pages/tech_page-05.png` [^https://github.com/mtaham84/moshtari-yab/blob/a6aa1fcb1f9c716e49d1b462989bfb636409b106/docs/tech_doc_pages/tech_page-05.png] | asset / باینری | 188,370 بایت | فقط فهرست دارایی؛ محتوا، مجوز و metadata ممیزی نشده.  |
| `docs/tech_doc_pages/tech_page-06.png` [^https://github.com/mtaham84/moshtari-yab/blob/a6aa1fcb1f9c716e49d1b462989bfb636409b106/docs/tech_doc_pages/tech_page-06.png] | asset / باینری | 141,574 بایت | فقط فهرست دارایی؛ محتوا، مجوز و metadata ممیزی نشده.  |
| `docs/tech_doc_pages/tech_page-07.png` [^https://github.com/mtaham84/moshtari-yab/blob/a6aa1fcb1f9c716e49d1b462989bfb636409b106/docs/tech_doc_pages/tech_page-07.png] | asset / باینری | 180,099 بایت | فقط فهرست دارایی؛ محتوا، مجوز و metadata ممیزی نشده.  |
| `docs/tech_doc_pages/tech_page-08.png` [^https://github.com/mtaham84/moshtari-yab/blob/a6aa1fcb1f9c716e49d1b462989bfb636409b106/docs/tech_doc_pages/tech_page-08.png] | asset / باینری | 130,223 بایت | فقط فهرست دارایی؛ محتوا، مجوز و metadata ممیزی نشده.  |
| `docs/tech_doc_pages/tech_page-09.png` [^https://github.com/mtaham84/moshtari-yab/blob/a6aa1fcb1f9c716e49d1b462989bfb636409b106/docs/tech_doc_pages/tech_page-09.png] | asset / باینری | 164,138 بایت | فقط فهرست دارایی؛ محتوا، مجوز و metadata ممیزی نشده.  |
| `docs/tech_doc_pages/tech_page-10.png` [^https://github.com/mtaham84/moshtari-yab/blob/a6aa1fcb1f9c716e49d1b462989bfb636409b106/docs/tech_doc_pages/tech_page-10.png] | asset / باینری | 160,107 بایت | فقط فهرست دارایی؛ محتوا، مجوز و metadata ممیزی نشده.  |
| `docs/technical_documentation.md` [^https://github.com/mtaham84/moshtari-yab/blob/a6aa1fcb1f9c716e49d1b462989bfb636409b106/docs/technical_documentation.md] | متن .md | 383 خط | فهرست/بازرسی متنی در دامنه ابزار؛ تأیید معنایی کامل انجام نشده.  |

## فایل‌های ریشه

| فایل و منبع | نوع / دامنه | سنجه | بررسی و نتیجه |
|---|---|---|---|
| `file_lock.py` [^https://github.com/mtaham84/moshtari-yab/blob/a6aa1fcb1f9c716e49d1b462989bfb636409b106/file_lock.py] | Python | 35 خط؛ max CC=3 | اسکن ساختاری؛ یافته مستقل ثبت نشده. توابع نمونه برای پیگیری: `exclusive_file_lock`. |
| `groups.example.txt` [^https://github.com/mtaham84/moshtari-yab/blob/a6aa1fcb1f9c716e49d1b462989bfb636409b106/groups.example.txt] | متن .txt | 3 خط | فهرست/بازرسی متنی در دامنه ابزار؛ تأیید معنایی کامل انجام نشده.  |

## instagram_discovery

| فایل و منبع | نوع / دامنه | سنجه | بررسی و نتیجه |
|---|---|---|---|
| `instagram_discovery/.gitignore` [^https://github.com/mtaham84/moshtari-yab/blob/a6aa1fcb1f9c716e49d1b462989bfb636409b106/instagram_discovery/.gitignore] | متن تنظیمات | 1 خط | فهرست/بازرسی متنی در دامنه ابزار؛ تأیید معنایی کامل انجام نشده.  |
| `instagram_discovery/README.md` [^https://github.com/mtaham84/moshtari-yab/blob/a6aa1fcb1f9c716e49d1b462989bfb636409b106/instagram_discovery/README.md] | متن .md | 106 خط | فهرست/بازرسی متنی در دامنه ابزار؛ تأیید معنایی کامل انجام نشده.  |
| `instagram_discovery/__init__.py` [^https://github.com/mtaham84/moshtari-yab/blob/a6aa1fcb1f9c716e49d1b462989bfb636409b106/instagram_discovery/__init__.py] | Python | 3 خط | اسکن ساختاری؛ یافته مستقل ثبت نشده.  |
| `instagram_discovery/__main__.py` [^https://github.com/mtaham84/moshtari-yab/blob/a6aa1fcb1f9c716e49d1b462989bfb636409b106/instagram_discovery/__main__.py] | Python | 4 خط | اسکن ساختاری؛ یافته مستقل ثبت نشده.  |
| `instagram_discovery/analyzer.py` [^https://github.com/mtaham84/moshtari-yab/blob/a6aa1fcb1f9c716e49d1b462989bfb636409b106/instagram_discovery/analyzer.py] | Python | 469 خط؛ max CC=70؛ تابع>100=2 | اسکن ساختاری؛ یافته مستقل ثبت نشده. مرز مسئولیت `analyze_post` در 323–464 بررسی تکمیلی شود. |
| `instagram_discovery/categories.example.json` [^https://github.com/mtaham84/moshtari-yab/blob/a6aa1fcb1f9c716e49d1b462989bfb636409b106/instagram_discovery/categories.example.json] | متن .json | 16 خط | فهرست/بازرسی متنی در دامنه ابزار؛ تأیید معنایی کامل انجام نشده.  |
| `instagram_discovery/categories.json` [^https://github.com/mtaham84/moshtari-yab/blob/a6aa1fcb1f9c716e49d1b462989bfb636409b106/instagram_discovery/categories.json] | متن .json | 16 خط | فهرست/بازرسی متنی در دامنه ابزار؛ تأیید معنایی کامل انجام نشده.  |
| `instagram_discovery/cli.py` [^https://github.com/mtaham84/moshtari-yab/blob/a6aa1fcb1f9c716e49d1b462989bfb636409b106/instagram_discovery/cli.py] | Python | 177 خط؛ max CC=31؛ except عمومی=1 | اسکن ساختاری؛ یافته مستقل ثبت نشده. بررسی تکمیلی: خطای قابل‌بازیابی از خطای داده/مجوز تفکیک شود. |
| `instagram_discovery/collector.py` [^https://github.com/mtaham84/moshtari-yab/blob/a6aa1fcb1f9c716e49d1b462989bfb636409b106/instagram_discovery/collector.py] | Python | 84 خط؛ max CC=18 | اسکن ساختاری؛ یافته مستقل ثبت نشده. توابع نمونه برای پیگیری: `__init__`, `check_available`. |
| `instagram_discovery/comment_evaluation.py` [^https://github.com/mtaham84/moshtari-yab/blob/a6aa1fcb1f9c716e49d1b462989bfb636409b106/instagram_discovery/comment_evaluation.py] | Python | 329 خط؛ max CC=28؛ except عمومی=2؛ تابع>100=1 | اسکن ساختاری؛ یافته مستقل ثبت نشده. مرز مسئولیت `evaluate_with_jev` در 101–237 بررسی تکمیلی شود. |
| `instagram_discovery/comment_intent.py` [^https://github.com/mtaham84/moshtari-yab/blob/a6aa1fcb1f9c716e49d1b462989bfb636409b106/instagram_discovery/comment_intent.py] | Python | 61 خط؛ max CC=9 | اسکن ساختاری؛ یافته مستقل ثبت نشده. توابع نمونه برای پیگیری: `_load_rules`, `classify_comment`. |
| `instagram_discovery/comment_metrics.py` [^https://github.com/mtaham84/moshtari-yab/blob/a6aa1fcb1f9c716e49d1b462989bfb636409b106/instagram_discovery/comment_metrics.py] | Python | 198 خط؛ max CC=17 | اسکن ساختاری؛ یافته مستقل ثبت نشده. توابع نمونه برای پیگیری: `_ratio`, `_rows`. |
| `instagram_discovery/comment_threshold.py` [^https://github.com/mtaham84/moshtari-yab/blob/a6aa1fcb1f9c716e49d1b462989bfb636409b106/instagram_discovery/comment_threshold.py] | Python | 12 خط؛ max CC=3 | اسکن ساختاری؛ یافته مستقل ثبت نشده. توابع نمونه برای پیگیری: `jev_selection_threshold`. |
| `instagram_discovery/labeling.py` [^https://github.com/mtaham84/moshtari-yab/blob/a6aa1fcb1f9c716e49d1b462989bfb636409b106/instagram_discovery/labeling.py] | Python | 140 خط؛ max CC=21 | اسکن ساختاری؛ یافته مستقل ثبت نشده. توابع نمونه برای پیگیری: `_metadata`, `_parent_caption`. |
| `instagram_discovery/post_label_store.py` [^https://github.com/mtaham84/moshtari-yab/blob/a6aa1fcb1f9c716e49d1b462989bfb636409b106/instagram_discovery/post_label_store.py] | Python | 99 خط؛ max CC=2؛ clone=10 | اسکن ساختاری؛ یافته مستقل ثبت نشده. کاندید استخراج helper مشترک؛ contract باید حفظ شود. |
| `instagram_discovery/post_metrics.py` [^https://github.com/mtaham84/moshtari-yab/blob/a6aa1fcb1f9c716e49d1b462989bfb636409b106/instagram_discovery/post_metrics.py] | Python | 526 خط؛ max CC=38؛ clone=10 | اسکن ساختاری؛ یافته مستقل ثبت نشده. کاندید استخراج helper مشترک؛ contract باید حفظ شود. |
| `instagram_discovery/sources.example.json` [^https://github.com/mtaham84/moshtari-yab/blob/a6aa1fcb1f9c716e49d1b462989bfb636409b106/instagram_discovery/sources.example.json] | متن .json | 8 خط | فهرست/بازرسی متنی در دامنه ابزار؛ تأیید معنایی کامل انجام نشده.  |
| `instagram_discovery/sources.json` [^https://github.com/mtaham84/moshtari-yab/blob/a6aa1fcb1f9c716e49d1b462989bfb636409b106/instagram_discovery/sources.json] | متن .json | 5 خط | فهرست/بازرسی متنی در دامنه ابزار؛ تأیید معنایی کامل انجام نشده.  |
| `instagram_discovery/targets.py` [^https://github.com/mtaham84/moshtari-yab/blob/a6aa1fcb1f9c716e49d1b462989bfb636409b106/instagram_discovery/targets.py] | Python | 57 خط؛ max CC=4 | اسکن ساختاری؛ یافته مستقل ثبت نشده. توابع نمونه برای پیگیری: `build_hashtag_seeds`. |
| `instagram_discovery/tests/fixtures/seller_post_comments.jsonl` [^https://github.com/mtaham84/moshtari-yab/blob/a6aa1fcb1f9c716e49d1b462989bfb636409b106/instagram_discovery/tests/fixtures/seller_post_comments.jsonl] | متن .jsonl؛ تست/اسکریپت آزمون | 28 خط | فهرست/بازرسی متنی در دامنه ابزار؛ تأیید معنایی کامل انجام نشده. نتیجه runnerها در test-results.md؛ پوشش این فایل از نام آن استنباط نمی‌شود. |
| `instagram_discovery/tests/test_analyzer.py` [^https://github.com/mtaham84/moshtari-yab/blob/a6aa1fcb1f9c716e49d1b462989bfb636409b106/instagram_discovery/tests/test_analyzer.py] | Python؛ تست/اسکریپت آزمون | 117 خط؛ max CC=10 | اسکن ساختاری؛ یافته مستقل ثبت نشده. نتیجه runnerها در test-results.md؛ پوشش این فایل از نام آن استنباط نمی‌شود. |
| `instagram_discovery/tests/test_comment_evaluation.py` [^https://github.com/mtaham84/moshtari-yab/blob/a6aa1fcb1f9c716e49d1b462989bfb636409b106/instagram_discovery/tests/test_comment_evaluation.py] | Python؛ تست/اسکریپت آزمون | 128 خط؛ max CC=13 | اسکن ساختاری؛ یافته مستقل ثبت نشده. نتیجه runnerها در test-results.md؛ پوشش این فایل از نام آن استنباط نمی‌شود. |
| `instagram_discovery/tests/test_comment_intent.py` [^https://github.com/mtaham84/moshtari-yab/blob/a6aa1fcb1f9c716e49d1b462989bfb636409b106/instagram_discovery/tests/test_comment_intent.py] | Python؛ تست/اسکریپت آزمون | 142 خط؛ max CC=12 | اسکن ساختاری؛ یافته مستقل ثبت نشده. نتیجه runnerها در test-results.md؛ پوشش این فایل از نام آن استنباط نمی‌شود. |
| `instagram_discovery/tests/test_comment_metrics.py` [^https://github.com/mtaham84/moshtari-yab/blob/a6aa1fcb1f9c716e49d1b462989bfb636409b106/instagram_discovery/tests/test_comment_metrics.py] | Python؛ تست/اسکریپت آزمون | 94 خط؛ max CC=11 | اسکن ساختاری؛ یافته مستقل ثبت نشده. نتیجه runnerها در test-results.md؛ پوشش این فایل از نام آن استنباط نمی‌شود. |
| `instagram_discovery/tests/test_post_label_store.py` [^https://github.com/mtaham84/moshtari-yab/blob/a6aa1fcb1f9c716e49d1b462989bfb636409b106/instagram_discovery/tests/test_post_label_store.py] | Python؛ تست/اسکریپت آزمون | 43 خط؛ max CC=6 | اسکن ساختاری؛ یافته مستقل ثبت نشده. نتیجه runnerها در test-results.md؛ پوشش این فایل از نام آن استنباط نمی‌شود. |
| `instagram_discovery/tests/test_post_metrics.py` [^https://github.com/mtaham84/moshtari-yab/blob/a6aa1fcb1f9c716e49d1b462989bfb636409b106/instagram_discovery/tests/test_post_metrics.py] | Python؛ تست/اسکریپت آزمون | 216 خط؛ max CC=16 | اسکن ساختاری؛ یافته مستقل ثبت نشده. نتیجه runnerها در test-results.md؛ پوشش این فایل از نام آن استنباط نمی‌شود. |
| `instagram_discovery/tests/test_targets.py` [^https://github.com/mtaham84/moshtari-yab/blob/a6aa1fcb1f9c716e49d1b462989bfb636409b106/instagram_discovery/tests/test_targets.py] | Python؛ تست/اسکریپت آزمون | 47 خط؛ max CC=6 | اسکن ساختاری؛ یافته مستقل ثبت نشده. نتیجه runnerها در test-results.md؛ پوشش این فایل از نام آن استنباط نمی‌شود. |

## instagram_engine

| فایل و منبع | نوع / دامنه | سنجه | بررسی و نتیجه |
|---|---|---|---|
| `instagram_engine/.env.example` [^https://github.com/mtaham84/moshtari-yab/blob/a6aa1fcb1f9c716e49d1b462989bfb636409b106/instagram_engine/.env.example] | متن .example | 39 خط | فهرست/بازرسی متنی در دامنه ابزار؛ تأیید معنایی کامل انجام نشده.  |
| `instagram_engine/__init__.py` [^https://github.com/mtaham84/moshtari-yab/blob/a6aa1fcb1f9c716e49d1b462989bfb636409b106/instagram_engine/__init__.py] | Python | 4 خط | اسکن ساختاری؛ یافته مستقل ثبت نشده.  |
| `instagram_engine/analyzer.py` [^https://github.com/mtaham84/moshtari-yab/blob/a6aa1fcb1f9c716e49d1b462989bfb636409b106/instagram_engine/analyzer.py] | Python | 440 خط؛ max CC=23؛ except عمومی=4 | شاهد هدفمند: R14؛ شرح و اصلاح در گزارش اصلی. |
| `instagram_engine/circuit_breaker.py` [^https://github.com/mtaham84/moshtari-yab/blob/a6aa1fcb1f9c716e49d1b462989bfb636409b106/instagram_engine/circuit_breaker.py] | Python | 140 خط؛ max CC=14؛ except عمومی=1 | اسکن ساختاری؛ یافته مستقل ثبت نشده. بررسی تکمیلی: خطای قابل‌بازیابی از خطای داده/مجوز تفکیک شود. |
| `instagram_engine/config.py` [^https://github.com/mtaham84/moshtari-yab/blob/a6aa1fcb1f9c716e49d1b462989bfb636409b106/instagram_engine/config.py] | Python | 89 خط؛ max CC=2 | اسکن ساختاری؛ یافته مستقل ثبت نشده. توابع نمونه برای پیگیری: `__init__`. |
| `instagram_engine/main.py` [^https://github.com/mtaham84/moshtari-yab/blob/a6aa1fcb1f9c716e49d1b462989bfb636409b106/instagram_engine/main.py] | Python | 218 خط؛ max CC=8؛ except عمومی=3 | اسکن ساختاری؛ یافته مستقل ثبت نشده. بررسی تکمیلی: خطای قابل‌بازیابی از خطای داده/مجوز تفکیک شود. |
| `instagram_engine/models.py` [^https://github.com/mtaham84/moshtari-yab/blob/a6aa1fcb1f9c716e49d1b462989bfb636409b106/instagram_engine/models.py] | Python | 158 خط؛ max CC=5 | شاهد هدفمند: R08, R25؛ شرح و اصلاح در گزارش اصلی. |
| `instagram_engine/moderation.py` [^https://github.com/mtaham84/moshtari-yab/blob/a6aa1fcb1f9c716e49d1b462989bfb636409b106/instagram_engine/moderation.py] | Python | 6 خط | اسکن ساختاری؛ یافته مستقل ثبت نشده.  |
| `instagram_engine/outreach_worker.py` [^https://github.com/mtaham84/moshtari-yab/blob/a6aa1fcb1f9c716e49d1b462989bfb636409b106/instagram_engine/outreach_worker.py] | Python | 327 خط؛ max CC=11؛ except عمومی=6؛ clone=24 | شاهد هدفمند: R11؛ شرح و اصلاح در گزارش اصلی. |
| `instagram_engine/post_filters.py` [^https://github.com/mtaham84/moshtari-yab/blob/a6aa1fcb1f9c716e49d1b462989bfb636409b106/instagram_engine/post_filters.py] | Python | 152 خط؛ max CC=9؛ clone=149 | شاهد هدفمند: R40؛ شرح و اصلاح در گزارش اصلی. |
| `instagram_engine/requirements.txt` [^https://github.com/mtaham84/moshtari-yab/blob/a6aa1fcb1f9c716e49d1b462989bfb636409b106/instagram_engine/requirements.txt] | متن .txt | 22 خط | شاهد هدفمند: R26؛ شرح و اصلاح در گزارش اصلی. |
| `instagram_engine/scraper_worker.py` [^https://github.com/mtaham84/moshtari-yab/blob/a6aa1fcb1f9c716e49d1b462989bfb636409b106/instagram_engine/scraper_worker.py] | Python | 369 خط؛ max CC=34؛ except عمومی=8؛ clone=24؛ تابع>100=1 | اسکن ساختاری؛ یافته مستقل ثبت نشده. مرز مسئولیت `process_query` در 150–299 بررسی تکمیلی شود. |
| `instagram_engine/session_manager.py` [^https://github.com/mtaham84/moshtari-yab/blob/a6aa1fcb1f9c716e49d1b462989bfb636409b106/instagram_engine/session_manager.py] | Python | 227 خط؛ max CC=6؛ except عمومی=7 | اسکن ساختاری؛ یافته مستقل ثبت نشده. بررسی تکمیلی: خطای قابل‌بازیابی از خطای داده/مجوز تفکیک شود. |
| `instagram_engine/tests/test_analyzer.py` [^https://github.com/mtaham84/moshtari-yab/blob/a6aa1fcb1f9c716e49d1b462989bfb636409b106/instagram_engine/tests/test_analyzer.py] | Python؛ تست/اسکریپت آزمون | 91 خط؛ max CC=2 | اسکن ساختاری؛ یافته مستقل ثبت نشده. نتیجه runnerها در test-results.md؛ پوشش این فایل از نام آن استنباط نمی‌شود. |

## فایل‌های ریشه

| فایل و منبع | نوع / دامنه | سنجه | بررسی و نتیجه |
|---|---|---|---|
| `manage.py` [^https://github.com/mtaham84/moshtari-yab/blob/a6aa1fcb1f9c716e49d1b462989bfb636409b106/manage.py] | Python | 16 خط؛ max CC=1 | اسکن ساختاری؛ یافته مستقل ثبت نشده. توابع نمونه برای پیگیری: `main`. |

## need_engine

| فایل و منبع | نوع / دامنه | سنجه | بررسی و نتیجه |
|---|---|---|---|
| `need_engine/README.md` [^https://github.com/mtaham84/moshtari-yab/blob/a6aa1fcb1f9c716e49d1b462989bfb636409b106/need_engine/README.md] | متن .md | 90 خط | فهرست/بازرسی متنی در دامنه ابزار؛ تأیید معنایی کامل انجام نشده.  |
| `need_engine/__init__.py` [^https://github.com/mtaham84/moshtari-yab/blob/a6aa1fcb1f9c716e49d1b462989bfb636409b106/need_engine/__init__.py] | Python | 20 خط؛ max CC=2 | اسکن ساختاری؛ یافته مستقل ثبت نشده. توابع نمونه برای پیگیری: `__getattr__`. |
| `need_engine/__main__.py` [^https://github.com/mtaham84/moshtari-yab/blob/a6aa1fcb1f9c716e49d1b462989bfb636409b106/need_engine/__main__.py] | Python | 145 خط؛ max CC=36؛ تابع>100=1 | اسکن ساختاری؛ یافته مستقل ثبت نشده. مرز مسئولیت `main` در 24–133 بررسی تکمیلی شود. |
| `need_engine/access.py` [^https://github.com/mtaham84/moshtari-yab/blob/a6aa1fcb1f9c716e49d1b462989bfb636409b106/need_engine/access.py] | Python | 133 خط؛ max CC=16 | اسکن ساختاری؛ یافته مستقل ثبت نشده. توابع نمونه برای پیگیری: `__init__`, `_conn`. |
| `need_engine/catalog.py` [^https://github.com/mtaham84/moshtari-yab/blob/a6aa1fcb1f9c716e49d1b462989bfb636409b106/need_engine/catalog.py] | Python | 161 خط؛ max CC=19؛ except عمومی=1 | اسکن ساختاری؛ یافته مستقل ثبت نشده. بررسی تکمیلی: خطای قابل‌بازیابی از خطای داده/مجوز تفکیک شود. |
| `need_engine/config.py` [^https://github.com/mtaham84/moshtari-yab/blob/a6aa1fcb1f9c716e49d1b462989bfb636409b106/need_engine/config.py] | Python | 210 خط؛ max CC=8؛ except عمومی=1 | اسکن ساختاری؛ یافته مستقل ثبت نشده. بررسی تکمیلی: خطای قابل‌بازیابی از خطای داده/مجوز تفکیک شود. |
| `need_engine/contacts.py` [^https://github.com/mtaham84/moshtari-yab/blob/a6aa1fcb1f9c716e49d1b462989bfb636409b106/need_engine/contacts.py] | Python | 76 خط؛ max CC=41 | اسکن ساختاری؛ یافته مستقل ثبت نشده. توابع نمونه برای پیگیری: `extract_public_contacts`. |
| `need_engine/crypto.py` [^https://github.com/mtaham84/moshtari-yab/blob/a6aa1fcb1f9c716e49d1b462989bfb636409b106/need_engine/crypto.py] | Python | 72 خط؛ max CC=6 | اسکن ساختاری؛ یافته مستقل ثبت نشده. توابع نمونه برای پیگیری: `_fernet`, `_fernets`. |
| `need_engine/embeddings.py` [^https://github.com/mtaham84/moshtari-yab/blob/a6aa1fcb1f9c716e49d1b462989bfb636409b106/need_engine/embeddings.py] | Python | 112 خط؛ max CC=13؛ except عمومی=1 | شاهد هدفمند: R18؛ شرح و اصلاح در گزارش اصلی. |
| `need_engine/engine.py` [^https://github.com/mtaham84/moshtari-yab/blob/a6aa1fcb1f9c716e49d1b462989bfb636409b106/need_engine/engine.py] | Python | 746 خط؛ max CC=57؛ except عمومی=7 | اسکن ساختاری؛ یافته مستقل ثبت نشده. بررسی تکمیلی: خطای قابل‌بازیابی از خطای داده/مجوز تفکیک شود. |
| `need_engine/eval/__init__.py` [^https://github.com/mtaham84/moshtari-yab/blob/a6aa1fcb1f9c716e49d1b462989bfb636409b106/need_engine/eval/__init__.py] | Python | 1 خط | اسکن ساختاری؛ یافته مستقل ثبت نشده.  |
| `need_engine/eval/golden.py` [^https://github.com/mtaham84/moshtari-yab/blob/a6aa1fcb1f9c716e49d1b462989bfb636409b106/need_engine/eval/golden.py] | Python | 154 خط؛ max CC=29 | شاهد هدفمند: R27؛ شرح و اصلاح در گزارش اصلی. |
| `need_engine/extract.py` [^https://github.com/mtaham84/moshtari-yab/blob/a6aa1fcb1f9c716e49d1b462989bfb636409b106/need_engine/extract.py] | Python | 420 خط؛ max CC=52؛ except عمومی=1 | اسکن ساختاری؛ یافته مستقل ثبت نشده. بررسی تکمیلی: خطای قابل‌بازیابی از خطای داده/مجوز تفکیک شود. |
| `need_engine/llm.py` [^https://github.com/mtaham84/moshtari-yab/blob/a6aa1fcb1f9c716e49d1b462989bfb636409b106/need_engine/llm.py] | Python | 315 خط؛ max CC=48؛ except عمومی=4؛ تابع>100=1 | شاهد هدفمند: R14, R15, R16, R27؛ شرح و اصلاح در گزارش اصلی. |
| `need_engine/mock.py` [^https://github.com/mtaham84/moshtari-yab/blob/a6aa1fcb1f9c716e49d1b462989bfb636409b106/need_engine/mock.py] | Python | 40 خط؛ max CC=16 | اسکن ساختاری؛ یافته مستقل ثبت نشده. توابع نمونه برای پیگیری: `mock_llm`. |
| `need_engine/platforms/__init__.py` [^https://github.com/mtaham84/moshtari-yab/blob/a6aa1fcb1f9c716e49d1b462989bfb636409b106/need_engine/platforms/__init__.py] | Python | 31 خط؛ max CC=3 | اسکن ساختاری؛ یافته مستقل ثبت نشده. توابع نمونه برای پیگیری: `get`, `for_message`. |
| `need_engine/platforms/base.py` [^https://github.com/mtaham84/moshtari-yab/blob/a6aa1fcb1f9c716e49d1b462989bfb636409b106/need_engine/platforms/base.py] | Python | 15 خط؛ max CC=1 | اسکن ساختاری؛ یافته مستقل ثبت نشده.  |
| `need_engine/platforms/telegram.py` [^https://github.com/mtaham84/moshtari-yab/blob/a6aa1fcb1f9c716e49d1b462989bfb636409b106/need_engine/platforms/telegram.py] | Python | 32 خط؛ max CC=5 | اسکن ساختاری؛ یافته مستقل ثبت نشده. توابع نمونه برای پیگیری: `_message_url`, `external_user_id`. |
| `need_engine/platforms/x.py` [^https://github.com/mtaham84/moshtari-yab/blob/a6aa1fcb1f9c716e49d1b462989bfb636409b106/need_engine/platforms/x.py] | Python | 74 خط؛ max CC=2 | اسکن ساختاری؛ یافته مستقل ثبت نشده. توابع نمونه برای پیگیری: `chat_id`, `is_chat_id`. |
| `need_engine/prompts.py` [^https://github.com/mtaham84/moshtari-yab/blob/a6aa1fcb1f9c716e49d1b462989bfb636409b106/need_engine/prompts.py] | Python | 55 خط | اسکن ساختاری؛ یافته مستقل ثبت نشده.  |
| `need_engine/registry.py` [^https://github.com/mtaham84/moshtari-yab/blob/a6aa1fcb1f9c716e49d1b462989bfb636409b106/need_engine/registry.py] | Python | 207 خط؛ max CC=13؛ except عمومی=2 | شاهد هدفمند: R17, R32؛ شرح و اصلاح در گزارش اصلی. |
| `need_engine/reply.py` [^https://github.com/mtaham84/moshtari-yab/blob/a6aa1fcb1f9c716e49d1b462989bfb636409b106/need_engine/reply.py] | Python | 232 خط؛ max CC=25 | اسکن ساختاری؛ یافته مستقل ثبت نشده. توابع نمونه برای پیگیری: `_similarity`, `sanitize_outreach_text`. |
| `need_engine/retention.py` [^https://github.com/mtaham84/moshtari-yab/blob/a6aa1fcb1f9c716e49d1b462989bfb636409b106/need_engine/retention.py] | Python | 6 خط | اسکن ساختاری؛ یافته مستقل ثبت نشده.  |
| `need_engine/retrieve.py` [^https://github.com/mtaham84/moshtari-yab/blob/a6aa1fcb1f9c716e49d1b462989bfb636409b106/need_engine/retrieve.py] | Python | 131 خط؛ max CC=41 | اسکن ساختاری؛ یافته مستقل ثبت نشده. توابع نمونه برای پیگیری: `rrf`, `adaptive_threshold`. |
| `need_engine/schemas.py` [^https://github.com/mtaham84/moshtari-yab/blob/a6aa1fcb1f9c716e49d1b462989bfb636409b106/need_engine/schemas.py] | Python | 240 خط؛ max CC=5 | اسکن ساختاری؛ یافته مستقل ثبت نشده. توابع نمونه برای پیگیری: `strength_weight`, `content_hash`. |
| `need_engine/scoring.py` [^https://github.com/mtaham84/moshtari-yab/blob/a6aa1fcb1f9c716e49d1b462989bfb636409b106/need_engine/scoring.py] | Python | 45 خط؛ max CC=28 | اسکن ساختاری؛ یافته مستقل ثبت نشده. توابع نمونه برای پیگیری: `score_match`. |
| `need_engine/sources.py` [^https://github.com/mtaham84/moshtari-yab/blob/a6aa1fcb1f9c716e49d1b462989bfb636409b106/need_engine/sources.py] | Python | 339 خط؛ max CC=22؛ except عمومی=2 | اسکن ساختاری؛ یافته مستقل ثبت نشده. بررسی تکمیلی: خطای قابل‌بازیابی از خطای داده/مجوز تفکیک شود. |
| `need_engine/store.py` [^https://github.com/mtaham84/moshtari-yab/blob/a6aa1fcb1f9c716e49d1b462989bfb636409b106/need_engine/store.py] | Python | 499 خط؛ max CC=10 | شاهد هدفمند: R31, R33, R34؛ شرح و اصلاح در گزارش اصلی. |
| `need_engine/tests/__init__.py` [^https://github.com/mtaham84/moshtari-yab/blob/a6aa1fcb1f9c716e49d1b462989bfb636409b106/need_engine/tests/__init__.py] | Python؛ تست/اسکریپت آزمون | 0 خط | اسکن ساختاری؛ یافته مستقل ثبت نشده. نتیجه runnerها در test-results.md؛ پوشش این فایل از نام آن استنباط نمی‌شود. |
| `need_engine/tests/test_golden_eval.py` [^https://github.com/mtaham84/moshtari-yab/blob/a6aa1fcb1f9c716e49d1b462989bfb636409b106/need_engine/tests/test_golden_eval.py] | Python؛ تست/اسکریپت آزمون | 66 خط؛ max CC=5 | شاهد هدفمند: R27؛ شرح و اصلاح در گزارش اصلی. |
| `need_engine/tests/test_links_and_cards.py` [^https://github.com/mtaham84/moshtari-yab/blob/a6aa1fcb1f9c716e49d1b462989bfb636409b106/need_engine/tests/test_links_and_cards.py] | Python؛ تست/اسکریپت آزمون | 100 خط؛ max CC=11 | اسکن ساختاری؛ یافته مستقل ثبت نشده. نتیجه runnerها در test-results.md؛ پوشش این فایل از نام آن استنباط نمی‌شود. |
| `need_engine/tests/test_need_engine.py` [^https://github.com/mtaham84/moshtari-yab/blob/a6aa1fcb1f9c716e49d1b462989bfb636409b106/need_engine/tests/test_need_engine.py] | Python؛ تست/اسکریپت آزمون | 635 خط؛ max CC=17 | اسکن ساختاری؛ یافته مستقل ثبت نشده. نتیجه runnerها در test-results.md؛ پوشش این فایل از نام آن استنباط نمی‌شود. |
| `need_engine/tests/test_platform_behaviour.py` [^https://github.com/mtaham84/moshtari-yab/blob/a6aa1fcb1f9c716e49d1b462989bfb636409b106/need_engine/tests/test_platform_behaviour.py] | Python؛ تست/اسکریپت آزمون | 123 خط؛ max CC=8 | اسکن ساختاری؛ یافته مستقل ثبت نشده. نتیجه runnerها در test-results.md؛ پوشش این فایل از نام آن استنباط نمی‌شود. |
| `need_engine/tests/test_registry.py` [^https://github.com/mtaham84/moshtari-yab/blob/a6aa1fcb1f9c716e49d1b462989bfb636409b106/need_engine/tests/test_registry.py] | Python؛ تست/اسکریپت آزمون | 130 خط؛ max CC=9 | شاهد هدفمند: R27؛ شرح و اصلاح در گزارش اصلی. |
| `need_engine/tests/test_reply.py` [^https://github.com/mtaham84/moshtari-yab/blob/a6aa1fcb1f9c716e49d1b462989bfb636409b106/need_engine/tests/test_reply.py] | Python؛ تست/اسکریپت آزمون | 66 خط؛ max CC=6 | اسکن ساختاری؛ یافته مستقل ثبت نشده. نتیجه runnerها در test-results.md؛ پوشش این فایل از نام آن استنباط نمی‌شود. |
| `need_engine/tests/test_x_quality_pure.py` [^https://github.com/mtaham84/moshtari-yab/blob/a6aa1fcb1f9c716e49d1b462989bfb636409b106/need_engine/tests/test_x_quality_pure.py] | Python؛ تست/اسکریپت آزمون | 273 خط؛ max CC=13 | اسکن ساختاری؛ یافته مستقل ثبت نشده. نتیجه runnerها در test-results.md؛ پوشش این فایل از نام آن استنباط نمی‌شود. |
| `need_engine/text.py` [^https://github.com/mtaham84/moshtari-yab/blob/a6aa1fcb1f9c716e49d1b462989bfb636409b106/need_engine/text.py] | Python | 57 خط؛ max CC=7 | اسکن ساختاری؛ یافته مستقل ثبت نشده. توابع نمونه برای پیگیری: `norm`, `tokens`. |
| `need_engine/verify.py` [^https://github.com/mtaham84/moshtari-yab/blob/a6aa1fcb1f9c716e49d1b462989bfb636409b106/need_engine/verify.py] | Python | 123 خط؛ max CC=20 | اسکن ساختاری؛ یافته مستقل ثبت نشده. توابع نمونه برای پیگیری: `evidence_messages`, `evidence_text`. |
| `need_engine/windowing.py` [^https://github.com/mtaham84/moshtari-yab/blob/a6aa1fcb1f9c716e49d1b462989bfb636409b106/need_engine/windowing.py] | Python | 129 خط؛ max CC=33؛ except عمومی=1 | اسکن ساختاری؛ یافته مستقل ثبت نشده. بررسی تکمیلی: خطای قابل‌بازیابی از خطای داده/مجوز تفکیک شود. |
| `need_engine/x_filters.py` [^https://github.com/mtaham84/moshtari-yab/blob/a6aa1fcb1f9c716e49d1b462989bfb636409b106/need_engine/x_filters.py] | Python | 180 خط؛ max CC=26؛ except عمومی=1 | اسکن ساختاری؛ یافته مستقل ثبت نشده. بررسی تکمیلی: خطای قابل‌بازیابی از خطای داده/مجوز تفکیک شود. |
| `need_engine/x_query_scoring.py` [^https://github.com/mtaham84/moshtari-yab/blob/a6aa1fcb1f9c716e49d1b462989bfb636409b106/need_engine/x_query_scoring.py] | Python | 14 خط؛ max CC=2 | اسکن ساختاری؛ یافته مستقل ثبت نشده. توابع نمونه برای پیگیری: `score_query`, `query_weight`. |
| `need_engine/x_replies.py` [^https://github.com/mtaham84/moshtari-yab/blob/a6aa1fcb1f9c716e49d1b462989bfb636409b106/need_engine/x_replies.py] | Python | 54 خط؛ max CC=7 | اسکن ساختاری؛ یافته مستقل ثبت نشده. توابع نمونه برای پیگیری: `sanitize_public_reply`, `x_weighted_length`. |

## فایل‌های ریشه

| فایل و منبع | نوع / دامنه | سنجه | بررسی و نتیجه |
|---|---|---|---|
| `pytest.ini` [^https://github.com/mtaham84/moshtari-yab/blob/a6aa1fcb1f9c716e49d1b462989bfb636409b106/pytest.ini] | متن .ini؛ تست/اسکریپت آزمون | 7 خط | شاهد هدفمند: R27؛ شرح و اصلاح در گزارش اصلی. |
| `requirements.txt` [^https://github.com/mtaham84/moshtari-yab/blob/a6aa1fcb1f9c716e49d1b462989bfb636409b106/requirements.txt] | متن .txt | 26 خط | شاهد هدفمند: R26؛ شرح و اصلاح در گزارش اصلی. |

## scripts

| فایل و منبع | نوع / دامنه | سنجه | بررسی و نتیجه |
|---|---|---|---|
| `scripts/check_proxy_leaks.py` [^https://github.com/mtaham84/moshtari-yab/blob/a6aa1fcb1f9c716e49d1b462989bfb636409b106/scripts/check_proxy_leaks.py] | Python | 53 خط؛ max CC=13 | اسکن ساختاری؛ یافته مستقل ثبت نشده. توابع نمونه برای پیگیری: `_source_bytes`, `main`. |
| `scripts/config_inventory.py` [^https://github.com/mtaham84/moshtari-yab/blob/a6aa1fcb1f9c716e49d1b462989bfb636409b106/scripts/config_inventory.py] | Python | 192 خط؛ max CC=67 | اسکن ساختاری؛ یافته مستقل ثبت نشده. توابع نمونه برای پیگیری: `_call_name`, `_process`. |
| `scripts/demo_sarv_outreach.py` [^https://github.com/mtaham84/moshtari-yab/blob/a6aa1fcb1f9c716e49d1b462989bfb636409b106/scripts/demo_sarv_outreach.py] | Python | 119 خط؛ max CC=8 | اسکن ساختاری؛ یافته مستقل ثبت نشده. توابع نمونه برای پیگیری: `run_multi_sender_demo`. |
| `scripts/import_graph.py` [^https://github.com/mtaham84/moshtari-yab/blob/a6aa1fcb1f9c716e49d1b462989bfb636409b106/scripts/import_graph.py] | Python | 109 خط؛ max CC=15 | اسکن ساختاری؛ یافته مستقل ثبت نشده. توابع نمونه برای پیگیری: `package_for`, `module_name`. |
| `scripts/make_release.sh` [^https://github.com/mtaham84/moshtari-yab/blob/a6aa1fcb1f9c716e49d1b462989bfb636409b106/scripts/make_release.sh] | متن .sh | 10 خط | فهرست/بازرسی متنی در دامنه ابزار؛ تأیید معنایی کامل انجام نشده.  |
| `scripts/profile_engine.py` [^https://github.com/mtaham84/moshtari-yab/blob/a6aa1fcb1f9c716e49d1b462989bfb636409b106/scripts/profile_engine.py] | Python | 265 خط؛ max CC=6؛ تابع>100=1 | اسکن ساختاری؛ یافته مستقل ثبت نشده. مرز مسئولیت `profile` در 136–238 بررسی تکمیلی شود. |
| `scripts/profile_panel.py` [^https://github.com/mtaham84/moshtari-yab/blob/a6aa1fcb1f9c716e49d1b462989bfb636409b106/scripts/profile_panel.py] | Python | 134 خط؛ max CC=9 | اسکن ساختاری؛ یافته مستقل ثبت نشده. توابع نمونه برای پیگیری: `_fixture`, `_request`. |
| `scripts/retention_report.py` [^https://github.com/mtaham84/moshtari-yab/blob/a6aa1fcb1f9c716e49d1b462989bfb636409b106/scripts/retention_report.py] | Python | 43 خط؛ max CC=6 | اسکن ساختاری؛ یافته مستقل ثبت نشده. توابع نمونه برای پیگیری: `main`. |
| `scripts/run-python.ps1` [^https://github.com/mtaham84/moshtari-yab/blob/a6aa1fcb1f9c716e49d1b462989bfb636409b106/scripts/run-python.ps1] | asset / باینری | 285 بایت | فقط فهرست دارایی؛ محتوا، مجوز و metadata ممیزی نشده.  |

## shared

| فایل و منبع | نوع / دامنه | سنجه | بررسی و نتیجه |
|---|---|---|---|
| `shared/__init__.py` [^https://github.com/mtaham84/moshtari-yab/blob/a6aa1fcb1f9c716e49d1b462989bfb636409b106/shared/__init__.py] | Python | 1 خط | اسکن ساختاری؛ یافته مستقل ثبت نشده.  |
| `shared/collector_kit/__init__.py` [^https://github.com/mtaham84/moshtari-yab/blob/a6aa1fcb1f9c716e49d1b462989bfb636409b106/shared/collector_kit/__init__.py] | Python | 0 خط | اسکن ساختاری؛ یافته مستقل ثبت نشده.  |
| `shared/collector_kit/heartbeat.py` [^https://github.com/mtaham84/moshtari-yab/blob/a6aa1fcb1f9c716e49d1b462989bfb636409b106/shared/collector_kit/heartbeat.py] | Python | 100 خط؛ max CC=7؛ except عمومی=2؛ clone=10 | اسکن ساختاری؛ یافته مستقل ثبت نشده. کاندید استخراج helper مشترک؛ contract باید حفظ شود. |
| `shared/collector_kit/jsonl.py` [^https://github.com/mtaham84/moshtari-yab/blob/a6aa1fcb1f9c716e49d1b462989bfb636409b106/shared/collector_kit/jsonl.py] | Python | 45 خط؛ max CC=7 | اسکن ساختاری؛ یافته مستقل ثبت نشده. توابع نمونه برای پیگیری: `append_line`, `write_latest`. |
| `shared/collector_kit/limits.py` [^https://github.com/mtaham84/moshtari-yab/blob/a6aa1fcb1f9c716e49d1b462989bfb636409b106/shared/collector_kit/limits.py] | Python | 49 خط؛ max CC=2 | اسکن ساختاری؛ یافته مستقل ثبت نشده. توابع نمونه برای پیگیری: `retry_delay`, `remaining`. |
| `shared/collector_kit/seen.py` [^https://github.com/mtaham84/moshtari-yab/blob/a6aa1fcb1f9c716e49d1b462989bfb636409b106/shared/collector_kit/seen.py] | Python | 428 خط؛ max CC=18؛ except عمومی=1؛ clone=14 | اسکن ساختاری؛ یافته مستقل ثبت نشده. کاندید استخراج helper مشترک؛ contract باید حفظ شود. |
| `shared/collector_kit/state_store.py` [^https://github.com/mtaham84/moshtari-yab/blob/a6aa1fcb1f9c716e49d1b462989bfb636409b106/shared/collector_kit/state_store.py] | Python | 295 خط؛ max CC=7؛ clone=24 | اسکن ساختاری؛ یافته مستقل ثبت نشده. کاندید استخراج helper مشترک؛ contract باید حفظ شود. |
| `shared/collector_kit/status.py` [^https://github.com/mtaham84/moshtari-yab/blob/a6aa1fcb1f9c716e49d1b462989bfb636409b106/shared/collector_kit/status.py] | Python | 18 خط؛ max CC=2 | اسکن ساختاری؛ یافته مستقل ثبت نشده. توابع نمونه برای پیگیری: `write_status`. |
| `shared/collector_kit/tests/test_state_store.py` [^https://github.com/mtaham84/moshtari-yab/blob/a6aa1fcb1f9c716e49d1b462989bfb636409b106/shared/collector_kit/tests/test_state_store.py] | Python؛ تست/اسکریپت آزمون | 108 خط؛ max CC=7؛ except عمومی=1 | اسکن ساختاری؛ یافته مستقل ثبت نشده. نتیجه runnerها در test-results.md؛ پوشش این فایل از نام آن استنباط نمی‌شود. |
| `shared/instagram.py` [^https://github.com/mtaham84/moshtari-yab/blob/a6aa1fcb1f9c716e49d1b462989bfb636409b106/shared/instagram.py] | Python | 123 خط؛ max CC=35 | اسکن ساختاری؛ یافته مستقل ثبت نشده. توابع نمونه برای پیگیری: `parse_page_ref`, `parse_post_ref`. |
| `shared/instagram_manual_post.py` [^https://github.com/mtaham84/moshtari-yab/blob/a6aa1fcb1f9c716e49d1b462989bfb636409b106/shared/instagram_manual_post.py] | Python | 32 خط؛ max CC=2 | اسکن ساختاری؛ یافته مستقل ثبت نشده. توابع نمونه برای پیگیری: `run_manual_post`. |
| `shared/mmr.py` [^https://github.com/mtaham84/moshtari-yab/blob/a6aa1fcb1f9c716e49d1b462989bfb636409b106/shared/mmr.py] | Python | 128 خط؛ max CC=12 | اسکن ساختاری؛ یافته مستقل ثبت نشده. توابع نمونه برای پیگیری: `clean_persian_token`, `tokenize_for_similarity`. |
| `shared/records.py` [^https://github.com/mtaham84/moshtari-yab/blob/a6aa1fcb1f9c716e49d1b462989bfb636409b106/shared/records.py] | Python | 106 خط؛ max CC=25 | اسکن ساختاری؛ یافته مستقل ثبت نشده. توابع نمونه برای پیگیری: `to_json_line`, `_created_at`. |
| `shared/x_filters.py` [^https://github.com/mtaham84/moshtari-yab/blob/a6aa1fcb1f9c716e49d1b462989bfb636409b106/shared/x_filters.py] | Python | 32 خط؛ max CC=4؛ except عمومی=1 | اسکن ساختاری؛ یافته مستقل ثبت نشده. بررسی تکمیلی: خطای قابل‌بازیابی از خطای داده/مجوز تفکیک شود. |

## static

| فایل و منبع | نوع / دامنه | سنجه | بررسی و نتیجه |
|---|---|---|---|
| `static/css/fonts.css` [^https://github.com/mtaham84/moshtari-yab/blob/a6aa1fcb1f9c716e49d1b462989bfb636409b106/static/css/fonts.css] | متن .css | 16 خط | فهرست/بازرسی متنی در دامنه ابزار؛ تأیید معنایی کامل انجام نشده.  |
| `static/css/ops.css` [^https://github.com/mtaham84/moshtari-yab/blob/a6aa1fcb1f9c716e49d1b462989bfb636409b106/static/css/ops.css] | متن .css | 89 خط | فهرست/بازرسی متنی در دامنه ابزار؛ تأیید معنایی کامل انجام نشده.  |
| `static/css/panel.css` [^https://github.com/mtaham84/moshtari-yab/blob/a6aa1fcb1f9c716e49d1b462989bfb636409b106/static/css/panel.css] | متن .css | 2737 خط | فهرست/بازرسی متنی در دامنه ابزار؛ تأیید معنایی کامل انجام نشده.  |
| `static/css/site.css` [^https://github.com/mtaham84/moshtari-yab/blob/a6aa1fcb1f9c716e49d1b462989bfb636409b106/static/css/site.css] | متن .css | 3301 خط | فهرست/بازرسی متنی در دامنه ابزار؛ تأیید معنایی کامل انجام نشده.  |
| `static/css/ui.css` [^https://github.com/mtaham84/moshtari-yab/blob/a6aa1fcb1f9c716e49d1b462989bfb636409b106/static/css/ui.css] | متن .css | 358 خط | فهرست/بازرسی متنی در دامنه ابزار؛ تأیید معنایی کامل انجام نشده.  |
| `static/fonts/Vazirmatn-variable.woff2` [^https://github.com/mtaham84/moshtari-yab/blob/a6aa1fcb1f9c716e49d1b462989bfb636409b106/static/fonts/Vazirmatn-variable.woff2] | asset / باینری | 111,152 بایت | فقط فهرست دارایی؛ محتوا، مجوز و metadata ممیزی نشده.  |
| `static/images/astronaut.svg` [^https://github.com/mtaham84/moshtari-yab/blob/a6aa1fcb1f9c716e49d1b462989bfb636409b106/static/images/astronaut.svg] | متن .svg | 113 خط | فهرست/بازرسی متنی در دامنه ابزار؛ تأیید معنایی کامل انجام نشده.  |
| `static/images/coverr-50-sale-sign-on-the-street-5893-1080p.mp4` [^https://github.com/mtaham84/moshtari-yab/blob/a6aa1fcb1f9c716e49d1b462989bfb636409b106/static/images/coverr-50-sale-sign-on-the-street-5893-1080p.mp4] | asset / باینری | 7,884,226 بایت | فقط فهرست دارایی؛ محتوا، مجوز و metadata ممیزی نشده.  |
| `static/images/dashboard-bg.jpg` [^https://github.com/mtaham84/moshtari-yab/blob/a6aa1fcb1f9c716e49d1b462989bfb636409b106/static/images/dashboard-bg.jpg] | asset / باینری | 13,217 بایت | فقط فهرست دارایی؛ محتوا، مجوز و metadata ممیزی نشده.  |
| `static/images/favicon.svg` [^https://github.com/mtaham84/moshtari-yab/blob/a6aa1fcb1f9c716e49d1b462989bfb636409b106/static/images/favicon.svg] | متن .svg | 5 خط | فهرست/بازرسی متنی در دامنه ابزار؛ تأیید معنایی کامل انجام نشده.  |
| `static/images/feature-reference-photo.webp` [^https://github.com/mtaham84/moshtari-yab/blob/a6aa1fcb1f9c716e49d1b462989bfb636409b106/static/images/feature-reference-photo.webp] | asset / باینری | 11,354 بایت | فقط فهرست دارایی؛ محتوا، مجوز و metadata ممیزی نشده.  |
| `static/images/hero-reference-photo.webp` [^https://github.com/mtaham84/moshtari-yab/blob/a6aa1fcb1f9c716e49d1b462989bfb636409b106/static/images/hero-reference-photo.webp] | asset / باینری | 9,624 بایت | فقط فهرست دارایی؛ محتوا، مجوز و metadata ممیزی نشده.  |
| `static/js/site.js` [^https://github.com/mtaham84/moshtari-yab/blob/a6aa1fcb1f9c716e49d1b462989bfb636409b106/static/js/site.js] | متن .js | 202 خط | فهرست/بازرسی متنی در دامنه ابزار؛ تأیید معنایی کامل انجام نشده.  |
| `static/js/ui.js` [^https://github.com/mtaham84/moshtari-yab/blob/a6aa1fcb1f9c716e49d1b462989bfb636409b106/static/js/ui.js] | متن .js | 16 خط | فهرست/بازرسی متنی در دامنه ابزار؛ تأیید معنایی کامل انجام نشده.  |

## telegram_crawler

| فایل و منبع | نوع / دامنه | سنجه | بررسی و نتیجه |
|---|---|---|---|
| `telegram_crawler/.env.example` [^https://github.com/mtaham84/moshtari-yab/blob/a6aa1fcb1f9c716e49d1b462989bfb636409b106/telegram_crawler/.env.example] | متن .example | 28 خط | فهرست/بازرسی متنی در دامنه ابزار؛ تأیید معنایی کامل انجام نشده.  |
| `telegram_crawler/README.md` [^https://github.com/mtaham84/moshtari-yab/blob/a6aa1fcb1f9c716e49d1b462989bfb636409b106/telegram_crawler/README.md] | متن .md | 49 خط | فهرست/بازرسی متنی در دامنه ابزار؛ تأیید معنایی کامل انجام نشده.  |
| `telegram_crawler/__init__.py` [^https://github.com/mtaham84/moshtari-yab/blob/a6aa1fcb1f9c716e49d1b462989bfb636409b106/telegram_crawler/__init__.py] | Python | 3 خط | اسکن ساختاری؛ یافته مستقل ثبت نشده.  |
| `telegram_crawler/config.py` [^https://github.com/mtaham84/moshtari-yab/blob/a6aa1fcb1f9c716e49d1b462989bfb636409b106/telegram_crawler/config.py] | Python | 107 خط؛ max CC=9 | اسکن ساختاری؛ یافته مستقل ثبت نشده. توابع نمونه برای پیگیری: `_int`, `_float`. |
| `telegram_crawler/db.py` [^https://github.com/mtaham84/moshtari-yab/blob/a6aa1fcb1f9c716e49d1b462989bfb636409b106/telegram_crawler/db.py] | Python | 136 خط؛ max CC=5 | اسکن ساختاری؛ یافته مستقل ثبت نشده. توابع نمونه برای پیگیری: `_values`, `_placeholders`. |
| `telegram_crawler/extractor.py` [^https://github.com/mtaham84/moshtari-yab/blob/a6aa1fcb1f9c716e49d1b462989bfb636409b106/telegram_crawler/extractor.py] | Python | 231 خط؛ max CC=31؛ except عمومی=3 | اسکن ساختاری؛ یافته مستقل ثبت نشده. بررسی تکمیلی: خطای قابل‌بازیابی از خطای داده/مجوز تفکیک شود. |
| `telegram_crawler/main.py` [^https://github.com/mtaham84/moshtari-yab/blob/a6aa1fcb1f9c716e49d1b462989bfb636409b106/telegram_crawler/main.py] | Python | 127 خط؛ max CC=24؛ except عمومی=1 | اسکن ساختاری؛ یافته مستقل ثبت نشده. بررسی تکمیلی: خطای قابل‌بازیابی از خطای داده/مجوز تفکیک شود. |
| `telegram_crawler/models.py` [^https://github.com/mtaham84/moshtari-yab/blob/a6aa1fcb1f9c716e49d1b462989bfb636409b106/telegram_crawler/models.py] | Python | 14 خط؛ max CC=1 | اسکن ساختاری؛ یافته مستقل ثبت نشده.  |
| `telegram_crawler/monitor.py` [^https://github.com/mtaham84/moshtari-yab/blob/a6aa1fcb1f9c716e49d1b462989bfb636409b106/telegram_crawler/monitor.py] | Python | 472 خط؛ max CC=23؛ except عمومی=10 | اسکن ساختاری؛ یافته مستقل ثبت نشده. بررسی تکمیلی: خطای قابل‌بازیابی از خطای داده/مجوز تفکیک شود. |
| `telegram_crawler/panel.py` [^https://github.com/mtaham84/moshtari-yab/blob/a6aa1fcb1f9c716e49d1b462989bfb636409b106/telegram_crawler/panel.py] | Python | 140 خط؛ max CC=13 | اسکن ساختاری؛ یافته مستقل ثبت نشده. توابع نمونه برای پیگیری: `classify_join_error`, `normalize_link`. |
| `telegram_crawler/ratelimit.py` [^https://github.com/mtaham84/moshtari-yab/blob/a6aa1fcb1f9c716e49d1b462989bfb636409b106/telegram_crawler/ratelimit.py] | Python | 57 خط؛ max CC=8 | اسکن ساختاری؛ یافته مستقل ثبت نشده. توابع نمونه برای پیگیری: `with_flood_retry`, `pace`. |
| `telegram_crawler/retention.py` [^https://github.com/mtaham84/moshtari-yab/blob/a6aa1fcb1f9c716e49d1b462989bfb636409b106/telegram_crawler/retention.py] | Python | 662 خط؛ max CC=73؛ تابع>100=1 | اسکن ساختاری؛ یافته مستقل ثبت نشده. مرز مسئولیت `purge_retained_data` در 373–660 بررسی تکمیلی شود. |
| `telegram_crawler/schema.sql` [^https://github.com/mtaham84/moshtari-yab/blob/a6aa1fcb1f9c716e49d1b462989bfb636409b106/telegram_crawler/schema.sql] | متن .sql | 95 خط | فهرست/بازرسی متنی در دامنه ابزار؛ تأیید معنایی کامل انجام نشده.  |
| `telegram_crawler/telegram_client.py` [^https://github.com/mtaham84/moshtari-yab/blob/a6aa1fcb1f9c716e49d1b462989bfb636409b106/telegram_crawler/telegram_client.py] | Python | 198 خط؛ max CC=16؛ except عمومی=1 | اسکن ساختاری؛ یافته مستقل ثبت نشده. بررسی تکمیلی: خطای قابل‌بازیابی از خطای داده/مجوز تفکیک شود. |
| `telegram_crawler/tests/__init__.py` [^https://github.com/mtaham84/moshtari-yab/blob/a6aa1fcb1f9c716e49d1b462989bfb636409b106/telegram_crawler/tests/__init__.py] | Python؛ تست/اسکریپت آزمون | 0 خط | اسکن ساختاری؛ یافته مستقل ثبت نشده. نتیجه runnerها در test-results.md؛ پوشش این فایل از نام آن استنباط نمی‌شود. |
| `telegram_crawler/tests/test_crawler.py` [^https://github.com/mtaham84/moshtari-yab/blob/a6aa1fcb1f9c716e49d1b462989bfb636409b106/telegram_crawler/tests/test_crawler.py] | Python؛ تست/اسکریپت آزمون | 439 خط؛ max CC=23 | اسکن ساختاری؛ یافته مستقل ثبت نشده. نتیجه runnerها در test-results.md؛ پوشش این فایل از نام آن استنباط نمی‌شود. |
| `telegram_crawler/tests/test_ratelimit.py` [^https://github.com/mtaham84/moshtari-yab/blob/a6aa1fcb1f9c716e49d1b462989bfb636409b106/telegram_crawler/tests/test_ratelimit.py] | Python؛ تست/اسکریپت آزمون | 23 خط؛ max CC=3 | اسکن ساختاری؛ یافته مستقل ثبت نشده. نتیجه runnerها در test-results.md؛ پوشش این فایل از نام آن استنباط نمی‌شود. |
| `telegram_crawler/tests/test_retention.py` [^https://github.com/mtaham84/moshtari-yab/blob/a6aa1fcb1f9c716e49d1b462989bfb636409b106/telegram_crawler/tests/test_retention.py] | Python؛ تست/اسکریپت آزمون | 105 خط؛ max CC=12 | اسکن ساختاری؛ یافته مستقل ثبت نشده. نتیجه runnerها در test-results.md؛ پوشش این فایل از نام آن استنباط نمی‌شود. |

## templates

| فایل و منبع | نوع / دامنه | سنجه | بررسی و نتیجه |
|---|---|---|---|
| `templates/accounts/dashboard.html` [^https://github.com/mtaham84/moshtari-yab/blob/a6aa1fcb1f9c716e49d1b462989bfb636409b106/templates/accounts/dashboard.html] | متن .html | 393 خط | شاهد هدفمند: R29؛ شرح و اصلاح در گزارش اصلی. |
| `templates/accounts/login.html` [^https://github.com/mtaham84/moshtari-yab/blob/a6aa1fcb1f9c716e49d1b462989bfb636409b106/templates/accounts/login.html] | متن .html | 235 خط | فهرست/بازرسی متنی در دامنه ابزار؛ تأیید معنایی کامل انجام نشده.  |
| `templates/accounts/onboarding.html` [^https://github.com/mtaham84/moshtari-yab/blob/a6aa1fcb1f9c716e49d1b462989bfb636409b106/templates/accounts/onboarding.html] | متن .html | 275 خط | فهرست/بازرسی متنی در دامنه ابزار؛ تأیید معنایی کامل انجام نشده.  |
| `templates/accounts/outreach.html` [^https://github.com/mtaham84/moshtari-yab/blob/a6aa1fcb1f9c716e49d1b462989bfb636409b106/templates/accounts/outreach.html] | متن .html | 956 خط | شاهد هدفمند: R29؛ شرح و اصلاح در گزارش اصلی. |
| `templates/accounts/settings.html` [^https://github.com/mtaham84/moshtari-yab/blob/a6aa1fcb1f9c716e49d1b462989bfb636409b106/templates/accounts/settings.html] | متن .html | 363 خط | فهرست/بازرسی متنی در دامنه ابزار؛ تأیید معنایی کامل انجام نشده.  |
| `templates/accounts/shop.html` [^https://github.com/mtaham84/moshtari-yab/blob/a6aa1fcb1f9c716e49d1b462989bfb636409b106/templates/accounts/shop.html] | متن .html | 90 خط | فهرست/بازرسی متنی در دامنه ابزار؛ تأیید معنایی کامل انجام نشده.  |
| `templates/accounts/shop_edit.html` [^https://github.com/mtaham84/moshtari-yab/blob/a6aa1fcb1f9c716e49d1b462989bfb636409b106/templates/accounts/shop_edit.html] | متن .html | 47 خط | فهرست/بازرسی متنی در دامنه ابزار؛ تأیید معنایی کامل انجام نشده.  |
| `templates/accounts/signup.html` [^https://github.com/mtaham84/moshtari-yab/blob/a6aa1fcb1f9c716e49d1b462989bfb636409b106/templates/accounts/signup.html] | متن .html | 226 خط | فهرست/بازرسی متنی در دامنه ابزار؛ تأیید معنایی کامل انجام نشده.  |
| `templates/base.html` [^https://github.com/mtaham84/moshtari-yab/blob/a6aa1fcb1f9c716e49d1b462989bfb636409b106/templates/base.html] | متن .html | 30 خط | فهرست/بازرسی متنی در دامنه ابزار؛ تأیید معنایی کامل انجام نشده.  |
| `templates/discovery/_x_styles.html` [^https://github.com/mtaham84/moshtari-yab/blob/a6aa1fcb1f9c716e49d1b462989bfb636409b106/templates/discovery/_x_styles.html] | متن .html | 67 خط | فهرست/بازرسی متنی در دامنه ابزار؛ تأیید معنایی کامل انجام نشده.  |
| `templates/discovery/_x_switch.html` [^https://github.com/mtaham84/moshtari-yab/blob/a6aa1fcb1f9c716e49d1b462989bfb636409b106/templates/discovery/_x_switch.html] | متن .html | 12 خط | فهرست/بازرسی متنی در دامنه ابزار؛ تأیید معنایی کامل انجام نشده.  |
| `templates/discovery/communities_list.html` [^https://github.com/mtaham84/moshtari-yab/blob/a6aa1fcb1f9c716e49d1b462989bfb636409b106/templates/discovery/communities_list.html] | متن .html | 234 خط | فهرست/بازرسی متنی در دامنه ابزار؛ تأیید معنایی کامل انجام نشده.  |
| `templates/discovery/instagram_comment_detail.html` [^https://github.com/mtaham84/moshtari-yab/blob/a6aa1fcb1f9c716e49d1b462989bfb636409b106/templates/discovery/instagram_comment_detail.html] | متن .html | 27 خط | فهرست/بازرسی متنی در دامنه ابزار؛ تأیید معنایی کامل انجام نشده.  |
| `templates/discovery/instagram_pages.html` [^https://github.com/mtaham84/moshtari-yab/blob/a6aa1fcb1f9c716e49d1b462989bfb636409b106/templates/discovery/instagram_pages.html] | متن .html | 175 خط | فهرست/بازرسی متنی در دامنه ابزار؛ تأیید معنایی کامل انجام نشده.  |
| `templates/discovery/instagram_post_review.html` [^https://github.com/mtaham84/moshtari-yab/blob/a6aa1fcb1f9c716e49d1b462989bfb636409b106/templates/discovery/instagram_post_review.html] | متن .html | 106 خط | فهرست/بازرسی متنی در دامنه ابزار؛ تأیید معنایی کامل انجام نشده.  |
| `templates/discovery/opportunities_list.html` [^https://github.com/mtaham84/moshtari-yab/blob/a6aa1fcb1f9c716e49d1b462989bfb636409b106/templates/discovery/opportunities_list.html] | متن .html | 804 خط | فهرست/بازرسی متنی در دامنه ابزار؛ تأیید معنایی کامل انجام نشده.  |
| `templates/discovery/opportunity_detail.html` [^https://github.com/mtaham84/moshtari-yab/blob/a6aa1fcb1f9c716e49d1b462989bfb636409b106/templates/discovery/opportunity_detail.html] | متن .html | 682 خط | فهرست/بازرسی متنی در دامنه ابزار؛ تأیید معنایی کامل انجام نشده.  |
| `templates/discovery/partials/reply_suggestion_card.html` [^https://github.com/mtaham84/moshtari-yab/blob/a6aa1fcb1f9c716e49d1b462989bfb636409b106/templates/discovery/partials/reply_suggestion_card.html] | متن .html | 48 خط | فهرست/بازرسی متنی در دامنه ابزار؛ تأیید معنایی کامل انجام نشده.  |
| `templates/discovery/partials/source_link.html` [^https://github.com/mtaham84/moshtari-yab/blob/a6aa1fcb1f9c716e49d1b462989bfb636409b106/templates/discovery/partials/source_link.html] | متن .html | 1 خط | فهرست/بازرسی متنی در دامنه ابزار؛ تأیید معنایی کامل انجام نشده.  |
| `templates/discovery/partials/source_status.html` [^https://github.com/mtaham84/moshtari-yab/blob/a6aa1fcb1f9c716e49d1b462989bfb636409b106/templates/discovery/partials/source_status.html] | متن .html | 12 خط | فهرست/بازرسی متنی در دامنه ابزار؛ تأیید معنایی کامل انجام نشده.  |
| `templates/discovery/x_management.html` [^https://github.com/mtaham84/moshtari-yab/blob/a6aa1fcb1f9c716e49d1b462989bfb636409b106/templates/discovery/x_management.html] | متن .html | 21 خط | فهرست/بازرسی متنی در دامنه ابزار؛ تأیید معنایی کامل انجام نشده.  |
| `templates/discovery/x_product.html` [^https://github.com/mtaham84/moshtari-yab/blob/a6aa1fcb1f9c716e49d1b462989bfb636409b106/templates/discovery/x_product.html] | متن .html | 114 خط | فهرست/بازرسی متنی در دامنه ابزار؛ تأیید معنایی کامل انجام نشده.  |
| `templates/discovery/x_search.html` [^https://github.com/mtaham84/moshtari-yab/blob/a6aa1fcb1f9c716e49d1b462989bfb636409b106/templates/discovery/x_search.html] | متن .html | 91 خط | فهرست/بازرسی متنی در دامنه ابزار؛ تأیید معنایی کامل انجام نشده.  |
| `templates/ops/_periods.html` [^https://github.com/mtaham84/moshtari-yab/blob/a6aa1fcb1f9c716e49d1b462989bfb636409b106/templates/ops/_periods.html] | متن .html | 1 خط | فهرست/بازرسی متنی در دامنه ابزار؛ تأیید معنایی کامل انجام نشده.  |
| `templates/ops/base.html` [^https://github.com/mtaham84/moshtari-yab/blob/a6aa1fcb1f9c716e49d1b462989bfb636409b106/templates/ops/base.html] | متن .html | 43 خط | فهرست/بازرسی متنی در دامنه ابزار؛ تأیید معنایی کامل انجام نشده.  |
| `templates/ops/communities.html` [^https://github.com/mtaham84/moshtari-yab/blob/a6aa1fcb1f9c716e49d1b462989bfb636409b106/templates/ops/communities.html] | متن .html | 66 خط | فهرست/بازرسی متنی در دامنه ابزار؛ تأیید معنایی کامل انجام نشده.  |
| `templates/ops/costs.html` [^https://github.com/mtaham84/moshtari-yab/blob/a6aa1fcb1f9c716e49d1b462989bfb636409b106/templates/ops/costs.html] | متن .html | 44 خط | فهرست/بازرسی متنی در دامنه ابزار؛ تأیید معنایی کامل انجام نشده.  |
| `templates/ops/dashboard.html` [^https://github.com/mtaham84/moshtari-yab/blob/a6aa1fcb1f9c716e49d1b462989bfb636409b106/templates/ops/dashboard.html] | متن .html | 53 خط | فهرست/بازرسی متنی در دامنه ابزار؛ تأیید معنایی کامل انجام نشده.  |
| `templates/ops/form.html` [^https://github.com/mtaham84/moshtari-yab/blob/a6aa1fcb1f9c716e49d1b462989bfb636409b106/templates/ops/form.html] | متن .html | 19 خط | فهرست/بازرسی متنی در دامنه ابزار؛ تأیید معنایی کامل انجام نشده.  |
| `templates/ops/funnel.html` [^https://github.com/mtaham84/moshtari-yab/blob/a6aa1fcb1f9c716e49d1b462989bfb636409b106/templates/ops/funnel.html] | متن .html | 30 خط | فهرست/بازرسی متنی در دامنه ابزار؛ تأیید معنایی کامل انجام نشده.  |
| `templates/ops/manage_dashboard.html` [^https://github.com/mtaham84/moshtari-yab/blob/a6aa1fcb1f9c716e49d1b462989bfb636409b106/templates/ops/manage_dashboard.html] | متن .html | 2992 خط | شاهد هدفمند: R29؛ شرح و اصلاح در گزارش اصلی. |
| `templates/ops/providers.html` [^https://github.com/mtaham84/moshtari-yab/blob/a6aa1fcb1f9c716e49d1b462989bfb636409b106/templates/ops/providers.html] | متن .html | 53 خط | فهرست/بازرسی متنی در دامنه ابزار؛ تأیید معنایی کامل انجام نشده.  |
| `templates/ops/wallet_detail.html` [^https://github.com/mtaham84/moshtari-yab/blob/a6aa1fcb1f9c716e49d1b462989bfb636409b106/templates/ops/wallet_detail.html] | متن .html | 24 خط | فهرست/بازرسی متنی در دامنه ابزار؛ تأیید معنایی کامل انجام نشده.  |
| `templates/ops/wallets.html` [^https://github.com/mtaham84/moshtari-yab/blob/a6aa1fcb1f9c716e49d1b462989bfb636409b106/templates/ops/wallets.html] | متن .html | 22 خط | فهرست/بازرسی متنی در دامنه ابزار؛ تأیید معنایی کامل انجام نشده.  |
| `templates/ops/x.html` [^https://github.com/mtaham84/moshtari-yab/blob/a6aa1fcb1f9c716e49d1b462989bfb636409b106/templates/ops/x.html] | متن .html | 177 خط | فهرست/بازرسی متنی در دامنه ابزار؛ تأیید معنایی کامل انجام نشده.  |
| `templates/pages/landing.html` [^https://github.com/mtaham84/moshtari-yab/blob/a6aa1fcb1f9c716e49d1b462989bfb636409b106/templates/pages/landing.html] | متن .html | 656 خط | فهرست/بازرسی متنی در دامنه ابزار؛ تأیید معنایی کامل انجام نشده.  |
| `templates/panel/base_panel.html` [^https://github.com/mtaham84/moshtari-yab/blob/a6aa1fcb1f9c716e49d1b462989bfb636409b106/templates/panel/base_panel.html] | متن .html | 453 خط | فهرست/بازرسی متنی در دامنه ابزار؛ تأیید معنایی کامل انجام نشده.  |
| `templates/partials/appearance.html` [^https://github.com/mtaham84/moshtari-yab/blob/a6aa1fcb1f9c716e49d1b462989bfb636409b106/templates/partials/appearance.html] | متن .html | 1 خط | فهرست/بازرسی متنی در دامنه ابزار؛ تأیید معنایی کامل انجام نشده.  |
| `templates/partials/icons.html` [^https://github.com/mtaham84/moshtari-yab/blob/a6aa1fcb1f9c716e49d1b462989bfb636409b106/templates/partials/icons.html] | متن .html | 1 خط | فهرست/بازرسی متنی در دامنه ابزار؛ تأیید معنایی کامل انجام نشده.  |
| `templates/products/product_detail.html` [^https://github.com/mtaham84/moshtari-yab/blob/a6aa1fcb1f9c716e49d1b462989bfb636409b106/templates/products/product_detail.html] | متن .html | 316 خط | فهرست/بازرسی متنی در دامنه ابزار؛ تأیید معنایی کامل انجام نشده.  |
| `templates/products/product_form.html` [^https://github.com/mtaham84/moshtari-yab/blob/a6aa1fcb1f9c716e49d1b462989bfb636409b106/templates/products/product_form.html] | متن .html | 1068 خط | شاهد هدفمند: R19, R30؛ شرح و اصلاح در گزارش اصلی. |
| `templates/products/product_list.html` [^https://github.com/mtaham84/moshtari-yab/blob/a6aa1fcb1f9c716e49d1b462989bfb636409b106/templates/products/product_list.html] | متن .html | 169 خط | فهرست/بازرسی متنی در دامنه ابزار؛ تأیید معنایی کامل انجام نشده.  |
| `templates/products/public_product_card.html` [^https://github.com/mtaham84/moshtari-yab/blob/a6aa1fcb1f9c716e49d1b462989bfb636409b106/templates/products/public_product_card.html] | متن .html | 363 خط | شاهد هدفمند: R29؛ شرح و اصلاح در گزارش اصلی. |

## فایل‌های ریشه

| فایل و منبع | نوع / دامنه | سنجه | بررسی و نتیجه |
|---|---|---|---|
| `test_dedup_and_semantic.py` [^https://github.com/mtaham84/moshtari-yab/blob/a6aa1fcb1f9c716e49d1b462989bfb636409b106/test_dedup_and_semantic.py] | Python؛ تست/اسکریپت آزمون | 80 خط | اسکن ساختاری؛ یافته مستقل ثبت نشده. نتیجه runnerها در test-results.md؛ پوشش این فایل از نام آن استنباط نمی‌شود. |

## tests

| فایل و منبع | نوع / دامنه | سنجه | بررسی و نتیجه |
|---|---|---|---|
| `tests/test_political_semantic_filter.py` [^https://github.com/mtaham84/moshtari-yab/blob/a6aa1fcb1f9c716e49d1b462989bfb636409b106/tests/test_political_semantic_filter.py] | Python؛ تست/اسکریپت آزمون | 43 خط؛ max CC=4 | اسکن ساختاری؛ یافته مستقل ثبت نشده. نتیجه runnerها در test-results.md؛ پوشش این فایل از نام آن استنباط نمی‌شود. |

## workers

| فایل و منبع | نوع / دامنه | سنجه | بررسی و نتیجه |
|---|---|---|---|
| `workers/__init__.py` [^https://github.com/mtaham84/moshtari-yab/blob/a6aa1fcb1f9c716e49d1b462989bfb636409b106/workers/__init__.py] | Python | 2 خط | اسکن ساختاری؛ یافته مستقل ثبت نشده.  |
| `workers/ig_collector/__init__.py` [^https://github.com/mtaham84/moshtari-yab/blob/a6aa1fcb1f9c716e49d1b462989bfb636409b106/workers/ig_collector/__init__.py] | Python | 3 خط | اسکن ساختاری؛ یافته مستقل ثبت نشده.  |
| `workers/ig_collector/__main__.py` [^https://github.com/mtaham84/moshtari-yab/blob/a6aa1fcb1f9c716e49d1b462989bfb636409b106/workers/ig_collector/__main__.py] | Python | 4 خط | اسکن ساختاری؛ یافته مستقل ثبت نشده.  |
| `workers/ig_collector/backends/__init__.py` [^https://github.com/mtaham84/moshtari-yab/blob/a6aa1fcb1f9c716e49d1b462989bfb636409b106/workers/ig_collector/backends/__init__.py] | Python | 4 خط | اسکن ساختاری؛ یافته مستقل ثبت نشده.  |
| `workers/ig_collector/backends/base.py` [^https://github.com/mtaham84/moshtari-yab/blob/a6aa1fcb1f9c716e49d1b462989bfb636409b106/workers/ig_collector/backends/base.py] | Python | 114 خط؛ max CC=4 | اسکن ساختاری؛ یافته مستقل ثبت نشده. توابع نمونه برای پیگیری: `url`, `outcome_for_error`. |
| `workers/ig_collector/backends/hikerapi_backend.py` [^https://github.com/mtaham84/moshtari-yab/blob/a6aa1fcb1f9c716e49d1b462989bfb636409b106/workers/ig_collector/backends/hikerapi_backend.py] | Python | 259 خط؛ max CC=18 | اسکن ساختاری؛ یافته مستقل ثبت نشده. توابع نمونه برای پیگیری: `_path`, `_first`. |
| `workers/ig_collector/backends/instaloader_backend.py` [^https://github.com/mtaham84/moshtari-yab/blob/a6aa1fcb1f9c716e49d1b462989bfb636409b106/workers/ig_collector/backends/instaloader_backend.py] | Python | 196 خط؛ max CC=12؛ except عمومی=3 | اسکن ساختاری؛ یافته مستقل ثبت نشده. بررسی تکمیلی: خطای قابل‌بازیابی از خطای داده/مجوز تفکیک شود. |
| `workers/ig_collector/backends/mock.py` [^https://github.com/mtaham84/moshtari-yab/blob/a6aa1fcb1f9c716e49d1b462989bfb636409b106/workers/ig_collector/backends/mock.py] | Python | 112 خط؛ max CC=13 | اسکن ساختاری؛ یافته مستقل ثبت نشده. توابع نمونه برای پیگیری: `_int`, `_date`. |
| `workers/ig_collector/cli.py` [^https://github.com/mtaham84/moshtari-yab/blob/a6aa1fcb1f9c716e49d1b462989bfb636409b106/workers/ig_collector/cli.py] | Python | 162 خط؛ max CC=39؛ تابع>100=1 | اسکن ساختاری؛ یافته مستقل ثبت نشده. مرز مسئولیت `_main` در 35–155 بررسی تکمیلی شود. |
| `workers/ig_collector/hikerapi_mapping.py` [^https://github.com/mtaham84/moshtari-yab/blob/a6aa1fcb1f9c716e49d1b462989bfb636409b106/workers/ig_collector/hikerapi_mapping.py] | Python | 63 خط؛ max CC=5 | اسکن ساختاری؛ یافته مستقل ثبت نشده. توابع نمونه برای پیگیری: `shortcode_to_media_id`. |
| `workers/ig_collector/lexicon.py` [^https://github.com/mtaham84/moshtari-yab/blob/a6aa1fcb1f9c716e49d1b462989bfb636409b106/workers/ig_collector/lexicon.py] | Python | 104 خط؛ max CC=25 | اسکن ساختاری؛ یافته مستقل ثبت نشده. توابع نمونه برای پیگیری: `normalize_fa`, `_contains`. |
| `workers/ig_collector/policies.py` [^https://github.com/mtaham84/moshtari-yab/blob/a6aa1fcb1f9c716e49d1b462989bfb636409b106/workers/ig_collector/policies.py] | Python | 51 خط؛ max CC=5 | اسکن ساختاری؛ یافته مستقل ثبت نشده. توابع نمونه برای پیگیری: `buyer_request`, `seller_post`. |
| `workers/ig_collector/post_selection.py` [^https://github.com/mtaham84/moshtari-yab/blob/a6aa1fcb1f9c716e49d1b462989bfb636409b106/workers/ig_collector/post_selection.py] | Python | 222 خط؛ max CC=16 | اسکن ساختاری؛ یافته مستقل ثبت نشده. توابع نمونه برای پیگیری: `seller_post_priority`, `select_comment_candidates`. |
| `workers/ig_collector/tests/fixtures/instagram.json` [^https://github.com/mtaham84/moshtari-yab/blob/a6aa1fcb1f9c716e49d1b462989bfb636409b106/workers/ig_collector/tests/fixtures/instagram.json] | متن .json؛ تست/اسکریپت آزمون | 21 خط | فهرست/بازرسی متنی در دامنه ابزار؛ تأیید معنایی کامل انجام نشده. نتیجه runnerها در test-results.md؛ پوشش این فایل از نام آن استنباط نمی‌شود. |
| `workers/ig_collector/tests/test_manual_post.py` [^https://github.com/mtaham84/moshtari-yab/blob/a6aa1fcb1f9c716e49d1b462989bfb636409b106/workers/ig_collector/tests/test_manual_post.py] | Python؛ تست/اسکریپت آزمون | 245 خط؛ max CC=20 | اسکن ساختاری؛ یافته مستقل ثبت نشده. نتیجه runnerها در test-results.md؛ پوشش این فایل از نام آن استنباط نمی‌شود. |
| `workers/ig_collector/tests/test_page_ref.py` [^https://github.com/mtaham84/moshtari-yab/blob/a6aa1fcb1f9c716e49d1b462989bfb636409b106/workers/ig_collector/tests/test_page_ref.py] | Python؛ تست/اسکریپت آزمون | 84 خط؛ max CC=4 | اسکن ساختاری؛ یافته مستقل ثبت نشده. نتیجه runnerها در test-results.md؛ پوشش این فایل از نام آن استنباط نمی‌شود. |
| `workers/ig_collector/tests/test_post_review_gate.py` [^https://github.com/mtaham84/moshtari-yab/blob/a6aa1fcb1f9c716e49d1b462989bfb636409b106/workers/ig_collector/tests/test_post_review_gate.py] | Python؛ تست/اسکریپت آزمون | 114 خط؛ max CC=6 | اسکن ساختاری؛ یافته مستقل ثبت نشده. نتیجه runnerها در test-results.md؛ پوشش این فایل از نام آن استنباط نمی‌شود. |
| `workers/ig_collector/tests/test_worker.py` [^https://github.com/mtaham84/moshtari-yab/blob/a6aa1fcb1f9c716e49d1b462989bfb636409b106/workers/ig_collector/tests/test_worker.py] | Python؛ تست/اسکریپت آزمون | 1136 خط؛ max CC=18 | اسکن ساختاری؛ یافته مستقل ثبت نشده. نتیجه runnerها در test-results.md؛ پوشش این فایل از نام آن استنباط نمی‌شود. |
| `workers/ig_collector/worker.py` [^https://github.com/mtaham84/moshtari-yab/blob/a6aa1fcb1f9c716e49d1b462989bfb636409b106/workers/ig_collector/worker.py] | Python | 1245 خط؛ max CC=228؛ except عمومی=8؛ clone=20؛ تابع>100=2 | شاهد هدفمند: R39؛ شرح و اصلاح در گزارش اصلی. |
| `workers/state_migrate.py` [^https://github.com/mtaham84/moshtari-yab/blob/a6aa1fcb1f9c716e49d1b462989bfb636409b106/workers/state_migrate.py] | Python | 33 خط؛ max CC=7 | اسکن ساختاری؛ یافته مستقل ثبت نشده. توابع نمونه برای پیگیری: `main`. |
| `workers/telegram_config_bot.py` [^https://github.com/mtaham84/moshtari-yab/blob/a6aa1fcb1f9c716e49d1b462989bfb636409b106/workers/telegram_config_bot.py] | Python | 377 خط؛ max CC=22؛ except عمومی=5 | شاهد هدفمند: R03؛ شرح و اصلاح در گزارش اصلی. |
| `workers/x_collector/__init__.py` [^https://github.com/mtaham84/moshtari-yab/blob/a6aa1fcb1f9c716e49d1b462989bfb636409b106/workers/x_collector/__init__.py] | Python | 2 خط | اسکن ساختاری؛ یافته مستقل ثبت نشده.  |
| `workers/x_collector/__main__.py` [^https://github.com/mtaham84/moshtari-yab/blob/a6aa1fcb1f9c716e49d1b462989bfb636409b106/workers/x_collector/__main__.py] | Python | 123 خط؛ max CC=41 | اسکن ساختاری؛ یافته مستقل ثبت نشده. توابع نمونه برای پیگیری: `main`, `_print_commands`. |
| `workers/x_collector/account_pool.py` [^https://github.com/mtaham84/moshtari-yab/blob/a6aa1fcb1f9c716e49d1b462989bfb636409b106/workers/x_collector/account_pool.py] | Python | 282 خط؛ max CC=16؛ except عمومی=6 | اسکن ساختاری؛ یافته مستقل ثبت نشده. بررسی تکمیلی: خطای قابل‌بازیابی از خطای داده/مجوز تفکیک شود. |
| `workers/x_collector/cli_mapping.py` [^https://github.com/mtaham84/moshtari-yab/blob/a6aa1fcb1f9c716e49d1b462989bfb636409b106/workers/x_collector/cli_mapping.py] | Python | 37 خط | اسکن ساختاری؛ یافته مستقل ثبت نشده.  |
| `workers/x_collector/client.py` [^https://github.com/mtaham84/moshtari-yab/blob/a6aa1fcb1f9c716e49d1b462989bfb636409b106/workers/x_collector/client.py] | Python | 393 خط؛ max CC=56؛ except عمومی=4؛ تابع>100=1 | اسکن ساختاری؛ یافته مستقل ثبت نشده. مرز مسئولیت `search_top_past_week` در 91–324 بررسی تکمیلی شود. |
| `workers/x_collector/families.py` [^https://github.com/mtaham84/moshtari-yab/blob/a6aa1fcb1f9c716e49d1b462989bfb636409b106/workers/x_collector/families.py] | Python | 222 خط؛ max CC=18؛ except عمومی=1 | اسکن ساختاری؛ یافته مستقل ثبت نشده. بررسی تکمیلی: خطای قابل‌بازیابی از خطای داده/مجوز تفکیک شود. |
| `workers/x_collector/intent_queries.py` [^https://github.com/mtaham84/moshtari-yab/blob/a6aa1fcb1f9c716e49d1b462989bfb636409b106/workers/x_collector/intent_queries.py] | Python | 426 خط؛ max CC=33؛ except عمومی=1 | اسکن ساختاری؛ یافته مستقل ثبت نشده. بررسی تکمیلی: خطای قابل‌بازیابی از خطای داده/مجوز تفکیک شود. |
| `workers/x_collector/llm_terms.py` [^https://github.com/mtaham84/moshtari-yab/blob/a6aa1fcb1f9c716e49d1b462989bfb636409b106/workers/x_collector/llm_terms.py] | Python | 211 خط؛ max CC=29؛ except عمومی=1 | اسکن ساختاری؛ یافته مستقل ثبت نشده. بررسی تکمیلی: خطای قابل‌بازیابی از خطای داده/مجوز تفکیک شود. |
| `workers/x_collector/proqe.py` [^https://github.com/mtaham84/moshtari-yab/blob/a6aa1fcb1f9c716e49d1b462989bfb636409b106/workers/x_collector/proqe.py] | Python | 596 خط؛ max CC=25؛ except عمومی=6 | اسکن ساختاری؛ یافته مستقل ثبت نشده. بررسی تکمیلی: خطای قابل‌بازیابی از خطای داده/مجوز تفکیک شود. |
| `workers/x_collector/query_source.py` [^https://github.com/mtaham84/moshtari-yab/blob/a6aa1fcb1f9c716e49d1b462989bfb636409b106/workers/x_collector/query_source.py] | Python | 218 خط؛ max CC=17؛ except عمومی=9 | اسکن ساختاری؛ یافته مستقل ثبت نشده. بررسی تکمیلی: خطای قابل‌بازیابی از خطای داده/مجوز تفکیک شود. |
| `workers/x_collector/tests/__init__.py` [^https://github.com/mtaham84/moshtari-yab/blob/a6aa1fcb1f9c716e49d1b462989bfb636409b106/workers/x_collector/tests/__init__.py] | Python؛ تست/اسکریپت آزمون | 2 خط | اسکن ساختاری؛ یافته مستقل ثبت نشده. نتیجه runnerها در test-results.md؛ پوشش این فایل از نام آن استنباط نمی‌شود. |
| `workers/x_collector/tests/fixtures/x_record_legacy.jsonl` [^https://github.com/mtaham84/moshtari-yab/blob/a6aa1fcb1f9c716e49d1b462989bfb636409b106/workers/x_collector/tests/fixtures/x_record_legacy.jsonl] | متن .jsonl؛ تست/اسکریپت آزمون | 1 خط | فهرست/بازرسی متنی در دامنه ابزار؛ تأیید معنایی کامل انجام نشده. نتیجه runnerها در test-results.md؛ پوشش این فایل از نام آن استنباط نمی‌شود. |
| `workers/x_collector/tests/fixtures/x_tweets.json` [^https://github.com/mtaham84/moshtari-yab/blob/a6aa1fcb1f9c716e49d1b462989bfb636409b106/workers/x_collector/tests/fixtures/x_tweets.json] | متن .json؛ تست/اسکریپت آزمون | 21 خط | فهرست/بازرسی متنی در دامنه ابزار؛ تأیید معنایی کامل انجام نشده. نتیجه runnerها در test-results.md؛ پوشش این فایل از نام آن استنباط نمی‌شود. |
| `workers/x_collector/tests/test_families.py` [^https://github.com/mtaham84/moshtari-yab/blob/a6aa1fcb1f9c716e49d1b462989bfb636409b106/workers/x_collector/tests/test_families.py] | Python؛ تست/اسکریپت آزمون | 53 خط؛ max CC=5 | اسکن ساختاری؛ یافته مستقل ثبت نشده. نتیجه runnerها در test-results.md؛ پوشش این فایل از نام آن استنباط نمی‌شود. |
| `workers/x_collector/tests/test_family_topics.py` [^https://github.com/mtaham84/moshtari-yab/blob/a6aa1fcb1f9c716e49d1b462989bfb636409b106/workers/x_collector/tests/test_family_topics.py] | Python؛ تست/اسکریپت آزمون | 88 خط؛ max CC=9 | اسکن ساختاری؛ یافته مستقل ثبت نشده. نتیجه runnerها در test-results.md؛ پوشش این فایل از نام آن استنباط نمی‌شود. |
| `workers/x_collector/tests/test_intent_queries.py` [^https://github.com/mtaham84/moshtari-yab/blob/a6aa1fcb1f9c716e49d1b462989bfb636409b106/workers/x_collector/tests/test_intent_queries.py] | Python؛ تست/اسکریپت آزمون | 289 خط؛ max CC=9 | اسکن ساختاری؛ یافته مستقل ثبت نشده. نتیجه runnerها در test-results.md؛ پوشش این فایل از نام آن استنباط نمی‌شود. |
| `workers/x_collector/tests/test_proqe.py` [^https://github.com/mtaham84/moshtari-yab/blob/a6aa1fcb1f9c716e49d1b462989bfb636409b106/workers/x_collector/tests/test_proqe.py] | Python؛ تست/اسکریپت آزمون | 284 خط؛ max CC=10 | اسکن ساختاری؛ یافته مستقل ثبت نشده. نتیجه runnerها در test-results.md؛ پوشش این فایل از نام آن استنباط نمی‌شود. |
| `workers/x_collector/tests/test_records.py` [^https://github.com/mtaham84/moshtari-yab/blob/a6aa1fcb1f9c716e49d1b462989bfb636409b106/workers/x_collector/tests/test_records.py] | Python؛ تست/اسکریپت آزمون | 88 خط؛ max CC=6 | اسکن ساختاری؛ یافته مستقل ثبت نشده. نتیجه runnerها در test-results.md؛ پوشش این فایل از نام آن استنباط نمی‌شود. |
| `workers/x_collector/tests/test_seen_store.py` [^https://github.com/mtaham84/moshtari-yab/blob/a6aa1fcb1f9c716e49d1b462989bfb636409b106/workers/x_collector/tests/test_seen_store.py] | Python؛ تست/اسکریپت آزمون | 210 خط؛ max CC=16 | اسکن ساختاری؛ یافته مستقل ثبت نشده. نتیجه runnerها در test-results.md؛ پوشش این فایل از نام آن استنباط نمی‌شود. |
| `workers/x_collector/tests/test_x_collector.py` [^https://github.com/mtaham84/moshtari-yab/blob/a6aa1fcb1f9c716e49d1b462989bfb636409b106/workers/x_collector/tests/test_x_collector.py] | Python؛ تست/اسکریپت آزمون | 401 خط؛ max CC=5 | اسکن ساختاری؛ یافته مستقل ثبت نشده. نتیجه runnerها در test-results.md؛ پوشش این فایل از نام آن استنباط نمی‌شود. |
| `workers/x_collector/worker.py` [^https://github.com/mtaham84/moshtari-yab/blob/a6aa1fcb1f9c716e49d1b462989bfb636409b106/workers/x_collector/worker.py] | Python | 708 خط؛ max CC=90؛ except عمومی=11؛ clone=20؛ تابع>100=1 | اسکن ساختاری؛ یافته مستقل ثبت نشده. مرز مسئولیت `_run_locked` در 274–476 بررسی تکمیلی شود. |

## x_ingest

| فایل و منبع | نوع / دامنه | سنجه | بررسی و نتیجه |
|---|---|---|---|
| `x_ingest/__init__.py` [^https://github.com/mtaham84/moshtari-yab/blob/a6aa1fcb1f9c716e49d1b462989bfb636409b106/x_ingest/__init__.py] | Python | 1 خط | اسکن ساختاری؛ یافته مستقل ثبت نشده.  |
| `x_ingest/__main__.py` [^https://github.com/mtaham84/moshtari-yab/blob/a6aa1fcb1f9c716e49d1b462989bfb636409b106/x_ingest/__main__.py] | Python | 224 خط؛ max CC=29؛ except عمومی=2 | اسکن ساختاری؛ یافته مستقل ثبت نشده. بررسی تکمیلی: خطای قابل‌بازیابی از خطای داده/مجوز تفکیک شود. |
| `x_ingest/tests/test_loader.py` [^https://github.com/mtaham84/moshtari-yab/blob/a6aa1fcb1f9c716e49d1b462989bfb636409b106/x_ingest/tests/test_loader.py] | Python؛ تست/اسکریپت آزمون | 90 خط؛ max CC=5 | اسکن ساختاری؛ یافته مستقل ثبت نشده. نتیجه runnerها در test-results.md؛ پوشش این فایل از نام آن استنباط نمی‌شود. |
| `x_ingest/tests/test_x_ingest.py` [^https://github.com/mtaham84/moshtari-yab/blob/a6aa1fcb1f9c716e49d1b462989bfb636409b106/x_ingest/tests/test_x_ingest.py] | Python؛ تست/اسکریپت آزمون | 26 خط؛ max CC=5 | اسکن ساختاری؛ یافته مستقل ثبت نشده. نتیجه runnerها در test-results.md؛ پوشش این فایل از نام آن استنباط نمی‌شود. |

## xray

| فایل و منبع | نوع / دامنه | سنجه | بررسی و نتیجه |
|---|---|---|---|
| `xray/config.example.json` [^https://github.com/mtaham84/moshtari-yab/blob/a6aa1fcb1f9c716e49d1b462989bfb636409b106/xray/config.example.json] | متن .json | 39 خط | فهرست/بازرسی متنی در دامنه ابزار؛ تأیید معنایی کامل انجام نشده.  |
| `xray/multi_proxy_manager.py` [^https://github.com/mtaham84/moshtari-yab/blob/a6aa1fcb1f9c716e49d1b462989bfb636409b106/xray/multi_proxy_manager.py] | Python | 759 خط؛ max CC=17؛ except عمومی=15؛ clone=184؛ تابع>100=1 | شاهد هدفمند: R04, R40؛ شرح و اصلاح در گزارش اصلی. |
| `xray/new_configs.example.txt` [^https://github.com/mtaham84/moshtari-yab/blob/a6aa1fcb1f9c716e49d1b462989bfb636409b106/xray/new_configs.example.txt] | متن .txt | 3 خط | فهرست/بازرسی متنی در دامنه ابزار؛ تأیید معنایی کامل انجام نشده.  |
| `xray/proxy_switcher.py` [^https://github.com/mtaham84/moshtari-yab/blob/a6aa1fcb1f9c716e49d1b462989bfb636409b106/xray/proxy_switcher.py] | Python | 851 خط؛ max CC=16؛ except عمومی=14؛ clone=184 | شاهد هدفمند: R40؛ شرح و اصلاح در گزارش اصلی. |