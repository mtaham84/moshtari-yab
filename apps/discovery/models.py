from django.db import models
from apps.businesses.models import Business
from apps.products.models import Product, Category

class ProductDailyMetric(models.Model):
    product = models.ForeignKey(
        Product,
        on_delete=models.CASCADE,
        related_name="daily_metrics",
        verbose_name="محصول"
    )
    date = models.DateField(
        db_index=True,
        verbose_name="تاریخ روز"
    )
    outreach_sent_count = models.PositiveIntegerField(
        default=0,
        verbose_name="پیام‌های ارسالی ایجنت"
    )
    clicks_count = models.PositiveIntegerField(
        default=0,
        verbose_name="کلیک‌های لینک محصول"
    )
    views_count = models.PositiveIntegerField(
        default=0,
        verbose_name="بازدید صفحه محصول"
    )
    orders_count = models.PositiveIntegerField(
        default=0,
        verbose_name="تعداد سفارش / خرید"
    )
    sales_amount = models.DecimalField(
        max_digits=14,
        decimal_places=0,
        default=0,
        verbose_name="مبلغ فروش (تومان)"
    )

    class Meta:
        verbose_name = "آمار روزانه محصول"
        verbose_name_plural = "آمارهای روزانه محصولات"
        unique_together = ("product", "date")
        ordering = ["-date", "-sales_amount"]

    def __str__(self):
        return f"{self.product.name} ({self.date}): {self.clicks_count} کلیک - {self.orders_count} خرید"


class ProductOrder(models.Model):
    STATUS_CHOICES = [
        ("PAID", "پرداخت‌شده و نهایی"),
        ("PENDING", "در انتظار تایید"),
        ("SHIPPED", "ارسال‌شده"),
    ]

    product = models.ForeignKey(
        Product,
        on_delete=models.CASCADE,
        related_name="orders",
        verbose_name="محصول"
    )
    opportunity = models.ForeignKey(
        "discovery.Opportunity",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="orders",
        verbose_name="فرصت فروش مرتبط"
    )
    customer_name = models.CharField(
        max_length=150,
        verbose_name="نام و نام خانوادگی خریدار"
    )
    customer_phone = models.CharField(
        max_length=30,
        verbose_name="شماره تماس خریدار"
    )
    shipping_address = models.TextField(
        blank=True,
        verbose_name="آدرس تحویل سفارش"
    )
    quantity = models.PositiveIntegerField(
        default=1,
        verbose_name="تعداد"
    )
    unit_price = models.DecimalField(
        max_digits=12,
        decimal_places=0,
        default=0,
        verbose_name="قیمت واحد (تومان)"
    )
    total_price = models.DecimalField(
        max_digits=14,
        decimal_places=0,
        default=0,
        verbose_name="مبلغ کل (تومان)"
    )
    status = models.CharField(
        max_length=20,
        choices=STATUS_CHOICES,
        default="PAID",
        verbose_name="وضعیت سفارش"
    )
    tracking_code = models.CharField(
        max_length=40,
        unique=True,
        verbose_name="کد پیگیری سفارش"
    )
    created_at = models.DateTimeField(
        auto_now_add=True,
        verbose_name="زمان ثبت سفارش"
    )

    class Meta:
        verbose_name = "سفارش خرید محصول"
        verbose_name_plural = "سفارشات خرید محصولات"
        ordering = ["-created_at"]

    def __str__(self):
        return f"سفارش {self.tracking_code} - {self.customer_name} ({self.total_price:,} تومان)"


# ==============================================================================
# UNIFIED CUSTOMER OPPORTUNITY CONTRACT & RESULT LAYER (PEYDA)
# ==============================================================================

PLATFORM_CHOICES = [
    ("telegram", "تلگرام (Telegram)"),
    ("x", "ایکس / توییتر (X / Twitter)"),
    ("instagram", "اینستاگرام (Instagram)"),
    ("divar", "دیوار (Divar)"),
    ("other", "سایر پلتفرم‌ها"),
]

