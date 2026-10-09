from django.db.models.signals import post_save
from django.dispatch import receiver

from apps.businesses.models import Business


@receiver(post_save, sender=Business)
def create_wallet(sender, instance, created, **kwargs):
    if created:
        from .services import ensure_wallet
        ensure_wallet(instance, with_gift=True)
