from django.apps import AppConfig


class BillingConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.billing"
    verbose_name = "پرداخت به‌ازای مصرف و مدل‌ها"

    def ready(self):
        from . import signals  # noqa: F401
