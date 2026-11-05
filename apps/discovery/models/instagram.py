from django.db import models
from django.utils import timezone as dj_timezone
from django.db.models.functions import Lower
from apps.businesses.models import Business
from apps.products.models import Product, Category


class InstagramPage(models.Model):
    KIND_CHOICES = [("own", "صفحهٔ خودم"), ("watched", "صفحهٔ رقیب")]

    business = models.ForeignKey(Business, on_delete=models.CASCADE, related_name="instagram_pages")
    handle = models.CharField(max_length=30)
    kind = models.CharField(max_length=10, choices=KIND_CHOICES, default="watched")
    default_product = models.ForeignKey(Product, on_delete=models.SET_NULL, null=True, blank=True,
                                        related_name="instagram_pages")
    is_active = models.BooleanField(default=True)
    last_run_at = models.DateTimeField(null=True, blank=True)
    queued_at = models.DateTimeField(null=True, blank=True)
    last_status = models.CharField(max_length=24, blank=True, default="")
    discovered_family_id = models.CharField(max_length=80, blank=True, default="", db_index=True)
    consecutive_failures = models.PositiveSmallIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [models.UniqueConstraint(Lower("handle"), "business",
                                               name="uniq_instagram_page_business_handle")]
        ordering = ["-is_active", "handle"]
        indexes = [models.Index(fields=["business", "is_active", "last_run_at"], name="igpage_active_fair_idx")]

    def __str__(self):
        return f"@{self.handle} ({self.business})"


class InstagramFamilyPlan(models.Model):
    """Cached Instagram discovery plan for one current product family."""

    STATUS_CHOICES = [("ready", "آماده"), ("failed", "ناموفق")]

    business = models.ForeignKey(Business, on_delete=models.CASCADE, related_name="instagram_family_plans")
    family_id = models.CharField(max_length=80, db_index=True)
    content_hash = models.CharField(max_length=64)
    hashtags = models.JSONField(default=list, blank=True)
    keywords = models.JSONField(default=list, blank=True)
    page_search_terms = models.JSONField(default=list, blank=True)
    buyer_signals = models.JSONField(default=list, blank=True)
    negative_signals = models.JSONField(default=list, blank=True)
    built_at = models.DateTimeField(default=dj_timezone.now)
    status = models.CharField(max_length=12, choices=STATUS_CHOICES, default="ready")
    error = models.CharField(max_length=255, blank=True, default="")

    class Meta:
        constraints = [models.UniqueConstraint(fields=["business", "family_id"], name="uniq_ig_plan_business_family")]
        indexes = [models.Index(fields=["business", "status", "built_at"], name="igplan_business_status_idx")]


class InstagramSourceStat(models.Model):
    SOURCE_CHOICES = [("page", "صفحه"), ("hashtag", "هشتگ"), ("keyword", "کلیدواژه"), ("x_query", "کوئری X")]

    business = models.ForeignKey(Business, on_delete=models.CASCADE, related_name="instagram_source_stats")
    family_id = models.CharField(max_length=80, db_index=True)
    source_type = models.CharField(max_length=12, choices=SOURCE_CHOICES)
    label = models.CharField(max_length=100)
    requests = models.PositiveIntegerField(default=0)
    leads = models.PositiveIntegerField(default=0)
    alpha = models.FloatField(default=1.0)
    beta = models.FloatField(default=1.0)
    generation_version = models.CharField(max_length=20, default="v1", db_index=True)
    last_run_at = models.DateTimeField(null=True, blank=True)

    @property
    def score(self) -> float:
        total = self.alpha + self.beta
        return round((self.alpha / total) * 100.0, 1) if total > 0 else 50.0

    @property
    def delta(self) -> float:
        return round(self.score - 50.0, 1)

    @property
    def type_display_persian(self) -> str:
        return {
            "hashtag": "هشتگ اینستاگرام",
            "keyword": "کلیدواژه اینستاگرام",
            "page": "صفحه هدف اینستاگرام",
            "x_query": "کوئری X (توییتر)"
        }.get(self.source_type, self.source_type)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["business", "family_id", "source_type", "label"],
                                                name="uniq_ig_source_stat")]
        indexes = [models.Index(fields=["business", "family_id", "source_type"], name="igstat_family_type_idx")]


