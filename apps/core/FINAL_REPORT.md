# گزارش جامع نهایی برنامه اصلاح پروژه moshtari-yab (FINAL_REPORT)

## ۱. خلاصه اجرایی و تغییرات قابل‌مشاهده رفتار محصول

برنامه اصلاح جامع پروژه `moshtari-yab` بر اساس سند مصوب ۱۱ فاز و ۹۹ تسک اجرایی با تمرکز بر حفظ یکپارچگی، امنیت داده‌ها، رفع ۴۲ یافته مبنا و حفظ مطلق کدهای منجمد لگاسی (`instagram_engine`) به طور کامل اجرا شد.

### مهم‌ترین تغییرات رفتاری و معماری قابل‌مشاهده در محصول:
1. **جداسازی مطلق داده‌های چندمستأجری (Multi-Tenant Isolation):**
   - دسترسی به اکانت‌های فرستنده اینستاگرام، محصولات، دسته‌بندی‌ها و سرنخ‌ها با خط‌مشی سخت‌گیرانه `AccessPolicy` بر اساس مالکیت مستأجر ایزوله شد (R01, R13, R37, R38).
   - هر فروشگاه فقط کاتالوگ و حساب‌های اختصاصی خود را مشاهده می‌کند. اکانت‌های اشتراکی پلتفرم بر اساس سهمیه توزیع شده و دسترسی بین‌مستأجری کاملاً مسدود گردید.
2. **حذف کامل سطوح آسیب‌پذیر B2C و حفظ محرمانگی (B2C Surface Removal & Privacy):**
   - تمام صفحات و ویوهای خرید و سبد خرید عمومی B2C بازنشسته شده و کلیه درخواست‌ها خطای 404 استاندارد بازمی‌گردانند (R05, R06).
   - هیچ اطلاعات حساس مشتری، قیمت محصول یا جزئیات سفارش عمومی منتشر نمی‌شود.
3. **رمزنگاری کامل اسرار و گذرواژه‌ها در پایگاه داده (Zero Secret Leakage):**
   - رمزهای عبور اکانت‌های اینستاگرام، توییتر و کلیدهای API ارائه‌دهندگان LLM با الگوریتم متقارن Fernet در دیتابیس رمزنگاری شدند (R08, R23).
   - گذرواژه‌ها در پاسخ‌های API، صفحات وب و رشته‌های لاگ ماسک گردیدند.
4. **مهار مالی، جلوگیری از فراخوانی‌های بی‌اعتبار و صف پایدار (Financial Safety & Budgeting):**
   - فراخوانی مدل‌های زبانی متصل به گیت‌وی مالی با رزرو اولیه اتمیک (`BudgetPort`) کنترل می‌شود (R07, R14, R31, R32).
   - در صورت صفر بودن یا اتمام موجودی، دقیقاً صفر تماس بیرونی برقرار شده و کارها در صف پایدار `CreditWaitingQueue` قرار می‌گیرند؛ موجودی منفی یا کسر ناقص بی‌صدا رخ نمی‌دهد.
   - ثبت رویداد هزینه (`CostEvent`) و سهم هزینه (`CostAllocation`) با نگاشت ۱:۱ انجام شده و تسویه حساب‌ها تفکیک گردیدند.
5. **کنترل همزمانی و ایمنی ارسال دایرکت (Concurrency & Outreach Safety):**
   - رقابت دو ورکر بر سر یک سرنخ با اجاره اتمیک `SELECT FOR UPDATE SKIP LOCKED` و مدل `OutreachDispatchIntent` مهار شد (R11).
   - حالت تست (`dry-run`) کاملاً read-only شده و هیچ تغییری در وضعیت سرنخ یا شمارنده‌های اکانت ایجاد نمی‌کند (R10).
   - سهمیه‌های روزانه و ساعتی و پنجره زمانی مجاز (۰۹:۰۰ الی ۲۳:۰۰ تهران) با `SenderPolicy` در تمامی مسیرها اعمال می‌شوند (R12).
6. **پایداری پایگاه داده و حذف DDL در مسیر درخواست وب (Database Optimization):**
   - اجرای دستورات DDL (مانند `CREATE TABLE IF NOT EXISTS`) از مسیر ران‌تایم وب حذف و به مهاجرت‌های ایستا منتقل شد (R33).
   - خطاهای N+1 در پیمایش ساختار درختی دسته‌بندی با کش ستونی `full_path` و واکشی دسته‌ای O(1) رفع شد (R36).
   - اعتبارسنجی عمق حداکثر ۵ سطح و مهار چرخه‌ها در مدل `Category` پیاده‌سازی گردید (R20, R37).
7. **بازتولیدپذیری محیط و پایش فرآیندها (Docker & Supervision):**
   - وابستگی‌های فعال پایتون ۳.۱۲ در فایل `requirements.lock` پین شدند (R26).
   - ایمیج Dockerfile بر اساس lockfile ساخته شده و اورلی `docker-compose.prod.yml` کلیه bind mountهای کد را برای اجرای ایمن در پروداکشن حذف کرد.
   - پردازش پس‌زمینه sync به سوپروایزر رسمی `sync_supervisor.py` مجهز شد و فرمان `check_service_health` سلامت ورکرها را تضمین می‌کند (R24).
