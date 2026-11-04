import inspect
from django.db import models
from apps.businesses.models import Business
from apps.products.models import Category
from ..sources import GLOBAL, PRIVATE, SCOPE_CHOICES, display_link, normalize_link, sync_error_message


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