class InstagramReplySuggestion(models.Model):
    """Persisted history and sent state for human-reviewed Instagram message drafts."""

    opportunity = models.ForeignKey("discovery.Opportunity", on_delete=models.CASCADE,
                                    related_name="instagram_reply_suggestions")
    product = models.ForeignKey(Product, on_delete=models.CASCADE, related_name="instagram_reply_suggestions")
    reply_variants = models.JSONField(default=dict, blank=True)
    history = models.JSONField(default=list, blank=True)
    sent = models.BooleanField(default=False)
    sent_at = models.DateTimeField(null=True, blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["opportunity", "product"], name="uniq_ig_reply_opp_product")]


class InstagramCategoryTarget(models.Model):
    business = models.ForeignKey(Business, on_delete=models.CASCADE, related_name="instagram_category_targets")
    category = models.ForeignKey(Category, on_delete=models.CASCADE, related_name="instagram_targets")
    terms = models.JSONField(default=list, blank=True)
    archived_terms = models.JSONField(default=list, blank=True)
    hashtags = models.JSONField(default=list, blank=True)
    max_posts = models.PositiveSmallIntegerField(default=5)
    window_days = models.PositiveSmallIntegerField(default=7)
    is_active = models.BooleanField(default=False)
    generation_version = models.CharField(max_length=20, default="v1", db_index=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["business", "category"], name="uniq_ig_category_target")]
        ordering = ["category__full_path"]

    def __str__(self):
        return f"{self.business}: {self.category}"


