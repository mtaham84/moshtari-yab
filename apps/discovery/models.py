import hashlib
import inspect
from datetime import timedelta
from django.db import models
from django.utils import timezone as dj_timezone
from django.db.models.functions import Lower
from apps.businesses.models import Business
from .sources import GLOBAL, PRIVATE, SCOPE_CHOICES, display_link, normalize_link, sync_error_message
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

SELLER_FEEDBACK_CHOICES = [
    ("", "ثبت نشده"),
    ("relevant", "مشتری واقعی"),
    ("irrelevant", "نامرتبط"),
]
SELLER_FEEDBACK_REASON_CHOICES = [
    ("not_a_buyer", "قصد خرید نداشت"),
    ("wrong_product", "محصول اشتباه بود"),
    ("already_bought", "قبلاً خریده بود"),
    ("spam", "هرزنامه یا تبلیغ"),
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
    contact_channels = models.JSONField(default=list, blank=True, verbose_name="راه‌های تماس عمومی خوداظهاری‌شده")
    linked_identities = models.JSONField(default=list, blank=True, verbose_name="هویت‌های پیوندخوردهٔ خوداظهاری‌شده")
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
    source_posted_at = models.DateTimeField(null=True, blank=True, db_index=True)
    source_posted_at_estimated = models.BooleanField(default=False)
    source_query = models.CharField(max_length=255, blank=True, db_index=True)
    lead_feedback = models.CharField(max_length=32, null=True, blank=True, choices=[("good", "مشتری مناسب"), ("bad_not_buyer", "قصد خرید ندارد"), ("bad_seller_or_ad", "فروشنده/تبلیغ"), ("bad_wrong_product", "محصول نامرتبط"), ("bad_too_old", "قدیمی")])
    lead_feedback_at = models.DateTimeField(null=True, blank=True)
    feedback = models.CharField(max_length=16, blank=True, choices=SELLER_FEEDBACK_CHOICES, default="", db_index=True)
    feedback_at = models.DateTimeField(null=True, blank=True)
    feedback_reason = models.CharField(max_length=32, blank=True, choices=SELLER_FEEDBACK_REASON_CHOICES, default="")
    thread_root_tweet_id = models.BigIntegerField(null=True, blank=True)
    parent_opportunity = models.ForeignKey("self", null=True, blank=True, on_delete=models.SET_NULL, related_name="thread_opportunities")
    demand_count = models.PositiveIntegerField(default=0)
    competitor_replies = models.JSONField(default=list, blank=True)
    thread_fetched_at = models.DateTimeField(null=True, blank=True)
    intent_type = models.CharField(
        max_length=32,
        blank=True,
        default="",
        choices=[
            ("buy_signal", "سیگنال خرید صریح"),
            ("recommendation_ask", "درخواست معرفی / پیشنهاد"),
            ("complaint", "نارضایتی از محصول فعلی"),
            ("comparison", "مقایسه گزینه‌ها"),
            ("switching", "تغییر و جایگزینی"),
            ("general", "عمومی"),
        ],
        verbose_name="نوع قصد خرید",
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
        constraints = [models.UniqueConstraint(
            fields=["business", "source_platform", "source_message_id"],
            condition=models.Q(source_platform="instagram") & ~models.Q(source_message_id=""),
            name="uniq_ig_opportunity_business_message",
        )]

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

    @property
    def instagram_post_url(self) -> str:
        if self.source_platform == "instagram" and isinstance(self.trace_metadata, dict):
            ig_meta = self.trace_metadata.get("instagram", {})
            return ig_meta.get("post_url", "")
        return ""

    @property
    def x_post_url(self) -> str:
        if self.source_platform == "x":
            if isinstance(self.trace_metadata, dict):
                x_meta = self.trace_metadata.get("x", {})
                if x_meta.get("url"):
                    return x_meta["url"]
                if x_meta.get("tweet_id") and self.customer and self.customer.source_username:
                    h = self.customer.source_username.lstrip("@").strip()
                    return f"https://x.com/{h}/status/{x_meta['tweet_id']}"
            if self.source_message_id and self.customer and self.customer.source_username:
                h = self.customer.source_username.lstrip("@").strip()
                return f"https://x.com/{h}/status/{self.source_message_id}"
        return ""

    @property
    def source_post_url(self) -> str:
        if self.source_platform == "instagram":
            return self.instagram_post_url
        if self.source_platform == "x":
            return self.x_post_url
        return ""

    @property
    def customer_profile_url(self) -> str:
        if self.customer and self.customer.source_profile_url:
            return self.customer.source_profile_url
        if self.customer and self.customer.source_username:
            u = self.customer.source_username.lstrip("@").strip()
            if not u:
                return ""
            if self.source_platform == "instagram":
                return f"https://www.instagram.com/{u}/"
            if self.source_platform == "x":
                return f"https://x.com/{u}"
            if self.source_platform == "telegram":
                return f"https://t.me/{u}"
        return ""

    @property
    def instagram_profile_url(self) -> str:
        return self.customer_profile_url

    @property
    def instagram_post_caption(self) -> str:
        if self.source_platform == "instagram" and isinstance(self.trace_metadata, dict):
            ig_meta = self.trace_metadata.get("instagram", {})
            return ig_meta.get("post_caption", "")
        return ""

    @property
    def raw_cost_usd(self) -> float:
        if hasattr(self, "ai_analysis") and self.ai_analysis:
            return float(self.ai_analysis.cost_usd or 0.0)
        return 0.0

    @property
    def raw_cost_toman(self) -> int:
        from apps.billing.models import BillingSettings
        s = BillingSettings.get()
        return int(round(self.raw_cost_usd * float(s.usd_to_toman)))


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
    reply_variants = models.JSONField(null=True, blank=True, verbose_name="پیشنویس‌های پاسخ X")
    reply_history = models.JSONField(default=list, blank=True, verbose_name="تاریخچهٔ پیش‌نویس‌های پیام")
    reply_sent = models.BooleanField(default=False, verbose_name="پیام توسط فروشنده ارسال شد")
    reply_sent_at = models.DateTimeField(null=True, blank=True)
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
        null=True,
        blank=True,
        related_name="monitored_communities",
        verbose_name="کسب‌وکار (برای منبع عمومی خالی)"
    )
    scope = models.CharField(
        max_length=10,
        choices=SCOPE_CHOICES,
        default=PRIVATE,
        db_index=True,
        verbose_name="نوع منبع"
    )
    normalized_link = models.CharField(max_length=255, blank=True, default="", db_index=True, editable=False)
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
        blank=True,
        default="",
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
        verbose_name = "منبع پایش (گروه/کانال تلگرام)"
        verbose_name_plural = "منابع پایش (گروه‌ها و کانال‌های تلگرام)"
        ordering = ["-is_active", "-created_at"]
        _cc_param = "condition" if "condition" in inspect.signature(models.CheckConstraint.__init__).parameters else "check"
        constraints = [
            models.CheckConstraint(
                name="community_scope_matches_owner",
                **{_cc_param: (models.Q(scope=GLOBAL, business__isnull=True) | models.Q(scope=PRIVATE, business__isnull=False))},
            ),
        ]

    def save(self, *args, **kwargs):
        self.scope = GLOBAL if self.business_id is None else PRIVATE
        self.normalized_link = normalize_link(self.handle_or_link)
        if self.normalized_link:
            self.handle_or_link = display_link(self.handle_or_link)
        update_fields = kwargs.get("update_fields")
        if update_fields is not None:
            kwargs["update_fields"] = set(update_fields) | {"scope", "normalized_link", "handle_or_link"}
        super().save(*args, **kwargs)

    @property
    def is_global(self) -> bool:
        return self.scope == GLOBAL

    @property
    def display_name(self) -> str:
        return self.name or self.handle_or_link

    @property
    def sync_error_display(self) -> str:
        return sync_error_message(self.sync_error)

    def __str__(self):
        return f"{self.display_name} ({self.handle_or_link})"


class InstagramPage(models.Model):
    KIND_CHOICES = [("own", "صفحهٔ خودم"), ("watched", "صفحهٔ رقیب")]

    business = models.ForeignKey(Business, on_delete=models.CASCADE, related_name="instagram_pages")
    handle = models.CharField(max_length=30)
    kind = models.CharField(max_length=10, choices=KIND_CHOICES, default="watched")
    default_product = models.ForeignKey(Product, on_delete=models.SET_NULL, null=True, blank=True,
                                        related_name="instagram_pages")
    is_active = models.BooleanField(default=True)
    last_run_at = models.DateTimeField(null=True, blank=True)
    queued_at = models.DateTimeField(null=True, blank=True)
    last_status = models.CharField(max_length=24, blank=True, default="")
    discovered_family_id = models.CharField(max_length=80, blank=True, default="", db_index=True)
    consecutive_failures = models.PositiveSmallIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [models.UniqueConstraint(Lower("handle"), "business",
                                               name="uniq_instagram_page_business_handle")]
        ordering = ["-is_active", "handle"]
        indexes = [models.Index(fields=["business", "is_active", "last_run_at"], name="igpage_active_fair_idx")]

    def __str__(self):
        return f"@{self.handle} ({self.business})"


class InstagramFamilyPlan(models.Model):
    """Cached Instagram discovery plan for one current product family."""

    STATUS_CHOICES = [("ready", "آماده"), ("failed", "ناموفق")]

    business = models.ForeignKey(Business, on_delete=models.CASCADE, related_name="instagram_family_plans")
    family_id = models.CharField(max_length=80, db_index=True)
    content_hash = models.CharField(max_length=64)
    hashtags = models.JSONField(default=list, blank=True)
    keywords = models.JSONField(default=list, blank=True)
    page_search_terms = models.JSONField(default=list, blank=True)
    buyer_signals = models.JSONField(default=list, blank=True)
    negative_signals = models.JSONField(default=list, blank=True)
    built_at = models.DateTimeField(default=dj_timezone.now)
    status = models.CharField(max_length=12, choices=STATUS_CHOICES, default="ready")
    error = models.CharField(max_length=255, blank=True, default="")

    class Meta:
        constraints = [models.UniqueConstraint(fields=["business", "family_id"], name="uniq_ig_plan_business_family")]
        indexes = [models.Index(fields=["business", "status", "built_at"], name="igplan_business_status_idx")]


class InstagramSourceStat(models.Model):
    SOURCE_CHOICES = [("page", "صفحه"), ("hashtag", "هشتگ"), ("keyword", "کلیدواژه"), ("x_query", "کوئری X")]

    business = models.ForeignKey(Business, on_delete=models.CASCADE, related_name="instagram_source_stats")
    family_id = models.CharField(max_length=80, db_index=True)
    source_type = models.CharField(max_length=12, choices=SOURCE_CHOICES)
    label = models.CharField(max_length=100)
    requests = models.PositiveIntegerField(default=0)
    leads = models.PositiveIntegerField(default=0)
    alpha = models.FloatField(default=1.0)
    beta = models.FloatField(default=1.0)
    last_run_at = models.DateTimeField(null=True, blank=True)

    @property
    def score(self) -> float:
        total = self.alpha + self.beta
        return round((self.alpha / total) * 100.0, 1) if total > 0 else 50.0

    @property
    def delta(self) -> float:
        return round(self.score - 50.0, 1)

    @property
    def type_display_persian(self) -> str:
        return {
            "hashtag": "هشتگ اینستاگرام",
            "keyword": "کلیدواژه اینستاگرام",
            "page": "صفحه هدف اینستاگرام",
            "x_query": "کوئری X (توییتر)"
        }.get(self.source_type, self.source_type)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["business", "family_id", "source_type", "label"],
                                                name="uniq_ig_source_stat")]
        indexes = [models.Index(fields=["business", "family_id", "source_type"], name="igstat_family_type_idx")]


