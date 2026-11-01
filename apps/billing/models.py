"""Pay-as-you-go: LLM providers/models with prices (read by need_engine/registry.py), wallets and their ledger."""
from decimal import Decimal

from django.conf import settings
from django.core.validators import MinValueValidator
from django.db import models

from apps.businesses.models import Business
from .fields import EncryptedTextField


class Provider(models.Model):
    """OpenAI-compatible endpoint (Gemini, OpenAI, OpenRouter, Avalai, …)."""

    name = models.CharField(max_length=100, unique=True, verbose_name="نام سرویس‌دهنده")
    base_url = models.URLField(max_length=300, verbose_name="Base URL",
                               help_text="مثال: https://generativelanguage.googleapis.com/v1beta/openai")
    # Fernet adds roughly one third of the plaintext size plus its token metadata.
    api_key = EncryptedTextField(max_length=12000, blank=True, verbose_name="API Key")
    is_active = models.BooleanField(default=True, verbose_name="فعال")
    notes = models.CharField(max_length=255, blank=True, verbose_name="یادداشت")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "سرویس‌دهنده‌ی مدل"
        verbose_name_plural = "سرویس‌دهنده‌های مدل"
        ordering = ["name"]

    @property
    def masked_key(self) -> str:
        k = self.api_key or ""
        return "—" if not k else ("•" * 6 + k[-4:] if len(k) > 8 else "•" * len(k))

    def __str__(self):
        return self.name


class AIModel(models.Model):
    """One model of a provider. Prices are USD per 1M tokens; roles pick which pipeline steps use it."""

    provider = models.ForeignKey(Provider, on_delete=models.CASCADE, related_name="models", verbose_name="سرویس‌دهنده")
    name = models.CharField(max_length=150, verbose_name="شناسه مدل", help_text="همان نامی که به API فرستاده می‌شود")
    label = models.CharField(max_length=150, blank=True, verbose_name="نام نمایشی")
    input_price_usd = models.DecimalField(max_digits=10, decimal_places=4, default=Decimal("0"),
                                          validators=[MinValueValidator(0)], verbose_name="قیمت ورودی ($ / ۱M توکن)")
    output_price_usd = models.DecimalField(max_digits=10, decimal_places=4, default=Decimal("0"),
                                           validators=[MinValueValidator(0)], verbose_name="قیمت خروجی ($ / ۱M توکن)")
    use_extract = models.BooleanField(default=False, verbose_name="استخراج نیاز / کارت محصول")
    use_verify = models.BooleanField(default=False, verbose_name="تطبیق و راستی‌آزمایی")
    use_reply = models.BooleanField(default=False, verbose_name="نوشتن پاسخ")
    priority = models.PositiveSmallIntegerField(default=100, verbose_name="اولویت",
                                                help_text="عدد کمتر = اولویت بیشتر وقتی چند مدل یک نقش دارند")
    rpm = models.PositiveIntegerField(null=True, blank=True, verbose_name="سقف درخواست در دقیقه")
    tpm = models.PositiveIntegerField(null=True, blank=True, verbose_name="سقف توکن در دقیقه")
    rpd = models.PositiveIntegerField(null=True, blank=True, verbose_name="سقف درخواست در روز")
    is_active = models.BooleanField(default=True, verbose_name="فعال")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "مدل هوش مصنوعی"
        verbose_name_plural = "مدل‌های هوش مصنوعی"
        ordering = ["priority", "name"]
        constraints = [models.UniqueConstraint(fields=["provider", "name"], name="unique_model_per_provider")]

    @property
    def roles_display(self) -> str:
        roles = [n for on, n in ((self.use_extract, "استخراج"), (self.use_verify, "تطبیق"), (self.use_reply, "پاسخ")) if on]
        return "، ".join(roles) or "—"

    @property
    def identity(self) -> str:
        return f"{self.provider.name}:{self.name}".lower()

    @property
    def model_ref(self):
        from apps.core.gateway_contract import ModelRef
        return ModelRef(provider_name=self.provider.name, model_name=self.name)

    def __str__(self):
        return self.label or self.name