class InstagramEngineAccount(models.Model):
    ROLE_CHOICES = [
        ("SCRAPER", "اسکرپر (پایش کامنت و هشتگ)"),
        ("SENDER", "فرستنده / پاسخگو (ارسال دایرکت)"),
    ]
    STATUS_CHOICES = [
        ("ACTIVE", "فعال و متصل"),
        ("COOLDOWN", "استراحت موقت (Cooldown)"),
        ("CHALLENGED", "نیازمند تایید / کد امنیتی (Challenge)"),
        ("BANNED", "مسدود شده (Banned)"),
        ("DISABLED", "متوقف (دستی)"),
    ]
    # Why the account is not ACTIVE (status stays coarse because the workers filter on it)
    REASON_CHOICES = [
        ("", "—"),
        ("needs_code", "منتظر کد تأیید"),
        ("needs_app_approval", "باید در اپ اینستاگرام تأیید شود"),
        ("session_expired", "سشن منقضی؛ ورود دوباره لازم است"),
        ("bad_password", "پسورد اشتباه است"),
        ("proxy_down", "پروکسی/کانفیگ قطع است"),
        ("rate_limited", "محدودیت موقت اینستاگرام"),
        ("suspended", "تعلیق یا مسدود شده"),
        ("paused", "توسط مدیر متوقف شده"),
        ("network", "خطای شبکه"),
        ("error", "خطای نامشخص"),
    ]
    TIER_CHOICES = [
        ("NEW", "جدید (زیر ۳ ماه) - ۱۰ تا ۲۰ پیام در روز | ۲ تا ۳ در ساعت"),
        ("MEDIUM", "متوسط (۳ تا ۱۲ ماه) - ۳۰ تا ۵۰ پیام در روز | ۵ تا ۱۰ در ساعت"),
        ("AGED", "قدیمی و فعال (بالای ۱ سال) - ۵۰ تا ۱۰۰ پیام در روز | ۱۰ تا ۱۵ در ساعت"),
    ]

    tenant_id = models.CharField(max_length=64, default="tenant_default", db_index=True, verbose_name="شناسه مستأجر / فروشگاه")
    username = models.CharField(max_length=100, unique=True, db_index=True, verbose_name="نام کاربری اینستاگرام")
    password = models.CharField(max_length=255, verbose_name="رمز عبور")
    role = models.CharField(max_length=20, choices=ROLE_CHOICES, default="SCRAPER", db_index=True, verbose_name="نقش اکانت")
    account_tier = models.CharField(max_length=20, choices=TIER_CHOICES, default="NEW", verbose_name="سن و رده اکانت")
    daily_limit = models.PositiveIntegerField(default=18, verbose_name="سقف ارسال روزانه")
    hourly_limit = models.PositiveIntegerField(default=3, verbose_name="حداکثر سرعت در ساعت")
    hourly_action_count = models.PositiveIntegerField(default=0, verbose_name="تعداد عملیات در این ساعت")
    last_hour_reset = models.DateTimeField(null=True, blank=True, verbose_name="زمان ریست شمارنده ساعتی")
    proxy_url = models.CharField(max_length=255, null=True, blank=True, verbose_name="پروکسی (SOCKS5/HTTP)")
    # The system (xray) config this account talks through; it stays with the account until the config dies.
    # proxy_url mirrors it as socks5h://xray:<local_port>. Null for accounts with their own proxy.
    proxy_config = models.ForeignKey("discovery.ProxyConfig", null=True, blank=True, on_delete=models.SET_NULL,
                                     related_name="instagram_accounts", verbose_name="کانفیگ اختصاصی سیستم")
    session_json = models.JSONField(null=True, blank=True, verbose_name="دیتای سشن")
    device_settings = models.JSONField(null=True, blank=True, verbose_name="تنظیمات سخت‌افزار")
    # The login in progress (ig_accounts.start_login): state, token, heartbeat, contact hint, the code typed in the panel
    pending_login = models.JSONField(null=True, blank=True, verbose_name="ورود در جریان (منتظر کد)")
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default="ACTIVE", db_index=True, verbose_name="وضعیت")
    status_reason = models.CharField(max_length=32, choices=REASON_CHOICES, blank=True, default="",
                                     verbose_name="دلیل وضعیت")
    daily_action_count = models.IntegerField(default=0, verbose_name="تعداد عملیات امروز")
    last_action_at = models.DateTimeField(null=True, blank=True, verbose_name="آخرین زمان فعالیت")
    cooldown_until = models.DateTimeField(null=True, blank=True, db_index=True, verbose_name="پایان دوره استراحت")
    next_outreach_at = models.DateTimeField(null=True, blank=True, verbose_name="زودترین زمان دایرکت سرد بعدی (فاصله انسانی)")
    status_message = models.CharField(max_length=255, blank=True, default="", verbose_name="پیام آخرین وضعیت / خطا")
    created_at = models.DateTimeField(auto_now_add=True, verbose_name="زمان ایجاد")
    updated_at = models.DateTimeField(auto_now=True, verbose_name="آخرین به‌روزرسانی")

    class Meta:
        db_table = "ig_accounts"
        verbose_name = "اکانت موتور اینستاگرام"
        verbose_name_plural = "اکانت‌های موتور اینستاگرام"
        indexes = [
            models.Index(fields=["tenant_id", "role", "status"], name="ix_ig_accounts_tr_status"),
        ]

    def is_available(self):
        from django.utils import timezone
        if self.status != "ACTIVE":
            return False
        if self.cooldown_until and self.cooldown_until > timezone.now():
            return False
        return True

    @property
    def clean_proxy(self) -> str:
        """The proxy every Instagram client of this account must use, without #rotate=... fragments.

        Its own proxy or system config; an account without one goes through the shared system pool
        (INSTAGRAM_DEFAULT_PROXY_URL), never silently from the server's own IP.
        """
        from apps.discovery.ig_accounts import effective_proxy_url
        return effective_proxy_url(self)

    @property
    def proxy_meta(self) -> dict:
        """Metadata for mobile/residential proxy, IP rotation, and provider classification."""
        ds = self.device_settings or {}
        if isinstance(ds, dict) and isinstance(ds.get("proxy_meta"), dict):
            return ds["proxy_meta"]
        return {}

    @property
    def proxy_rotation_url(self) -> str:
        from apps.discovery.proxy_service import extract_rotation_url
        return extract_rotation_url(self)

    @property
    def is_mobile_proxy(self) -> bool:
        return self.proxy_meta.get("classification") == "MOBILE_4G" or bool(self.proxy_rotation_url)

    @property
    def device_info(self) -> dict:
        """Normalized device profile representation for dashboard and logging."""
        ds = self.device_settings or {}
        inner = ds.get("device_settings") if isinstance(ds.get("device_settings"), dict) else ds
        model = inner.get("model") or ds.get("model") or ""
        manufacturer = inner.get("manufacturer") or ds.get("manufacturer") or ""
        device = inner.get("device") or ds.get("device") or model
        android_release = inner.get("android_release") or ds.get("android_release") or "14"
        resolution = inner.get("resolution") or ds.get("resolution") or ""
        dpi = inner.get("dpi") or ds.get("dpi") or ""
        name = ds.get("device_name") or ""

        if not name:
            if model == "SM-S928B":
                name = "سامسونگ Galaxy S24 Ultra"
            elif model == "SM-S916B":
                name = "سامسونگ Galaxy S23+"
            elif model in ("Pixel 8 Pro", "husky"):
                name = "گوگل Pixel 8 Pro"
            elif model in ("Pixel 7 Pro", "cheetah"):
                name = "گوگل Pixel 7 Pro"
            elif model == "23116PN5BC":
                name = "شیائومی 14 Pro"
            elif model == "CPH2581":
                name = "وان‌پلاس 12"
            elif model == "XQ-DQ72":
                name = "سونی Xperia 1 V"
            elif model:
                name = f"{manufacturer.split('/')[0].strip().title()} {model}"
            else:
                name = "دیوایس شبیه‌سازی‌شده"

        color = "#1d4ed8" if ("سامسونگ" in name or "Samsung" in manufacturer) else \
                "#15803d" if ("گوگل" in name or "Pixel" in model) else \
                "#ea580c" if ("شیائومی" in name or "Xiaomi" in manufacturer) else \
                "#dc2626" if ("وان‌پلاس" in name or "OnePlus" in manufacturer) else \
                "#7c3aed" if ("سونی" in name or "Sony" in manufacturer) else "#0369a1"

        return {
            "name": name,
            "model": model,
            "device": device,
            "manufacturer": manufacturer.split('/')[0].strip().title(),
            "android_release": str(android_release),
            "resolution": resolution,
            "dpi": dpi,
            "color": color,
        }

    @property
    def tier_info(self) -> dict:
        t = self.account_tier or "NEW"
        if t == "AGED":
            return {
                "key": "AGED",
                "title": "قدیمی و فعال (بالای ۱ سال)",
                "daily_desc": "۵۰ تا ۱۰۰ پیام در روز",
                "hourly_desc": "۱۰ تا ۱۵ در ساعت",
                "delay_desc": "۴ تا ۶ دقیقه وقفه",
                "color": "#15803d",
                "bg_color": "#dcfce7",
            }
        elif t == "MEDIUM":
            return {
                "key": "MEDIUM",
                "title": "متوسط (۳ تا ۱۲ ماه)",
                "daily_desc": "۳۰ تا ۵۰ پیام در روز",
                "hourly_desc": "۵ تا ۱۰ در ساعت",
                "delay_desc": "۶ تا ۱۲ دقیقه وقفه",
                "color": "#b45309",
                "bg_color": "#fef3c7",
            }
        else:
            return {
                "key": "NEW",
                "title": "جدید (زیر ۳ ماه)",
                "daily_desc": "۱۰ تا ۲۰ پیام در روز",
                "hourly_desc": "۲ تا ۳ در ساعت",
                "delay_desc": "۲۰ تا ۳۰ دقیقه وقفه",
                "color": "#1d4ed8",
                "bg_color": "#dbeafe",
            }

    def save(self, *args, **kwargs):
        from apps.discovery.device_emulator import has_device_profile, resolve_device_settings

        if not has_device_profile(self.device_settings):
            # Reuse the phone Instagram already knows (pending login / saved session) instead of inventing a new one:
            # a fresh random device right after a login is exactly what gets sessions flagged.
            self.device_settings = resolve_device_settings(self.device_settings, self.session_json,
                                                           self.username or self.pk or "default")
            fields = kwargs.get("update_fields")
            if fields is not None and "device_settings" not in fields:
                kwargs["update_fields"] = [*fields, "device_settings"]
        super().save(*args, **kwargs)

    def __str__(self):
        return f"@{self.username} ({self.get_role_display()} - {self.get_status_display()})"