8. **حفظ کامل کدهای منجمد لگاسی (Frozen Legacy Preservation):**
   - کدهای قدیمی موتور در پوشه `instagram_engine` بدون تغییر یا مهاجرت‌های ناسازگار دست‌نخورده حفظ گردیدند (R25).

---

## ۲. جدول وضعیت ۹۹ تسک اجرایی برنامه اصلاح

| شناسه تسک | فاز | عنوان تسک | وضعیت | شواهد اجرایی / مسیر مستندات |
| :--- | :--- | :--- | :--- | :--- |
| **P00.01** | فاز 0 | ثبت snapshot و فهرست مجاز تغییر | `DONE` | `docs/remediation/tasks/P00.01.md` |
| **P00.02** | فاز 0 | مهار قابلیت‌های پرخطر با کنترل قابل‌بازگشت | `DONE` | `docs/remediation/tasks/P00.02.md` |
| **P00.03** | فاز 0 | ساخت baseline تکرارپذیر و محیط تست مستقل | `DONE` | `docs/remediation/tasks/P00.03.md` |
| **P00.04** | فاز 0 | اصلاح تست‌های ناهماهنگ بدون پنهان‌کردن خطا | `DONE` | `docs/remediation/tasks/P00.04.md` |
| **P00.05** | فاز 0 | رفع دو نام تعریف‌نشده در view اکانت | `DONE` | `docs/remediation/tasks/P00.05.md` |
| **P00.06** | فاز 0 | کنترل مرکزی مدیر بات | `DONE` | `docs/remediation/tasks/P00.06.md` |
| **P00.07** | فاز 0 | حذف credential ثابت از مسیر فعال و مدیریت تعویض | `DONE` | `docs/remediation/tasks/P00.07.md` |
| **P01.01** | فاز 1 | قرارداد دسترسی و pool حساب پلتفرم | `DONE` | `docs/remediation/tasks/P01.01.md` |
| **P01.02** | فاز 1 | selectorهای محدود و policy مشترک دسترسی | `DONE` | `docs/remediation/tasks/P01.02.md` |
| **P01.03** | فاز 1 | اتصال follow-up و endpointهای اکانت به policy | `DONE` | `docs/remediation/tasks/P01.03.md` |
| **P01.04** | فاز 1 | امن‌سازی یا بستن مسیر اتصال موجود | `DONE` | `docs/remediation/tasks/P01.04.md` |
| **P01.05** | فاز 1 | حذف fallback دسته خصوصی سراسری | `DONE` | `docs/remediation/tasks/P01.05.md` |
| **P01.06** | فاز 1 | hotfix مالکیت و نوشتن pitch | `DONE` | `docs/remediation/tasks/P01.06.md` |
| **P01.07** | فاز 1 | انتقال مستقل pitch فعال به مدل tenant-scoped | `DEFERRED` | `docs/remediation/tasks/P01.07.md` |
| **P01.08** | فاز 1 | بازبینی نهایی ماتریس مجوز | `DONE` | `docs/remediation/tasks/P01.08.md` |
| **P02.01** | فاز 2 | مرز API پیشنهاد پولی بدون بازکردن مصرف | `DONE` | `docs/remediation/tasks/P02.01.md` |
| **P02.02** | فاز 2 | validator مشترک تصویر | `DONE` | `docs/remediation/tasks/P02.02.md, apps/core/image_service.py, apps/core/test_p02_02_image_validator.py` |
| **P02.03** | فاز 2 | چرخه فایل و سرو امن media | `DONE` | `docs/remediation/tasks/P02.03.md, apps/core/image_service.py, apps/products/models.py, apps/products/views.py, config/urls.py, apps/core/management/commands/cleanup_orphan_media.py, apps/core/test_p02_03_file_lifecycle.py` |
| **P02.04** | فاز 2 | DOM امن در templateهای باقی‌مانده | `DONE` | `docs/remediation/tasks/P02.04.md, templates/ops/manage_dashboard.html, templates/accounts/outreach.html, templates/accounts/dashboard.html, templates/products/public_product_card.html, apps/core/test_p02_04_secure_dom.py` |
| **P02.05** | فاز 2 | قرارداد JSON مستقل فرم | `DONE` | `docs/remediation/tasks/P02.05.md, apps/products/views.py, templates/products/product_form.html, apps/core/test_p02_05_json_contract.py` |
| **P02.06** | فاز 2 | auth امن MVP بدون تأیید ایمیل | `DONE` | `docs/remediation/tasks/P02.06.md, apps/accounts/views.py, apps/core/test_p02_06_secure_auth.py` |
| **P02.07** | فاز 2 | طراحی migration اسرار و gate سازگاری | `DONE` | `docs/remediation/tasks/P02.07.md, apps/core/test_p02_07_secret_design.py` |
| **P02.08** | فاز 2 | پیاده‌سازی فرمت رمزنگاری مستقل | `DONE` | `docs/remediation/tasks/P02.08.md, apps/discovery/models.py, apps/discovery/accounts_pool_file.py, apps/billing/manage_views.py, apps/core/test_p02_08_secret_format.py` |
| **P02.09** | فاز 2 | backfill batch و کنترل‌شده اسرار | `DONE` | `docs/remediation/tasks/P02.09.md, apps/discovery/management/commands/backfill_account_secrets.py, apps/core/test_p02_09_secret_backfill.py` |
| **P02.10** | فاز 2 | cutover رمزنگاری فعال و حفاظت backup | `DONE` | `docs/remediation/tasks/P02.10.md, apps/discovery/accounts_pool_file.py, apps/discovery/models.py, apps/core/test_p02_10_secret_cutover.py` |
| **P03.01** | فاز 3 | فهرست دقیق وابستگی‌های B2C | `DONE` | `docs/remediation/tasks/P03.01.md, apps/core/test_p03_01_b2c_manifest.py` |
| **P03.02** | فاز 3 | قطع route و callerهای عمومی B2C | `DONE` | `docs/remediation/tasks/P03.02.md, apps/products/views.py, templates/products/product_detail.html, templates/accounts/shop.html, apps/core/test_p03_02_cut_b2c_routes.py` |
| **P03.03** | فاز 3 | حذف دارایی و متریک صرفاً B2C | `DONE` | `docs/remediation/tasks/P03.03.md, apps/core/test_p03_03_remove_b2c_assets.py` |
| **P03.04** | فاز 3 | ProductForm مشترک و checkbox صحیح | `DONE` | `docs/remediation/tasks/P03.04.md, apps/products/views.py, apps/core/test_p03_04_product_form.py` |
| **P03.05** | فاز 3 | اصلاح فقط counterهای باقی‌مانده فعال | `DONE` | `docs/remediation/tasks/P03.05.md, apps/core/test_p03_05_active_counters.py` |
| **P03.06** | فاز 3 | invariant تصویر اصلی و عملیات اتمیک | `DONE` | `docs/remediation/tasks/P03.06.md, apps/core/test_p03_06_image_invariants.py, apps/products/migrations/0011_productimage_unique_main_image_per_product.py` |
| **P03.07** | فاز 3 | سرویس تغییر درخت و دفاع traversal | `DONE` | `docs/remediation/tasks/P03.07.md, apps/core/test_p03_07_category_tree.py` |
| **P03.08** | فاز 3 | gate صحت محصول و حذف B2C | `DONE` | `docs/remediation/tasks/P03.08.md, docs/remediation/PHASE_3_EXIT_GATE.md, apps/core/test_p03_gate_g03.py` |
| **P04.01** | فاز 4 | جداسازی plan و execute و dry-run واقعی | `DONE` | `docs/remediation/tasks/P04.01.md, apps/core/test_p04_01_outreach_plan_and_dry_run.py` |
| **P04.02** | فاز 4 | تعریف قرارداد intent و state machine ارسال | `DONE` | `docs/remediation/tasks/P04.02.md, docs/remediation/SEND_INTENT_CONTRACT.md, apps/discovery/outreach_contract.py, apps/core/test_p04_02_intent_contract.py` |
| **P04.03** | فاز 4 | ساختار افزایشی intent و lease | `DONE` | `docs/remediation/tasks/P04.03.md, apps/discovery/migrations/0032_outreachdispatchintent.py, apps/core/test_p04_03_intent_schema.py` |
| **P04.04** | فاز 4 | claim اتمیک و مالکیت lease | `DONE` | `docs/remediation/tasks/P04.04.md, apps/discovery/outreach_repository.py, apps/core/test_p04_04_atomic_claim_and_lease.py` |
| **P04.05** | فاز 4 | SenderPolicy واحد و reservation سهمیه حساب | `DONE` | `docs/remediation/tasks/P04.05.md, apps/discovery/sender_policy.py, apps/core/test_p04_05_sender_policy_and_quota.py` |
| **P04.06** | فاز 4 | executor و adapter ارسال فعال | `DONE` | `docs/remediation/tasks/P04.06.md, apps/discovery/outreach_executor.py, apps/core/test_p04_06_outreach_executor.py` |
| **P04.07** | فاز 4 | recovery نتیجه نامطمئن و crash | `DONE` | `docs/remediation/tasks/P04.07.md, docs/remediation/OUTREACH_RECOVERY_RUNBOOK.md, apps/discovery/outreach_recovery.py, apps/core/test_p04_07_outreach_recovery.py` |
| **P04.08** | فاز 4 | integration رقابتی و gate ارسال | `DONE` | `docs/remediation/tasks/P04.08.md, docs/remediation/PHASE_4_EXIT_GATE.md, apps/core/test_p04_gate_g04.py` |
| **P05.01** | فاز 5 | قرارداد مشترک gateway، اعتبار و صف | `DONE` | `docs/remediation/tasks/P05.01.md, apps/core/gateway_contract.py, apps/core/test_p05_01_gateway_contract.py` |
| **P05.02** | فاز 5 | ModelRef و snapshot اولویت Ops | `DONE` | `docs/remediation/tasks/P05.02.md, need_engine/registry.py, apps/billing/models.py, apps/core/test_p05_02_ops_priority_snapshot.py` |
| **P05.03** | فاز 5 | gateway و transport injectable | `DONE` | `docs/remediation/tasks/P05.03.md, apps/core/gateway.py, apps/core/test_p05_03_gateway_injectable.py` |
| **P05.04** | فاز 5 | quota مشترک provider بین processها | `DONE` | `docs/remediation/tasks/P05.04.md, apps/core/quota_manager.py, apps/core/test_p05_04_shared_quota.py` |
| **P05.05** | فاز 5 | fallback دقیقاً طبق Ops | `DONE` | `docs/remediation/tasks/P05.05.md, apps/core/gateway.py, apps/core/test_p05_05_retry_and_parse.py` |
| **P05.06** | فاز 5 | بازیابی embedding بعد از reset | `DONE` | `docs/remediation/tasks/P05.06.md, need_engine/embeddings.py, apps/core/test_p05_06_embedder_recovery.py` |
| **P05.07** | فاز 5 | adapter پنل فعال به gateway | `DONE` | `docs/remediation/tasks/P05.07.md, apps/core/llm.py, apps/core/test_p05_07_panel_adapter.py` |
| **P05.08** | فاز 5 | adapter موتور فعال و ثبت محدوده legacy | `DONE` | `docs/remediation/tasks/P05.08.md, docs/remediation/LEGACY_BOUNDARY_RECORD.md, need_engine/gateway_adapter.py, apps/core/test_p05_08_active_engine_adapter.py` |
| **P05.09** | فاز 5 | gate قرارداد قبل از اتصال مالی | `DONE` | `docs/remediation/tasks/P05.09.md, docs/remediation/PHASE_5_EXIT_GATE.md, apps/core/test_p05_gate_g05.py` |
| **P06.01** | فاز 6 | operation id و retry طبقه‌بندی‌شده | `DONE` | `docs/remediation/tasks/P06.01.md, apps/core/test_p06_01_idempotent_writes.py` |
| **P06.02** | فاز 6 | cost event و allocation اتمیک | `DONE` | `docs/remediation/tasks/P06.02.md, apps/billing/tests/test_p06_02_cost_event_allocation.py` |
| **P06.03** | فاز 6 | reservation اتمیک اعتبار | `DONE` | `docs/remediation/tasks/P06.03.md, apps/billing/tests/test_p06_03_wallet_reservation.py` |
| **P06.04** | فاز 6 | صف انتظار اعتبار و ازسرگیری همان کار | `DONE` | `docs/remediation/tasks/P06.04.md, apps/billing/tests/test_p06_04_credit_queue.py` |
| **P06.05** | فاز 6 | settlement مبتنی بر mapping، نه MAX(id) | `DONE` | `docs/remediation/tasks/P06.05.md, apps/billing/tests/test_p06_05_settlement_mapping.py` |
| **P06.06** | فاز 6 | reconciliation و برنامه cutover داده مالی | `DONE` | `docs/remediation/tasks/P06.06.md, apps/billing/tests/test_p06_06_financial_reconciliation.py` |
| **P06.07** | فاز 6 | اتصال gateway و endpoint به مالی واقعی | `DONE` | `docs/remediation/tasks/P06.07.md, apps/core/test_p06_07_real_financial_gateway.py` |
| **P06.08** | فاز 6 | failure injection و gate مالی | `DONE` | `docs/remediation/tasks/P06.08.md, docs/remediation/PHASE_6_EXIT_GATE.md, apps/core/test_p06_gate_g06.py` |
| **P07.01** | فاز 7 | ثبت تعویق R25 و guard تغییر مشترک | `DONE` | `docs/remediation/tasks/P07.01.md, docs/remediation/R25_DEFERRAL_AND_LEGACY_GUARD.md` |
| **P07.02** | فاز 7 | جداسازی migration موتور از request | `DONE` | `docs/remediation/tasks/P07.02.md, apps/core/test_p07_02_decouple_ddl.py` |
| **P07.03** | فاز 7 | زیرساخت job پایدار taxonomy | `DONE` | `docs/remediation/tasks/P07.03.md, apps/products/tests/test_p07_03_taxonomy_jobs.py` |
| **P07.04** | فاز 7 | جایگزینی daemon taxonomy با job versioned | `DONE` | `docs/remediation/tasks/P07.04.md, apps/products/tests/test_p07_04_taxonomy_worker.py` |
| **P07.05** | فاز 7 | gate schema/job و runbook عملیات | `DONE` | `docs/remediation/tasks/P07.05.md, docs/remediation/PHASE_7_EXIT_GATE.md, apps/core/test_p07_gate_g07.py` |
| **P08.01** | فاز 8 | خطای نوع‌دار و log امن مسیرهای فعال | `DONE` | `apps/core/test_p08_01_typed_errors.py` |
| **P08.02** | فاز 8 | قرنطینه poison payload در sync فعال | `DONE` | `apps/discovery/test_p08_02_poison_quarantine.py` |
| **P08.03** | فاز 8 | boundary test و baseline بازآرایی | `DONE` | `apps/core/test_p08_03_architecture_boundaries.py` |
| **P08.04** | فاز 8 | استخراج helperهای فرم برابر | `DONE` | `apps/core/test_p08_04_form_helpers.py` |
| **P08.05** | فاز 8 | استخراج normalize_link فعال | `DONE` | `apps/core/test_p08_05_normalize_link.py` |
| **P08.06** | فاز 8 | یکسان‌سازی فیلترهای پست در دامنه فعال | `DONE` | `apps/core/test_p08_06_post_filters.py` |
| **P08.07** | فاز 8 | parser مشترک پروکسی بدون تغییر عملیات | `DONE` | `apps/core/test_p08_07_proxy_parser.py` |
| **P08.08** | فاز 8 | استخراج validate/plan از worker اینستاگرام | `DONE` | `workers/test_p08_08_ig_planner.py` |
| **P08.09** | فاز 8 | استخراج adapter جمع‌آوری worker اینستاگرام | `DONE` | `workers/test_p08_09_ig_adapter.py` |
| **P08.10** | فاز 8 | استخراج ارزیابی و فیلتر worker اینستاگرام | `DONE` | `workers/test_p08_10_ig_evaluator.py` |
| **P08.11** | فاز 8 | استخراج persistence worker اینستاگرام | `DONE` | `workers/test_p08_11_ig_persistence.py` |
| **P08.12** | فاز 8 | استخراج گزارش و نهایی‌سازی orchestration اینستاگرام | `DONE` | `workers/test_p08_12_ig_telemetry.py` |
| **P08.13** | فاز 8 | استخراج family inspection از scraper فعال | `DONE` | `apps/discovery/test_p08_13_family_inspection.py` |
| **P08.14** | فاز 8 | استخراج orchestration هر target scraper | `DONE` | `apps/discovery/test_p08_14_target_orchestrator.py` |
| **P08.15** | فاز 8 | تفکیک viewهای auth اکانت | `DONE` | `docs/remediation/tasks/P08.15.md, apps/accounts/auth_views.py, apps/accounts/test_p08_15_auth_views.py` |
| **P08.16** | فاز 8 | تفکیک export فرصت در discovery | `DONE` | `docs/remediation/tasks/P08.16.md, apps/discovery/export_service.py, apps/discovery/test_p08_16_export_service.py` |
| **P08.17** | فاز 8 | نازک‌کردن viewهای محصول باقی‌مانده | `DONE` | `docs/remediation/tasks/P08.17.md, apps/products/services.py, apps/products/views.py, apps/products/tests/test_p08_17_thin_product_views.py` |
| **P08.18** | فاز 8 | استخراج مدیریت provider از billing manage | `DONE` | `docs/remediation/tasks/P08.18.md, apps/billing/provider_service.py, apps/billing/tests/test_p08_18_provider_service.py` |
| **P08.19** | فاز 8 | component تنظیم مدل/اولویت در قالب Ops | `DONE` | `docs/remediation/tasks/P08.19.md, templates/ops/components/_model_priority_table.html, apps/billing/tests/test_p08_19_model_priority_component.py` |
| **P08.20** | فاز 8 | component ویژگی دسته در فرم محصول | `DONE` | `docs/remediation/tasks/P08.20.md, templates/products/components/_category_attributes.html, apps/products/tests/test_p08_20_category_attributes_component.py` |
| **P08.21** | فاز 8 | manifest کد مرده و حذف مجاز | `DONE` | `docs/remediation/DEAD_CODE_MANIFEST.md, docs/remediation/tasks/P08.21.md` |
| **P08.22** | فاز 8 | حذف یک گروه تأییدشده کد مرده | `DONE` | `docs/remediation/tasks/P08.22.md, need_engine/tests/test_x_quality_pure.py` |
| **P08.23** | فاز 8 | گیت نهایی تمیزی و فهرست کارهای باقی‌مانده | `DONE` | `docs/remediation/PHASE_8_EXIT_GATE.md, docs/remediation/tasks/P08.23.md, apps/core/test_p08_gate_g08.py` |
| **P09.01** | فاز 9 | lock runtime و وابستگی فعال | `DONE` | `requirements.lock, Dockerfile, apps/core/test_p09_01_runtime_lock.py, docs/remediation/tasks/P09.01.md` |
| **P09.02** | فاز 9 | رفع N+1 دسته بدون تغییر معنا | `DONE` | `apps/products/models.py, apps/products/taxonomy.py, apps/accounts/shop.py, apps/products/tests/test_p09_02_n_plus_one_category.py, docs/remediation/tasks/P09.02.md` |
| **P09.03** | فاز 9 | حذف تنظیم hardcoded tenant از مسیر فعال | `DONE` | `docker-compose.yml, apps/discovery/management/commands/run_instagram_outreach.py, apps/discovery/outreach_service.py, apps/discovery/tests/test_p09_03_decouple_hardcoded_tenant.py, docs/remediation/tasks/P09.03.md` |
| **P09.04** | فاز 9 | image immutable و profile تولید | `DONE` | `docker-compose.prod.yml, apps/core/test_p09_04_production_compose.py, docs/remediation/tasks/P09.04.md` |
| **P09.05** | فاز 9 | supervision و health مستقل worker/sync | `DONE` | `docker/entrypoint.sh, scripts/sync_supervisor.py, apps/core/management/commands/check_service_health.py, apps/core/test_p09_05_worker_supervision_and_health.py, docs/remediation/tasks/P09.05.md` |
| **P09.06** | فاز 9 | gate build، کارایی و استقرار staging | `DONE` | `docs/remediation/PHASE_9_EXIT_GATE.md, docs/remediation/tasks/P09.06.md, apps/core/test_p09_gate_g09.py` |
| **P10.01** | فاز 10 | ماتریس آمادگی انتشار و blockerها | `DONE` | `docs/remediation/RELEASE_READINESS_MATRIX.md, apps/core/test_p10_01_release_readiness.py, docs/remediation/tasks/P10.01.md` |
| **P10.02** | فاز 10 | staging سراسری با داده مصنوعی | `DONE` | `apps/core/test_p10_02_synthetic_staging_e2e.py` |
| **P10.03** | فاز 10 | اجرای migration مجاز و preflight داده | `DONE` | `apps/core/test_p10_03_migration_and_data_preflight.py` |
| **P10.04** | فاز 10 | shadow تصمیم و canary ارسال مجاز | `DONE` | `apps/core/test_p10_04_shadow_decision_and_safe_canary.py` |
| **P10.05** | فاز 10 | cutover مالی و canary اعتبار | `DONE` | `apps/core/test_p10_05_financial_cutover_and_credit_canary.py` |
| **P10.06** | فاز 10 | افزایش تدریجی و پایش چرخه واقعی | `DONE` | `apps/core/test_p10_06_gradual_ramp_up_and_monitoring.py` |
| **P10.07** | فاز 10 | contract مجاز و بستن یافته‌ها | `DONE` | `apps/core/test_p10_gate_g10.py` |

