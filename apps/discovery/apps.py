from django.apps import AppConfig


class DiscoveryConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.discovery"
    verbose_name = "پایش و کشف هوشمند مشتریان"

    def ready(self):
        import sys
        # Apply runtime patches for instagrapi
        from apps.discovery import instagrapi_patch
        instagrapi_patch.patch_instagrapi_password_keys()

        from django.db.models.signals import pre_delete

        from apps.discovery.models import ProxyConfig

        def _release_config(sender, instance, **kwargs):
            # its Instagram accounts go to the shared pool (logged) until ensure_system_configs gives them a new one
            from apps.discovery.ig_accounts import release_config
            release_config(instance, note=f"کانفیگ #{instance.pk} ({instance.tag or instance.server}) حذف شد")

        pre_delete.connect(_release_config, sender=ProxyConfig, weak=False, dispatch_uid="ig_release_proxy_config")

        if "test" in sys.argv[1:2] or "pytest" in sys.modules:   # tests must never rewrite the real pool file
            return
        from django.db import transaction
        from django.db.models.signals import post_delete, post_save

        from apps.discovery.models import InstagramEngineAccount, TwitterEngineAccount

        def _sync(sender, **kwargs):
            from apps.discovery.accounts_pool_file import export_pool
            transaction.on_commit(export_pool)

        for model in (InstagramEngineAccount, TwitterEngineAccount):
            post_save.connect(_sync, sender=model, weak=False, dispatch_uid=f"pool_save_{model.__name__}")
            post_delete.connect(_sync, sender=model, weak=False, dispatch_uid=f"pool_del_{model.__name__}")