class InstagramAccountEvent(models.Model):
    """What happened to an Instagram account: logins, codes, challenges, proxy/IP changes, rests, edits."""
    KIND_CHOICES = [
        ("created", "اکانت اضافه شد"),
        ("edited", "تنظیمات ویرایش شد"),
        ("login_ok", "ورود موفق"),
        ("login_failed", "ورود ناموفق"),
        ("code_requested", "اینستاگرام کد تأیید خواست"),
        ("code_ok", "کد تأیید پذیرفته شد"),
        ("code_failed", "کد تأیید رد شد"),
        ("session_set", "سشن دستی ثبت شد"),
        ("session_expired", "سشن منقضی شد"),
        ("check_ok", "تست اتصال موفق"),
        ("check_failed", "تست اتصال ناموفق"),
        ("proxy_changed", "پروکسی/کانفیگ عوض شد"),
        ("proxy_down", "پروکسی/کانفیگ قطع شد"),
        ("ip_checked", "IP خروجی بررسی شد"),
        ("challenge", "چالش امنیتی"),
        ("rate_limited", "محدودیت موقت"),
        ("status_changed", "وضعیت عوض شد"),
        ("config_exported", "کانفیگ خروجی گرفته شد"),
    ]

    account = models.ForeignKey(InstagramEngineAccount, on_delete=models.CASCADE, related_name="events")
    kind = models.CharField(max_length=32, choices=KIND_CHOICES, db_index=True)
    message = models.CharField(max_length=255, blank=True, default="")
    data = models.JSONField(null=True, blank=True)
    actor = models.CharField(max_length=150, blank=True, default="", help_text="staff username, or empty for the system")
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        db_table = "ig_account_events"
        ordering = ["-created_at", "-id"]
        indexes = [models.Index(fields=["account", "-created_at"], name="ix_ig_events_account_time")]
        verbose_name = "رویداد اکانت اینستاگرام"
        verbose_name_plural = "رویدادهای اکانت‌های اینستاگرام"

    def __str__(self):
        return f"@{self.account_id} {self.kind} {self.created_at:%Y-%m-%d %H:%M}"