class BillingSettings(models.Model):
    """Single row (pk=1)."""

    usd_to_toman = models.DecimalField(max_digits=12, decimal_places=0, default=Decimal("268000"),
                                       verbose_name="نرخ دلار (تومان)")
    markup_percent = models.DecimalField(max_digits=6, decimal_places=2, default=Decimal("150.0"),
                                         validators=[MinValueValidator(0)], verbose_name="سود روی هزینه‌ی واقعی (٪)")
    charge_cached = models.BooleanField(default=False, verbose_name="کسر هزینه برای پاسخ‌های کش‌شده",
                                        help_text="پاسخ کش‌شده برای ما هزینه‌ای ندارد؛ پیش‌فرض: رایگان برای فروشنده")
    enforce_balance = models.BooleanField(default=True, verbose_name="توقف تحلیل با اتمام اعتبار")
    min_balance_toman = models.DecimalField(max_digits=14, decimal_places=0, default=Decimal("0"),
                                            verbose_name="حداقل اعتبار برای ادامه‌ی تحلیل (تومان)")
    signup_credit_toman = models.DecimalField(max_digits=14, decimal_places=0, default=Decimal("0"),
                                              validators=[MinValueValidator(0)], verbose_name="اعتبار هدیه‌ی ثبت‌نام (تومان)")
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "تنظیمات پرداخت"
        verbose_name_plural = "تنظیمات پرداخت"

    @classmethod
    def get(cls) -> "BillingSettings":
        obj, created = cls.objects.get_or_create(
            pk=1,
            defaults={"usd_to_toman": Decimal("268000"), "markup_percent": Decimal("150.0")}
        )
        return obj

    @property
    def multiplier(self) -> Decimal:
        return 1 + self.markup_percent / 100

    def __str__(self):
        return "تنظیمات پرداخت"


class Wallet(models.Model):
    business = models.OneToOneField(Business, on_delete=models.CASCADE, related_name="wallet", verbose_name="کسب‌وکار")
    balance_toman = models.DecimalField(max_digits=18, decimal_places=2, default=Decimal("0"), verbose_name="موجودی (تومان)")
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "کیف پول"
        verbose_name_plural = "کیف پول‌ها"

    def __str__(self):
        return f"{self.business.name}: {self.balance_toman:,.0f}"


class WalletTransaction(models.Model):
    TOPUP, USAGE, ADJUST, REFUND, GIFT = "TOPUP", "USAGE", "ADJUST", "REFUND", "GIFT"
    KIND_CHOICES = [(TOPUP, "شارژ"), (USAGE, "مصرف"), (ADJUST, "اصلاح دستی"), (REFUND, "بازگشت وجه"), (GIFT, "هدیه")]

    wallet = models.ForeignKey(Wallet, on_delete=models.CASCADE, related_name="transactions", verbose_name="کیف پول")
    kind = models.CharField(max_length=10, choices=KIND_CHOICES, verbose_name="نوع")
    amount_toman = models.DecimalField(max_digits=18, decimal_places=2, verbose_name="مبلغ (تومان)",
                                       help_text="مثبت = افزایش اعتبار، منفی = کسر")
    balance_after = models.DecimalField(max_digits=18, decimal_places=2, verbose_name="موجودی بعد از تراکنش")
    cost_usd = models.DecimalField(max_digits=14, decimal_places=6, default=Decimal("0"), verbose_name="هزینه‌ی واقعی ($)")
    calls = models.PositiveIntegerField(default=0, verbose_name="تعداد فراخوانی")
    description = models.CharField(max_length=255, blank=True, verbose_name="توضیح")
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
                                   related_name="+", verbose_name="ثبت‌کننده")
    created_at = models.DateTimeField(auto_now_add=True, db_index=True, verbose_name="زمان")

    class Meta:
        verbose_name = "تراکنش کیف پول"
        verbose_name_plural = "تراکنش‌های کیف پول"
        ordering = ["-created_at", "-id"]

    def __str__(self):
        return f"{self.get_kind_display()} {self.amount_toman:,.0f}"


