from django.conf import settings
from django.db import models

class Business(models.Model):
    BUSINESS_TYPE_CHOICES = [
        ("PHYSICAL", "کالایی / فروشگاهی"),
        ("SERVICE", "خدماتی"),
        ("ONLINE", "آنلاین / دیجیتال"),
        ("HYBRID", "ترکیبی (کالایی و خدماتی)"),
    ]

    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="business",
        verbose_name="مالک کسب‌وکار"
    )
    name = models.CharField(max_length=255, verbose_name="نام کسب‌وکار")
    business_type = models.CharField(
        max_length=50,
        choices=BUSINESS_TYPE_CHOICES,
        default="PHYSICAL",
        verbose_name="نوع کسب‌وکار"
    )
    business_domain = models.CharField(
        max_length=150,
        verbose_name="حوزه فعالیت",
        help_text="مثال: پوشاک و مد، برنامه‌نویسی و IT، آموزش آنلاین، لوازم دیجیتال"
    )
    description = models.TextField(blank=True, verbose_name="توضیحات و ارزش پیشنهادی")
    location = models.CharField(max_length=150, blank=True, verbose_name="موقعیت جغرافیایی / شهر")
    target_customer_description = models.TextField(
        blank=True,
        verbose_name="شرح مشتریان ایده‌آل (ICP)"
    )
    created_at = models.DateTimeField(auto_now_add=True, verbose_name="تاریخ ایجاد")
    updated_at = models.DateTimeField(auto_now=True, verbose_name="آخرین به‌روزرسانی")

    class Meta:
        verbose_name = "کسب‌وکار"
        verbose_name_plural = "کسب‌وکارها"

    def __str__(self):
        return f"{self.name} ({self.get_business_type_display()})"