class InstagramReplySuggestion(models.Model):
    """Persisted history and sent state for human-reviewed Instagram message drafts."""

    opportunity = models.ForeignKey("discovery.Opportunity", on_delete=models.CASCADE,
                                    related_name="instagram_reply_suggestions")
    product = models.ForeignKey(Product, on_delete=models.CASCADE, related_name="instagram_reply_suggestions")
    reply_variants = models.JSONField(default=dict, blank=True)
    history = models.JSONField(default=list, blank=True)
    sent = models.BooleanField(default=False)
    sent_at = models.DateTimeField(null=True, blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["opportunity", "product"], name="uniq_ig_reply_opp_product")]


class InstagramCategoryTarget(models.Model):
    business = models.ForeignKey(Business, on_delete=models.CASCADE, related_name="instagram_category_targets")
    category = models.ForeignKey(Category, on_delete=models.CASCADE, related_name="instagram_targets")
    terms = models.JSONField(default=list, blank=True)
    hashtags = models.JSONField(default=list, blank=True)
    max_posts = models.PositiveSmallIntegerField(default=5)
    window_days = models.PositiveSmallIntegerField(default=7)
    is_active = models.BooleanField(default=False)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["business", "category"], name="uniq_ig_category_target")]
        ordering = ["category__full_path"]

    def __str__(self):
        return f"{self.business}: {self.category}"