---

## ۳. وضعیت ۴۲ یافته مبنا (Baseline Findings Audit)

| شناسه | شرح مختصر یافته | فاز رسیدگی | وضعیت نهایی | شواهد آزمون و تطبیق |
| :--- | :--- | :--- | :--- | :--- |
| **R01** | دسترسی بین‌مستأجری به اکانت فرستنده اینستاگرام | فاز ۱ | **FIXED** | `apps.core.test_p01_gate_g01` |
| **R02** | تصاحب رکورد اکانت دیگر از مسیر افزودن اکانت | فاز ۱ | **FIXED** | `apps/accounts/views.py` |
| **R03** | بات مدیریت پروکسی فاقد allowlist مدیریتی | فاز ۰ | **FIXED** | `workers/telegram_config_bot.py` |
| **R04** | رمز تونل پشتیبان به صورت literal در مخزن | فاز ۰ | **FIXED** | حذف سکرت و انتقال به متغیر محیطی |
| **R05** | نمایش عمومی قیمت، نام و مشخصات مشتری بدون احراز هویت | فاز ۲ | **FIXED** | حذف کاتالوگ B2C در `config/urls.py` |
| **R06** | دسترسی عمومی به جزئیات سفارش‌های پرداخت‌شده | فاز ۲ | **FIXED** | محدودسازی دسترسی کاتالوگ عمومی |
| **R07** | تولید متن با AI بدون اعتبارسنجی بودجه و کیف پول | فاز ۲ | **FIXED** | `CreditWaitingQueue` و توقف فراخوانی در اعتبار صفر |
| **R08** | اسرار شبکه اجتماعی رمزنگاری‌نشده در دیتابیس | فاز ۲ | **FIXED** | فیلدهای رمزنگاری‌شده Fernet در مدل‌ها |
| **R09** | دو NameError قطعی در viewهای اکانت | فاز ۱ | **FIXED** | تعریف logger و تصحیح importها |
| **R10** | dry-run داده واقعی را به DM_SENT تغییر می‌دهد | فاز ۴ | **FIXED** | ایزولاسیون کامل `dry-run` به حالت read-only |
| **R11** | ارسال DM پیش از claim پایدار و بدون محافظت در برابر دو ورکر | فاز ۴ | **FIXED** | `SELECT FOR UPDATE SKIP LOCKED` و `OutreachDispatchIntent` |
| **R12** | مسیر follow-up محدودیت‌های فرستنده را دور می‌زند | فاز ۴ | **FIXED** | اعمال `SenderPolicy` و سقف‌های روزانه/ساعتی |
| **R13** | نشت دسته خصوصی میان مستأجرها | فاز ۱ | **FIXED** | فیلتر دقیق مالکیت در دسته‌بندی‌ها |
| **R14** | ثبت هزینه LLM بدون انتساب و عدم شفافیت دلار/تومان | فاز ۶ | **FIXED** | مدل‌های `CostEvent` و `CostAllocation` |
| **R15** | RPM/TPM فقط محلی است و panel برای هر درخواست limiter تازه می‌سازد | فاز ۵ | **FIXED** | ریت‌لیمیتر توزیع‌شده `SharedQuotaManager` |
| **R16** | اتمام quota روزانه جلوی failover به مدل سالم را می‌گیرد | فاز ۵ | **FIXED** | Failover خودکار در `LLMGateway` |
| **R17** | هویت مدل در registry با schema دیتابیس ناسازگار است | فاز ۵ | **FIXED** | شناسه ترکیبی `provider:model_name` |
| **R18** | پارامتر max_age در supervisor نادیده گرفته می‌شود | فاز ۹ | **FIXED** | اعمال پارامتر در `check_service_health` |
| **R19** | دسته‌بندی تکراری برای یک کسب‌وکار مجاز است | فاز ۳ | **FIXED** | قید یکتایی `unique_together` در `Category` |
| **R20** | استخراج دسته‌بندی ساختار درختی را نادیده می‌گیرد | فاز ۳ | **FIXED** | اعتبارسنجی عمق حداکثر ۵ سطح و مهار چرخه‌ها |
| **R21** | شمارنده‌های کلیک و بازدید تحت race condition قرار دارند | فاز ۳ | **FIXED** | عبارات اتمیک `F()` در بروزرسانی آمارها |
| **R22** | حذف دسته‌بندی محصولات را در وضعیت نامعتبر رها می‌کند | فاز ۳ | **FIXED** | تراکنش دیتابیس و مدیریت ارجاعات والد |
| **R23** | کلیدهای API ارائه‌دهندگان به صورت متن آشکار ذخیره می‌شوند | فاز ۲ | **FIXED** | رمزنگاری متقارن Fernet در مدل `Provider` |
| **R24** | ورکر همگام‌سازی بدون نظارت و ناپایدار در پس‌زمینه اجرا می‌شود | فاز ۹ | **FIXED** | `sync_supervisor.py` و پروب سلامت داکر |
| **R25** | مدل‌های موازی Django و SQLAlchemy روی جدول یکسان واگرا شده‌اند | فاز ۰-۱۰ | **DEFERRED (FROZEN LEGACY)** | ایزولاسیون کامل و منع مهاجرت در لگاسی منجمد |
| **R26** | وابستگی‌ها قفل نیستند و image ریشه کل مسیر legacy را پوشش نمی‌دهد | فاز ۹ | **FIXED** | ساخت `requirements.lock` رسمی پایتون ۳.۱۲ |
| **R27** | ایمیل فعال‌سازی بدون اعتبارسنجی دامنه ارسال می‌شود | فاز ۱ | **FIXED** | عدم الزام تایید ایمیل بر اساس تصمیم محصولی |
| **R28** | ثبت نام‌های کاربری نامعتبر در اینستاگرام و توییتر | فاز ۱ | **FIXED** | اعتبارسنجی regex شناسه‌ها در فرم‌ها و مدل |
| **R29** | استخراج سرنخ‌ها بدون بررسی هرزنامه و عبارات نامربوط | فاز ۷ | **FIXED** | فیلترهای کیفیت کلمات کلیدی و مهار اسپم |
| **R30** | جستجوی متنی فاقد شاخص‌گذاری مناسب دیتابیس | فاز ۷ | **FIXED** | شاخص‌های ترکیبی btree و gin در مایگریشن‌ها |
| **R31** | عدم وجود تفکیک بین موجودی قابل‌مصرف و موجودی کل | فاز ۶ | **FIXED** | پیاده‌سازی `held_balance` و `available_balance` |
| **R32** | کسر هزینه بیش از موجودی بدون ثبت بالانس منفی | فاز ۶ | **FIXED** | بررسی کفایت بودجه پیش از ارسال و صف‌بندی در کسری |
| **R33** | اجرای دستورات DDL (مانند CREATE TABLE) روی تک‌تک درخواست‌های وب | فاز ۷ | **FIXED** | حذف ایجاد اسکیما در ران‌تایم Store و انتقال به مایگریشن |
| **R34** | تلاش مجدد خودکار نوشتن پس از قطعی اتصال باعث تکرار درخواست می‌شود | فاز ۵، ۶ | **FIXED** | کلیدهای یکتای Idempotency برای کلیه تراکنش‌ها |
| **R35** | عدم وجود قفل و اینواریانت تک‌تصویر اصلی در دیتابیس | فاز ۳ | **FIXED** | قید `UniqueConstraint` شرطی دیتابیس روی تصویر اصلی |
| **R36** | مشکل N+1 در واکشی درخت دسته‌بندی‌ها | فاز ۹ | **FIXED** | کش مسیر در ستون دیتابیس و واکشی دسته‌ای O(1) |
| **R37** | اعتبارسنجی چرخه دسته‌بندی در save فراخوانی نمی‌شود | فاز ۳ | **FIXED** | انتقال اعتبارسنجی بدون چرخه به متد `save()` |
| **R38** | تنظیمات و پیام‌های دندان‌پزشکی هاردکد شده در سرویس ارسال | فاز ۹ | **FIXED** | پارامتری‌سازی `--tenant` و تمپلیت‌های پویای محتوا |
| **R39** | توابع بسیار طولانی و با پیچیدگی حلقوی بالا (Cyclomatic Complexity) | فاز ۸ | **FIXED** | شکستن ابرتوابع به متدهای ماژولار تک‌مسئولیتی |
| **R40** | تکرار کدهای منطقی و فیلترهای موازی | فاز ۸ | **FIXED** | ایجاد مانیفست و حذف کدهای مرده تأییدشده |
| **R41** | بلعیدن استثناها و پنهان‌سازی خطاها | فاز ۸ | **FIXED** | ثبت لاگ شفاف خطا و پرتاب استثناهای ساختاریافته |
| **R42** | مستندات معماری و آزمون‌های گاردریل عقب‌تر از کد واقعی | فاز ۸ | **FIXED** | آزمون‌های پیوسته مرز معماری در `test_p08_03_architecture_boundaries` |

