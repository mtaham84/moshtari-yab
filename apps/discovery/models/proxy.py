from django.db import models


class ProxyConfig(models.Model):
    STATUS_CHOICES = [
        ("ACTIVE", "فعال و متصل"),
        ("DEAD", "قطع / نامعتبر (پینگ منفی یک)"),
        ("TESTING", "در حال تست"),
    ]
    PLATFORM_CHOICES = [
        ("INSTAGRAM", "اینستاگرام"),
        ("TWITTER", "توییتر / X"),
        ("TELEGRAM", "تلگرام"),
        ("FREE", "آزاد / بدون انتساب"),
    ]

    raw_url = models.TextField(unique=True, verbose_name="لینک خام کانفیگ (VLESS/VMess/Trojan/SS)")
    protocol = models.CharField(max_length=30, db_index=True, verbose_name="پروتکل")
    tag = models.CharField(max_length=150, blank=True, default="", verbose_name="نام / تگ کانفیگ")
    server = models.CharField(max_length=255, blank=True, default="", verbose_name="آدرس سرور")
    port = models.IntegerField(default=443, verbose_name="پورت سرور")
    local_port = models.IntegerField(null=True, blank=True, unique=True, db_index=True, verbose_name="پورت اختصاصی Xray (مثلاً 1081)")
    ping_ms = models.FloatField(default=-1.0, verbose_name="پینگ / تاخیر (میلی‌ثانیه)")
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default="TESTING", db_index=True, verbose_name="وضعیت")
    assigned_platform = models.CharField(max_length=20, choices=PLATFORM_CHOICES, default="FREE", db_index=True, verbose_name="پلتفرم منتسب")
    assigned_to = models.CharField(max_length=100, blank=True, default="", db_index=True, verbose_name="نام اکانت منتسب")
    last_checked_at = models.DateTimeField(null=True, blank=True, verbose_name="آخرین زمان تست پینگ")
    created_at = models.DateTimeField(auto_now_add=True, verbose_name="زمان ثبت")
    updated_at = models.DateTimeField(auto_now=True, verbose_name="آخرین به‌روزرسانی")

    class Meta:
        db_table = "proxy_configs"
        verbose_name = "کانفیگ پروکسی"
        verbose_name_plural = "کانفیگ‌های پروکسی"
        ordering = ["-status", "ping_ms"]

    @property
    def proxy_url(self) -> str | None:
        if self.local_port:
            return f"socks5h://xray:{self.local_port}"
        return None

    def __str__(self):
        return f"{self.tag or self.server}:{self.port} ({self.protocol}) - {self.ping_ms}ms"


class ConfigChannelSource(models.Model):
    channel_username = models.CharField(max_length=150, unique=True, db_index=True, verbose_name="شناسه کانال تلگرام")
    title = models.CharField(max_length=200, blank=True, default="", verbose_name="عنوان یا برچسب کانال")
    is_active = models.BooleanField(default=True, db_index=True, verbose_name="فعال برای پایش")
    last_synced_at = models.DateTimeField(null=True, blank=True, verbose_name="آخرین زمان بررسی")
    total_configs_found = models.PositiveIntegerField(default=0, verbose_name="مجموع کانفیگ‌های ثبت‌شده")
    created_at = models.DateTimeField(auto_now_add=True, verbose_name="زمان ثبت")
    updated_at = models.DateTimeField(auto_now=True, verbose_name="آخرین به‌روزرسانی")

    class Meta:
        db_table = "config_channel_sources"
        verbose_name = "کانال تلگرام تأمین کانفیگ"
        verbose_name_plural = "کانال‌های تلگرام تأمین کانفیگ"
        ordering = ["-is_active", "-created_at"]

    @property
    def telegram_link(self) -> str:
        clean = self.channel_username.lstrip("@").strip()
        return f"https://t.me/{clean}"

    def __str__(self):
        return f"@{self.channel_username} ({'فعال' if self.is_active else 'غیرفعال'})"