class EngineSyncCursor(models.Model):
    """Last ``need_engine.opportunities.seq`` imported by ``manage.py sync_opportunities``."""

    name = models.CharField(max_length=64, unique=True, default="opportunities")
    position = models.BigIntegerField(default=0)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"{self.name}: {self.position}"


class XSearchQuery(models.Model):
    KIND = [(x, x) for x in ("auto_intent", "auto_problem", "auto_product", "manual", "suggested")]
    STATE = [(x, x) for x in ("active", "paused_manual", "paused_auto", "suggested", "rejected", "stale")]
    business = models.ForeignKey(Business, on_delete=models.CASCADE, related_name="x_search_queries")
    product = models.ForeignKey(Product, on_delete=models.SET_NULL, null=True, blank=True, related_name="x_search_queries")
    text = models.CharField(max_length=100)
    normalized_text = models.CharField(max_length=100)
    kind = models.CharField(max_length=20, choices=KIND, default="manual")
    state = models.CharField(max_length=20, choices=STATE, default="active", db_index=True)
    weight = models.FloatField(default=1.0)
    state_reason = models.CharField(max_length=255, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    last_state_change_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["business", "normalized_text"], name="uniq_x_query_business_normalized")]
        indexes = [models.Index(fields=["business", "state"], name="xquery_business_state_idx")]