OPPORTUNITY_STATUS_CHOICES = [
    ("NEW", "جدید"),
    ("REVIEWED", "بررسی شده"),
    ("CONTACTED", "تماس گرفته شده"),
    ("CONVERTED", "مشتری نهایی"),
    ("REJECTED", "رد شده"),
    ("RESOLVED", "نیاز برطرف شد"),
    ("EXPIRED", "منقضی شده"),
]
# statuses written by the engine; the seller's own statuses above are never overwritten by it
ENGINE_STATUS_MAP = {"open": "NEW", "resolved": "RESOLVED", "expired": "EXPIRED"}

EVIDENCE_TYPE_CHOICES = [
    ("customer_message", "متن پیام مشتری"),
    ("need_signal", "سیگنال نیاز"),
    ("product_match", "تطابق ویژگی‌های محصول"),
    ("category_match", "تطابق دسته‌بندی"),
    ("other", "سایر شواهد"),
]


class Customer(models.Model):
    """
    Normalized Customer / Candidate identity model.
    Source-agnostic across Telegram, X, Instagram, Divar.
    phone_number and name are nullable/optional.
    """
    business = models.ForeignKey(
        Business,
        on_delete=models.CASCADE,
        related_name="discovered_customers",
        verbose_name="کسب‌وکار"
    )
    name = models.CharField(
        max_length=150,
        blank=True,
        verbose_name="نام مشتری"
    )
    phone_number = models.CharField(
        max_length=50,
        null=True,
        blank=True,
        verbose_name="شماره تماس"
    )
    external_user_id = models.CharField(
        max_length=150,
        blank=True,
        db_index=True,
        verbose_name="شناسه کاربر در پلتفرم"
    )
    source_platform = models.CharField(
        max_length=30,
        choices=PLATFORM_CHOICES,
        default="telegram",
        db_index=True,
        verbose_name="پلتفرم مبدا"
    )
    source_profile_url = models.URLField(
        blank=True,
        verbose_name="آدرس پروفایل"
    )
    source_username = models.CharField(
        max_length=150,
        blank=True,
        verbose_name="نام کاربری در پلتفرم"
    )
    metadata = models.JSONField(
        default=dict,
        blank=True,
        verbose_name="متادیتای تکمیلی مشتری"
    )
    created_at = models.DateTimeField(
        auto_now_add=True,
        verbose_name="زمان ثبت"
    )
    updated_at = models.DateTimeField(
        auto_now=True,
        verbose_name="آخرین به‌روزرسانی"
    )

    class Meta:
        verbose_name = "مشتری کشف‌شده"
        verbose_name_plural = "مشتریان کشف‌شده"
        ordering = ["-created_at"]

    def __str__(self):
        display_name = self.name or self.source_username or self.external_user_id or "کاربر ناشناس"
        return f"{display_name} ({self.get_source_platform_display()})"

    @property
    def display_identifier(self) -> str:
        if self.name:
            return self.name
        if self.source_username:
            return f"@{self.source_username.lstrip('@')}"
        if self.phone_number:
            return self.phone_number
        if self.external_user_id:
            return f"کاربر {self.external_user_id}"
        return "کاربر بدون نام"