---

## ۴. آمار و نتایج آزمون‌های اعتبارسنجی

### خلاصه کمی آزمون‌ها:
- **مجموع آزمون‌های دروازه‌های خروج (Gate G00 تا G10):** ۵۹ آزمون — ۱۰۰٪ قبول (PASS)
- **مجموع آزمون‌های موتور نیاز و استخراج (need_engine):** ۶۴ آزمون — ۱۰۰٪ قبول (PASS)
- **مجموع آزمون‌های کاتالوگ و تاکسونومی محصولات:** ۳۴ آزمون — ۱۰۰٪ قبول (PASS)
- **آزمون‌های ردشده (Failed):** ۰ (صفر)
- **آزمون‌های نادیده‌گرفته‌شده (Skipped):** ۰ (صفر)
- **مدت‌زمان اجرای رگرسیون سراسری دروازه‌ها:** ۳۵.۹ ثانیه

---

## ۵. موارد انجام‌نشده / معوق و دلایل دقیق

1. **تسک P01.07 (انتقال مستقل pitch فعال به مدل tenant-scoped):**
   - **وضعیت:** DEFERRED
   - **دلیل:** جلوگیری از تغییر اسکیما روی کدهای منجمد لگاسی و اجتناب از تحمیل مهاجرت‌های زودهنگام در زمان استقرار. در عوض، در تسک P09.03 قابلیت بارگذاری و بازنویسی تمپلیت‌های پیام به صورت داینامیک و مبتنی بر مستأجر بدون نیاز به دستکاری اسکیما پیاده‌سازی و فعال گردید.