class XNegativeTerm(models.Model):
    business = models.ForeignKey(Business, on_delete=models.CASCADE, related_name="x_negative_terms")
    product = models.ForeignKey(Product, on_delete=models.CASCADE, null=True, blank=True, related_name="x_negative_terms")
    term = models.CharField(max_length=40)
    normalized_term = models.CharField(max_length=40)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["business", "product", "normalized_term"], name="uniq_x_negative_term")]


class XCollectorState(models.Model):
    STATUSES = [(x, x) for x in ("idle", "collecting", "rate_limited", "daily_cap", "circuit_open", "auth_failed", "failed", "unknown")]
    id = models.PositiveSmallIntegerField(primary_key=True, default=1, editable=False)
    status = models.CharField(max_length=20, choices=STATUSES, default="unknown")
    cooldown_until = models.DateTimeField(null=True, blank=True)
    collected_today = models.PositiveIntegerField(default=0)
    daily_cap = models.PositiveIntegerField(default=0)
    last_cycle_at = models.DateTimeField(null=True, blank=True)
    last_cycle_collected = models.PositiveIntegerField(default=0)
    failures_last_cycle = models.PositiveIntegerField(default=0)
    updated_at = models.DateTimeField(auto_now=True)


class XQueryDaily(models.Model):
    day = models.DateField()
    query_text = models.CharField(max_length=100)
    fetched = models.PositiveIntegerField(default=0)
    new_posts = models.PositiveIntegerField(default=0)
    dropped = models.JSONField(default=dict)
    analysed = models.PositiveIntegerField(default=0)
    needs = models.PositiveIntegerField(default=0)
    opportunities = models.PositiveIntegerField(default=0)
    cost_toman = models.FloatField(default=0)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["day", "query_text"], name="uniq_x_query_daily")]
        indexes = [models.Index(fields=["day", "query_text"], name="xquerydaily_day_query_idx")]


