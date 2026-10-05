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
        default="DIRECT",
        verbose_name="نحوه ارتباط"
    )
    outreach_message = models.TextField(
        blank=True,
        verbose_name="پیش‌نویس پیام ارتباطی هوشمند"
    )
    outreach_status = models.CharField(
        max_length=20,
        choices=OUTREACH_STATUS_CHOICES,
        default="DRAFT",
        verbose_name="وضعیت ارسال پیام"
    )
    sent_from_handle = models.CharField(
        max_length=150,
        blank=True,
        verbose_name="ارسال‌شده از حساب فروشنده"
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

    def __str__(self):
        return f"{self.lead_handle} ({self.intent_score}%) - {self.product.name}"
