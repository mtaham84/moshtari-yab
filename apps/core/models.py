from django.core.validators import MaxValueValidator
from django.db import models


class SiteAppearance(models.Model):
    """Single row edited in /admin/: the photo behind the panels and its darkening."""

    background_image = models.ImageField(
        upload_to="site/", blank=True, null=True, verbose_name="تصویر پس‌زمینه پنل و داشبوردها",
        help_text="ترجیحاً افقی و باکیفیت (حداقل ۱۹۲۰×۱۰۸۰). اگر خالی باشد تصویر پیش‌فرض استفاده می‌شود.",
    )
    overlay_percent = models.PositiveSmallIntegerField(
        default=20, validators=[MaxValueValidator(80)], verbose_name="تیرگی روی تصویر (۰ تا ۸۰ درصد)",
        help_text="برای خوانایی بهتر متن روی تصویرهای روشن مقدار را بیشتر کنید.",
    )
    updated_at = models.DateTimeField(auto_now=True, verbose_name="آخرین تغییر")

    class Meta:
        verbose_name = "ظاهر سایت"
        verbose_name_plural = "ظاهر سایت"

    def __str__(self):
        return "ظاهر سایت"

    def save(self, *args, **kwargs):
        self.pk = 1   # singleton
        super().save(*args, **kwargs)

    @classmethod
    def current(cls):
        return cls.objects.filter(pk=1).first()


class LLMUsageLog(models.Model):
    """Every direct model call made by the web app (taxonomy, product organising …), so it shows in model usage lists."""

    model = models.CharField(max_length=80, db_index=True)
    purpose = models.CharField(max_length=40, db_index=True)
    business_id = models.PositiveIntegerField(null=True, blank=True)
    tokens_in = models.PositiveIntegerField(default=0)
    tokens_out = models.PositiveIntegerField(default=0)
    cost_usd = models.DecimalField(max_digits=12, decimal_places=6, default=0)
    ok = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ["-created_at"]