class XPostFeedback(models.Model):
    business = models.ForeignKey(Business, on_delete=models.CASCADE, related_name="x_post_feedback")
    tweet_id = models.CharField(max_length=40)
    verdict = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["business", "tweet_id"], name="uniq_x_post_feedback")]


class XQueryScore(models.Model):
    business = models.ForeignKey(Business, on_delete=models.CASCADE, related_name="x_query_scores")
    query = models.ForeignKey(XSearchQuery, on_delete=models.CASCADE, related_name="scores")
    computed_at = models.DateTimeField(auto_now_add=True)
    metrics = models.JSONField(default=dict)
    score = models.FloatField(default=0)
    recommendation = models.CharField(max_length=40, default="insufficient_data")


class InstagramEngineAccount(models.Model):
    ROLE_CHOICES = [
        ("SCRAPER", "اسکرپر (پایش کامنت و هشتگ)"),
        ("SENDER", "فرستنده / پاسخگو (ارسال دایرکت)"),
    ]
    STATUS_CHOICES = [
        ("ACTIVE", "فعال و متصل"),
        ("COOLDOWN", "استراحت موقت (Cooldown)"),
        ("CHALLENGED", "نیازمند تایید / کد امنیتی (Challenge)"),
        ("BANNED", "مسدود شده (Banned)"),
    ]

    tenant_id = models.CharField(max_length=64, default="tenant_default", db_index=True, verbose_name="شناسه مستأجر / فروشگاه")
    username = models.CharField(max_length=100, unique=True, db_index=True, verbose_name="نام کاربری اینستاگرام")
    password = models.CharField(max_length=255, verbose_name="رمز عبور")
    role = models.CharField(max_length=20, choices=ROLE_CHOICES, default="SCRAPER", db_index=True, verbose_name="نقش اکانت")
    proxy_url = models.CharField(max_length=255, null=True, blank=True, verbose_name="پروکسی (SOCKS5/HTTP)")
    session_json = models.JSONField(null=True, blank=True, verbose_name="دیتای سشن")
    device_settings = models.JSONField(null=True, blank=True, verbose_name="تنظیمات سخت‌افزار")
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default="ACTIVE", db_index=True, verbose_name="وضعیت")
    daily_action_count = models.IntegerField(default=0, verbose_name="تعداد عملیات امروز")
    last_action_at = models.DateTimeField(null=True, blank=True, verbose_name="آخرین زمان فعالیت")
    cooldown_until = models.DateTimeField(null=True, blank=True, db_index=True, verbose_name="پایان دوره استراحت")
    status_message = models.CharField(max_length=255, blank=True, default="", verbose_name="پیام آخرین وضعیت / خطا")
    created_at = models.DateTimeField(auto_now_add=True, verbose_name="زمان ایجاد")
    updated_at = models.DateTimeField(auto_now=True, verbose_name="آخرین به‌روزرسانی")

    class Meta:
        db_table = "ig_accounts"
        verbose_name = "اکانت موتور اینستاگرام"
        verbose_name_plural = "اکانت‌های موتور اینستاگرام"
        indexes = [
            models.Index(fields=["tenant_id", "role", "status"], name="ix_ig_accounts_tr_status"),
        ]

    def is_available(self):
        from django.utils import timezone
        if self.status != "ACTIVE":
            return False
        if self.cooldown_until and self.cooldown_until > timezone.now():
            return False
        return True

    def __str__(self):
        return f"@{self.username} ({self.get_role_display()} - {self.get_status_display()})"