class CostEvent(models.Model):
    """
    Authoritative record of a single LLM invocation attempt (P06.02 / R14, R31).
    Immutable ledger of token usage, model reference, pricing snapshot, and calculated costs.
    """
    operation_id = models.CharField(max_length=128, db_index=True, verbose_name="شناسه عملیات")
    attempt_id = models.CharField(max_length=128, unique=True, db_index=True, verbose_name="شناسه تلاش")
    provider_name = models.CharField(max_length=100, verbose_name="نام سرویس‌دهنده")
    model_name = models.CharField(max_length=150, verbose_name="شناسه مدل")
    stage = models.CharField(max_length=100, verbose_name="مرحله فراخوانی")
    prompt_tokens = models.PositiveIntegerField(default=0, verbose_name="توکن ورودی")
    completion_tokens = models.PositiveIntegerField(default=0, verbose_name="توکن خروجی")
    total_tokens = models.PositiveIntegerField(default=0, verbose_name="مجموع توکن")
    is_cached = models.BooleanField(default=False, verbose_name="کش‌شده")
    is_estimated = models.BooleanField(default=False, verbose_name="تخمینی")
    is_unknown = models.BooleanField(default=False, verbose_name="نامشخص")
    price_version = models.CharField(max_length=64, blank=True, verbose_name="نسخه قیمت‌گذاری")
    usd_to_toman_rate = models.DecimalField(max_digits=12, decimal_places=0, verbose_name="نرخ دلار (تومان)")
    markup_multiplier = models.DecimalField(max_digits=6, decimal_places=4, default=Decimal("1.5000"), verbose_name="ضریب سود")
    total_cost_usd = models.DecimalField(max_digits=14, decimal_places=6, default=Decimal("0.000000"), verbose_name="هزینه کل ($)")
    total_cost_toman = models.DecimalField(max_digits=18, decimal_places=2, default=Decimal("0.00"), verbose_name="هزینه کل (تومان)")
    created_at = models.DateTimeField(auto_now_add=True, db_index=True, verbose_name="زمان ثبت")

    class Meta:
        verbose_name = "رویداد هزینه مدل"
        verbose_name_plural = "رویدادهای هزینه مدل"
        ordering = ["-created_at", "-id"]

    def __str__(self):
        return f"{self.operation_id}:{self.attempt_id} ({self.total_cost_toman:,.0f} تومان)"


class CostAllocation(models.Model):
    """
    Atomic share of a CostEvent allocated to a payer (business or platform) (P06.02 / R14, R31).
    """
    STATUS_PENDING = "PENDING"
    STATUS_SETTLED = "SETTLED"
    STATUS_WAIVED = "WAIVED"
    STATUS_UNRESOLVED = "UNRESOLVED"
    STATUS_CHOICES = [
        (STATUS_PENDING, "در انتظار تسویه"),
        (STATUS_SETTLED, "تسویه شده"),
        (STATUS_WAIVED, "رایگان/بخشوده"),
        (STATUS_UNRESOLVED, "نیازمند بررسی"),
    ]

    cost_event = models.ForeignKey(CostEvent, on_delete=models.CASCADE, related_name="allocations", verbose_name="رویداد هزینه")
    business = models.ForeignKey(Business, on_delete=models.SET_NULL, null=True, blank=True,
                                 related_name="cost_allocations", verbose_name="کسب‌وکار پرداخت‌کننده")
    part_index = models.PositiveSmallIntegerField(default=0, verbose_name="شاخص سهم")
    payer_count = models.PositiveSmallIntegerField(default=1, verbose_name="تعداد پرداخت‌کنندگان")
    charge_factor = models.DecimalField(max_digits=6, decimal_places=2, default=Decimal("1.00"), verbose_name="ضریب شارژ")
    allocated_usd = models.DecimalField(max_digits=14, decimal_places=6, default=Decimal("0.000000"), verbose_name="سهم هزینه ($)")
    allocated_toman = models.DecimalField(max_digits=18, decimal_places=2, default=Decimal("0.00"), verbose_name="سهم هزینه (تومان)")
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default=STATUS_PENDING, db_index=True, verbose_name="وضعیت تسویه")
    created_at = models.DateTimeField(auto_now_add=True, db_index=True, verbose_name="زمان ثبت")

    class Meta:
        verbose_name = "تخصیص سهم هزینه"
        verbose_name_plural = "تخصیص‌های سهم هزینه"
        ordering = ["cost_event", "part_index"]
        constraints = [
            models.UniqueConstraint(fields=["cost_event", "part_index"], name="unique_allocation_per_event_part")
        ]

    def __str__(self):
        payer_str = self.business.name if self.business else "پلتفرم"
        return f"{self.cost_event.operation_id} [{payer_str}] -> {self.allocated_toman:,.0f} تومان"


