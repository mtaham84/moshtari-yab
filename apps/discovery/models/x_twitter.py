import hashlib
from datetime import timedelta
from django.db import models
from django.utils import timezone as dj_timezone
from apps.businesses.models import Business
from apps.products.models import Product, Category


class XSearchQuery(models.Model):
    KIND = [(x, x) for x in ("auto_intent", "auto_problem", "auto_product", "manual", "suggested")]
    STATE = [(x, x) for x in ("active", "paused_manual", "paused_auto", "suggested", "rejected", "stale", "archived")]
    business = models.ForeignKey(Business, on_delete=models.CASCADE, related_name="x_search_queries")
    product = models.ForeignKey(Product, on_delete=models.SET_NULL, null=True, blank=True, related_name="x_search_queries")
    category = models.ForeignKey(Category, on_delete=models.SET_NULL, null=True, blank=True, related_name="x_search_queries")
    text = models.CharField(max_length=100)
    normalized_text = models.CharField(max_length=100)
    kind = models.CharField(max_length=20, choices=KIND, default="manual")
    state = models.CharField(max_length=20, choices=STATE, default="active", db_index=True)
    weight = models.FloatField(default=1.0)
    generation_version = models.CharField(max_length=20, default="v1", db_index=True)
    domain = models.CharField(max_length=50, blank=True, default="")
    state_reason = models.CharField(max_length=255, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    last_state_change_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["business", "normalized_text"], name="uniq_x_query_business_normalized")]
        indexes = [models.Index(fields=["business", "state"], name="xquery_business_state_idx")]


class XNegativeTerm(models.Model):
    business = models.ForeignKey(Business, on_delete=models.CASCADE, related_name="x_negative_terms")
    product = models.ForeignKey(Product, on_delete=models.CASCADE, null=True, blank=True, related_name="x_negative_terms")
    term = models.CharField(max_length=40)
    normalized_term = models.CharField(max_length=40)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["business", "product", "normalized_term"], name="uniq_x_negative_term")]


class XCollectorState(models.Model):
    STATUSES = [(x, x) for x in ("idle", "collecting", "rate_limited", "daily_cap", "circuit_open", "auth_failed", "failed", "unknown")]
    id = models.PositiveSmallIntegerField(primary_key=True, default=1, editable=False)
    status = models.CharField(max_length=20, choices=STATUSES, default="unknown")
    cooldown_until = models.DateTimeField(null=True, blank=True)
    collected_today = models.PositiveIntegerField(default=0)
    daily_cap = models.PositiveIntegerField(default=0)
    last_cycle_at = models.DateTimeField(null=True, blank=True)
    last_cycle_collected = models.PositiveIntegerField(default=0)
    failures_last_cycle = models.PositiveIntegerField(default=0)
    updated_at = models.DateTimeField(auto_now=True)


class XQueryDaily(models.Model):
    day = models.DateField()
    query_text = models.CharField(max_length=100)
    fetched = models.PositiveIntegerField(default=0)
    new_posts = models.PositiveIntegerField(default=0)
    dropped = models.JSONField(default=dict)
    analysed = models.PositiveIntegerField(default=0)
    needs = models.PositiveIntegerField(default=0)
    opportunities = models.PositiveIntegerField(default=0)
    cost_toman = models.FloatField(default=0)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["day", "query_text"], name="uniq_x_query_daily")]
        indexes = [models.Index(fields=["day", "query_text"], name="xquerydaily_day_query_idx")]


class XPostFeedback(models.Model):
    business = models.ForeignKey(Business, on_delete=models.CASCADE, related_name="x_post_feedback")
    tweet_id = models.CharField(max_length=40)
    verdict = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["business", "tweet_id"], name="uniq_x_post_feedback")]


class XQueryScore(models.Model):
    business = models.ForeignKey(Business, on_delete=models.CASCADE, related_name="x_query_scores")
    query = models.ForeignKey(XSearchQuery, on_delete=models.CASCADE, related_name="scores")
    computed_at = models.DateTimeField(auto_now_add=True)
    metrics = models.JSONField(default=dict)
    score = models.FloatField(default=0)
    recommendation = models.CharField(max_length=40, default="insufficient_data")


