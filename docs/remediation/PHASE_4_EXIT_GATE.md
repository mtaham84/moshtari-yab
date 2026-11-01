# گزارش خروج فاز ۴ — ارسال، dry-run و کنترل همزمانی (Gate G04)

## ۱. وضعیت نهایی فاز ۴
- **تاریخ ارزیابی:** ۱ نوامبر ۲۰۲۶
- **وضعیت فاز:** **۱۰۰٪ تکمیل شده (DONE)**
- **وضعیت گیت G04:** **پاس شد (PASSED)**

---

## ۲. خلاصه تسک‌های انجام‌شده در فاز ۴

| شناسه تسک | عنوان تسک | یافته مرتبط | وضعیت برنامه‌ریزی | وضعیت پیاده‌سازی | وضعیت تست | وضعیت فعال‌سازی | وضعیت نهایی |
| :--- | :--- | :--- | :---: | :---: | :---: | :---: | :---: |
| **P04.01** | تفکیک plan از execute و dry-run واقعی | R10 | DONE | DONE | DONE | DONE | **DONE** |
| **P04.02** | قرارداد و ماشین حالت Intent ارسال | R11, R12 | DONE | DONE | DONE | DONE | **DONE** |
| **P04.03** | جدول افزایشی intent و lease | R11 | DONE | DONE | DONE | DONE | **DONE** |
| **P04.04** | claim اتمیک و تصاحب lease | R11 | DONE | DONE | DONE | DONE | **DONE** |
| **P04.05** | SenderPolicy واحد و reservation سهمیه | R12 | DONE | DONE | DONE | DONE | **DONE** |
| **P04.06** | مجری فعال outreach و mock adapter | R01, R11, R12 | DONE | DONE | DONE | DONE | **DONE** |
| **P04.07** | recovery نتیجه نامطمئن و crash | R11, R12, R41 | DONE | DONE | DONE | DONE | **DONE** |
| **P04.08** | gate ارسال و انزوای همزمانی | G04 (R01, R10, R11, R12, R41) | DONE | DONE | DONE | DONE | **DONE** |

---

## ۳. برآورده شدن معیارهای پذیرش گیت G04
1. **تفکیک کامل Plan/Execute و رفتار Dry-Run:**
   - متد `plan_outreach_batch` به صورت خالص (Pure & Read-Only) طراحی شد؛ صفر جهش در دیتابیس ایجاد می‌کند.
   - اجرای سوئیچ `--dry-run` در دستور مدیریتی `run_instagram_outreach` بررسی ریپلای و ریست سهمیه را کلاً دور زده و صرفاً برنامه ارسالی را نمایش می‌دهد.
2. **قرارداد Intent و ماشین حالت ارسال:**
   - مدل افزایشی `OutreachDispatchIntent` بدون تغییر جدول دست‌نخورده legacy (`leads`) اضافه شد و قید یکتایی مانع ثبت قصد تکراری برای یک لید می‌شود.
   - ماشین حالت با وضعیت‌های مجزای `PENDING`, `CLAIMED`, `SENDING`, `SENT`, `FAILED`, `UNKNOWN`, `SKIPPED` پیاده‌سازی گردید.
3. **قفل اتمیک و مدیریت همزمانی:**
   - متد `claim_pending_intent` با بهره‌گیری از `select_for_update(skip_locked=True)` و الگوریتم مقایسه-و-تعویض (CAS) روی شماره نسل (`generation`) از رقابت همزمان ورکرها و ارسال مجدد ناخواسته جلوگیری می‌کند.
4. **سیاست یکپارچه سهمیه فرستنده‌ها (SenderPolicy):**
   - سهمیه‌بندی اشتراکی اکانت‌های پلتفرم و اختصاصی با رزرو پیش از ارسال (`reserve_quota`) تثبیت شد تا پیش از ارسال شبکه ظرفیت قفل شود.
   - بازه کاری ساعات بیداری تهران (۰۹:۰۰ الی ۲۳:۰۰) و محدودیت‌های ساعتی و روزانه پیاده‌سازی شدند.
5. **مدیریت کرش و وضعیت نامطمئن (Crash Recovery):**
   - در صورت بروز قطعی شبکه یا خطای نامشخص هنگام ارسال، رکورد به هیچ وجه دوباره به صف ارسال برنمی‌گردد بلکه به وضعیت امن `UNKNOWN` منتقل می‌شود.
   - ریکاوری از طریق تطبیق خودکار با دایرکت یا تصمیم‌گیری دستی اپراتور با ثبت لاگ در `security.audit` بر اساس مستندات `OUTREACH_RECOVERY_RUNBOOK.md` تضمین شد.

---

## ۴. ماتریس تایید تست‌ها
اجرای سوئیت ۳۷ تستی فاز ۴ در کانتینر ایزوله تست:
```bash
docker compose exec -T web python manage.py test \
  apps.core.test_p04_01_outreach_plan_and_dry_run \
  apps.core.test_p04_02_intent_contract \
  apps.core.test_p04_03_intent_schema \
  apps.core.test_p04_04_atomic_claim_and_lease \
  apps.core.test_p04_05_sender_policy_and_quota \
  apps.core.test_p04_06_outreach_executor \
  apps.core.test_p04_07_outreach_recovery \
  apps.core.test_p04_gate_g04 \
  --keepdb
```
- **تعداد کل تست‌های فاز ۴:** ۳۷
- **تست‌های پاس‌شده:** ۳۷ (۱۰۰٪)
- **شکست یا ارور:** ۰
- **مدت زمان اجرا:** ۳۰.۹۱۱ ثانیه

اجرای آزمون‌های رگرسیون گیت‌های G00 تا G04:
- `apps.core.test_containment_gates` (G00)
- `apps.core.test_p01_08_phase1_gate_matrix` (G01)
- `apps.core.test_p02_gate_g02` (G02)
- `apps.core.test_p03_gate_g03` (G03)
- `apps.core.test_p04_gate_g04` (G04)
- **نتیجه:** ۲۷ از ۲۷ تست سبز (OK).

---

## ۵. تصمیم ورود به فاز بعدی
کلیه شرایط خروج گیت G04 محقق گردیده است. بر اساس اختیارات محوله، مجوز ورود به **فاز ۵ — معماری gateway و ledger (P05.01 تا P05.08)** صادر می‌گردد.
