from django.test import TestCase

from apps.discovery.tenants import tenant_ids, tenant_pitch


class TenantIdsTests(TestCase):
    def test_alias_and_canonical_resolve_to_the_same_family(self):
        self.assertEqual(tenant_ids("sarv"), ["tenant_4", "sarv"])
        self.assertEqual(tenant_ids("tenant_4"), ["tenant_4", "sarv"])
        self.assertEqual(tenant_ids("tenant_9"), ["tenant_9"])

    def test_fallback_pitch_only_for_its_tenant(self):
        self.assertTrue(tenant_pitch("tenant_4", usernames=[])["pitch_template"])
        self.assertEqual(tenant_pitch("tenant_9", usernames=[])["pitch_template"], "")


class OutreachTenantsTests(TestCase):
    """TEN-03: dispatcher / reply sync / outreach page are not pinned to tenant_4."""

    def setUp(self):
        from apps.discovery.models import InstagramEngineAccount

        InstagramEngineAccount.objects.create(username="s1", password="x", role="SENDER", status="ACTIVE", tenant_id="sarv")
        InstagramEngineAccount.objects.create(username="s2", password="x", role="SENDER", status="ACTIVE", tenant_id="tenant_7")
        InstagramEngineAccount.objects.create(username="s3", password="x", role="SENDER", status="PAUSED", tenant_id="tenant_8")
        InstagramEngineAccount.objects.create(username="sc", password="x", role="SCRAPER", status="ACTIVE", tenant_id="tenant_9")

    def test_default_is_every_tenant_with_an_active_sender(self):
        from apps.discovery.tenants import outreach_tenant_ids, outreach_tenants

        with self.settings(OUTREACH_TENANTS=[]):
            self.assertEqual(sorted(outreach_tenants()), ["tenant_4", "tenant_7"])
            self.assertEqual(sorted(outreach_tenant_ids()), ["sarv", "tenant_4", "tenant_7"])

    def test_env_setting_pins_the_tenants(self):
        from apps.discovery.tenants import outreach_tenants

        with self.settings(OUTREACH_TENANTS=["sarv", "tenant_7", "tenant_4"]):
            self.assertEqual(outreach_tenants(), ["tenant_4", "tenant_7"])

    def test_celery_dispatch_serves_each_tenant(self):
        from unittest.mock import patch

        from apps.discovery import tasks

        calls = []

        def fake_batch(ids, **kwargs):
            calls.append(ids)
            return {"sent": 1, "last": {"action": "sent"}}

        with self.settings(OUTREACH_TENANTS=[], INSTAGRAM_OUTREACH_DISPATCHER="celery"), \
                patch.object(tasks, "run_outreach_batch", side_effect=fake_batch):
            res = tasks.task_dispatch_instagram_outreach.apply().get()
        self.assertEqual(sorted(map(tuple, calls)), [("tenant_4", "sarv"), ("tenant_7",)])
        self.assertEqual(res["sent_count"], 2)

    def test_operator_outreach_business_has_no_hardcoded_ids(self):
        from types import SimpleNamespace

        from apps.accounts.outreach_views import _operator_outreach_business

        b1, b7, b9 = (SimpleNamespace(id=i) for i in (1, 7, 9))
        all_b = [b1, b7, b9]
        with self.settings(OUTREACH_TENANTS=["tenant_7"]):
            self.assertIs(_operator_outreach_business("9", b1, all_b), b9)
            self.assertIs(_operator_outreach_business(None, b1, all_b), b7)
            self.assertIs(_operator_outreach_business(None, b7, all_b), b7)
        with self.settings(OUTREACH_TENANTS=["tenant_99"]):
            self.assertIs(_operator_outreach_business(None, b1, all_b), b1)
            self.assertIs(_operator_outreach_business(None, None, all_b), b1)
