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
        constraints = [
            # one identity per platform account inside a business (a Telegram id and an Instagram pk can collide)
            models.UniqueConstraint(
                fields=["business", "source_platform", "external_user_id"],
                condition=~models.Q(external_user_id=""),
                name="uniq_customer_per_platform",
            ),
        ]

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
    expires_at = models.DateTimeField(null=True, blank=True, verbose_name="زمان انقضای فرصت")
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
    created_at = models.DateTimeField(auto_now_add=True, verbose_name="زمان ایجاد فرصت")
    updated_at = models.DateTimeField(auto_now=True, verbose_name="آخرین به‌روزرسانی")

    class Meta:
        verbose_name = "فرصت فروش"
        verbose_name_plural = "فرصت‌های فروش"
        ordering = ["-created_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["business", "source_platform", "source_message_id"],
                condition=models.Q(source_platform="instagram") & ~models.Q(source_message_id=""),
                name="uniq_ig_opportunity_business_message",
            ),
            models.UniqueConstraint(
                fields=["business", "engine_opportunity_id"],
                condition=~models.Q(engine_opportunity_id=""),
                name="uniq_opportunity_business_engine_id",
            ),
        ]

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

    @property
    def instagram_lead(self):
        if hasattr(self, "_cached_ig_lead"):
            return self._cached_ig_lead
        lead = None
        if isinstance(self.trace_metadata, dict):
            lead_id = self.trace_metadata.get("instagram", {}).get("lead_id")
            if lead_id:
                from apps.discovery.models import InstagramEngineLead
                lead = InstagramEngineLead.objects.filter(id=lead_id).first()
        self._cached_ig_lead = lead
        return lead

    @property
    def instagram_comment_created_at(self):
        from django.utils.dateparse import parse_datetime
        if isinstance(self.trace_metadata, dict):
            ig = self.trace_metadata.get("instagram", {})
            c_at = ig.get("comment_created_at")
            if c_at:
                if isinstance(c_at, str):
                    parsed = parse_datetime(c_at)
                    if parsed:
                        return parsed
                return c_at
        lead = self.instagram_lead
        if lead and lead.comment_created_at:
            return lead.comment_created_at
        if self.source_message_timestamp:
            return self.source_message_timestamp
        return None

    @property
    def instagram_comment_created_at_jalali(self) -> str:
        dt = self.instagram_comment_created_at
        if not dt:
            return ""
        from apps.core.jalali import format_jalali_datetime
        try:
            return format_jalali_datetime(dt)
        except Exception:
            return str(dt)[:19]

    @property
    def instagram_source_page_username(self) -> str:
        if isinstance(self.trace_metadata, dict):
            ig = self.trace_metadata.get("instagram", {})
            u = ig.get("source_page_username")
            if u:
                return u
        lead = self.instagram_lead
        if lead and lead.source_page_username:
            return lead.source_page_username
        if isinstance(self.trace_metadata, dict):
            ig = self.trace_metadata.get("instagram", {})
            return ig.get("source_label", "")
        return ""

    @property
    def instagram_source_page_title(self) -> str:
        if isinstance(self.trace_metadata, dict):
            ig = self.trace_metadata.get("instagram", {})
            t = ig.get("source_page_title")
            if t:
                return t
        lead = self.instagram_lead
        if lead and lead.source_page_title:
            return lead.source_page_title
        return ""

    @property
    def instagram_source_page_url(self) -> str:
        u = self.instagram_source_page_username
        return f"https://www.instagram.com/{u.lstrip('@')}/" if u else ""

    @property
    def instagram_post_created_at(self):
        from django.utils.dateparse import parse_datetime
        if isinstance(self.trace_metadata, dict):
            ig = self.trace_metadata.get("instagram", {})
            p_at = ig.get("post_created_at")
            if p_at:
                if isinstance(p_at, str):
                    parsed = parse_datetime(p_at)
                    if parsed:
                        return parsed
                return p_at
        lead = self.instagram_lead
        if lead and lead.post_created_at:
            return lead.post_created_at
        return None

    @property
    def instagram_post_created_at_jalali(self) -> str:
        dt = self.instagram_post_created_at
        if not dt:
            return ""
        from apps.core.jalali import format_jalali_datetime
        try:
            return format_jalali_datetime(dt)
        except Exception:
            return str(dt)[:19]

    @property
    def is_private_account(self) -> bool:
        if isinstance(self.trace_metadata, dict):
            ig = self.trace_metadata.get("instagram", {})
            if "is_private" in ig and ig["is_private"] is not None:
                return bool(ig["is_private"])
        lead = self.instagram_lead
        if lead and lead.is_private is not None:
            return bool(lead.is_private)
        if self.customer and isinstance(self.customer.metadata, dict):
            return bool(self.customer.metadata.get("is_private", False))
        return False

    @property
    def display_page_type(self) -> str:
        return "خصوصی (Private)" if self.is_private_account else "عمومی (Public)"

    @property
    def outreach_score_val(self) -> int:
        if isinstance(self.trace_metadata, dict):
            ig = self.trace_metadata.get("instagram", {})
            if "outreach_score" in ig and ig["outreach_score"]:
                return int(ig["outreach_score"])
        lead = self.instagram_lead
        if lead and lead.outreach_score:
            return int(lead.outreach_score)
        if hasattr(self, "ai_analysis") and self.ai_analysis and self.ai_analysis.intent_score:
            return int(round(float(self.ai_analysis.intent_score) * 45.0))
        return int(round((self.intent_score_percentage / 100.0) * 45))

    @property
    def success_probability_pct(self) -> int:
        if isinstance(self.trace_metadata, dict):
            ig = self.trace_metadata.get("instagram", {})
            if "success_probability" in ig and ig["success_probability"]:
                return int(ig["success_probability"])
        lead = self.instagram_lead
        if lead and lead.success_probability:
            return int(lead.success_probability)
        score = self.outreach_score_val
        is_priv = self.is_private_account
        from apps.discovery.instagram_scoring import calculate_success_probability
        age_days = 0.0
        c_at = self.instagram_comment_created_at
        if c_at and hasattr(c_at, "timestamp"):
            import django.utils.timezone as dj_tz
            try:
                age_days = max(0.0, (dj_tz.now() - c_at).total_seconds() / 86400.0)
            except Exception:
                age_days = 0.0
        return calculate_success_probability(
            outreach_score=score,
            is_private=is_priv,
            intent_type=self.intent_type or "OTHER",
            age_days=age_days,
            comment_text=self.source_raw_message or "",
        )


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


class EngineSyncCursor(models.Model):
    """Last ``need_engine.opportunities.seq`` imported by ``manage.py sync_opportunities``."""

    name = models.CharField(max_length=64, unique=True, default="opportunities")
    position = models.BigIntegerField(default=0)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"{self.name}: {self.position}"
