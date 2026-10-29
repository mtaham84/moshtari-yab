from django.apps import AppConfig


class DiscoveryConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.discovery"
    verbose_name = "پایش و کشف هوشمند مشتریان"

    def ready(self):
        from django.db import transaction
        from django.db.models.signals import post_delete, post_save

        from apps.discovery.models import InstagramEngineAccount, TwitterEngineAccount

        def _sync(sender, **kwargs):
            from apps.discovery.accounts_pool_file import export_pool
            transaction.on_commit(export_pool)

        for model in (InstagramEngineAccount, TwitterEngineAccount):
            post_save.connect(_sync, sender=model, weak=False, dispatch_uid=f"pool_save_{model.__name__}")
            post_delete.connect(_sync, sender=model, weak=False, dispatch_uid=f"pool_del_{model.__name__}")