class OutreachTenantProfile(models.Model):
    """Per-customer (tenant) outreach identity: the consultant name/role the DMs speak as, and the pitch template.

    Replaces the per-username ``sender_pitches`` block of data/sarv_outreach_config.json, which is still read
    as a fallback for tenants that have no row yet.
    """
    tenant_id = models.CharField(max_length=64, unique=True, db_index=True, verbose_name="شناسه مستأجر / فروشگاه")
    consultant_name = models.CharField(max_length=100, blank=True, default="", verbose_name="نام مشاور")
    consultant_role = models.CharField(max_length=150, blank=True, default="", verbose_name="سمت مشاور")
    pitch_template = models.TextField(blank=True, default="", verbose_name="الگوی پیام معرفی")
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "outreach_tenant_profiles"
        verbose_name = "پروفایل پیام‌رسانی مشتری"
        verbose_name_plural = "پروفایل‌های پیام‌رسانی مشتری‌ها"

    def __str__(self):
        return f"{self.tenant_id}: {self.consultant_name or '-'}"


class InstagramEngineLead(models.Model):
    STATUS_CHOICES = [
        ("NEW", "جدید (در صف ارسال اولویت‌دار)"),
        ("DM_SENT", "دایرکت ارسال شد (در انتظار پاسخ)"),
        ("REPLIED", "پاسخ مشتری دریافت شد"),
        ("FOLLOWUP_SENT", "پیام دوم ارسال شد"),
        ("FAILED", "ارسال ناموفق"),
        ("SKIPPED", "رد شده (نامعتبر یا گاردریل اسپم)"),
    ]

    tenant_id = models.CharField(max_length=64, default="tenant_default", db_index=True, verbose_name="شناسه مستأجر")
    ig_user_pk = models.CharField(max_length=64, db_index=True, verbose_name="شناسه عددی کاربر اینستاگرام")
    username = models.CharField(max_length=100, db_index=True, verbose_name="نام کاربری لید")
    full_name = models.CharField(max_length=255, null=True, blank=True, verbose_name="نام کاربر")
    profile_url = models.CharField(max_length=255, null=True, blank=True, verbose_name="لینک پروفایل")
    comment_text = models.TextField(verbose_name="متن کامنت خریدار")
    source_post_url = models.CharField(max_length=255, null=True, blank=True, verbose_name="لینک پست مبدا")
    post_caption_snippet = models.TextField(null=True, blank=True, verbose_name="خلاصه کپشن پست")
    profile_bio = models.TextField(blank=True, default="", verbose_name="بیوی عمومیِ دریافت‌شده همراه کامنت")
    commenter_is_business = models.BooleanField(null=True, blank=True, verbose_name="حساب کاری طبق اطلاعات عمومی")
    follower_count = models.PositiveIntegerField(null=True, blank=True, verbose_name="تعداد دنبال‌کنندگانِ دریافت‌شده")
    matched_cta = models.CharField(max_length=100, null=True, blank=True, verbose_name="کال‌تواکشن منطبق")
    intent_score = models.FloatField(default=0.85, db_index=True, verbose_name="ضریب احتمال خرید")
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default="NEW", db_index=True, verbose_name="وضعیت لید")

    # Priority Scoring & Freshness System (Max 35 points: 15 Public + 15 Intent + 5 Freshness)
    comment_created_at = models.DateTimeField(null=True, blank=True, db_index=True, verbose_name="زمان ارسال کامنت")
    is_private = models.BooleanField(default=False, db_index=True, verbose_name="حساب خصوصی (Private)")
    comment_intent_type = models.CharField(
        max_length=32,
        choices=[
            ("QUESTION_INQUIRY", "سوال و استعلام نیاز"),
            ("DIRECT_SHORT_CTA", "پیام مستقیم و کوتاه"),
            ("GENERAL_BUYER", "قصد خرید عمومی"),
            ("OTHER", "سایر"),
        ],
        default="DIRECT_SHORT_CTA",
        db_index=True,
        verbose_name="نوع کامنت و نیت"
    )
    outreach_score = models.IntegerField(default=0, db_index=True, verbose_name="امتیاز اولویت دایرکت (تا ۴۵ امتیاز)")
    success_probability = models.PositiveIntegerField(default=0, db_index=True, verbose_name="درصد احتمال موفقیت لید (۰ تا ۱۰۰)")
    score_breakdown = models.JSONField(default=dict, blank=True, verbose_name="جزئیات و تفکیک امتیاز")

    # Source Page (Target Account) & Post Meta
    source_page_username = models.CharField(max_length=100, blank=True, default="", db_index=True, verbose_name="نام کاربری پیج مبدا (ناشر پست)")
    source_page_title = models.CharField(max_length=255, blank=True, default="", verbose_name="عنوان یا نام کامل پیج مبدا")
    post_created_at = models.DateTimeField(null=True, blank=True, db_index=True, verbose_name="زمان انتشار پست مبدا")

    # Outreach tracking & multi-turn conversations
    sender_account = models.CharField(max_length=100, blank=True, default="", verbose_name="اکانت ارسال‌کننده پیام اول")
    initial_dm_text = models.TextField(blank=True, default="", verbose_name="متن پیام اول ارسالی")
    initial_dm_at = models.DateTimeField(null=True, blank=True, verbose_name="زمان ارسال پیام اول")
    has_replied = models.BooleanField(default=False, db_index=True, verbose_name="پاسخ داده است")
    reply_text = models.TextField(blank=True, default="", verbose_name="متن آخرین پاسخ مشتری")
    reply_at = models.DateTimeField(null=True, blank=True, db_index=True, verbose_name="زمان دریافت پاسخ")
    second_message_text = models.TextField(blank=True, default="", verbose_name="متن پیام دوم / پیگیری")
    second_sender_username = models.CharField(max_length=100, blank=True, default="", verbose_name="اکانت ارسال‌کننده پیام دوم")
    second_message_at = models.DateTimeField(null=True, blank=True, verbose_name="زمان ارسال پیام دوم")
    conversation_history = models.JSONField(default=list, blank=True, verbose_name="تاریخچه گفتگو")

    created_at = models.DateTimeField(auto_now_add=True, verbose_name="زمان استخراج")
    updated_at = models.DateTimeField(auto_now=True, verbose_name="آخرین به‌روزرسانی")

    class Meta:
        db_table = "leads"
        verbose_name = "لید اینستاگرام"
        verbose_name_plural = "لیدهای اینستاگرام"
        constraints = [
            models.UniqueConstraint(fields=["tenant_id", "ig_user_pk"], name="uq_tenant_lead_pk"),
        ]
        indexes = [
            models.Index(fields=["tenant_id", "status"], name="ix_leads_t_status"),
            models.Index(fields=["intent_score"], name="ix_leads_intent_score"),
            models.Index(fields=["tenant_id", "status", "-outreach_score"], name="ix_leads_t_stat_score"),
            models.Index(fields=["outreach_score"], name="ix_leads_outreach_score"),
            models.Index(fields=["comment_created_at"], name="ix_leads_comment_created"),
            models.Index(fields=["success_probability"], name="ix_leads_success_prob"),
            models.Index(fields=["tenant_id", "-outreach_score", "-comment_created_at"], name="ix_leads_t_score_time"),
        ]

    def __str__(self):
        return f"@{self.username} (احتمال خرید: {int(self.intent_score * 100)}% - {self.get_status_display()})"


