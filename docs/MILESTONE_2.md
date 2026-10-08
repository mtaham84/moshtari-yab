# مرحله تحویل ۲ — ایجنت تحلیل مشترک (قیف سه‌مرحله‌ای)

## خلاصه
ایجنت `analysis/` حالا پیام‌های صف مشترک را (از هر منبعی) تحلیل می‌کند:

```
صف (inbox) ─► ۱. فیلتر رایگان ─► ۲. غربال دسته‌ای (مدل کوچک) ─► ۳. تحلیل عمیق هر جفت پیام/محصول (مدل بزرگ) ─► محافظ پاسخ ─► verdicts
                 قوانین           همه محصولات، ۱۵ پیام در هر فراخوانی    زمینه کامل گفتگو + few-shot           لینک، تلفن، طول
```

برای **هر پیام** حداقل یک نتیجه (`AgentVerdict`) ثبت می‌شود: تا کدام مرحله رفت، چرا متوقف شد، تحلیل، پیش‌نویس پاسخ، و **هزینه هر مرحله** (توکن واقعی از `usage` ارائه‌دهنده × قیمت مدل × نرخ دلار).

## فایل‌های جدید
| فایل | کار |
| --- | --- |
| `analysis/llm.py` | کلاینت سازگار با OpenAI (Groq/xAI/OpenAI)، حالت JSON، تلاش دوباره روی 429/5xx با `Retry-After`، سقف تعداد فراخوانی، دفتر ثبت همه فراخوانی‌ها |
| `analysis/prefilter.py` | مرحله ۱: بات، خیلی کوتاه، فقط لینک، احوال‌پرسی، تبلیغ (امتیاز ≥۲ از الگوهای فروشنده). **شرط کلمه کلیدی محصول ندارد** |
| `analysis/triage.py` | مرحله ۲: غربال دسته‌ای؛ اعتبارسنجی شناسه‌ها و محصولات؛ خروجی خراب → نصف کردن دسته؛ تقسیم دقیق توکن‌ها بین پیام‌ها |
| `analysis/deep.py` | مرحله ۳: نمای فشرده گفتگو با سقف کاراکتر، ۴ مثال few-shot، اعتبارسنجی Pydantic، یک تلاش اصلاحی |
| `analysis/guards.py` | حذف لینک‌های غیرکاتالوگ، تلفن و ایمیل؛ درج لینک ردیابی‌دار `?src=agent&ref=...`؛ کوتاه کردن؛ رد پاسخ غیرفارسی |
| `analysis/pipeline.py` | اجرای قیف، تصمیم نهایی، رهاسازی پیام‌ها در خطا یا تمام شدن سقف، کش نتیجه غربال |
| `analysis/worker.py` | خط فرمان: `run`، `stats`، `verdicts`، `show`، `replay`، `enqueue-file` |
| `analysis/labeling.py` | خروجی CSV برای برچسب‌زنی تیم + اعتبارسنجی فایل برچسب‌خورده (تقسیم ثابت dev/test) |
| `analysis/mock_llm.py` | مدل ساختگی آفلاین برای دمو و تست (**کیفیت واقعی ندارد**) |
| `analysis/prompts/` | `triage_system.md`، `deep_system.md`، `deep_examples.json` (شامل مثال دوره برای مبتدی، سؤال حرفه‌ای، عینک با نیاز پنهان، آگهی فروشنده) |
| `data/sample/messages.jsonl` | ۳۸ پیام **ساختگی** تلگرام و X برای دمو و تست |

## قوانین تصمیم
- پاسخ فقط وقتی پیشنهاد می‌شود که: مدل بگوید `respond` **و** `fit_score ≥ FIT_THRESHOLD` (پیش‌فرض ۶۰) **و** محافظ‌ها رد نکنند.
- دلیل توقف همیشه ثبت می‌شود: `too_short`، `advertisement`، `no_product_match`، `model_discard`، `below_threshold(..)`، `guard_failed`، `invalid_deep_output`.

## هزینه
- **جمع کل** = جمع همه فراخوانی‌های واقعی (حتی خروجی‌های خراب).
- **هزینه هر پیام** = سهم آن از فراخوانی دسته‌ای غربال (به نسبت طول متن) + هزینه تحلیل عمیق آن. جمع هزینه پیام‌ها دقیقاً برابر جمع کل است (در تست چک شده).
- اگر پیامی بعد از غربال به خاطر خطا یا سقف برگردد، نتیجه غربال کش می‌شود و **دوباره پول غربال داده نمی‌شود**.
- قیمت‌ها را در `MODEL_PRICES` از صفحه قیمت ارائه‌دهنده به‌روز کنید. بدون قیمت، توکن‌ها ثبت می‌شوند ولی هزینه صفر نشان داده می‌شود.
- بیشترین هزینه مرحله ۳ مربوط به مثال‌های few-shot است؛ با `--few-shot 2` یا `0` کم می‌شود (روی کیفیت در مرحله ۳ تحویل اندازه‌گیری می‌کنیم).

## اجرا
```bash
pip install -r requirements.txt
cp .env.example .env        # LLM_API_KEY و MODEL_PRICES و USD_TO_TOMAN را پر کنید

# دمو آفلاین بدون کلید (مدل ساختگی)
python -m analysis.worker enqueue-file data/sample/messages.jsonl
python -m analysis.worker run --mock-llm
python -m analysis.worker stats
python -m analysis.worker verdicts --decision respond

# اجرای واقعی
python -m telegram_crawler.main --links-file groups.txt --no-live --quiet   # پر کردن صف از تلگرام
python -m analysis.worker run --limit 200                                  # تحلیل با Groq
python -m analysis.worker run --loop --interval 30                         # همراه با خزنده زنده
python -m analysis.worker verdicts --out data/results.csv

# پخش مجدد بعد از تغییر پرامپت (تحلیل دوباره بدون خواندن دوباره تلگرام)
python -m analysis.worker replay --all && python -m analysis.worker run

# برچسب‌زنی تیم (فاز ۲)
python -m analysis.labeling export --out data/labeling.csv
python -m analysis.labeling validate data/labeling.csv
```

## تست
```bash
python -m pytest -q        # 41 تست (۲۲ تست مرحله ۱ + ۱۹ تست مرحله ۲)
```
تست‌ها با مدل اسکریپتی و مدل ساختگی اجرا می‌شوند.

## محدودیت‌ها و کارهای مرحله بعد
- با **Groq واقعی اجرا نشده** (کلید در دسترس نبود). اولین اجرای واقعی را با `--limit 20` انجام دهید و اگر خطایی دیدید لاگ را بفرستید.
- خروجی `--mock-llm` معیار کیفیت نیست (مثلاً اسپرسوساز صنعتی را به کاربر خانگی پیشنهاد می‌دهد). کیفیت واقعی فقط با مدل واقعی و دیتاست برچسب‌خورده سنجیده می‌شود.
- اتصال نتایج به پنل جنگو، کارت قیف، API امن و اسکریپت ارزیابی دقت → **مرحله تحویل ۳**.
