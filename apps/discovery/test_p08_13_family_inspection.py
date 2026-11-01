"""
Tests for family inspection service (P08.13 / R39).
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock, patch
import pytest

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone as dj_timezone

from apps.businesses.models import Business
from apps.discovery.family_inspection_service import (
    FamilyInspectionResult,
    FamilyInspectionService,
)
from apps.discovery.models import (
    InstagramCrawledPost,
    InstagramEngineAccount,
    InstagramEngineLead,
)

User = get_user_model()


class FamilyInspectionServiceTests(TestCase):
    def setUp(self):
        self.user = User.objects.create(username="bot_owner_test")
        self.business = Business.objects.create(
            user=self.user,
            name="بوتیک تست",
            business_domain="پوشاک",
        )
        self.account = InstagramEngineAccount.objects.create(
            username="scraper_bot_1",
            status="active",
        )
        self.family = SimpleNamespace(
            category_path="پوشاک زنانه > مانتو",
            title="مانتو کتی",
            titles=["مانتو کتی", "مانتو پاییزه"],
            notes="فروش مستقیم مانتو مزونی",
        )
        self.plan = SimpleNamespace(
            buyer_signals=["قیمت", "خرید", "سایز"],
            negative_signals=["همکاری", "تبلیغ"],
        )
        self.target = {
            "business": self.business,
            "family": self.family,
            "plan": self.plan,
            "tenant_id": str(self.business.pk),
        }
        self.source = SimpleNamespace(
            source_type="hashtag",
            label="مانتو_کتی",
        )
        self.service = FamilyInspectionService()

    def test_empty_medias_returns_retired_status(self):
        res = self.service.inspect_family_medias(
            cl=None,
            account=self.account,
            target=self.target,
            source=self.source,
            medias=[],
            min_comments=10,
        )
        self.assertEqual(res.posts_inspected, 0)
        self.assertEqual(res.qualifying_posts, 0)
        self.assertEqual(res.leads_saved, 0)
        self.assertTrue(res.retired)
        self.assertEqual(res.as_tuple(), (0, 0, 0, True))

    def test_filters_old_posts(self):
        old_time = dj_timezone.now() - timedelta(days=35)
        media_old = SimpleNamespace(
            id="media_old_1",
            code="CODE_OLD",
            taken_at=old_time,
            comment_count=50,
            caption_text="پست قدیمی",
        )
        res = self.service.inspect_family_medias(
            cl=None,
            account=self.account,
            target=self.target,
            source=self.source,
            medias=[media_old],
            min_comments=10,
        )
        self.assertEqual(res.posts_inspected, 1)
        self.assertEqual(res.qualifying_posts, 0)
        self.assertTrue(res.retired)

    def test_filters_low_comment_posts(self):
        recent_time = dj_timezone.now() - timedelta(days=2)
        media_low = SimpleNamespace(
            id="media_low_1",
            code="CODE_LOW",
            taken_at=recent_time,
            comment_count=5,  # Less than min 10
            caption_text="پست با کامنت کم",
        )
        res = self.service.inspect_family_medias(
            cl=None,
            account=self.account,
            target=self.target,
            source=self.source,
            medias=[media_low],
            min_comments=10,
        )
        self.assertEqual(res.posts_inspected, 1)
        self.assertEqual(res.qualifying_posts, 0)
        self.assertTrue(res.retired)

    @patch("apps.discovery.family_inspection_service.chat_json")
    def test_reply_template_caching(self, mock_chat_json):
        mock_chat_json.return_value = {"template": "سلام {username} عزیز، قیمت کالا خدمت شما ارسال شد."}
        family_ctx = {
            "category_path": "پوشاک",
            "product_titles": ["مانتو"],
            "seller_notes": "تست",
        }
        tpl1 = self.service.get_or_create_post_reply_template(
            post_hash="hash_1234567890abcdef",
            caption="توضیح پست مانتو",
            cta_word="قیمت",
            family_context=family_ctx,
            business_id=self.business.pk,
        )
        self.assertIn("{username}", tpl1)
        self.assertEqual(mock_chat_json.call_count, 1)

        # Second call with same hash must return cached template without calling LLM
        tpl2 = self.service.get_or_create_post_reply_template(
            post_hash="hash_1234567890abcdef",
            caption="توضیح پست مانتو",
            cta_word="قیمت",
            family_context=family_ctx,
            business_id=self.business.pk,
        )
        self.assertEqual(tpl1, tpl2)
        self.assertEqual(mock_chat_json.call_count, 1)
