from django.db import models
from apps.businesses.models import Business

class Category(models.Model):
    PRODUCT_TYPE_CHOICES = [
        ("PHYSICAL", "کالایی"),
        ("SERVICE", "خدماتی"),
    ]

    name = models.CharField(max_length=150, verbose_name="نام دسته")
    slug = models.SlugField(max_length=160, unique=True, allow_unicode=True, verbose_name="شناسه یکتا (اسلاگ)")
    parent = models.ForeignKey(
        "self",
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="children",
        verbose_name="دسته والد"
    )
    product_type = models.CharField(
        max_length=20,
        choices=PRODUCT_TYPE_CHOICES,
        default="PHYSICAL",
        verbose_name="نوع ماهیتی"
    )
    description = models.TextField(blank=True, verbose_name="توضیحات دسته")
    suggested_attributes = models.JSONField(
        default=list,
        blank=True,
        verbose_name="ویژگی‌های پیش‌فرض این دسته",
        help_text="لیست کلیدها و برچسب‌های ویژگی‌ها به صورت JSON"
    )
    is_active = models.BooleanField(default=True, verbose_name="فعال")
    created_at = models.DateTimeField(auto_now_add=True, verbose_name="زمان ایجاد")

    class Meta:
        verbose_name = "دسته‌بندی"
        verbose_name_plural = "دسته‌بندی‌ها"
        ordering = ["product_type", "name"]

    def get_ancestors(self):
        """Returns ancestor categories from root down to parent."""
        ancestors = []
        curr = self.parent
        while curr:
            ancestors.insert(0, curr)
            curr = curr.parent
        return ancestors

    def get_full_path(self):
        """Returns human-readable path: e.g. پوشاک > مردانه > شلوار"""
        parts = [c.name for c in self.get_ancestors()] + [self.name]
        return " > ".join(parts)

    def __str__(self):
        return f"{self.get_full_path()} ({self.get_product_type_display()})"


class Product(models.Model):
    PRODUCT_TYPE_CHOICES = [
        ("PHYSICAL", "کالایی"),
        ("SERVICE", "خدماتی"),
    ]

    STATUS_CHOICES = [
        ("ACTIVE", "فعال برای کشف"),
        ("INACTIVE", "غیرفعال"),
    ]

    business = models.ForeignKey(
        Business,
        on_delete=models.CASCADE,
        related_name="products",
        verbose_name="کسب‌وکار"
    )
    name = models.CharField(max_length=255, verbose_name="نام محصول یا خدمت")
    product_type = models.CharField(
        max_length=20,
        choices=PRODUCT_TYPE_CHOICES,
        default="PHYSICAL",
        verbose_name="نوع محصول"
    )
    category = models.ForeignKey(
        Category,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="products",
        verbose_name="دسته‌بندی سلسله‌مراتبی"
    )
    description = models.TextField(verbose_name="شرح مزایا و کاربرد محصول")
    price = models.DecimalField(
        max_digits=12,
        decimal_places=0,
        null=True,
        blank=True,
        verbose_name="قیمت تقریبی (تومان)"
    )
    url = models.URLField(blank=True, verbose_name="لینک صفحه محصول")
    attributes = models.JSONField(
        default=dict,
        blank=True,
        verbose_name="ویژگی‌های اختصاصی دسته"
    )
    target_customer = models.TextField(
        blank=True,
        verbose_name="خریداران ایده‌آل (ICP)"
    )
    status = models.CharField(
        max_length=20,
        choices=STATUS_CHOICES,
        default="ACTIVE",
        verbose_name="وضعیت"
    )
    created_at = models.DateTimeField(auto_now_add=True, verbose_name="تاریخ ایجاد")
    updated_at = models.DateTimeField(auto_now=True, verbose_name="آخرین به‌روزرسانی")

    class Meta:
        verbose_name = "محصول / خدمت"
        verbose_name_plural = "محصولات و خدمات"
        ordering = ["-created_at"]

    def formatted_price(self):
        if not self.price:
            return "توافقی"
        persian_digits = str.maketrans("0123456789", "۰۱۲۳۴۵۶۷۸۹")
        formatted = f"{self.price:,}".replace(",", "،").translate(persian_digits)
        return f"{formatted} تومان"

    @property
    def main_image(self):
        """Returns the main image or first image, or None."""
        return self.images.filter(is_main=True).first() or self.images.first()

    @property
    def images_count(self):
        return self.images.count()

    @property
    def can_add_images(self):
        return self.images.count() < 10

    @property
    def remaining_image_slots(self):
        return max(0, 10 - self.images.count())

    def get_attributes_display(self):
        """Returns list of (key, label, value) tuples."""
        if not self.attributes:
            return []
        items = []
        for k, v in self.attributes.items():
            items.append({"key": k, "value": v})
        return items

    def __str__(self):
        return f"{self.name} - {self.business.name}"


class ProductImage(models.Model):
    MAX_PER_PRODUCT = 10

    product = models.ForeignKey(
        Product,
        on_delete=models.CASCADE,
        related_name="images",
        verbose_name="محصول مرتبط"
    )
    image = models.ImageField(
        upload_to="products/%Y/%m/",
        verbose_name="فایل تصویر"
    )
    is_main = models.BooleanField(
        default=False,
        verbose_name="تصویر اصلی"
    )
    order = models.PositiveSmallIntegerField(
        default=0,
        verbose_name="ترتیب نمایش"
    )
    created_at = models.DateTimeField(
        auto_now_add=True,
        verbose_name="زمان بارگذاری"
    )

    class Meta:
        verbose_name = "تصویر محصول"
        verbose_name_plural = "تصاویر محصولات"
        ordering = ["-is_main", "order", "id"]

    def save(self, *args, **kwargs):
        if self.is_main and self.product_id:
            ProductImage.objects.filter(product_id=self.product_id, is_main=True).exclude(pk=self.pk).update(is_main=False)
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        was_main = self.is_main
        product = self.product
        storage = self.image.storage if self.image else None
        path = self.image.path if self.image and hasattr(self.image, "path") else None
        super().delete(*args, **kwargs)
        if storage and path and storage.exists(path):
            try:
                storage.delete(path)
            except Exception:
                pass
        if was_main and product:
            first_img = product.images.first()
            if first_img:
                first_img.is_main = True
                first_img.save(update_fields=["is_main"])

    def __str__(self):
        return f"تصویر {self.id} برای {self.product.name} ({'اصلی' if self.is_main else 'فرعی'})"

