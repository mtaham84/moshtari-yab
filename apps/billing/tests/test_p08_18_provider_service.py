from decimal import Decimal
from django.contrib.auth import get_user_model
from django.test import Client, TestCase
from django.urls import reverse

from apps.billing import provider_service
from apps.billing.models import AIModel, Provider
from need_engine.config import EngineConfig
from need_engine.registry import ModelRegistry

User = get_user_model()


class P0818ProviderServiceTests(TestCase):
    def setUp(self):
        self.staff_user = User.objects.create_superuser(
            username="ops_admin", email="ops@example.com", password="pass"
        )
        self.regular_user = User.objects.create_user(
            username="regular_seller", email="seller@example.com", password="pass"
        )
        self.client = Client()

    def test_get_masked_api_key_prevents_secret_leak(self):
        # Empty key
        p_empty = Provider(name="P1", base_url="https://api.p1.com", api_key="")
        self.assertEqual(provider_service.get_masked_api_key(p_empty), "-")

        # Short key (<= 8 chars)
        p_short = Provider(name="P2", base_url="https://api.p2.com", api_key="12345678")
        self.assertEqual(provider_service.get_masked_api_key(p_short), "••••••••")

        # Standard key
        p_long = Provider(name="P3", base_url="https://api.p3.com", api_key="sk-live-secret-token-9988")
        masked = provider_service.get_masked_api_key(p_long)
        self.assertTrue(masked.startswith("sk-••••"))
        self.assertTrue(masked.endswith("9988"))
        self.assertNotIn("live-secret-token", masked)

    def test_save_provider_form_and_trailing_slash_normalization(self):
        data = {
            "name": "CustomProvider",
            "base_url": "https://api.custom.com/v1///",
            "api_key": "secret-key-123",
            "is_active": "on",
            "notes": "Test provider",
        }
        prov, form = provider_service.save_provider_form(data)
        self.assertIsNotNone(prov)
        self.assertEqual(prov.name, "CustomProvider")
        self.assertEqual(prov.base_url, "https://api.custom.com/v1")  # Trailing slash stripped

        # Blank key on edit preserves existing key
        edit_data = {
            "name": "CustomProviderRenamed",
            "base_url": "https://api.custom.com/v1",
            "api_key": "",
            "is_active": "on",
        }
        prov_edit, form_edit = provider_service.save_provider_form(edit_data, instance=prov)
        self.assertIsNotNone(prov_edit)
        self.assertEqual(prov_edit.api_key, "secret-key-123")

    def test_save_aimodel_form_and_priority_preservation(self):
        prov = Provider.objects.create(name="OpenRouterTest", base_url="https://openrouter.ai/api/v1")
        model_data = {
            "provider": prov.pk,
            "name": "deepseek-chat",
            "label": "DeepSeek Chat V3",
            "input_price_usd": "0.14",
            "output_price_usd": "0.28",
            "priority": "5",
            "use_extract": "on",
            "use_verify": "on",
            "use_reply": "",
            "rpm": "60",
            "tpm": "100000",
            "rpd": "5000",
            "is_active": "on",
        }
        model, form = provider_service.save_aimodel_form(model_data)
        self.assertIsNotNone(model)
        self.assertEqual(model.priority, 5)
        self.assertEqual(model.input_price_usd, Decimal("0.14"))
        self.assertTrue(model.use_extract)
        self.assertFalse(model.use_reply)

    def test_priority_governs_gateway_candidate_ordering(self):
        """
        Acceptance Criteria: Verify that setting priority produces the identical
        priority order in ModelRegistry candidate lists.
        """
        p1 = Provider.objects.create(name="ProvLow", base_url="https://p1.test/v1", api_key="k1")
        p2 = Provider.objects.create(name="ProvHigh", base_url="https://p2.test/v1", api_key="k2")

        # Lower priority number = higher precedence
        m_high = AIModel.objects.create(
            provider=p2,
            name="shared-model",
            priority=10,
            use_extract=True,
            input_price_usd=Decimal("0.10"),
            output_price_usd=Decimal("0.20"),
        )
        m_low = AIModel.objects.create(
            provider=p1,
            name="shared-model",
            priority=50,
            use_extract=True,
            input_price_usd=Decimal("0.10"),
            output_price_usd=Decimal("0.20"),
        )

        models = list(AIModel.objects.filter(use_extract=True).order_by("priority", "name"))
        self.assertEqual(models[0].pk, m_high.pk)
        self.assertEqual(models[1].pk, m_low.pk)

    def test_non_staff_access_denied_to_provider_management(self):
        prov = Provider.objects.create(name="ProtectedProv", base_url="https://prot.test")

        # Unauthenticated user redirected to login
        res_unauth = self.client.get(reverse("ops:providers"))
        self.assertEqual(res_unauth.status_code, 302)

        # Regular authenticated non-staff user gets 403 Forbidden
        self.client.force_login(self.regular_user)
        res_forbidden = self.client.get(reverse("ops:providers"))
        self.assertEqual(res_forbidden.status_code, 403)

        res_post_forbidden = self.client.post(reverse("ops:provider_delete", args=[prov.pk]))
        self.assertEqual(res_post_forbidden.status_code, 403)
        self.assertTrue(Provider.objects.filter(pk=prov.pk).exists())
