"""
Provider and Model management services for Ops panel.
Decouples provider/model validation, key masking, priority enforcement, and lifecycle
from billing views (P08.18 - R39).
"""

import logging
from typing import Any
from django.db import transaction

from apps.billing import services
from apps.billing.forms import AIModelForm, ProviderForm
from apps.billing.models import AIModel, Provider

logger = logging.getLogger(__name__)


def list_providers_with_usage(days: int = 30) -> tuple[list[Provider], list[dict[str, Any]], bool]:
    """
    Returns (providers_list, usage_all_list, orphan_hint).
    Annotates each model in each provider with usage statistics.
    """
    usage = {u["key"]: u for u in services.model_usage(float(days))}
    providers_qs = list(Provider.objects.prefetch_related("models"))
    for prov in providers_qs:
        for m in prov.models.all():
            m.usage = usage.get(m.name)
    orphan_hint = not Provider.objects.exists()
    return providers_qs, list(usage.values()), orphan_hint


def get_masked_api_key(provider: Provider) -> str:
    """
    Returns a masked representation of the provider's API key.
    Raw plaintext secrets must never be exposed directly in views or logs.
    """
    raw_key = (provider.api_key or "").strip()
    if not raw_key:
        return "-"
    if len(raw_key) <= 8:
        return "••••••••"
    return f"{raw_key[:3]}••••{raw_key[-4:]}"


def save_provider_form(post_data, instance: Provider | None = None) -> tuple[Provider | None, ProviderForm]:
    """
    Validates and saves a Provider instance using ProviderForm.
    Respects key encryption and cleans trailing slashes from base_url.
    Returns (saved_provider, form).
    """
    form = ProviderForm(post_data, instance=instance)
    if form.is_valid():
        provider = form.save()
        return provider, form
    return None, form


def save_aimodel_form(
    post_data,
    instance: AIModel | None = None,
    initial: dict | None = None,
) -> tuple[AIModel | None, AIModelForm]:
    """
    Validates and saves an AIModel instance using AIModelForm.
    Enforces Ops priority ordering and role selection.
    Returns (saved_model, form).
    """
    form = AIModelForm(post_data, instance=instance, initial=initial)
    if form.is_valid():
        model = form.save()
        return model, form
    return None, form


def delete_provider(provider: Provider) -> str:
    """Cascades deletion of provider and its models and returns confirmation message."""
    name = provider.name
    provider.delete()
    return f"«{name}» و مدل‌هایش حذف شدند."


def test_provider_connectivity(provider: Provider) -> tuple[bool, str]:
    """Tests connectivity to the provider endpoint and returns (is_ok, message)."""
    return services.test_provider(provider)


def import_provider_from_env() -> tuple[Provider, int]:
    """Imports provider and models from environment configuration."""
    return services.import_from_env()


def delete_aimodel(model: AIModel) -> str:
    """Deletes an AI model and returns confirmation message."""
    name = model.name
    model.delete()
    return f"مدل «{name}» حذف شد."


def toggle_aimodel(model: AIModel) -> bool:
    """Toggles active state of model and returns new is_active boolean."""
    model.is_active = not model.is_active
    model.save(update_fields=["is_active", "updated_at"])
    return model.is_active
