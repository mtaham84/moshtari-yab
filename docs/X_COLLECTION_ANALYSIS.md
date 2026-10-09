# جمع‌آوری X (workers/x_collector)

## وضعیت
- worker مستقل از Django است، فقط می‌خواند (هیچ پست/ریپلای/لایک/دایرکتی نمی‌فرستد) و خروجی را در
  `data/x_collected/YYYY-MM-DD.jsonl` می‌نویسد.
- عبارت‌های جست‌وجو از `data/x_queries.txt` (هر خط یک query) خوانده می‌شوند.
- **هنوز به need_engine وصل نیست**؛ مرحله‌ی بعد افزودن X به‌عنوان منبع دوم موتور است.

## اجرا
```bash
python -m workers.x_collector --mock --once      # بدون شبکه
python -m workers.x_collector --dry-run          # فقط نمایش فرمان CLI (بدون secrets)
python -m workers.x_collector --once
python -m workers.x_collector --loop --interval 300
```

## نکته‌ی مهم درباره‌ی CLI
نگاشت فعلی `workers/x_collector/cli_mapping.py` تأییدنشده است. خروجی واقعی `twitter-cli` (Agent Reach):
- فرمان: `twitter search "<q>" -t Latest --exclude retweets --max N --json`
- خروجی: `{"ok": true, "data": [{id, text, author{id,name,screenName}, createdAtISO, isRetweet, lang, ...}]}`
- خطا: `{"ok": false, "error": {"code": "rate_limited" | "not_authenticated" | ...}}`

تا اصلاح نگاشت، اجرای زنده رکوردی تولید نمی‌کند.

## ریسک‌ها
خواندن با کوکی (`TWITTER_AUTH_TOKEN`, `TWITTER_CT0`) ممکن است با شرایط X مغایر باشد و به محدودیت/ban منجر شود.
فقط حساب burner، کوکی در env امن، و کنترل‌های پیش‌فرض: سقف ۵۰ نتیجه برای هر query و ۵۰۰ در روز، jitter، backoff
و circuit breaker پس از ۴ خطای متوالی. برای محصول عملیاتی API رسمی یا تأمین‌کننده‌ی دارای مجوز توصیه می‌شود.