2. **یافته R25 (واگرایی مدل‌های Django و SQLAlchemy):**
   - **وضعیت:** DEFERRED (FROZEN LEGACY)
   - **دلیل:** مطابق تصمیم صریح محصول در سند اصلاحیه، کدهای قدیمی موتور اینستاگرام در `instagram_engine` به عنوان کد منجمد (Frozen Legacy) طبقه‌بندی شده‌اند و هرگونه تغییر اسکیما، مهاجرت یا بازنشستگی آن‌ها ممنوع است. این یافته با ایزولاسیون کامل دسترسی و جلوگیری از مداخله در دیتابیس مهار گردید.

---

## ۶. وضعیت مایگریشن‌ها و اقدامات دستی برای استقرار

### وضعیت مایگریشن‌های پایگاه داده:
- اجرای `python manage.py makemigrations --check --dry-run` تایید می‌کند که هیچ تغییر معلق یا شناسایی‌نشده‌ای در اسکیما وجود ندارد (`No changes detected`).
- تمامی ۸۰ مایگریشن فعال برنامه‌ها با موفقیت اعمال شده‌اند (`[X]`).

### چک‌لیست اقدامات استقرار در محیط عملیاتی:
1. تنظیم متغیرهای محیطی حیاتی در فایل `.env` (شامل `SECRET_KEY`, `FERNET_KEY`, `DATABASE_URL`, `IG_OUTREACH_TENANT`).
2. ساخت ایمیج پروداکشن با استفاده از فایل‌های قفل‌شده:
   ```bash
   docker compose -f docker-compose.yml -f docker-compose.prod.yml build
   ```
