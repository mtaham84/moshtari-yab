import concurrent.futures
import json
import shutil
import tempfile
from pathlib import Path
from unittest import mock
from django.contrib.auth import get_user_model
from django.test import Client, TestCase
from django.urls import reverse
from apps.businesses.models import Business
from apps.core.access_policy import AccessPolicy, PLATFORM_TENANT_ID
from apps.discovery.models import InstagramEngineAccount
from apps.discovery.management.commands.run_instagram_outreach import (
    DEFAULT_SENDER_PITCHES,
    load_outreach_config,
)

User = get_user_model()


class PitchOwnershipAndLockingTestCase(TestCase):
    """
    P01.06 Test Suite:
    Validates:
    - Foreign sender pitches cannot be overwritten by unauthorized tenants (R23).
    - Pitches for shared platform senders remain strictly tenant-isolated without leaking.
    - Concurrent writes do not cause corrupted JSON or lost updates (flock + atomic replace).
    - Outreach config loader returns fully merged pitches including defaults and tenant overrides.
    """

    def setUp(self):
        self.client = Client()

        # Temporary config directory for test isolation
        self.test_dir = tempfile.mkdtemp()
        self.config_path = Path(self.test_dir) / "sarv_outreach_config.json"
        self._orig_config_path = AccessPolicy.CONFIG_PATH
        AccessPolicy.CONFIG_PATH = self.config_path

        # Tenant A
        self.user_a = User.objects.create_user(username="seller_a@example.com", password="password123")
        self.business_a = Business.objects.create(user=self.user_a, name="فروشگاه الف", onboarding_done=True)
        self.tenant_a = f"tenant_{self.business_a.id}"

        # Tenant B
        self.user_b = User.objects.create_user(username="seller_b@example.com", password="password123")
        self.business_b = Business.objects.create(user=self.user_b, name="فروشگاه ب", onboarding_done=True)
        self.tenant_b = f"tenant_{self.business_b.id}"

        # Senders
        self.sender_a = InstagramEngineAccount.objects.create(
            tenant_id=self.tenant_a,
            username="sender_a",
            role="SENDER",
            status="ACTIVE",
        )
        self.sender_b = InstagramEngineAccount.objects.create(
            tenant_id=self.tenant_b,
            username="sender_b",
            role="SENDER",
            status="ACTIVE",
        )
        self.sender_platform = InstagramEngineAccount.objects.create(
            tenant_id=PLATFORM_TENANT_ID,
            username="sender_platform",
            role="SENDER",
            status="ACTIVE",
        )

    def tearDown(self):
        AccessPolicy.CONFIG_PATH = self._orig_config_path
        shutil.rmtree(self.test_dir, ignore_errors=True)

    def test_foreign_pitch_cannot_be_modified_by_other_tenant(self):
        """
        Tenant A attempts to save pitch for Tenant B's private sender ('sender_b').
        Must be rejected with 403; Tenant B's sender pitch remains unmodified.
        """
        # Seed Tenant B's pitch
        AccessPolicy.save_tenant_pitch(
            self.tenant_b,
            "sender_b",
            {"consultant_name": "مشاور ب", "template": "الگوی اصلی ب"},
        )

        self.client.force_login(self.user_a)
        url = reverse("accounts:save_pitch")
        res = self.client.post(
            url,
            {
                "username": "sender_b",
                "consultant_name": "نفوذی الف",
                "template": "تلاش برای بازنویسی پیام ب",
            },
            HTTP_X_REQUESTED_WITH="XMLHttpRequest",
        )
        self.assertEqual(res.status_code, 403)

        pitch_b = AccessPolicy.get_tenant_pitch(self.tenant_b, "sender_b")
        self.assertEqual(pitch_b["template"], "الگوی اصلی ب")
        self.assertEqual(pitch_b["consultant_name"], "مشاور ب")

    def test_shared_platform_sender_pitch_is_strictly_isolated_per_tenant(self):
        """
        Both Tenant A and Tenant B customize the pitch on shared platform sender ('sender_platform').
        Pitches MUST remain completely isolated per tenant without overwriting or leaking.
        """
        self.client.force_login(self.user_a)
        url = reverse("accounts:save_pitch")
        res_a = self.client.post(
            url,
            {
                "username": "sender_platform",
                "consultant_name": "سارا از الف",
                "template": "سلام از فروشگاه الف برای لید الف",
            },
            HTTP_X_REQUESTED_WITH="XMLHttpRequest",
        )
        self.assertEqual(res_a.status_code, 200)

        self.client.force_login(self.user_b)
        res_b = self.client.post(
            url,
            {
                "username": "sender_platform",
                "consultant_name": "رضا از ب",
                "template": "سلام از فروشگاه ب برای لید ب",
            },
            HTTP_X_REQUESTED_WITH="XMLHttpRequest",
        )
        self.assertEqual(res_b.status_code, 200)

        pitch_a = AccessPolicy.get_tenant_pitch(self.tenant_a, "sender_platform")
        pitch_b = AccessPolicy.get_tenant_pitch(self.tenant_b, "sender_platform")

        self.assertEqual(pitch_a["template"], "سلام از فروشگاه الف برای لید الف")
        self.assertEqual(pitch_a["consultant_name"], "سارا از الف")

        self.assertEqual(pitch_b["template"], "سلام از فروشگاه ب برای لید ب")
        self.assertEqual(pitch_b["consultant_name"], "رضا از ب")

    def test_concurrent_writers_do_not_corrupt_json_or_lose_updates(self):
        """
        Multiple concurrent workers write tenant pitches simultaneously.
        Inter-process flock + atomic replace guarantees valid JSON and zero lost updates.
        """
        def write_pitch(index: int):
            t_key = f"tenant_worker_{index}"
            u_name = f"sender_worker_{index}"
            AccessPolicy.save_tenant_pitch(
                t_key,
                u_name,
                {"consultant_name": f"مشاور_{index}", "template": f"الگو_{index}"},
            )
            return index

        # Execute 10 concurrent writes across a thread pool
        with concurrent.futures.ThreadPoolExecutor(max_workers=5) as executor:
            futures = [executor.submit(write_pitch, i) for i in range(10)]
            results = [f.result() for f in concurrent.futures.as_completed(futures)]
        self.assertEqual(len(results), 10)

        # Verify JSON integrity and ensure no lost updates
        self.assertTrue(self.config_path.exists())
        cfg = json.loads(self.config_path.read_text(encoding="utf-8"))
        self.assertIn("tenant_pitches", cfg)
        for i in range(10):
            t_key = f"tenant_worker_{i}"
            u_name = f"sender_worker_{i}"
            self.assertIn(t_key, cfg["tenant_pitches"])
            self.assertIn(u_name, cfg["tenant_pitches"][t_key])
            self.assertEqual(cfg["tenant_pitches"][t_key][u_name]["consultant_name"], f"مشاور_{i}")

    @mock.patch("apps.discovery.management.commands.run_instagram_outreach.CONFIG_FILE")
    def test_loader_returns_merged_pitches_including_defaults_and_tenant_override(self, mock_cfg_file):
        """
        load_outreach_config returns fully merged sender_pitches (defaults + file + tenant overrides).
        """
        mock_cfg_file.exists.return_value = True
        fake_cfg = {
            "sender_pitches": {
                "custom_sender": {"consultant_name": "مشاور سفارشی", "template": "الگوی سفارشی"},
            },
            "tenant_pitches": {
                self.tenant_a: {
                    "sender_platform": {"consultant_name": "سارا الف", "template": "الگوی الف"},
                },
            },
        }
        mock_cfg_file.read_text.return_value = json.dumps(fake_cfg)

        cfg_without_tenant = load_outreach_config()
        pitches_raw = cfg_without_tenant.get("sender_pitches", {})
        # Defaults are preserved
        self.assertIn("raha.rostamim", pitches_raw)
        self.assertIn("pania.mehrim", pitches_raw)
        # Custom sender is included
        self.assertIn("custom_sender", pitches_raw)

        # With tenant_a specified, tenant override is merged in
        cfg_with_tenant = load_outreach_config(tenant_id=self.tenant_a)
        pitches_tenant_a = cfg_with_tenant.get("sender_pitches", {})
        self.assertIn("sender_platform", pitches_tenant_a)
        self.assertEqual(pitches_tenant_a["sender_platform"]["consultant_name"], "سارا الف")
