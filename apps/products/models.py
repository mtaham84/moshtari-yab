import uuid
from django.db import models
from django.utils.text import slugify
from apps.businesses.models import Business

class Category(models.Model):
    PRODUCT_TYPE_CHOICES = [
        ("PHYSICAL", "کالایی"),
        ("SERVICE", "خدماتی"),
    ]

    business = models.ForeignKey(
        Business,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="custom_categories",
        verbose_name="کسب‌وکار صاحب دسته"
    )
    name = models.CharField(max_length=150, verbose_name="نام دسته")
    slug = models.SlugField(max_length=160, blank=True, allow_unicode=True, verbose_name="شناسه یکتا (اسلاگ)")
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
    normalized_name = models.CharField(
        max_length=150,
        blank=True,
        verbose_name="نام نرمال‌شده"
    )
    full_path = models.CharField(
        max_length=500,
        blank=True,
        db_index=True,
        verbose_name="مسیر کامل دسته‌بندی"
    )
    depth = models.PositiveSmallIntegerField(
        default=1,
        verbose_name="عمق دسته‌بندی (۱ تا ۵)"
    )
    embedding = models.JSONField(
        default=list,
        blank=True,
        verbose_name="بردار تعبیه‌شده دسته (Embedding)"
    )
    embedding_model = models.CharField(
        max_length=100,
        blank=True,
        default="text-embedding-3-small",
        verbose_name="مدل بردارساز"
    )
    embedding_version = models.CharField(
        max_length=50,
        blank=True,
        default="v1",
        verbose_name="نسخه بردار"
    )
    taxonomy_version = models.PositiveIntegerField(
        default=1,
        verbose_name="نسخه تاکسونومی"
    )
    keywords = models.JSONField(
        default=list,
        blank=True,
        verbose_name="کلمات کلیدی و مترادف‌ها"
    )
    created_at = models.DateTimeField(auto_now_add=True, verbose_name="زمان ایجاد")

    class Meta:
        verbose_name = "دسته‌بندی"
        verbose_name_plural = "دسته‌بندی‌ها"
        ordering = ["product_type", "name"]

    def clean(self):
        from django.core.exceptions import ValidationError
        if self.parent:
            if self.parent_id == self.id:
                raise ValidationError("یک دسته‌بندی نمی‌تواند والد خودش باشد.")
            curr = self.parent
            d = 2
            while curr.parent:
                if curr.parent_id == self.id:
                    raise ValidationError("حلقه دورانی در ساختار درختی مجاز نیست.")
                curr = curr.parent
                d += 1
            if d > 5:
                raise ValidationError("حداکثر عمق مجاز دسته‌بندی ۵ سطح است.")

    def save(self, *args, **kwargs):
        from django.core.exceptions import ValidationError
        if not self.slug:
            base_slug = slugify(self.name, allow_unicode=True) or "cat"
            unique_suffix = uuid.uuid4().hex[:6]
            self.slug = f"{base_slug}-{unique_suffix}"

        from apps.discovery.taxonomy.normalizer import normalize_persian_text
        self.normalized_name = normalize_persian_text(self.name)

        if self.parent:
            if self.parent_id == self.id:
                raise ValidationError("دسته‌بندی نمی‌تواند والد خودش باشد.")
            self.depth = self.parent.depth + 1
            if self.depth > 5:
                raise ValidationError("حداکثر عمق مجاز دسته‌بندی ۵ سطح است.")
        else:
            self.depth = 1

        self.full_path = self.get_full_path()
        super().save(*args, **kwargs)

        # Invalidate taxonomy cache & increment version on Business if changed
        if self.business_id:
            try:
                from apps.discovery.taxonomy.cache import invalidate_seller_taxonomy_cache
                invalidate_seller_taxonomy_cache(self.business)
            except (ImportError, Exception):
                pass

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
    is_discovery_active = models.BooleanField(
        default=True,
        verbose_name="فعال در پایش روزانه هوش مصنوعی",
        help_text="آیا این کالا در سهمیه جستجوی مشتری بالقوه قرار گیرد؟"
    )
    discovery_priority = models.PositiveSmallIntegerField(
        default=1,
        verbose_name="اولویت جستجو",
        help_text="اولویت پایش از ۱ (عادی) تا ۵ (فوری)"
    )
    telegram_outreach_enabled = models.BooleanField(
        default=True,
        verbose_name="ارتباط از طریق تلگرام"
    )
    x_outreach_enabled = models.BooleanField(
        default=True,
        verbose_name="ارتباط از طریق X (توییتر)"
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

    def save(self, *args, **kwargs):
        super().save(*args, **kwargs)
        if self.business_id:
            try:
                from apps.discovery.taxonomy.cache import invalidate_seller_taxonomy_cache
                invalidate_seller_taxonomy_cache(self.business)
            except Exception:
                pass

    def delete(self, *args, **kwargs):
        b = self.business
        res = super().delete(*args, **kwargs)
        if b:
            try:
                from apps.discovery.taxonomy.cache import invalidate_seller_taxonomy_cache
                invalidate_seller_taxonomy_cache(b)
            except Exception:
                pass
        return res

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

