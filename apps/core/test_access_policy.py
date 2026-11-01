import tempfile
from datetime import timedelta
from pathlib import Path
from unittest import mock
from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied
from django.test import TestCase
from django.utils import timezone
from apps.businesses.models import Business
from apps.core.access_policy import AccessPolicy, PLATFORM_TENANT_ID
from apps.discovery.models import InstagramEngineAccount, InstagramEngineLead

User = get_user_model()


class AccessPolicyContractTestCase(TestCase):
    def setUp(self):
        # Tenant A
        self.user_a = User.objects.create_user(username="owner_a@example.com", password="password123")
        self.business_a = Business.objects.create(user=self.user_a, name="کسب‌وکار الف")
        self.tenant_a = f"tenant_{self.business_a.id}"

        # Tenant B
        self.user_b = User.objects.create_user(username="owner_b@example.com", password="password123")
        self.business_b = Business.objects.create(user=self.user_b, name="کسب‌وکار ب")
        self.tenant_b = f"tenant_{self.business_b.id}"

        # Superuser / Platform Admin
        self.admin_user = User.objects.create_superuser(username="admin@example.com", password="adminpassword")

        # Dedicated senders
        self.sender_a = InstagramEngineAccount.objects.create(
            tenant_id=self.tenant_a,
            username="sender_dedicated_a",
            role="SENDER",
            status="ACTIVE",
        )
        self.sender_b = InstagramEngineAccount.objects.create(
            tenant_id=self.tenant_b,
            username="sender_dedicated_b",
            role="SENDER",
            status="ACTIVE",
        )

        # Shared platform pool sender
        self.platform_sender = InstagramEngineAccount.objects.create(
            tenant_id=PLATFORM_TENANT_ID,
            username="sender_shared_platform",
            role="SENDER",
            status="ACTIVE",
        )

        # Leads (both contacted via platform sender, but owned by distinct tenants)
        self.lead_a = InstagramEngineLead.objects.create(
            tenant_id=self.tenant_a,
            username="buyer_lead_for_a",
            sender_account="sender_shared_platform",
            status="DM_SENT",
        )
        self.lead_b = InstagramEngineLead.objects.create(
            tenant_id=self.tenant_b,
            username="buyer_lead_for_b",
            sender_account="sender_shared_platform",
            status="DM_SENT",
        )

        # Temp directory for outreach config tests
        self.temp_dir = tempfile.TemporaryDirectory()
        self.temp_cfg_path = Path(self.temp_dir.name) / "outreach_config.json"
        self._cfg_patch = mock.patch("apps.core.access_policy.CONFIG_PATH", self.temp_cfg_path)
        self._cfg_patch.start()

    def tearDown(self):
        self._cfg_patch.stop()
        self.temp_dir.cleanup()

    def test_platform_sender_available_to_both_tenants_in_search(self):
        """Both Tenant A and Tenant B include the shared platform pool in allowed senders."""
        search_a = AccessPolicy.sender_search_tenants(self.user_a)
        self.assertIn(self.tenant_a, search_a)
        self.assertIn(PLATFORM_TENANT_ID, search_a)
        self.assertNotIn(self.tenant_b, search_a)

        search_b = AccessPolicy.sender_search_tenants(self.user_b)
        self.assertIn(self.tenant_b, search_b)
        self.assertIn(PLATFORM_TENANT_ID, search_b)
        self.assertNotIn(self.tenant_a, search_b)

        # Querying available senders for Tenant A gets dedicated A + platform
        senders_for_a = InstagramEngineAccount.objects.filter(
            tenant_id__in=search_a, role="SENDER"
        ).values_list("username", flat=True)
        self.assertIn("sender_dedicated_a", senders_for_a)
        self.assertIn("sender_shared_platform", senders_for_a)
        self.assertNotIn("sender_dedicated_b", senders_for_a)

    def test_lead_isolation_is_strictly_enforced(self):
        """Tenant A can NEVER access Tenant B's lead, even if contacted via shared platform sender."""
        self.assertTrue(AccessPolicy.can_view_or_modify_lead(self.user_a, self.lead_a))
        self.assertFalse(AccessPolicy.can_view_or_modify_lead(self.user_a, self.lead_b))
        self.assertFalse(AccessPolicy.can_view_or_modify_lead(self.user_b, self.lead_a))
        self.assertTrue(AccessPolicy.can_view_or_modify_lead(self.user_b, self.lead_b))

        with self.assertRaises(PermissionDenied):
            AccessPolicy.assert_can_access_lead(self.user_a, self.lead_b)

        # Superuser can access all leads
        self.assertTrue(AccessPolicy.can_view_or_modify_lead(self.admin_user, self.lead_a))
        self.assertTrue(AccessPolicy.can_view_or_modify_lead(self.admin_user, self.lead_b))

    def test_platform_sender_management_is_admin_only(self):
        """Platform accounts cannot be edited or cleared by individual tenants."""
        # Dedicated accounts can be managed by their owner
        self.assertTrue(AccessPolicy.can_manage_sender_account(self.user_a, self.sender_a))
        self.assertFalse(AccessPolicy.can_manage_sender_account(self.user_b, self.sender_a))

        # Platform account cannot be managed by regular tenants
        self.assertFalse(AccessPolicy.can_manage_sender_account(self.user_a, self.platform_sender))
        self.assertFalse(AccessPolicy.can_manage_sender_account(self.user_b, self.platform_sender))
        with self.assertRaises(PermissionDenied):
            AccessPolicy.assert_can_manage_account(self.user_a, self.platform_sender)

        # Superuser can manage both dedicated and platform accounts
        self.assertTrue(AccessPolicy.can_manage_sender_account(self.admin_user, self.platform_sender))
        self.assertTrue(AccessPolicy.can_manage_sender_account(self.admin_user, self.sender_a))

    def test_platform_pool_enrollment_guard(self):
        """Non-admin users cannot enroll accounts into the platform pool."""
        self.assertFalse(AccessPolicy.can_enroll_in_platform_pool(self.user_a, PLATFORM_TENANT_ID))
        self.assertFalse(AccessPolicy.can_enroll_in_platform_pool(self.user_b, PLATFORM_TENANT_ID))
        self.assertTrue(AccessPolicy.can_enroll_in_platform_pool(self.admin_user, PLATFORM_TENANT_ID))
        self.assertTrue(AccessPolicy.can_enroll_in_platform_pool(self.user_a, self.tenant_a))

    def test_tenant_pitch_isolation_on_shared_account(self):
        """Tenant A and Tenant B maintain completely isolated pitches for the same shared account."""
        pitch_a = {
            "consultant_name": "مشاور فروشگاه الف",
            "consultant_role": "پشتیبانی الف",
            "template": "سلام از طرف فروشگاه الف! محصول ما تخفیف دارد.",
        }
        pitch_b = {
            "consultant_name": "مشاور شرکت ب",
            "consultant_role": "پشتیبانی ب",
            "template": "درود از شرکت ب! در خدمت شما هستیم.",
        }

        AccessPolicy.save_tenant_pitch(self.tenant_a, "sender_shared_platform", pitch_a)
        AccessPolicy.save_tenant_pitch(self.tenant_b, "sender_shared_platform", pitch_b)

        loaded_a = AccessPolicy.get_tenant_pitch(self.tenant_a, "sender_shared_platform")
        loaded_b = AccessPolicy.get_tenant_pitch(self.tenant_b, "sender_shared_platform")

        self.assertEqual(loaded_a["template"], pitch_a["template"])
        self.assertEqual(loaded_b["template"], pitch_b["template"])
        self.assertNotEqual(loaded_a["template"], loaded_b["template"])
        self.assertEqual(loaded_a["consultant_name"], "مشاور فروشگاه الف")
        self.assertEqual(loaded_b["consultant_name"], "مشاور شرکت ب")

    def test_platform_sender_cooldown_is_shared(self):
        """If shared platform sender is placed on cooldown, it becomes unavailable for all tenants."""
        self.assertTrue(AccessPolicy.is_sender_available(self.platform_sender))

        self.platform_sender.status = "COOLDOWN"
        self.platform_sender.cooldown_until = timezone.now() + timedelta(hours=3)
        self.platform_sender.save()

        self.assertFalse(AccessPolicy.is_sender_available(self.platform_sender))