class InstagramCrawledPost(models.Model):
    post_hash = models.CharField(max_length=64, unique=True, db_index=True, verbose_name="هش شناسه پست")
    media_id = models.CharField(max_length=64, db_index=True, verbose_name="شناسه پست اینستاگرام")
    media_code = models.CharField(max_length=32, blank=True, verbose_name="کد کوتاه پست")
    account_username = models.CharField(max_length=100, verbose_name="اکانت اسکرپر")
    posted_at = models.DateTimeField(db_index=True, verbose_name="زمان انتشار پست")
    crawled_at = models.DateTimeField(auto_now_add=True, db_index=True, verbose_name="زمان ثبت کرول")
    caption = models.TextField(blank=True, default="")
    comments = models.JSONField(default=list, blank=True)
    comment_count = models.PositiveIntegerField(default=0)
    comments_cached = models.BooleanField(default=False)

    class Meta:
        verbose_name = "پست کرول‌شده اینستاگرام"
        verbose_name_plural = "پست‌های کرول‌شده اینستاگرام"
        indexes = [
            models.Index(fields=["crawled_at"], name="ix_ig_crawled_at"),
            models.Index(fields=["posted_at"], name="ix_ig_posted_at"),
            models.Index(fields=["post_hash"], name="ix_ig_post_hash"),
        ]

    def __str__(self):
        return f"Post {self.media_code or self.media_id} by @{self.account_username} ({self.post_hash[:8]})"


