import uuid
from django.db import models, transaction
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
    keywords = models.JSONField(
        default=list,
        blank=True,
        verbose_name="کلمات کلیدی و مترادف‌ها"
    )
    origin = models.ForeignKey("self", null=True, blank=True, on_delete=models.SET_NULL, related_name="copies",
                               verbose_name="دستهٔ سیستمی منبع")
    source = models.CharField(max_length=10, default="system", verbose_name="منبع (system / ai / user)")
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

            # Tenant isolation:
            # - If tenant category: parent must be same tenant or system category (business=None)
            # - If system category: parent MUST be system category
            if self.business_id:
                if self.parent.business_id and self.parent.business_id != self.business_id:
                    raise ValidationError("والد دسته‌بندی باید متعلق به همین کسب‌وکار یا یک دسته سیستمی عمومی باشد.")
            else:
                if self.parent.business_id:
                    raise ValidationError("یک دسته‌بندی عمومی/سیستمی نمی‌تواند والد اختصاصی مستأجر داشته باشد.")

            # Product type consistency
            if self.parent.product_type != self.product_type:
                raise ValidationError(
                    f"نوع ماهیتی دسته ({self.get_product_type_display()}) با والد ({self.parent.get_product_type_display()}) مطابقت ندارد."
                )

            # Cycle detection & depth limit (traversing upward through all ancestors)
            curr = self.parent
            d = 1
            visited = {self.id} if self.id else set()
            while curr:
                if curr.id in visited:
                    raise ValidationError("حلقه دورانی در ساختار درختی مجاز نیست.")
                visited.add(curr.id)
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

        from need_engine.text import norm
        self.normalized_name = norm(self.name)

        if self.parent and self.product_type == "PHYSICAL" and self.parent.product_type != "PHYSICAL":
            self.product_type = self.parent.product_type

        update_fields = kwargs.get("update_fields")
        tree_fields_touched = (
            update_fields is None
            or "parent" in update_fields
            or "parent_id" in update_fields
            or "product_type" in update_fields
            or "business" in update_fields
            or "name" in update_fields
        )

        if tree_fields_touched:
            self.clean()
            if self.parent:
                self.depth = self.parent.depth + 1
            else:
                self.depth = 1

        self.full_path = self.get_full_path()

        if self.business_id and tree_fields_touched:
            with transaction.atomic():
                Business.objects.select_for_update().filter(pk=self.business_id).exists()
                super().save(*args, **kwargs)
        else:
            super().save(*args, **kwargs)

    def get_ancestors(self, max_depth: int = 10):
        """Returns ancestor categories from root down to parent with cycle protection."""
        ancestors = []
        curr = self.parent
        visited = {self.id} if self.id else set()
        count = 0
        while curr and count < max_depth:
            if curr.id in visited:
                import logging
                logging.getLogger("apps.products").error(
                    "CATEGORY_CYCLE_DETECTED: Loop encountered at category id=%s (parent_id=%s) while getting ancestors for %s",
                    curr.id,
                    curr.parent_id,
                    self.id,
                )
                break
            visited.add(curr.id)
            ancestors.insert(0, curr)
            curr = curr.parent
            count += 1
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
    url = models.URLField(max_length=2048, blank=True, verbose_name="لینک صفحه محصول")
    attributes = models.JSONField(
        default=dict,
        blank=True,
        verbose_name="ویژگی‌های اختصاصی دسته"
    )
    target_customer = models.TextField(
        blank=True,
        verbose_name="خریداران ایده‌آل (ICP)"
    )
    instagram_terms = models.JSONField(default=list, blank=True, verbose_name="واژه‌های مرتبط اینستاگرام")
    instagram_hashtags = models.JSONField(default=list, blank=True, verbose_name="هشتگ‌های هدف اینستاگرام")
    instagram_discovery_enabled = models.BooleanField(default=False, verbose_name="پایش هشتگی اینستاگرام")
    instagram_max_posts = models.PositiveSmallIntegerField(default=5, verbose_name="سقف پست هشتگی اینستاگرام")
    instagram_window_days = models.PositiveSmallIntegerField(default=7, verbose_name="بازهٔ پست هشتگی اینستاگرام (روز)")
    agent_card_override = models.JSONField(
        null=True,
        blank=True,
        verbose_name="برداشت ایجنت (ویرایش‌شده توسط فروشنده)",
        help_text="خالی = نسخه‌ی خود ایجنت. پر = همین متن مستقیم در تطبیق استفاده می‌شود."
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
    x_search_enabled = models.BooleanField(
        default=False,
        verbose_name="جستجوی مشتری در X (توییتر)",
        help_text="برای این محصول جداگانه در X جستجو و تحلیل می‌شود؛ هزینه از کیف پول کسر می‌شود.",
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
        constraints = [
            models.UniqueConstraint(
                fields=["product"],
                condition=models.Q(is_main=True),
                name="unique_main_image_per_product",
            )
        ]

    def save(self, *args, **kwargs):
        from apps.products.models import Product
        if self.product_id:
            with transaction.atomic():
                Product.objects.select_for_update().filter(pk=self.product_id).exists()
                if self.is_main:
                    ProductImage.objects.filter(product_id=self.product_id, is_main=True).exclude(pk=self.pk).update(is_main=False)
                else:
                    # If this product has no other image, this image must be the main image
                    has_other_main = ProductImage.objects.filter(product_id=self.product_id, is_main=True).exclude(pk=self.pk).exists()
                    if not has_other_main and not ProductImage.objects.filter(product_id=self.product_id).exclude(pk=self.pk).exists():
                        self.is_main = True
                super().save(*args, **kwargs)
        else:
            super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        from apps.core.image_service import schedule_file_cleanup
        from apps.products.models import Product
        was_main = self.is_main
        product_id = self.product_id
        storage = self.image.storage if self.image else None
        image_name = self.image.name if self.image else None
        super().delete(*args, **kwargs)
        if storage and image_name:
            schedule_file_cleanup(storage, image_name)
        if was_main and product_id:
            try:
                with transaction.atomic():
                    Product.objects.select_for_update().filter(pk=product_id).exists()
                    if not ProductImage.objects.filter(product_id=product_id, is_main=True).exists():
                        next_img = ProductImage.objects.filter(product_id=product_id).order_by("order", "id").first()
                        if next_img:
                            next_img.is_main = True
                            next_img.save(update_fields=["is_main"])
            except Exception:
                pass

    def __str__(self):
        return f"تصویر {self.id} برای {self.product.name} ({'اصلی' if self.is_main else 'فرعی'})"


class TaxonomyJob(models.Model):
    """
    Durable taxonomy organisation job (P07.03 / R24).
    Replaces unmanaged threading.Thread daemon with persistent, leased, retry-limited job records.
    """
    STATUS_PENDING = "PENDING"
    STATUS_RUNNING = "RUNNING"
    STATUS_COMPLETED = "COMPLETED"
    STATUS_FAILED = "FAILED"
    STATUS_DEAD_LETTER = "DEAD_LETTER"
    STATUS_CHOICES = [
        (STATUS_PENDING, "در انتظار"),
        (STATUS_RUNNING, "در حال اجرا"),
        (STATUS_COMPLETED, "تکمیل شده"),
        (STATUS_FAILED, "ناموفق"),
        (STATUS_DEAD_LETTER, "خطای دائم (Dead-Letter)"),
    ]

    job_key = models.CharField(max_length=128, unique=True, db_index=True, verbose_name="کلید یکتای کار")
    business = models.ForeignKey(
        "businesses.Business",
        on_delete=models.CASCADE,
        related_name="taxonomy_jobs",
        verbose_name="کسب‌وکار",
    )
    catalog_version = models.PositiveIntegerField(default=1, verbose_name="نسخه کاتالوگ")
    status = models.CharField(
        max_length=20,
        choices=STATUS_CHOICES,
        default=STATUS_PENDING,
        db_index=True,
        verbose_name="وضعیت",
    )
    lease_owner = models.CharField(max_length=100, blank=True, null=True, verbose_name="شناسه مجری")
    lease_expires_at = models.DateTimeField(blank=True, null=True, db_index=True, verbose_name="انقضای قفل")
    attempts = models.PositiveSmallIntegerField(default=0, verbose_name="تعداد تلاش")
    max_attempts = models.PositiveSmallIntegerField(default=3, verbose_name="حداکثر تلاش مجاز")
    result = models.JSONField(blank=True, null=True, verbose_name="نتیجه پردازش")
    error_message = models.TextField(blank=True, verbose_name="پیام آخرین خطا")
    created_at = models.DateTimeField(auto_now_add=True, db_index=True, verbose_name="زمان ایجاد")
    updated_at = models.DateTimeField(auto_now=True, verbose_name="زمان به‌روزرسانی")

    class Meta:
        verbose_name = "کار دسته‌بندی پایدار"
        verbose_name_plural = "کارهای دسته‌بندی پایدار"
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.job_key} [{self.status}]"


