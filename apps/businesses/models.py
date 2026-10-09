from django.conf import settings
from django.db import models
from django.core.validators import MaxValueValidator, MinValueValidator

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


class MessageStyle(models.Model):
    """How the agent writes reply drafts for this seller. Only tone/format: the engine's fixed rules always win."""

    TONE_CHOICES = [("FORMAL", "رسمی"), ("FRIENDLY", "دوستانه"), ("CASUAL", "خودمانی")]

    business = models.OneToOneField(Business, on_delete=models.CASCADE, related_name="message_style",
                                    verbose_name="کسب‌وکار")
    tone = models.CharField(max_length=10, choices=TONE_CHOICES, default="FRIENDLY", verbose_name="لحن")
    max_sentences = models.PositiveSmallIntegerField(
        default=3, validators=[MinValueValidator(1), MaxValueValidator(5)], verbose_name="حداکثر تعداد جمله")
    use_emoji = models.BooleanField(default=False, verbose_name="استفاده از ایموجی")
    signature = models.CharField(max_length=100, blank=True, default="", verbose_name="امضا")
    include_link = models.BooleanField(default=True, verbose_name="لینک محصول در پیام")
    extra_instructions = models.TextField(max_length=500, blank=True, default="", verbose_name="توضیحات سبک (اختیاری)")
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "سبک پیام"
        verbose_name_plural = "سبک‌های پیام"

    @classmethod
    def for_business(cls, business: Business) -> "MessageStyle":
        """Saved style, or an unsaved default one."""
        try:
            return business.message_style
        except cls.DoesNotExist:
            return cls(business=business)

    def as_engine_dict(self) -> dict:
        return {"tone": self.tone, "max_sentences": self.max_sentences, "use_emoji": self.use_emoji,
                "signature": self.signature, "include_link": self.include_link,
                "extra_instructions": self.extra_instructions}

    def __str__(self):
        return f"سبک پیام {self.business}"