class Opportunity(models.Model):
    """
    Central AI-Assisted Opportunity result model.
    Connects Customer, Category, Product recommendations, AI analysis, and Evidence.
    Source-agnostic: Telegram, X, Instagram, Divar produce the same Opportunity structure.
    """
    business = models.ForeignKey(
        Business,
        on_delete=models.CASCADE,
        related_name="opportunities",
        verbose_name="کسب‌وکار"
    )
    customer = models.ForeignKey(
        Customer,
        on_delete=models.CASCADE,
        related_name="opportunities",
        verbose_name="مشتری"
    )
    category = models.ForeignKey(
        Category,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="opportunities",
        verbose_name="دسته‌بندی مرتبط"
    )
    category_name_snapshot = models.CharField(
        max_length=255,
        blank=True,
        verbose_name="عنوان شاخه در زمان کشف"
    )
    source_platform = models.CharField(
        max_length=30,
        choices=PLATFORM_CHOICES,
        default="telegram",
        db_index=True,
        verbose_name="پلتفرم مبدا"
    )
    source_message_id = models.CharField(
        max_length=150,
        blank=True,
        db_index=True,
        verbose_name="شناسه پیام در پلتفرم"
    )
    engine_opportunity_id = models.CharField(
        max_length=64,
        blank=True,
        db_index=True,
        verbose_name="شناسه فرصت در موتور تحلیل"
    )
    expires_at = models.DateTimeField(
        null=True,
        blank=True,
        verbose_name="زمان انقضای فرصت"
    )
    source_raw_message = models.TextField(
        verbose_name="متن پیام کاربر در شبکه اجتماعی"
    )
    normalized_message = models.TextField(
        blank=True,
        verbose_name="متن پیام نرمال‌شده"
    )
    category_confidence = models.FloatField(
        default=0.0,
        verbose_name="ضریب اطمینان تطابق دسته (بین ۰ تا ۱)"
    )
    category_path_snapshot = models.CharField(
        max_length=500,
        blank=True,
        verbose_name="مسیر کامل شاخه در زمان کشف"
    )
    source_message_timestamp = models.DateTimeField(
        null=True,
        blank=True,
        verbose_name="زمان ارسال پیام در پلتفرم"
    )
    status = models.CharField(
        max_length=30,
        choices=OPPORTUNITY_STATUS_CHOICES,
        default="NEW",
        db_index=True,
        verbose_name="وضعیت فرصت"
    )
    trace_metadata = models.JSONField(
        default=dict,
        blank=True,
        verbose_name="متادیتای ردیابی و لاگ پردازش"
    )
    created_at = models.DateTimeField(
        auto_now_add=True,
        verbose_name="زمان ایجاد فرصت"
    )
    updated_at = models.DateTimeField(
        auto_now=True,
        verbose_name="آخرین به‌روزرسانی"
    )

    class Meta:
        verbose_name = "فرصت فروش"
        verbose_name_plural = "فرصت‌های فروش"
        ordering = ["-created_at"]

    def __str__(self):
        return f"فرصت #{self.id} - {self.customer} - {self.get_status_display()}"

    @property
    def primary_product_match(self):
        return self.product_matches.order_by("rank", "-match_score").first()

    @property
    def intent_score_percentage(self) -> int:
        if hasattr(self, "ai_analysis") and self.ai_analysis:
            return int(self.ai_analysis.intent_score * 100)
        return 0

    @property
    def product_fit_score_percentage(self) -> int:
        if hasattr(self, "ai_analysis") and self.ai_analysis:
            return int(self.ai_analysis.product_fit_score * 100)
        return 0

    @property
    def confidence_percentage(self) -> int:
        if hasattr(self, "ai_analysis") and self.ai_analysis:
            return int(self.ai_analysis.confidence * 100)
        return 0

    @property
    def created_at_jalali(self) -> str:
        from apps.core.jalali import format_jalali_datetime
        if not self.created_at:
            return ""
        return format_jalali_datetime(self.created_at)

    @property
    def message_timestamp_jalali(self) -> str:
        from apps.core.jalali import format_jalali_datetime
        dt = self.source_message_timestamp or self.created_at
        if not dt:
            return ""
        return format_jalali_datetime(dt)

    @property
    def cost_toman_display(self) -> str:
        if hasattr(self, "ai_analysis") and self.ai_analysis:
            cost = self.ai_analysis.cost_toman
            return f"{cost:,}".replace(",", "،") if cost else "۰"
        return "۰"


