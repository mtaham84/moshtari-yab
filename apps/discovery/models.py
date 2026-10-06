import hashlib
from django.db import models
from apps.businesses.models import Business
from apps.products.models import Product, Category

class CategoryBranchMemory(models.Model):
    category = models.ForeignKey(
        Category,
        on_delete=models.CASCADE,
        related_name="branch_memories",
        verbose_name="شاخه دسته‌بندی"
    )
    business = models.ForeignKey(
        Business,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="branch_memories",
        verbose_name="کسب‌وکار مرتبط"
    )
    keywords = models.JSONField(
        default=list,
        blank=True,
        verbose_name="کلیدواژه‌های قصد خرید شاخه"
    )
    negative_keywords = models.JSONField(
        default=list,
        blank=True,
        verbose_name="کلمات کلیدی منفی (عدم تطابق)"
    )
    last_scanned_at = models.DateTimeField(
        auto_now=True,
        verbose_name="آخرین زمان پایش"
    )
    total_scanned_count = models.PositiveIntegerField(
        default=0,
        verbose_name="تعداد پیام‌های بررسی‌شده"
    )
    leads_found_count = models.PositiveIntegerField(
        default=0,
        verbose_name="تعداد سرنخ‌های کشف‌شده"
    )

    class Meta:
        verbose_name = "حافظه پایش شاخه دسته‌بندی"
        verbose_name_plural = "حافظه‌های پایش شاخه‌ها"

    def __str__(self):
        return f"حافظه شاخه: {self.category.name}"


class ProcessedMessageHash(models.Model):
    fingerprint = models.CharField(
        max_length=64,
        unique=True,
        db_index=True,
        verbose_name="چکیده یکتای پیام"
    )
    channel = models.CharField(
        max_length=20,
        choices=[("TELEGRAM", "تلگرام"), ("X", "X / توییتر")],
        verbose_name="کانال"
    )
    created_at = models.DateTimeField(
        auto_now_add=True,
        verbose_name="زمان ثبت"
    )

    class Meta:
        verbose_name = "هش پیام پردازش‌شده"
        verbose_name_plural = "هش پیام‌های پردازش‌شده"

    @classmethod
    def calculate_hash(cls, channel: str, user_handle: str, text: str) -> str:
        raw = f"{channel.upper()}:{user_handle.strip().lower()}:{text.strip()}"
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()


