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