class InstagramEngineLead(models.Model):
    STATUS_CHOICES = [
        ("NEW", "جدید (در صف ارسال اولویت‌دار)"),
        ("DM_SENT", "دایرکت ارسال شد"),
        ("FAILED", "ارسال ناموفق"),
        ("SKIPPED", "رد شده (نامعتبر یا گاردریل اسپم)"),
    ]

    tenant_id = models.CharField(max_length=64, default="tenant_default", db_index=True, verbose_name="شناسه مستأجر")
    ig_user_pk = models.CharField(max_length=64, db_index=True, verbose_name="شناسه عددی کاربر اینستاگرام")
    username = models.CharField(max_length=100, db_index=True, verbose_name="نام کاربری لید")
    full_name = models.CharField(max_length=255, null=True, blank=True, verbose_name="نام کاربر")
    profile_url = models.CharField(max_length=255, null=True, blank=True, verbose_name="لینک پروفایل")
    comment_text = models.TextField(verbose_name="متن کامنت خریدار")
    source_post_url = models.CharField(max_length=255, null=True, blank=True, verbose_name="لینک پست مبدا")
    post_caption_snippet = models.TextField(null=True, blank=True, verbose_name="خلاصه کپشن پست")
    profile_bio = models.TextField(blank=True, default="", verbose_name="بیوی عمومیِ دریافت‌شده همراه کامنت")
    commenter_is_business = models.BooleanField(null=True, blank=True, verbose_name="حساب کاری طبق اطلاعات عمومی")
    follower_count = models.PositiveIntegerField(null=True, blank=True, verbose_name="تعداد دنبال‌کنندگانِ دریافت‌شده")
    matched_cta = models.CharField(max_length=100, null=True, blank=True, verbose_name="کال‌تواکشن منطبق")
    intent_score = models.FloatField(default=0.85, db_index=True, verbose_name="ضریب احتمال خرید")
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default="NEW", db_index=True, verbose_name="وضعیت لید")
    created_at = models.DateTimeField(auto_now_add=True, verbose_name="زمان استخراج")
    updated_at = models.DateTimeField(auto_now=True, verbose_name="آخرین به‌روزرسانی")

    class Meta:
        db_table = "leads"
        verbose_name = "لید اینستاگرام"
        verbose_name_plural = "لیدهای اینستاگرام"
        constraints = [
            models.UniqueConstraint(fields=["tenant_id", "ig_user_pk"], name="uq_tenant_lead_pk"),
        ]
        indexes = [
            models.Index(fields=["tenant_id", "status"], name="ix_leads_t_status"),
            models.Index(fields=["intent_score"], name="ix_leads_intent_score"),
        ]

    def __str__(self):
        return f"@{self.username} (احتمال خرید: {int(self.intent_score * 100)}% - {self.get_status_display()})"


class InstagramCrawledPost(models.Model):
    post_hash = models.CharField(max_length=64, unique=True, db_index=True, verbose_name="هش شناسه پست")
    media_id = models.CharField(max_length=64, db_index=True, verbose_name="شناسه پست اینستاگرام")
    media_code = models.CharField(max_length=32, blank=True, verbose_name="کد کوتاه پست")
    account_username = models.CharField(max_length=100, verbose_name="اکانت اسکرپر")
    posted_at = models.DateTimeField(db_index=True, verbose_name="زمان انتشار پست")
    crawled_at = models.DateTimeField(auto_now_add=True, db_index=True, verbose_name="زمان ثبت کرول")
    caption = models.TextField(blank=True, default="")
    comments = models.JSONField(default=list, blank=True)
    comment_count = models.PositiveIntegerField(default=0)
    comments_cached = models.BooleanField(default=False)

    class Meta:
        verbose_name = "پست کرول‌شده اینستاگرام"
        verbose_name_plural = "پست‌های کرول‌شده اینستاگرام"
        indexes = [
            models.Index(fields=["crawled_at"], name="ix_ig_crawled_at"),
            models.Index(fields=["posted_at"], name="ix_ig_posted_at"),
            models.Index(fields=["post_hash"], name="ix_ig_post_hash"),
        ]

    def __str__(self):
        return f"Post {self.media_code or self.media_id} by @{self.account_username} ({self.post_hash[:8]})"