class WalletReservation(models.Model):
    """
    Atomic reservation of wallet balance before initiating paid LLM calls (P06.03 / R32).
    """
    STATUS_HELD = "HELD"
    STATUS_SETTLED = "SETTLED"
    STATUS_RELEASED = "RELEASED"
    STATUS_EXPIRED = "EXPIRED"
    STATUS_UNRESOLVED = "UNRESOLVED"
    STATUS_CHOICES = [
        (STATUS_HELD, "نگهداری شده"),
        (STATUS_SETTLED, "تسویه شده"),
        (STATUS_RELEASED, "آزاد شده"),
        (STATUS_EXPIRED, "منقضی شده"),
        (STATUS_UNRESOLVED, "نیازمند بررسی"),
    ]

    reservation_id = models.CharField(max_length=128, unique=True, db_index=True, verbose_name="شناسه رزرو")
    operation_id = models.CharField(max_length=128, db_index=True, verbose_name="شناسه عملیات")
    wallet = models.ForeignKey(Wallet, on_delete=models.CASCADE, related_name="reservations", verbose_name="کیف پول")
    reserved_amount_toman = models.DecimalField(max_digits=18, decimal_places=2, verbose_name="مبلغ رزرو شده (تومان)")
    estimated_cost_usd = models.DecimalField(max_digits=14, decimal_places=6, default=Decimal("0.000000"), verbose_name="هزینه تخمینی ($)")
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default=STATUS_HELD, db_index=True, verbose_name="وضعیت")
    expires_at = models.DateTimeField(db_index=True, verbose_name="زمان انقضا")
    created_at = models.DateTimeField(auto_now_add=True, db_index=True, verbose_name="زمان ایجاد")
    updated_at = models.DateTimeField(auto_now=True, verbose_name="زمان به‌روزرسانی")

    class Meta:
        verbose_name = "رزرو اعتبار کیف پول"
        verbose_name_plural = "رزروهای اعتبار کیف پول"
        ordering = ["-created_at", "-id"]

    def __str__(self):
        return f"Reserve {self.reservation_id}: {self.reserved_amount_toman:,.0f} تومان ({self.status})"


class CreditWaitingQueue(models.Model):
    """
    Persistent queue for work items waiting for wallet top-up (P06.04 / R32, R07).
    Single authoritative source of truth for waiting_for_credit.
    """
    STATUS_WAITING = "WAITING"
    STATUS_RESUMED = "RESUMED"
    STATUS_CANCELLED = "CANCELLED"
    STATUS_EXPIRED = "EXPIRED"
    STATUS_CHOICES = [
        (STATUS_WAITING, "در انتظار شارژ"),
        (STATUS_RESUMED, "ازسرگیری شده"),
        (STATUS_CANCELLED, "لغو شده"),
        (STATUS_EXPIRED, "منقضی شده"),
    ]

    work_item_id = models.CharField(max_length=128, unique=True, db_index=True, verbose_name="شناسه کار")
    operation_id = models.CharField(max_length=128, db_index=True, verbose_name="شناسه عملیات")
    business = models.ForeignKey(Business, on_delete=models.CASCADE, related_name="waiting_credit_items", verbose_name="کسب‌وکار")
    stage = models.CharField(max_length=100, verbose_name="مرحله عملیات")
    payload = models.JSONField(default=dict, verbose_name="داده کار")
    estimated_toman = models.DecimalField(max_digits=18, decimal_places=2, default=Decimal("0.00"), verbose_name="مبلغ مورد نیاز (تومان)")
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default=STATUS_WAITING, db_index=True, verbose_name="وضعیت")
    retry_count = models.PositiveSmallIntegerField(default=0, verbose_name="تعداد تلاش")
    last_checked_at = models.DateTimeField(null=True, blank=True, verbose_name="آخرین بررسی")
    created_at = models.DateTimeField(auto_now_add=True, db_index=True, verbose_name="زمان ثبت")
    updated_at = models.DateTimeField(auto_now=True, verbose_name="زمان به‌روزرسانی")

    class Meta:
        verbose_name = "کار در انتظار اعتبار"
        verbose_name_plural = "کارهای در انتظار اعتبار"
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.work_item_id} [{self.business.name}] - {self.status}"


class SettlementMapping(models.Model):
    """
    Discrete 1:1 mapping between CostAllocation and Wallet settlement (P06.05 / R31, R32).
    Eliminates reliance on MAX(id) cursor synchronization.
    """
    allocation = models.OneToOneField(CostAllocation, on_delete=models.CASCADE, related_name="settlement", verbose_name="تخصیص هزینه")
    reservation = models.ForeignKey(WalletReservation, on_delete=models.SET_NULL, null=True, blank=True,
                                    related_name="settlements", verbose_name="رزرو اعتبار")
    transaction = models.ForeignKey(WalletTransaction, on_delete=models.SET_NULL, null=True, blank=True,
                                    related_name="settlements", verbose_name="تراکنش کیف پول")
    settled_amount_toman = models.DecimalField(max_digits=18, decimal_places=2, verbose_name="مبلغ تسویه شده (تومان)")
    settled_at = models.DateTimeField(auto_now_add=True, db_index=True, verbose_name="زمان تسویه")

    class Meta:
        verbose_name = "نگاشت تسویه هزینه"
        verbose_name_plural = "نگاشت‌های تسویه هزینه"
        ordering = ["-settled_at"]

    def __str__(self):
        return f"Settlement for {self.allocation_id}: {self.settled_amount_toman:,.0f} تومان"

