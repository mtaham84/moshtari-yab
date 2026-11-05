from django.test import TestCase

from apps.discovery.ig_accounts import tenant_ids, tenant_pitch


class TenantIdsTests(TestCase):
    def test_alias_and_canonical_resolve_to_the_same_family(self):
        self.assertEqual(tenant_ids("sarv"), ["tenant_4", "sarv"])
        self.assertEqual(tenant_ids("tenant_4"), ["tenant_4", "sarv"])
        self.assertEqual(tenant_ids("tenant_9"), ["tenant_9"])

    def test_fallback_pitch_only_for_its_tenant(self):
        self.assertTrue(tenant_pitch("tenant_4", usernames=[])["pitch_template"])
        self.assertEqual(tenant_pitch("tenant_9", usernames=[])["pitch_template"], "")