class InstagramCrawlExecution(models.Model):
    """
    Audit log of each discovery crawl execution per query/hashtag and scraper account.
    Tracks exact Persian Jalali date, Tehran time, account used, and discovery metrics.
    """
    STATUS_CHOICES = [
        ("SUCCESS", "موفق"),
        ("NO_POSTS", "بدون پست جدید"),
        ("THROTTLED", "محدودیت موقت"),
        ("FAILED", "خطا"),
        ("REMOVED_UNQUALIFIED", "حذف‌شده (کمتر از ۳ پست واجد شرایط)"),
    ]

    business = models.ForeignKey(
        Business,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="instagram_crawl_executions",
        verbose_name="کسب‌وکار مستأجر",
    )
    tenant_id = models.CharField(
        max_length=64,
        default="tenant_default",
        db_index=True,
        verbose_name="شناسه مستأجر",
    )
    query_text = models.CharField(
        max_length=255,
        db_index=True,
        verbose_name="کوئری یا هشتگ",
    )
    target_type = models.CharField(
        max_length=50,
        default="HASHTAG",
        choices=[("HASHTAG", "هشتگ"), ("KEYWORD", "کلمه کلیدی"), ("PAGE", "صفحه هدف")],
        verbose_name="نوع هدف",
    )
    account_username = models.CharField(
        max_length=150,
        db_index=True,
        verbose_name="نام کاربری اکانت اسکرپر",
    )
    account = models.ForeignKey(
        "discovery.InstagramEngineAccount",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="crawl_executions",
        verbose_name="اکانت ثبت‌شده",
    )
    status = models.CharField(
        max_length=50,
        choices=STATUS_CHOICES,
        default="SUCCESS",
        verbose_name="وضعیت اجرا",
    )
    posts_inspected = models.PositiveIntegerField(
        default=0,
        verbose_name="پست‌های بررسی‌شده",
    )
    requests = models.PositiveSmallIntegerField(default=1, verbose_name="تعداد درخواست‌های API")
    qualifying_posts = models.PositiveIntegerField(
        default=0,
        verbose_name="پست‌های حائز شرایط (کامنت >= ۵۰)",
    )
    leads_found = models.PositiveIntegerField(
        default=0,
        verbose_name="لیدهای یافت‌شده",
    )
    message = models.TextField(
        blank=True,
        verbose_name="پیام و جزییات نتیجه",
    )
    crawled_at = models.DateTimeField(
        default=dj_timezone.now,
        db_index=True,
        verbose_name="زمان کرول (UTC)",
    )

    class Meta:
        verbose_name = "لاگ کرول اینستاگرام"
        verbose_name_plural = "لاگ‌های کرول اینستاگرام"
        ordering = ["-crawled_at"]
        indexes = [
            models.Index(fields=["crawled_at"], name="ix_ig_exec_crawled_at"),
            models.Index(fields=["tenant_id", "query_text"], name="ix_ig_exec_tenant_query"),
            models.Index(fields=["account_username"], name="ix_ig_exec_account"),
        ]

    def __str__(self):
        return f"{self.query_text} (@{self.account_username}) - {self.crawled_at_jalali}"

    @property
    def crawled_at_tehran(self):
        import zoneinfo
        tz = zoneinfo.ZoneInfo("Asia/Tehran")
        if self.crawled_at:
            return self.crawled_at.astimezone(tz)
        return None

    @property
    def crawled_at_jalali(self) -> str:
        from apps.core.jalali import format_jalali_date, to_persian_digits
        if not self.crawled_at:
            return "—"
        dt = self.crawled_at_tehran
        date_part = format_jalali_date(dt.date(), persian_digits=True)
        time_part = to_persian_digits(f"{dt.hour:02d}:{dt.minute:02d}:{dt.second:02d}")
        return f"{date_part} ساعت {time_part}"
