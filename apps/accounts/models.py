from datetime import timedelta
from django.db import models
from django.utils import timezone

class EmailVerification(models.Model):
    MAX_ATTEMPTS = 5
    BLOCK_DURATION_MINUTES = 2
    EXPIRATION_MINUTES = 10

    email = models.EmailField(db_index=True, verbose_name="ایمیل")
    code = models.CharField(max_length=6, verbose_name="کد تأیید")
    session_data = models.JSONField(default=dict, verbose_name="اطلاعات ثبت‌نام")
    created_at = models.DateTimeField(auto_now_add=True, verbose_name="زمان ایجاد")
    expires_at = models.DateTimeField(verbose_name="زمان انقضا")
    attempts = models.PositiveIntegerField(default=0, verbose_name="تعداد تلاش‌های ناموفق")
    last_attempt_at = models.DateTimeField(null=True, blank=True, verbose_name="زمان آخرین تلاش")
    blocked_until = models.DateTimeField(null=True, blank=True, verbose_name="مسدود تا")
    is_verified = models.BooleanField(default=False, verbose_name="تأیید شده")

    class Meta:
        verbose_name = "احراز هویت ایمیل"
        verbose_name_plural = "احراز هویت‌های ایمیل"
        ordering = ["-created_at"]

    def is_expired(self):
        return timezone.now() > self.expires_at

    def is_blocked(self):
        """
        Returns (is_blocked_bool, remaining_seconds)
        """
        if self.blocked_until and timezone.now() < self.blocked_until:
            remaining = int((self.blocked_until - timezone.now()).total_seconds())
            return True, remaining
        return False, 0

    def record_failed_attempt(self):
        """
        Increments attempts. If attempts >= MAX_ATTEMPTS, blocks for BLOCK_DURATION_MINUTES.
        """
        now = timezone.now()
        self.attempts += 1
        self.last_attempt_at = now
        if self.attempts >= self.MAX_ATTEMPTS:
            self.blocked_until = now + timedelta(minutes=self.BLOCK_DURATION_MINUTES)
        self.save(update_fields=["attempts", "last_attempt_at", "blocked_until"])

    def reset_attempts_if_unblocked(self):
        """
        If the block period has passed, reset attempts counter.
        """
        if self.blocked_until and timezone.now() >= self.blocked_until:
            self.attempts = 0
            self.blocked_until = None
            self.save(update_fields=["attempts", "blocked_until"])

    def __str__(self):
        return f"{self.email} - Code: {self.code} - Attempts: {self.attempts}"


class ActivationRequest(models.Model):
    STATUS_PENDING = "PENDING"
    STATUS_APPROVED = "APPROVED"
    STATUS_ACTIVATED = "ACTIVATED"
    STATUS_REJECTED = "REJECTED"

    STATUS_CHOICES = [
        (STATUS_PENDING, "در انتظار بررسی"),
        (STATUS_APPROVED, "تایید شده"),
        (STATUS_ACTIVATED, "فعال‌شده و ایجاد حساب"),
        (STATUS_REJECTED, "رد شده"),
    ]

    full_name = models.CharField(max_length=150, verbose_name="نام و نام خانوادگی")
    company_name = models.CharField(max_length=200, verbose_name="نام کسب‌وکار / شرکت")
    phone_number = models.CharField(max_length=30, verbose_name="شماره تماس / موبایل")
    email = models.EmailField(verbose_name="ایمیل کاری")
    team_size = models.CharField(max_length=50, blank=True, verbose_name="اندازه تیم")
    problem_statement = models.TextField(blank=True, verbose_name="شرح چالش‌ها و نیازمندی‌ها")

    status = models.CharField(
        max_length=20,
        choices=STATUS_CHOICES,
        default=STATUS_PENDING,
        db_index=True,
        verbose_name="وضعیت درخواست"
    )

    created_user = models.ForeignKey(
        "auth.User",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="activation_requests",
        verbose_name="حساب کاربری ایجادشده"
    )
    created_business = models.ForeignKey(
        "businesses.Business",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="activation_requests",
        verbose_name="کسب‌وکار متناظر"
    )
    admin_notes = models.TextField(blank=True, verbose_name="یادداشت‌های مدیر")
    created_at = models.DateTimeField(auto_now_add=True, db_index=True, verbose_name="تاریخ ثبت")
    updated_at = models.DateTimeField(auto_now=True, verbose_name="آخرین به‌روزرسانی")

    class Meta:
        verbose_name = "درخواست فعال‌سازی و دمو"
        verbose_name_plural = "درخواست‌های فعال‌سازی و دمو"
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.full_name} ({self.company_name}) - {self.get_status_display()}"