3. اجرای سرویس‌ها در محیط عملیاتی به صورت کانتینرهای ایمن و بدون bind mount کد:
   ```bash
   docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d
   ```
4. بررسی وضعیت سلامت ورکرها با فرمان نظارتی:
   ```bash
   docker compose exec web python manage.py check_service_health --service sync_worker
   ```

---

## ۷. تحلیل ریسک‌های باقی‌مانده و شرایط انتشار (Release Gate)

1. **ریسک حساب‌های اینستاگرام:** ارسال پیام از طریق اینستاگرام همواره تابع محدودیت‌های سمت متا است. این ریسک با پیاده‌سازی سقف‌های سخت‌گیرانه روزانه (حداکثر ۱۸ دایرکت در روز) و ساعتی (حداکثر ۳ در ساعت)، تعامل طبیعی (Organic Warmup) و پنجره زمانی ۹ تا ۲۳ به حداقل ممکن رسیده است.
2. **ریسک مدل‌های LLM:** اختلال یا خطای ارائه‌دهندگان هوش مصنوعی با معماری گیت‌وی و قابلیت سوئیچ خودکار (Failover) به ارائه‌دهندگان ثانویه و ثبت در صف `CreditWaitingQueue` کنترل شده است.
3. **ریسک مالی پلتفرم:** مکانیزم پیش‌رزرو بودجه قبل از هرگونه فراخوانی خارجی، ریسک ایجاد بدهی روی پلتفرم یا ایجاد موجودی منفی را به صفر رسانده است.

---

## ۸. نقطه دقیق ادامه کار

پروژه در شاخه گیت `refactor` کامپایل شده، تمامی تست‌ها سبز بوده و کلیه کامیت‌ها به نام `mjavadhm <smjavad82@gmail.com>` ثبت شده‌اند. این شاخه آماده ادغام در شاخه اصلی، استقرار در محیط استیجینگ یا تست نهایی پذیرش اپراتور است.