class DiscoveredLead(models.Model):
    STATUS_CHOICES = [
        ("NEW", "جدید (بررسی نشده)"),
        ("CONTACTED", "پیام ارسال شد"),
        ("CONVERTED", "مشتری نهایی"),
        ("IGNORED", "نادیده گرفته شد"),
    ]

    CHANNEL_CHOICES = [
        ("TELEGRAM", "تلگرام"),
        ("X", "X (توییتر)"),
    ]

    OUTREACH_MODE_CHOICES = [
        ("DIRECT", "دایرکت خصوصی"),
        ("COMMENT", "کامنت / ریپلای عمومی"),
    ]

    OUTREACH_STATUS_CHOICES = [
        ("DRAFT", "پیش‌نویس پیام"),
        ("SENT", "ارسال موفق"),
        ("FAILED", "خطا در ارسال"),
    ]

    business = models.ForeignKey(
        Business,
        on_delete=models.CASCADE,
        related_name="discovered_leads",
        verbose_name="کسب‌وکار"
    )
    product = models.ForeignKey(
        Product,
        on_delete=models.CASCADE,
        related_name="leads",
        verbose_name="کالای منطبق"
    )
    channel = models.CharField(
        max_length=20,
        choices=CHANNEL_CHOICES,
        verbose_name="شبکه اجتماعی"
    )
    lead_handle = models.CharField(
        max_length=150,
        verbose_name="شناسه کاربری مشتری"
    )
    lead_display_name = models.CharField(
        max_length=150,
        blank=True,
        verbose_name="نام کاربر"
    )
    post_url = models.URLField(
        blank=True,
        verbose_name="لینک پست یا گفت‌وگو"
    )
    content_snippet = models.TextField(
        verbose_name="متن درخواست یا سوال مشتری"
    )
    intent_score = models.PositiveSmallIntegerField(
        default=30,
        verbose_name="احتمال خرید (درصد)",
        help_text="حداقل ۳۰ درصد برای ثبت در سیستم"
    )
    intent_reasoning = models.TextField(
        blank=True,
        verbose_name="تحلیل هوش مصنوعی و علت انطباق"
    )
    matched_branch = models.CharField(
        max_length=255,
        blank=True,
        verbose_name="شاخه درختی منطبق"
    )
    outreach_mode = models.CharField(
        max_length=20,
        choices=OUTREACH_MODE_CHOICES,
        default="COMMENT",
        verbose_name="نحوه ارتباط"
    )
    outreach_message = models.TextField(
        blank=True,
        verbose_name="پیام ارسالی هوشمند ایجنت"
    )
    outreach_status = models.CharField(
        max_length=20,
        choices=OUTREACH_STATUS_CHOICES,
        default="SENT",
        verbose_name="وضعیت ارسال پیام"
    )
    sent_from_handle = models.CharField(
        max_length=150,
        blank=True,
        verbose_name="ارسال‌شده از حساب یا بات پیدا"
    )
    bot_agent_name = models.CharField(
        max_length=100,
        default="بات پیدا (@peyda_bot)",
        verbose_name="ایجنت / بات ارسال‌کننده"
    )
    customer_reply = models.TextField(
        blank=True,
        verbose_name="پاسخ دریافت‌شده از مشتری"
    )
    customer_reply_at = models.DateTimeField(
        null=True,
        blank=True,
        verbose_name="زمان دریافت پاسخ مشتری"
    )
    message_count = models.PositiveSmallIntegerField(
        default=1,
        verbose_name="تعداد پیام‌های تبادل‌شده",
        help_text="حداکثر سقف مجاز: ۱۰ پیام"
    )
    is_conversation_capped = models.BooleanField(
        default=False,
        verbose_name="رسیدن به سقف ۱۰ پیام"
    )
    guardrail_status = models.CharField(
        max_length=50,
        default="SAFE_IN_DOMAIN",
        verbose_name="وضعیت انطباق کانتکست محصول"
    )
    direct_link_sent = models.CharField(
        max_length=255,
        blank=True,
        verbose_name="لینک مستقیم محصول ارسالی در کامنت"
    )
    status = models.CharField(
        max_length=20,
        choices=STATUS_CHOICES,
        default="NEW",
        verbose_name="وضعیت سرنخ"
    )
    discovered_at = models.DateTimeField(
        auto_now_add=True,
        verbose_name="زمان کشف"
    )
    updated_at = models.DateTimeField(
        auto_now=True,
        verbose_name="آخرین وضعیت"
    )

    class Meta:
        verbose_name = "سرنخ کشف‌شده"
        verbose_name_plural = "سرنخ‌های کشف‌شده"
        ordering = ["-intent_score", "-discovered_at"]

    @property
    def intent_priority_rank(self) -> int:
        """
        1: READY_TO_BUY (85-100)
        2: COMPARING (60-84)
        3: INITIAL_NEED (30-59)
        """
        if self.intent_score >= 85:
            return 1
        if self.intent_score >= 60:
            return 2
        return 3

    @property
    def intent_stage_display(self) -> str:
        if self.intent_score >= 85:
            return "آماده خرید / تصمیم نهایی"
        if self.intent_score >= 60:
            return "در حال مقایسه و ارزیابی"
        return "ابراز نیاز اولیه"

    def __str__(self):
        return f"{self.lead_handle} ({self.intent_score}%) - {self.product.name}"


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
    lead = models.ForeignKey(
        DiscoveredLead,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="orders",
        verbose_name="سرنخ خریدار مرتبط"
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