class AIAnalysis(models.Model):
    """
    Structured AI Analysis result for an Opportunity.
    Stores explicit user-facing explanations and metrics, NO chain-of-thought.
    """
    opportunity = models.OneToOneField(
        Opportunity,
        on_delete=models.CASCADE,
        related_name="ai_analysis",
        verbose_name="فرصت مرتبط"
    )
    need = models.TextField(
        verbose_name="نیاز یا مسئله اصلی مشتری"
    )
    intent_score = models.FloatField(
        default=0.0,
        verbose_name="امتیاز قصد خرید (بین ۰ تا ۱)"
    )
    product_fit_score = models.FloatField(
        default=0.0,
        verbose_name="امتیاز تطابق با کاتالوگ (بین ۰ تا ۱)"
    )
    confidence = models.FloatField(
        default=0.0,
        verbose_name="میزان اطمینان کلی هوش مصنوعی (بین ۰ تا ۱)"
    )
    why_selected = models.TextField(
        verbose_name="دلیل انتخاب این مشتری (توضیح کوتاه و شفاف)"
    )

    @property
    def decision_reason(self) -> str:
        return self.why_selected or ""
    suggested_reply = models.TextField(
        blank=True,
        verbose_name="پیش‌نویس پیام پیشنهادی هوش مصنوعی برای ارسال توسط فروشنده"
    )
    model_name = models.CharField(
        max_length=100,
        blank=True,
        verbose_name="نام مدل زبانی"
    )
    model_version = models.CharField(
        max_length=50,
        blank=True,
        verbose_name="نسخه مدل"
    )
    tokens_used = models.PositiveIntegerField(
        default=0,
        verbose_name="توکن‌های مصرفی"
    )
    cost_usd = models.DecimalField(
        max_digits=10,
        decimal_places=6,
        default=0,
        verbose_name="هزینه دلاری"
    )
    cost_toman = models.PositiveIntegerField(
        default=0,
        verbose_name="هزینه تومانی"
    )
    created_at = models.DateTimeField(
        auto_now_add=True,
        verbose_name="زمان ثبت تحلیل"
    )

    class Meta:
        verbose_name = "تحلیل هوش مصنوعی فرصت"
        verbose_name_plural = "تحلیل‌های هوش مصنوعی فرصت‌ها"

    def __str__(self):
        return f"تحلیل هوش مصنوعی برای فرصت #{self.opportunity_id} (اطمینان: {int(self.confidence * 100)}٪)"


class OpportunityProductMatch(models.Model):
    """
    Product recommendation for an Opportunity (supports multiple ranked products).
    """
    opportunity = models.ForeignKey(
        Opportunity,
        on_delete=models.CASCADE,
        related_name="product_matches",
        verbose_name="فرصت مرتبط"
    )
    product = models.ForeignKey(
        Product,
        on_delete=models.CASCADE,
        related_name="opportunity_matches",
        verbose_name="محصول پیشنهادی"
    )
    match_score = models.FloatField(
        default=0.0,
        verbose_name="امتیاز انطباق کالا (بین ۰ تا ۱)"
    )
    recommendation_reason = models.TextField(
        blank=True,
        verbose_name="علت پیشنهاد این کالا"
    )
    matching_attributes = models.JSONField(
        default=dict,
        blank=True,
        verbose_name="ویژگی‌های منطبق کالا"
    )
    rank = models.PositiveSmallIntegerField(
        default=1,
        verbose_name="رتبه در بین پیشنهادات"
    )
    created_at = models.DateTimeField(
        auto_now_add=True,
        verbose_name="زمان ثبت"
    )

    class Meta:
        verbose_name = "انطباق محصول با فرصت"
        verbose_name_plural = "انطباق‌های محصول با فرصت‌ها"
        ordering = ["rank", "-match_score"]

    def __str__(self):
        return f"رتبه {self.rank}: {self.product.name} (تطابق: {int(self.match_score * 100)}٪)"

    @property
    def match_score_percentage(self) -> int:
        return int(self.match_score * 100)


class Evidence(models.Model):
    """
    Structured evidence citation supporting the opportunity decision.
    """
    opportunity = models.ForeignKey(
        Opportunity,
        on_delete=models.CASCADE,
        related_name="evidence_items",
        verbose_name="فرصت مرتبط"
    )
    evidence_type = models.CharField(
        max_length=50,
        choices=EVIDENCE_TYPE_CHOICES,
        default="customer_message",
        verbose_name="نوع شاهد"
    )
    content = models.TextField(
        verbose_name="محتوای شاهد متنی"
    )
    source_reference = models.CharField(
        max_length=255,
        blank=True,
        verbose_name="ارجاع یا منبع شاهد"
    )
    created_at = models.DateTimeField(
        auto_now_add=True,
        verbose_name="زمان ثبت"
    )

    class Meta:
        verbose_name = "شاهد متنی تصمیم"
        verbose_name_plural = "شواهد متنی تصمیمات"
        ordering = ["id"]

    def __str__(self):
        return f"[{self.get_evidence_type_display()}] {self.content[:40]}"


