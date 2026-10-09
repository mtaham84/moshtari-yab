"""Pay-as-you-go: LLM providers/models with prices (read by need_engine/registry.py), wallets and their ledger."""
from decimal import Decimal

from django.conf import settings
from django.core.validators import MinValueValidator
from django.db import models

from apps.businesses.models import Business


class Provider(models.Model):
    """OpenAI-compatible endpoint (Gemini, OpenAI, OpenRouter, Avalai, …)."""

    name = models.CharField(max_length=100, unique=True, verbose_name="نام سرویس‌دهنده")
    base_url = models.URLField(max_length=300, verbose_name="Base URL",
                               help_text="مثال: https://generativelanguage.googleapis.com/v1beta/openai")
    api_key = models.CharField(max_length=500, blank=True, verbose_name="API Key")
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

    def __str__(self):
        return self.label or self.name


class BillingSettings(models.Model):
    """Single row (pk=1)."""

    usd_to_toman = models.DecimalField(max_digits=12, decimal_places=0, default=Decimal("100000"),
                                       verbose_name="نرخ دلار (تومان)")
    markup_percent = models.DecimalField(max_digits=6, decimal_places=2, default=Decimal("30"),
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
        obj, _ = cls.objects.get_or_create(pk=1)
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