class InstagramCrawlExecution(models.Model):
    """
    Audit log of each discovery crawl execution per query/hashtag and scraper account.
    Tracks exact Persian Jalali date, Tehran time, account used, and discovery metrics.
    """
    STATUS_CHOICES = [
        ("SUCCESS", "موفق"),
        ("NO_POSTS", "بدون پست جدید"),
        ("THROTTLED", "محدودیت موقت"),
        ("FAILED", "خطا"),
        ("REMOVED_UNQUALIFIED", "حذف‌شده (کمتر از ۳ پست واجد شرایط)"),
    ]

    business = models.ForeignKey(
        Business,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="instagram_crawl_executions",
        verbose_name="کسب‌وکار مستأجر",
    )
    tenant_id = models.CharField(
        max_length=64,
        default="tenant_default",
        db_index=True,
        verbose_name="شناسه مستأجر",
    )
    query_text = models.CharField(
        max_length=255,
        db_index=True,
        verbose_name="کوئری یا هشتگ",
    )
    target_type = models.CharField(
        max_length=50,
        default="HASHTAG",
        choices=[("HASHTAG", "هشتگ"), ("KEYWORD", "کلمه کلیدی"), ("PAGE", "صفحه هدف")],
        verbose_name="نوع هدف",
    )
    account_username = models.CharField(
        max_length=150,
        db_index=True,
        verbose_name="نام کاربری اکانت اسکرپر",
    )
    account = models.ForeignKey(
        "discovery.InstagramEngineAccount",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="crawl_executions",
        verbose_name="اکانت ثبت‌شده",
    )
    status = models.CharField(
        max_length=50,
        choices=STATUS_CHOICES,
        default="SUCCESS",
        verbose_name="وضعیت اجرا",
    )
    posts_inspected = models.PositiveIntegerField(
        default=0,
        verbose_name="پست‌های بررسی‌شده",
    )
    requests = models.PositiveSmallIntegerField(default=1, verbose_name="تعداد درخواست‌های API")
    qualifying_posts = models.PositiveIntegerField(
        default=0,
        verbose_name="پست‌های حائز شرایط (کامنت >= ۵۰)",
    )
    leads_found = models.PositiveIntegerField(
        default=0,
        verbose_name="لیدهای یافت‌شده",
    )
    message = models.TextField(
        blank=True,
        verbose_name="پیام و جزییات نتیجه",
    )
    crawled_at = models.DateTimeField(
        default=dj_timezone.now,
        db_index=True,
        verbose_name="زمان کرول (UTC)",
    )

    class Meta:
        verbose_name = "لاگ کرول اینستاگرام"
        verbose_name_plural = "لاگ‌های کرول اینستاگرام"
        ordering = ["-crawled_at"]
        indexes = [
            models.Index(fields=["crawled_at"], name="ix_ig_exec_crawled_at"),
            models.Index(fields=["tenant_id", "query_text"], name="ix_ig_exec_tenant_query"),
            models.Index(fields=["account_username"], name="ix_ig_exec_account"),
        ]

    def __str__(self):
        return f"{self.query_text} (@{self.account_username}) - {self.crawled_at_jalali}"

    @property
    def crawled_at_tehran(self):
        import zoneinfo
        tz = zoneinfo.ZoneInfo("Asia/Tehran")
        if self.crawled_at:
            return self.crawled_at.astimezone(tz)
        return None

    @property
    def crawled_at_jalali(self) -> str:
        from apps.core.jalali import format_jalali_date, to_persian_digits
        if not self.crawled_at:
            return "—"
        dt = self.crawled_at_tehran
        date_part = format_jalali_date(dt.date(), persian_digits=True)
        time_part = to_persian_digits(f"{dt.hour:02d}:{dt.minute:02d}:{dt.second:02d}")
        return f"{date_part} ساعت {time_part}"