# ==============================================================================
# MONITORED ONLINE COMMUNITIES (TELEGRAM CHANNELS & GROUPS)
# ==============================================================================

class MonitoredCommunity(models.Model):
    """
    Online community (Telegram public channel, supergroup or discussion board)
    monitored by the customer discovery engine.
    Currently focused exclusively on Telegram.
    """
    COMMUNITY_TYPE_CHOICES = [
        ("CHANNEL", "کانال تلگرام"),
        ("GROUP", "گروه / سوپرگروه تلگرام"),
    ]

    business = models.ForeignKey(
        Business,
        on_delete=models.CASCADE,
        related_name="monitored_communities",
        verbose_name="کسب‌وکار"
    )
    platform = models.CharField(
        max_length=20,
        default="telegram",
        choices=[("telegram", "تلگرام (Telegram)")],
        verbose_name="پلتفرم"
    )
    community_type = models.CharField(
        max_length=20,
        choices=COMMUNITY_TYPE_CHOICES,
        default="CHANNEL",
        verbose_name="نوع جامعه"
    )
    name = models.CharField(
        max_length=255,
        verbose_name="نام کانال یا گروه"
    )
    handle_or_link = models.CharField(
        max_length=255,
        verbose_name="آیدی یا لینک تلگرام"
    )
    category = models.ForeignKey(
        Category,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="communities",
        verbose_name="دسته‌بندی مرتبط"
    )
    description = models.TextField(
        blank=True,
        verbose_name="توضیحات و حوزه فعالیت"
    )
    is_active = models.BooleanField(
        default=True,
        verbose_name="فعال برای پایش"
    )
    members_count = models.PositiveIntegerField(
        default=0,
        verbose_name="تعداد اعضا / مخاطبان"
    )
    messages_scanned_count = models.PositiveIntegerField(
        default=0,
        verbose_name="پیام‌های رصدشده"
    )
    leads_discovered_count = models.PositiveIntegerField(
        default=0,
        verbose_name="سرنخ‌های کشف‌شده"
    )
    last_scanned_at = models.DateTimeField(
        null=True,
        blank=True,
        verbose_name="آخرین زمان پایش"
    )
    # Written by the Telegram crawler (telegram_crawler/panel.py), which polls this table.
    SYNC_STATUS_CHOICES = [
        ("PENDING", "در صف اتصال"),
        ("ACTIVE", "در حال پایش"),
        ("PAUSED", "متوقف"),
        ("ERROR", "خطا در اتصال"),
    ]
    telegram_chat_id = models.BigIntegerField(null=True, blank=True, db_index=True, verbose_name="شناسه چت تلگرام")
    sync_status = models.CharField(max_length=10, choices=SYNC_STATUS_CHOICES, default="PENDING", verbose_name="وضعیت اتصال کراولر")
    sync_error = models.TextField(blank=True, default="", verbose_name="خطای اتصال")
    created_at = models.DateTimeField(
        auto_now_add=True,
        verbose_name="تاریخ افزودن"
    )

    class Meta:
        verbose_name = "جامعه آنلاین پایش‌شده"
        verbose_name_plural = "جوامع آنلاین پایش‌شده"
        ordering = ["-is_active", "-created_at"]

    def __str__(self):
        return f"{self.name} ({self.handle_or_link})"


class EngineSyncCursor(models.Model):
    """Last ``need_engine.opportunities.seq`` imported by ``manage.py sync_opportunities``."""

    name = models.CharField(max_length=64, unique=True, default="opportunities")
    position = models.BigIntegerField(default=0)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"{self.name}: {self.position}"
