# نتایج و مبنای تکرارپذیر آزمون‌ها (Baseline Test Results)

- **شناسه سند:** `BASELINE_TEST_RESULTS`
- **تسک متناظر:** `P00.03`
- **تاریخ اجرای اندازه‌گیری:** ۲۰۲۶-۱۱-۰۱
- **محیط اجرا:** Python 3.12.16 درون کانتینر داکر `moshtari-yab-web-1` (مطابق دقیق با محیط CI گیت‌هاب)
- **پایگاه داده آزمون:** PostgreSQL 16 با افزونه `pgvector`

---

## ۱. مشخصات محیط و متغیرهای ایزولاسیون

| مؤلفه | مقدار / تنظیم | هدف |
|---|---|---|
| پایتون | Python 3.12.16 | تطابق کامل با CI بدون وابستگی به نسخه پایتون ماشین میزبان |
| پایگاه داده | `postgresql://postgres:postgres@db:5432/customer_yab` | اجرای تست‌ها روی پایگاه داده ایزوله و تست اسکیمای pgvector |
| ایزولاسیون دایرکتوری تمپ | `--basetemp=/tmp/pytest -o cache_dir=/tmp/.pytest_cache` | جلوگیری از خطای Permission Denied در نوشتن کش روی دیسک کاربری کانتینر |
| عدم نشت شبکه | `NE_PANEL_MOCK_LLM=true` و ماک سرویس‌های خارجی | اطمینان از ایزوله بودن تست‌های واحد و عدم ارسال درخواست به ارائه‌دهندگان پولی |

---

## ۲. نتایج تفکیک‌شده دو Test Runner

طبق دستورالعمل تسک P00.03، نتایج دو رانر Pytest و Django به هیچ عنوان با هم جمع نمی‌شوند و هرکدام مستقلاً ثبت و گزارش می‌گردند.

### ۲.۱. اجرای Pytest
- **فرمان بازاجرا:**
  ```bash
  docker exec -e TEST_DATABASE_URL="postgresql://postgres:postgres@db:5432/customer_yab" moshtari-yab-web-1 python -m pytest -o cache_dir=/tmp/.pytest_cache --basetemp=/tmp/pytest
  ```
- **آمار کل:** ۳۹۴ تست
- **موفق (PASSED):** ۳۵۳ تست
- **نادیده‌گرفته‌شده (SKIPPED):** ۴۱ تست
- **شکست (FAILED):** ۰ تست
- **مدت زمان اجرا:** حدود ۲۰ ثانیه
- **علت Skipها:**
  * ۶ مورد در `test_crawler.py`: وابستگی به فایل سشن واقعی ورود تلگرام (`telegram_crawler_session.session`).
  * ۱۶ مورد در `test_need_engine.py`: تست‌های نیازمند دیتابیس خارجی یا ماک‌های اختصاصی درایور.
  * ۴ مورد در `x_ingest/tests/test_loader.py`: تست‌های مرتبط با حجم‌های بالای فایل خارجی.
  * ۵ مورد در `test_state_store.py`: تست‌های مرتبط با درایور SQLite همزمان.
  * سایر موارد: بررسی رفتار در شرایط قطع شبکه.

---

### ۲.۲. اجرای رانر تست جنگو (Django App Tests)
- **فرمان بازاجرا:**
  ```bash
  docker exec moshtari-yab-web-1 python manage.py test apps --keepdb
  ```
- **آمار کل:** ۱۹۶ تست (شامل ۶ تست جدید گیت‌های مهار P00.02)
- **موفق (PASSED):** ۱۸۱ تست
- **نادیده‌گرفته‌شده (SKIPPED):** ۱ تست (`test_pool_file_is_git_ignored` درون کانتینر فاقد باینری گیت)
- **خطا / شکست‌های شناخته‌شده باقی‌مانده:** ۱۴ تست
  * ۳ تست شناخته‌شده از گزارش اولیه:
    - `test_source_reward_and_empty_run_penalty_are_persisted` (اختلاف انتظار در پاداش منبع)
    - `test_instagram_report_ui_allows_operator_labels_after_jev` (عدم تطابق لایه‌بندی UI گزارش)
    - `test_plan_rejects_handles_and_urls_and_requires_minimum_phrases` (تفاوت در اعتبارسنجی عبارات ورودی)
  * ۱۰ تست در `apps/billing/test_manage_sessions.py` (ناشی از کامیت اخیر `3b592ec` و نحوه ماک اندپوینت تنظیمات توییتر)
  * ۱ تست در `apps.billing.tests.EngineReadsPanelTests.test_registry_from_panel_tables`

---

### ۲.۳. اجرای آزمون‌های گیت مهار فوری (`apps.core.test_containment_gates`)
- **فرمان بازاجرا:**
  ```bash
  docker exec moshtari-yab-web-1 python manage.py test apps.core.test_containment_gates --keepdb
  ```
- **آمار کل:** ۶ تست
- **موفق (PASSED):** ۶ تست (۱۰۰٪)
- **پوشش:** مهارهای R01 (نشت چندتننت), R02 (تصاحب اکانت), R05 (آپلود غیراصل), R06 (سفارش کارت B2C), R07 (فراخوانی ناشناس هوش مصنوعی), R10 (شبیه‌سازی dry-run بدون دستکاری داده).