class XCrawledPost(models.Model):
    """
    Tracks crawled X (Twitter) tweets with SHA-256 hash deduplication.
    Strictly isolated from Instagram hashes by prefixing with 'x_tweet:'.
    Has a 10-day TTL (tweets older than 10 days are excluded and pruned).
    """
    post_hash = models.CharField(max_length=64, unique=True, db_index=True, verbose_name="هش شناسه توییت")
    tweet_id = models.CharField(max_length=64, unique=True, db_index=True, verbose_name="شناسه توییت X")
    author_handle = models.CharField(max_length=100, blank=True, verbose_name="اکانت توییت")
    posted_at = models.DateTimeField(null=True, blank=True, db_index=True, verbose_name="زمان انتشار توییت")
    crawled_at = models.DateTimeField(auto_now_add=True, db_index=True, verbose_name="زمان ثبت کرول")
    tweet_url = models.CharField(max_length=255, blank=True, verbose_name="آدرس توییت")

    class Meta:
        verbose_name = "توییت کرول‌شده X"
        verbose_name_plural = "توییت‌های کرول‌شده X"
        indexes = [
            models.Index(fields=["crawled_at"], name="ix_x_crawled_at"),
            models.Index(fields=["posted_at"], name="ix_x_posted_at"),
            models.Index(fields=["post_hash"], name="ix_x_post_hash"),
            models.Index(fields=["tweet_id"], name="ix_x_tweet_id"),
        ]

    def __str__(self):
        return f"Tweet {self.tweet_id} by @{self.author_handle} ({self.post_hash[:8]})"

    @classmethod
    def compute_hash(cls, tweet_id: str | int) -> str:
        clean_id = str(tweet_id).strip()
        # Strictly isolated namespace prefix 'x_tweet:'
        return hashlib.sha256(f"x_tweet:{clean_id}".encode("utf-8")).hexdigest()

    @classmethod
    def is_crawled(cls, tweet_id: str | int) -> bool:
        phash = cls.compute_hash(tweet_id)
        return cls.objects.filter(post_hash=phash).exists()

    @classmethod
    def purge_older_than_10_days(cls) -> int:
        ten_days_ago = dj_timezone.now() - timedelta(days=10)
        deleted_count, _ = cls.objects.filter(crawled_at__lt=ten_days_ago).delete()
        return deleted_count


class TwitterEngineAccount(models.Model):
    STATUS_CHOICES = [
        ("ACTIVE", "فعال و متصل"),
        ("COOLDOWN", "استراحت موقت (Rate Limit / Cooldown)"),
        ("CHALLENGED", "نیازمند سشن معتبر / چالش امنیتی (Challenge)"),
        ("BANNED", "مسدود شده (Banned)"),
        ("DISABLED", "غیرفعال دستی"),
    ]

    username = models.CharField(max_length=100, unique=True, db_index=True, verbose_name="نام کاربری توییتر / X")
    password = models.CharField(max_length=255, blank=True, default="", verbose_name="رمز عبور")
    auth_token = models.CharField(max_length=255, blank=True, default="", verbose_name="کوکی auth_token (Session)")
    ct0 = models.CharField(max_length=255, blank=True, default="", verbose_name="کوکی ct0 (CSRF Token)")
    proxy_url = models.CharField(max_length=255, blank=True, default="socks5h://xray:1080", verbose_name="پروکسی (SOCKS5/HTTP)")
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default="ACTIVE", db_index=True, verbose_name="وضعیت")
    status_message = models.CharField(max_length=255, blank=True, default="آماده به کار", verbose_name="پیام وضعیت")
    daily_action_count = models.IntegerField(default=0, verbose_name="تعداد سرچ‌های امروز")
    total_action_count = models.IntegerField(default=0, verbose_name="کل سرچ‌ها")
    posts_found_count = models.IntegerField(default=0, verbose_name="تعداد توییت‌های یافته‌شده")
    last_action_at = models.DateTimeField(null=True, blank=True, verbose_name="آخرین زمان فعالیت")
    cooldown_until = models.DateTimeField(null=True, blank=True, db_index=True, verbose_name="پایان استراحت")
    is_active = models.BooleanField(default=True, verbose_name="فعال در چرخه چرخش")
    created_at = models.DateTimeField(auto_now_add=True, verbose_name="زمان ثبت")
    updated_at = models.DateTimeField(auto_now=True, verbose_name="آخرین به‌روزرسانی")

    class Meta:
        db_table = "x_accounts"
        verbose_name = "اکانت اسکرپر توییتر / X"
        verbose_name_plural = "اکانت‌های اسکرپر توییتر / X"
        indexes = [
            models.Index(fields=["status", "is_active"], name="ix_x_acc_status_active"),
        ]

    def is_available(self):
        from django.utils import timezone
        if not self.is_active or self.status in ("BANNED", "DISABLED", "CHALLENGED"):
            return False
        if self.cooldown_until and self.cooldown_until > timezone.now():
            return False
        return True

    def __str__(self):
        return f"@{self.username} ({self.get_status_display()})"


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
