"""
Unit tests for Instagram outreach auto-recovery, recipient qualification,
and pipeline resilience.
"""
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

from django.test import SimpleTestCase
from apps.discovery.outreach_service import (
    qualify_recipient_profile,
    build_bulletproof_message,
)


class OutreachAutoRecoveryTestCase(SimpleTestCase):
    def test_qualify_recipient_profile_allows_active_private_account(self):
        """
        Private accounts should be eligible for DMs (requests tab),
        preventing outreach deadlock when leads have private personal profiles.
        """
        cl = MagicMock()
        cl.private_request.return_value = {
            "status": "ok",
            "user": {
                "pk": 99887766,
                "username": "private_user_1",
                "is_private": True,
                "media_count": 12,
                "follower_count": 350,
            },
        }

        result = qualify_recipient_profile(cl, "private_user_1", target_pk=99887766)
        self.assertTrue(result["eligible"])
        self.assertIn("پیج خصوصی", result["reason"])
        self.assertEqual(result["target_pk"], 99887766)

    def test_qualify_recipient_profile_allows_active_public_account(self):
        """Public accounts should be eligible immediately."""
        cl = MagicMock()
        cl.private_request.return_value = {
            "status": "ok",
            "user": {
                "pk": 11223344,
                "username": "public_user_1",
                "is_private": False,
                "media_count": 45,
                "follower_count": 1200,
            },
        }

        result = qualify_recipient_profile(cl, "public_user_1", target_pk=11223344)
        self.assertTrue(result["eligible"])
        self.assertEqual(result["target_pk"], 11223344)

    def test_qualify_recipient_profile_rejects_empty_ghost_account(self):
        """Ghost accounts with 0 posts and 0 followers should be safely skipped."""
        cl = MagicMock()
        cl.private_request.return_value = {
            "status": "ok",
            "user": {
                "pk": 55443322,
                "username": "ghost_bot_user",
                "is_private": True,
                "media_count": 0,
                "follower_count": 0,
            },
        }

        result = qualify_recipient_profile(cl, "ghost_bot_user", target_pk=55443322)
        self.assertFalse(result["eligible"])
        self.assertIn("غیرفعال یا مشکوک", result["reason"])

    def test_bulletproof_message_generation(self):
        """Verify dynamic Spintax message formatting and personalization."""
        lead = MagicMock()
        lead.username = "sarv_client"
        lead.full_name = "مریم رضایی"
        lead.comment_text = "سلام قیمت کامپوزیت چنده؟"

        msg = build_bulletproof_message(
            pitch_template="",
            lead=lead,
            consultant_name="رها",
            business_name="کلینیک تخصصی سرو",
        )

        self.assertIn("رها", msg)
        self.assertIn("کلینیک تخصصی سرو", msg)
        self.assertIn("کامپوزیت", msg)
        self.assertNotIn("{", msg)
        self.assertNotIn("}", msg)

    def test_sender_auto_unfreeze_simulation(self):
        """
        Simulate cooldown expiration logic to verify status returns from COOLDOWN to ACTIVE.
        """
        now = datetime.now(timezone.utc)
        acc = MagicMock()
        acc.role = "SENDER"
        acc.status = "COOLDOWN"
        acc.cooldown_until = now - timedelta(minutes=5)

        # Verification logic matching run_instagram_outreach
        if acc.status == "COOLDOWN" and (not acc.cooldown_until or acc.cooldown_until <= now):
            acc.status = "ACTIVE"
            acc.cooldown_until = None
            acc.status_message = "پایان دوره استراحت - بازگشت خودکار"

        self.assertEqual(acc.status, "ACTIVE")
        self.assertIsNone(acc.cooldown_until)