class XCrawledPost(models.Model):
    """
    Tracks crawled X (Twitter) tweets with SHA-256 hash deduplication.
    Strictly isolated from Instagram hashes by prefixing with 'x_tweet:'.
    Has a 10-day TTL (tweets older than 10 days are excluded and pruned).
    """
    post_hash = models.CharField(max_length=64, unique=True, db_index=True, verbose_name="هش شناسه توییت")
    tweet_id = models.CharField(max_length=64, unique=True, db_index=True, verbose_name="شناسه توییت X")
    author_handle = models.CharField(max_length=100, blank=True, verbose_name="اکانت توییت")
    posted_at = models.DateTimeField(null=True, blank=True, db_index=True, verbose_name="زمان انتشار توییت")
    crawled_at = models.DateTimeField(auto_now_add=True, db_index=True, verbose_name="زمان ثبت کرول")
    tweet_url = models.CharField(max_length=255, blank=True, verbose_name="آدرس توییت")

    class Meta:
        verbose_name = "توییت کرول‌شده X"
        verbose_name_plural = "توییت‌های کرول‌شده X"
        indexes = [
            models.Index(fields=["crawled_at"], name="ix_x_crawled_at"),
            models.Index(fields=["posted_at"], name="ix_x_posted_at"),
            models.Index(fields=["post_hash"], name="ix_x_post_hash"),
            models.Index(fields=["tweet_id"], name="ix_x_tweet_id"),
        ]

    def __str__(self):
        return f"Tweet {self.tweet_id} by @{self.author_handle} ({self.post_hash[:8]})"

    @classmethod
    def compute_hash(cls, tweet_id: str | int) -> str:
        clean_id = str(tweet_id).strip()
        # Strictly isolated namespace prefix 'x_tweet:'
        return hashlib.sha256(f"x_tweet:{clean_id}".encode("utf-8")).hexdigest()

    @classmethod
    def is_crawled(cls, tweet_id: str | int) -> bool:
        phash = cls.compute_hash(tweet_id)
        return cls.objects.filter(post_hash=phash).exists()

    @classmethod
    def purge_older_than_10_days(cls) -> int:
        ten_days_ago = dj_timezone.now() - timedelta(days=10)
        deleted_count, _ = cls.objects.filter(crawled_at__lt=ten_days_ago).delete()
        return deleted_count


class TwitterEngineAccount(models.Model):
    STATUS_CHOICES = [
        ("ACTIVE", "فعال و متصل"),
        ("COOLDOWN", "استراحت موقت (Rate Limit / Cooldown)"),
        ("CHALLENGED", "نیازمند سشن معتبر / چالش امنیتی (Challenge)"),
        ("BANNED", "مسدود شده (Banned)"),
        ("DISABLED", "غیرفعال دستی"),
    ]

    username = models.CharField(max_length=100, unique=True, db_index=True, verbose_name="نام کاربری توییتر / X")
    password = models.CharField(max_length=255, blank=True, default="", verbose_name="رمز عبور")
    auth_token = models.CharField(max_length=255, blank=True, default="", verbose_name="کوکی auth_token (Session)")
    ct0 = models.CharField(max_length=255, blank=True, default="", verbose_name="کوکی ct0 (CSRF Token)")
    proxy_url = models.CharField(max_length=255, blank=True, default="socks5h://xray:1080", verbose_name="پروکسی (SOCKS5/HTTP)")
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default="ACTIVE", db_index=True, verbose_name="وضعیت")
    status_message = models.CharField(max_length=255, blank=True, default="آماده به کار", verbose_name="پیام وضعیت")
    daily_action_count = models.IntegerField(default=0, verbose_name="تعداد سرچ‌های امروز")
    total_action_count = models.IntegerField(default=0, verbose_name="کل سرچ‌ها")
    posts_found_count = models.IntegerField(default=0, verbose_name="تعداد توییت‌های یافته‌شده")
    last_action_at = models.DateTimeField(null=True, blank=True, verbose_name="آخرین زمان فعالیت")
    cooldown_until = models.DateTimeField(null=True, blank=True, db_index=True, verbose_name="پایان استراحت")
    is_active = models.BooleanField(default=True, verbose_name="فعال در چرخه چرخش")
    created_at = models.DateTimeField(auto_now_add=True, verbose_name="زمان ثبت")
    updated_at = models.DateTimeField(auto_now=True, verbose_name="آخرین به‌روزرسانی")

    class Meta:
        db_table = "x_accounts"
        verbose_name = "اکانت اسکرپر توییتر / X"
        verbose_name_plural = "اکانت‌های اسکرپر توییتر / X"
        indexes = [
            models.Index(fields=["status", "is_active"], name="ix_x_acc_status_active"),
        ]

    def is_available(self):
        from django.utils import timezone
        if not self.is_active or self.status in ("BANNED", "DISABLED", "CHALLENGED"):
            return False
        if self.cooldown_until and self.cooldown_until > timezone.now():
            return False
        return True

    def __str__(self):
        return f"@{self.username} ({self.get_status_display()})"
