from django.conf import settings
from django.db import models

class Business(models.Model):
    BUSINESS_TYPE_CHOICES = [
        ("PHYSICAL", "کالایی / فروشگاهی"),
        ("SERVICE", "خدماتی"),
        ("ONLINE", "آنلاین / دیجیتال"),
        ("HYBRID", "ترکیبی (کالایی و خدماتی)"),
    ]

    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="business",
        verbose_name="مالک کسب‌وکار"
    )
    name = models.CharField(max_length=255, verbose_name="نام کسب‌وکار")
    business_type = models.CharField(
        max_length=50,
        choices=BUSINESS_TYPE_CHOICES,
        default="PHYSICAL",
        verbose_name="نوع کسب‌وکار"
    )
    business_domain = models.CharField(
        max_length=150,
        verbose_name="حوزه فعالیت",
        help_text="مثال: پوشاک و مد، برنامه‌نویسی و IT، آموزش آنلاین، لوازم دیجیتال"
    )
    description = models.TextField(blank=True, verbose_name="توضیحات و ارزش پیشنهادی")
    location = models.CharField(max_length=150, blank=True, verbose_name="موقعیت جغرافیایی / شهر")
    target_locations = models.CharField(
        max_length=255,
        default="سراسر کشور",
        blank=True,
        verbose_name="موقعیت جغرافیایی و شهرهای هدف"
    )
    target_min_age = models.PositiveSmallIntegerField(
        null=True,
        blank=True,
        verbose_name="حداقل سن مخاطب"
    )
    target_max_age = models.PositiveSmallIntegerField(
        null=True,
        blank=True,
        verbose_name="حداکثر سن مخاطب"
    )
    target_customer_description = models.TextField(
        blank=True,
        verbose_name="شرح مشتریان ایده‌آل (ICP)"
    )
    daily_discovery_limit = models.PositiveIntegerField(
        default=50,
        verbose_name="سقف روزانه کالاهای فعال برای پایش"
    )
    telegram_account_handle = models.CharField(
        max_length=100,
        blank=True,
        verbose_name="شناسه تلگرام فروشنده"
    )
    telegram_session_or_bot = models.CharField(
        max_length=255,
        blank=True,
        verbose_name="توکن یا سشن تلگرام"
    )
    x_account_handle = models.CharField(
        max_length=100,
        blank=True,
        verbose_name="شناسه توییتر / X فروشنده"
    )
    x_access_token = models.CharField(
        max_length=255,
        blank=True,
        verbose_name="توکن دسترسی X"
    )
    preferred_outreach_mode = models.CharField(
        max_length=20,
        choices=[("DIRECT", "دایرکت خصوصی"), ("COMMENT", "کامنت و پاسخ عمومی")],
        default="DIRECT",
        verbose_name="روش ترجیحی ارسال پیام"
    )
    created_at = models.DateTimeField(auto_now_add=True, verbose_name="تاریخ ایجاد")
    updated_at = models.DateTimeField(auto_now=True, verbose_name="آخرین به‌روزرسانی")

    @property
    def active_discovery_products_count(self):
        if hasattr(self, "products"):
            return self.products.filter(is_discovery_active=True).count()
        return 0

    class Meta:
        verbose_name = "کسب‌وکار"
        verbose_name_plural = "کسب‌وکارها"

    def __str__(self):
        return f"{self.name} ({self.get_business_type_display()})"
