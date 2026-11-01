# پرونده پیشرفت پایدار اصلاح پروژه moshtari-yab (۱۱ فاز و ۹۹ تسک)

- **آخرین به‌روزرسانی:** `2026-11-01T04:58:07.551294`
- **مجموع تسک‌ها:** 99

## خلاصه وضعیت کلی

| وضعیت | تعداد |
|---|---|
| **PLANNED** | 59 |
| **IN_PROGRESS** | 1 |
| **DONE** | 38 |
| **BLOCKED** | 0 |
| **DEFERRED** | 1 |
| **N/A** | 0 |

## ماتریس وضعیت تمام ۹۹ تسک

| شناسه | فاز | عنوان | پیش‌نیاز | برنامه‌ریزی | پیاده‌سازی | تست | فعال‌سازی | وضعیت کلی | گزارش / سند |
|---|---|---|---|---|---|---|---|---|---|
| `P00.01` | 0 | ثبت snapshot و فهرست مجاز تغییر | ندارد؛ اولین تسک است. | DONE | DONE | DONE | DONE | **DONE** | [P00.01](tasks/P00.01.md) |
| `P00.02` | 0 | مهار قابلیت‌های پرخطر با کنترل قابل‌بازگشت | P00.01 | DONE | DONE | DONE | DONE | **DONE** | [P00.02](tasks/P00.02.md) |
| `P00.03` | 0 | ساخت baseline تکرارپذیر و محیط تست مستقل | P00.01 | DONE | DONE | DONE | DONE | **DONE** | [P00.03](tasks/P00.03.md) |
| `P00.04` | 0 | اصلاح تست‌های ناهماهنگ بدون پنهان‌کردن خطا | P00.03 | IN_PROGRESS | PLANNED | PLANNED | PLANNED | **IN_PROGRESS** | [P00.04](tasks/P00.04.md) |
| `P00.05` | 0 | رفع دو نام تعریف‌نشده در view اکانت | P00.03 | DONE | DONE | DONE | DONE | **DONE** | [P00.05](tasks/P00.05.md) |
| `P00.06` | 0 | کنترل مرکزی مدیر بات | P00.01, P00.03 | DONE | DONE | DONE | DONE | **DONE** | [P00.06](tasks/P00.06.md) |
| `P00.07` | 0 | حذف credential ثابت از مسیر فعال و مدیریت تعویض | P00.01 | DONE | DONE | DONE | DONE | **DONE** | [P00.07](tasks/P00.07.md) |
| `P01.01` | 1 | قرارداد دسترسی و pool حساب پلتفرم | P00.01 | DONE | DONE | DONE | DONE | **DONE** | [P01.01](tasks/P01.01.md) |
| `P01.02` | 1 | selectorهای محدود و policy مشترک دسترسی | P01.01, P00.03 | DONE | DONE | DONE | DONE | **DONE** | [P01.02](tasks/P01.02.md) |
| `P01.03` | 1 | اتصال follow-up و endpointهای اکانت به policy | P01.02, P00.05 | DONE | DONE | DONE | DONE | **DONE** | [P01.03](tasks/P01.03.md) |
| `P01.04` | 1 | امن‌سازی یا بستن مسیر اتصال موجود | P01.02 | DONE | DONE | DONE | DONE | **DONE** | [P01.04](tasks/P01.04.md) |
| `P01.05` | 1 | حذف fallback دسته خصوصی سراسری | P01.02 | DONE | DONE | DONE | DONE | **DONE** | [P01.05](tasks/P01.05.md) |
| `P01.06` | 1 | hotfix مالکیت و نوشتن pitch | P01.02 | DONE | DONE | DONE | DONE | **DONE** | [P01.06](tasks/P01.06.md) |
| `P01.07` | 1 | انتقال مستقل pitch فعال به مدل tenant-scoped | P01.06 | DONE | DEFERRED | N/A | DEFERRED | **DEFERRED** | [P01.07](tasks/P01.07.md) |
| `P01.08` | 1 | بازبینی نهایی ماتریس مجوز | P01.03, P01.04, P01.05, P01.06 | DONE | DONE | DONE | DONE | **DONE** | [P01.08](tasks/P01.08.md) |
| `P02.01` | 2 | مرز API پیشنهاد پولی بدون بازکردن مصرف | P01.05, P00.02 | DONE | DONE | DONE | DONE | **DONE** | [P02.01](tasks/P02.01.md) |
| `P02.02` | 2 | validator مشترک تصویر | P00.02, P00.03 | DONE | DONE | DONE | DONE | **DONE** | [P02.02](tasks/P02.02.md) |
| `P02.03` | 2 | چرخه فایل و سرو امن media | P02.02 | DONE | DONE | DONE | DONE | **DONE** | [P02.03](tasks/P02.03.md) |
| `P02.04` | 2 | DOM امن در templateهای باقی‌مانده | P00.02 | DONE | DONE | DONE | DONE | **DONE** | [P02.04](tasks/P02.04.md) |
| `P02.05` | 2 | قرارداد JSON مستقل فرم | P01.05 | DONE | DONE | DONE | DONE | **DONE** | [P02.05](tasks/P02.05.md) |
| `P02.06` | 2 | auth امن MVP بدون تأیید ایمیل | P00.03 | DONE | DONE | DONE | DONE | **DONE** | [P02.06](tasks/P02.06.md) |
| `P02.07` | 2 | طراحی migration اسرار و gate سازگاری | P00.01 | DONE | DONE | DONE | DONE | **DONE** | [P02.07](tasks/P02.07.md) |
| `P02.08` | 2 | پیاده‌سازی فرمت رمزنگاری مستقل | P02.07 | DONE | DONE | DONE | DONE | **DONE** | [P02.08](tasks/P02.08.md) |
| `P02.09` | 2 | backfill batch و کنترل‌شده اسرار | P02.08 | DONE | DONE | DONE | DONE | **DONE** | [P02.09](tasks/P02.09.md) |
| `P02.10` | 2 | cutover رمزنگاری فعال و حفاظت backup | P02.09 | DONE | DONE | DONE | DONE | **DONE** | [P02.10](tasks/P02.10.md) |
| `P03.01` | 3 | فهرست دقیق وابستگی‌های B2C | P00.01, P00.02 | DONE | DONE | DONE | DONE | **DONE** | [P03.01](tasks/P03.01.md) |
| `P03.02` | 3 | قطع route و callerهای عمومی B2C | P03.01 | DONE | DONE | DONE | DONE | **DONE** | [P03.02](tasks/P03.02.md) |
| `P03.03` | 3 | حذف دارایی و متریک صرفاً B2C | P03.02 | DONE | DONE | DONE | DONE | **DONE** | [P03.03](tasks/P03.03.md) |
| `P03.04` | 3 | ProductForm مشترک و checkbox صحیح | P02.05 | DONE | DONE | DONE | DONE | **DONE** | [P03.04](tasks/P03.04.md) |
| `P03.05` | 3 | اصلاح فقط counterهای باقی‌مانده فعال | P03.03 | DONE | DONE | DONE | DONE | **DONE** | [P03.05](tasks/P03.05.md) |
| `P03.06` | 3 | invariant تصویر اصلی و عملیات اتمیک | P02.03 | DONE | DONE | DONE | DONE | **DONE** | [P03.06](tasks/P03.06.md) |
| `P03.07` | 3 | سرویس تغییر درخت و دفاع traversal | P01.05 | DONE | DONE | DONE | DONE | **DONE** | [P03.07](tasks/P03.07.md) |
| `P03.08` | 3 | gate صحت محصول و حذف B2C | P03.03, P03.04, P03.05, P03.06, P03.07 | DONE | DONE | DONE | DONE | **DONE** | [P03.08](tasks/P03.08.md) |
| `P04.01` | 4 | جداسازی plan و execute و dry-run واقعی | P01.03, P00.03 | DONE | DONE | DONE | DONE | **DONE** | [P04.01](tasks/P04.01.md) |
| `P04.02` | 4 | تعریف قرارداد intent و state machine ارسال | P04.01, P01.01 | DONE | DONE | DONE | DONE | **DONE** | [P04.02](tasks/P04.02.md) |
| `P04.03` | 4 | ساختار افزایشی intent و lease | P04.02 | DONE | DONE | DONE | DONE | **DONE** | [P04.03](tasks/P04.03.md) |
| `P04.04` | 4 | claim اتمیک و مالکیت lease | P04.03 | DONE | DONE | DONE | DONE | **DONE** | [P04.04](tasks/P04.04.md) |
| `P04.05` | 4 | SenderPolicy واحد و reservation سهمیه حساب | P04.04, P01.02 | DONE | DONE | DONE | DONE | **DONE** | [P04.05](tasks/P04.05.md) |
| `P04.06` | 4 | executor و adapter ارسال فعال | P04.04, P04.05 | DONE | DONE | DONE | DONE | **DONE** | [P04.06](tasks/P04.06.md) |
| `P04.07` | 4 | recovery نتیجه نامطمئن و crash | P04.06 | DONE | DONE | DONE | DONE | **DONE** | [P04.07](tasks/P04.07.md) |
| `P04.08` | 4 | integration رقابتی و gate ارسال | P04.07, P04.01 | PLANNED | PLANNED | PLANNED | PLANNED | **PLANNED** | `P04.08` |
| `P05.01` | 5 | قرارداد مشترک gateway، اعتبار و صف | P01.02, P00.03 | PLANNED | PLANNED | PLANNED | PLANNED | **PLANNED** | `P05.01` |
| `P05.02` | 5 | ModelRef و snapshot اولویت Ops | P05.01 | PLANNED | PLANNED | PLANNED | PLANNED | **PLANNED** | `P05.02` |
| `P05.03` | 5 | gateway و transport injectable | P05.02 | PLANNED | PLANNED | PLANNED | PLANNED | **PLANNED** | `P05.03` |
| `P05.04` | 5 | quota مشترک provider بین processها | P05.03 | PLANNED | PLANNED | PLANNED | PLANNED | **PLANNED** | `P05.04` |
| `P05.05` | 5 | fallback دقیقاً طبق Ops | P05.04 | PLANNED | PLANNED | PLANNED | PLANNED | **PLANNED** | `P05.05` |
| `P05.06` | 5 | بازیابی embedding بعد از reset | P05.05 | PLANNED | PLANNED | PLANNED | PLANNED | **PLANNED** | `P05.06` |
| `P05.07` | 5 | adapter پنل فعال به gateway | P05.05, P02.01 | PLANNED | PLANNED | PLANNED | PLANNED | **PLANNED** | `P05.07` |
| `P05.08` | 5 | adapter موتور فعال و ثبت محدوده legacy | P05.05 | PLANNED | PLANNED | PLANNED | PLANNED | **PLANNED** | `P05.08` |
| `P05.09` | 5 | gate قرارداد قبل از اتصال مالی | P05.06, P05.07, P05.08 | PLANNED | PLANNED | PLANNED | PLANNED | **PLANNED** | `P05.09` |
| `P06.01` | 6 | operation id و retry طبقه‌بندی‌شده | P05.01, P00.03 | PLANNED | PLANNED | PLANNED | PLANNED | **PLANNED** | `P06.01` |
| `P06.02` | 6 | cost event و allocation اتمیک | P06.01, P05.02 | PLANNED | PLANNED | PLANNED | PLANNED | **PLANNED** | `P06.02` |
| `P06.03` | 6 | reservation اتمیک اعتبار | P06.02 | PLANNED | PLANNED | PLANNED | PLANNED | **PLANNED** | `P06.03` |
| `P06.04` | 6 | صف انتظار اعتبار و ازسرگیری همان کار | P06.03 | PLANNED | PLANNED | PLANNED | PLANNED | **PLANNED** | `P06.04` |
| `P06.05` | 6 | settlement مبتنی بر mapping، نه MAX(id) | P06.02, P06.03 | PLANNED | PLANNED | PLANNED | PLANNED | **PLANNED** | `P06.05` |
| `P06.06` | 6 | reconciliation و برنامه cutover داده مالی | P06.04, P06.05 | PLANNED | PLANNED | PLANNED | PLANNED | **PLANNED** | `P06.06` |
| `P06.07` | 6 | اتصال gateway و endpoint به مالی واقعی | P05.09, P06.06 | PLANNED | PLANNED | PLANNED | PLANNED | **PLANNED** | `P06.07` |
| `P06.08` | 6 | failure injection و gate مالی | P06.07 | PLANNED | PLANNED | PLANNED | PLANNED | **PLANNED** | `P06.08` |
| `P07.01` | 7 | ثبت تعویق R25 و guard تغییر مشترک | P00.01 | PLANNED | PLANNED | PLANNED | PLANNED | **PLANNED** | `P07.01` |
| `P07.02` | 7 | جداسازی migration موتور از request | P07.01, P06.08 | PLANNED | PLANNED | PLANNED | PLANNED | **PLANNED** | `P07.02` |
| `P07.03` | 7 | زیرساخت job پایدار taxonomy | P03.07, P06.04 | PLANNED | PLANNED | PLANNED | PLANNED | **PLANNED** | `P07.03` |
| `P07.04` | 7 | جایگزینی daemon taxonomy با job versioned | P07.03 | PLANNED | PLANNED | PLANNED | PLANNED | **PLANNED** | `P07.04` |
| `P07.05` | 7 | gate schema/job و runbook عملیات | P07.02, P07.04 | PLANNED | PLANNED | PLANNED | PLANNED | **PLANNED** | `P07.05` |
| `P08.01` | 8 | خطای نوع‌دار و log امن مسیرهای فعال | P02.03, P04.08, P06.08 | PLANNED | PLANNED | PLANNED | PLANNED | **PLANNED** | `P08.01` |
| `P08.02` | 8 | قرنطینه poison payload در sync فعال | P08.01 | PLANNED | PLANNED | PLANNED | PLANNED | **PLANNED** | `P08.02` |
| `P08.03` | 8 | boundary test و baseline بازآرایی | P07.05, P08.01 | PLANNED | PLANNED | PLANNED | PLANNED | **PLANNED** | `P08.03` |
| `P08.04` | 8 | استخراج helperهای فرم برابر | P08.03, P03.04 | PLANNED | PLANNED | PLANNED | PLANNED | **PLANNED** | `P08.04` |
| `P08.05` | 8 | استخراج normalize_link فعال | P08.03 | PLANNED | PLANNED | PLANNED | PLANNED | **PLANNED** | `P08.05` |
| `P08.06` | 8 | یکسان‌سازی فیلترهای پست در دامنه فعال | P08.03 | PLANNED | PLANNED | PLANNED | PLANNED | **PLANNED** | `P08.06` |
| `P08.07` | 8 | parser مشترک پروکسی بدون تغییر عملیات | P08.03, P00.07 | PLANNED | PLANNED | PLANNED | PLANNED | **PLANNED** | `P08.07` |
| `P08.08` | 8 | استخراج validate/plan از worker اینستاگرام | P08.03, P04.08 | PLANNED | PLANNED | PLANNED | PLANNED | **PLANNED** | `P08.08` |
| `P08.09` | 8 | استخراج adapter جمع‌آوری worker اینستاگرام | P08.08 | PLANNED | PLANNED | PLANNED | PLANNED | **PLANNED** | `P08.09` |
| `P08.10` | 8 | استخراج ارزیابی و فیلتر worker اینستاگرام | P08.09 | PLANNED | PLANNED | PLANNED | PLANNED | **PLANNED** | `P08.10` |
| `P08.11` | 8 | استخراج persistence worker اینستاگرام | P08.10 | PLANNED | PLANNED | PLANNED | PLANNED | **PLANNED** | `P08.11` |
| `P08.12` | 8 | استخراج گزارش و نهایی‌سازی orchestration اینستاگرام | P08.11 | PLANNED | PLANNED | PLANNED | PLANNED | **PLANNED** | `P08.12` |
| `P08.13` | 8 | استخراج family inspection از scraper فعال | P08.03, P04.08 | PLANNED | PLANNED | PLANNED | PLANNED | **PLANNED** | `P08.13` |
| `P08.14` | 8 | استخراج orchestration هر target scraper | P08.13 | PLANNED | PLANNED | PLANNED | PLANNED | **PLANNED** | `P08.14` |
| `P08.15` | 8 | تفکیک viewهای auth اکانت | P08.03, P02.06, P01.08 | PLANNED | PLANNED | PLANNED | PLANNED | **PLANNED** | `P08.15` |
| `P08.16` | 8 | تفکیک export فرصت در discovery | P08.03, P01.08 | PLANNED | PLANNED | PLANNED | PLANNED | **PLANNED** | `P08.16` |
| `P08.17` | 8 | نازک‌کردن viewهای محصول باقی‌مانده | P08.03, P03.08 | PLANNED | PLANNED | PLANNED | PLANNED | **PLANNED** | `P08.17` |
| `P08.18` | 8 | استخراج مدیریت provider از billing manage | P08.03, P05.09, P06.08 | PLANNED | PLANNED | PLANNED | PLANNED | **PLANNED** | `P08.18` |
| `P08.19` | 8 | component تنظیم مدل/اولویت در قالب Ops | P08.18, P02.04 | PLANNED | PLANNED | PLANNED | PLANNED | **PLANNED** | `P08.19` |
| `P08.20` | 8 | component ویژگی دسته در فرم محصول | P08.17, P02.05 | PLANNED | PLANNED | PLANNED | PLANNED | **PLANNED** | `P08.20` |
| `P08.21` | 8 | manifest کد مرده و حذف مجاز | P08.03, P03.03 | PLANNED | PLANNED | PLANNED | PLANNED | **PLANNED** | `P08.21` |
| `P08.22` | 8 | حذف یک گروه تأییدشده کد مرده | P08.21 | PLANNED | PLANNED | PLANNED | PLANNED | **PLANNED** | `P08.22` |
| `P08.23` | 8 | گیت نهایی تمیزی و فهرست کارهای باقی‌مانده | P08.01, P08.02, P08.03, P08.04, P08.05, P08.06, P08.07, P08.08, P08.09, P08.10, P08.11, P08.12, P08.13, P08.14, P08.15, P08.16, P08.17, P08.18, P08.19, P08.20, P08.21 | PLANNED | PLANNED | PLANNED | PLANNED | **PLANNED** | `P08.23` |
| `P09.01` | 9 | lock runtime و وابستگی فعال | P00.03, P08.23 | PLANNED | PLANNED | PLANNED | PLANNED | **PLANNED** | `P09.01` |
| `P09.02` | 9 | رفع N+1 دسته بدون تغییر معنا | P03.07, P01.05 | PLANNED | PLANNED | PLANNED | PLANNED | **PLANNED** | `P09.02` |
| `P09.03` | 9 | حذف تنظیم hardcoded tenant از مسیر فعال | P01.08, P04.08 | PLANNED | PLANNED | PLANNED | PLANNED | **PLANNED** | `P09.03` |
| `P09.04` | 9 | image immutable و profile تولید | P09.01, P07.02 | PLANNED | PLANNED | PLANNED | PLANNED | **PLANNED** | `P09.04` |
| `P09.05` | 9 | supervision و health مستقل worker/sync | P09.04, P07.05 | PLANNED | PLANNED | PLANNED | PLANNED | **PLANNED** | `P09.05` |
| `P09.06` | 9 | gate build، کارایی و استقرار staging | P09.02, P09.03, P09.05 | PLANNED | PLANNED | PLANNED | PLANNED | **PLANNED** | `P09.06` |
| `P10.01` | 10 | ماتریس آمادگی انتشار و blockerها | P01.08, P02.10, P03.08, P04.08, P06.08, P07.05, P09.06؛ فقط بازبینی وضعیت، نه عبور از blocker | PLANNED | PLANNED | PLANNED | PLANNED | **PLANNED** | `P10.01` |
| `P10.02` | 10 | staging سراسری با داده مصنوعی | P10.01 | PLANNED | PLANNED | PLANNED | PLANNED | **PLANNED** | `P10.02` |
| `P10.03` | 10 | اجرای migration مجاز و preflight داده | P10.02 | PLANNED | PLANNED | PLANNED | PLANNED | **PLANNED** | `P10.03` |
| `P10.04` | 10 | shadow تصمیم و canary ارسال مجاز | P10.03, P04.08 | PLANNED | PLANNED | PLANNED | PLANNED | **PLANNED** | `P10.04` |
| `P10.05` | 10 | cutover مالی و canary اعتبار | P10.03, P06.08, P05.09 | PLANNED | PLANNED | PLANNED | PLANNED | **PLANNED** | `P10.05` |
| `P10.06` | 10 | افزایش تدریجی و پایش چرخه واقعی | P10.04, P10.05 | PLANNED | PLANNED | PLANNED | PLANNED | **PLANNED** | `P10.06` |
| `P10.07` | 10 | contract مجاز و بستن یافته‌ها | P10.06 | PLANNED | PLANNED | PLANNED | PLANNED | **PLANNED** | `P10.07` |
