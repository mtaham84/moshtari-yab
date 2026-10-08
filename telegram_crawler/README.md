# telegram_crawler — آرشیو پیام‌های گروه‌های تلگرام

کراولر فقط یک کار می‌کند: **همه‌ی پیام‌های** گروه‌های مشخص‌شده را (تاریخچه‌ی اخیر + پیام‌های زنده) در جدول `messages` ذخیره می‌کند.
هیچ فیلتر یا تحلیلی انجام نمی‌دهد؛ تشخیص نیاز با `need_engine` است که همین جدول را فقط‌خواندنی می‌خواند.

```bash
cp telegram_crawler/.env.example telegram_crawler/.env      # TG_API_ID / TG_API_HASH
python -m telegram_crawler.main --link https://t.me/group_name --link @other_group
python -m telegram_crawler.main --links-file groups.txt --hours 48 --limit 500
```

| آرگومان | پیش‌فرض | توضیح |
|---|---|---|
| `--link` | — | لینک عمومی/دعوت گروه (قابل تکرار) |
| `--links-file` | — | فایل متنی، هر خط یک لینک |
| `--hours` / `--limit` | ۲۴ / ۲۰۰ | بازه و حداکثر تاریخچه‌ی هر گروه |
| `--no-resume` | خیر | نادیده گرفتن آخرین پیام اسکن‌شده |
| `--no-live` | خیر | فقط تاریخچه و خروج |
| `--db-path` | `data/leads.db` | مسیر آرشیو |

- جدول‌ها: `group_monitors` (عنوان، لینک، آخرین پیام) و `messages` (فرستنده، متن، زمان، reply). شماره‌ی تلفن جمع‌آوری نمی‌شود.
- اجرای دوباره از آخرین پیام ادامه می‌دهد؛ FloodWait مدیریت می‌شود.
