"""
Unit tests for Instagram Live Logs view (/manage/instagram/logs/).
Validates access control, context rendering, telemetry calculations, and JSON polling endpoint.
"""
from datetime import timedelta
import json

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from apps.discovery.models import (
    InstagramCrawledPost,
    InstagramCrawlExecution,
    InstagramEngineAccount,
    InstagramEngineLead,
)

User = get_user_model()


class InstagramLogsViewTests(TestCase):
    def setUp(self):
        self.staff_user = User.objects.create_user(
            username="ops_admin",
            email="ops@example.com",
            password="test-password-123",
            is_staff=True,
        )
        self.normal_user = User.objects.create_user(
            username="normal_user",
            email="user@example.com",
            password="test-password-123",
            is_staff=False,
        )
        self.url = reverse("manage:instagram_logs")

    def test_access_control(self):
        # Anonymous user should be redirected to login
        res_anon = self.client.get(self.url)
        self.assertEqual(res_anon.status_code, 302)

        # Non-staff user should be denied access (302 or 403 based on manage_access_required)
        self.client.force_login(self.normal_user)
        res_user = self.client.get(self.url)
        self.assertIn(res_user.status_code, (302, 403))

        # Staff user should get 200 OK
        self.client.force_login(self.staff_user)
        res_staff = self.client.get(self.url)
        self.assertEqual(res_staff.status_code, 200)

    def test_logs_empty_state_renders_cleanly(self):
        self.client.force_login(self.staff_user)
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "ops/instagram_logs.html")
        self.assertIn("summary", response.context)
        self.assertIsNone(response.context["latest_execution"])
        self.assertEqual(len(response.context["scrapers"]), 0)
        self.assertEqual(len(response.context["recent_posts"]), 0)
        self.assertEqual(len(response.context["recent_leads"]), 0)

    def test_logs_populated_state_html_and_json(self):
        now = timezone.now()

        # 1. Scraper account
        scraper = InstagramEngineAccount.objects.create(
            username="scraper_acc_test",
            password="test_password",
            role="SCRAPER",
            status="ACTIVE",
        )

        # 2. Crawl execution
        execution = InstagramCrawlExecution.objects.create(
            account=scraper,
            account_username=scraper.username,
            query_text="تست_ایمپلنت",
            target_type="HASHTAG",
            status="SUCCESS",
            posts_inspected=10,
            qualifying_posts=3,
            leads_found=2,
            comments_found=25,
            top_post_code="POST_ABC123",
            top_post_url="https://www.instagram.com/p/POST_ABC123/",
            crawled_at=now,
        )

        # 3. Crawled post
        post = InstagramCrawledPost.objects.create(
            post_hash="hash_test_123",
            media_id="1122334455",
            media_code="POST_ABC123",
            account_username=scraper.username,
            posted_at=now - timedelta(days=2),
            caption="توضیحات پست نمونه برای تست کرولر",
            comment_count=25,
            comments_cached=True,
            crawled_at=now,
        )

        # 4. Discovered lead
        lead = InstagramEngineLead.objects.create(
            username="lead_customer_1",
            full_name="مشتری نمونه",
            comment_text="شرایط اقساط چطوریه؟",
            comment_intent_type="QUESTION_INQUIRY",
            outreach_score=38,
            success_probability=85,
            status="READY",
            source_post_url="https://www.instagram.com/p/POST_ABC123/",
            source_page_username="dentist_page",
            created_at=now,
        )

        self.client.force_login(self.staff_user)

        # HTML Test
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["summary"]["leads_total"], 1)
        self.assertEqual(response.context["summary"]["scrapers_total"], 1)
        self.assertEqual(response.context["summary"]["scrapers_active"], 1)

        exec_ctx = response.context["latest_execution"]
        self.assertIsNotNone(exec_ctx)
        self.assertEqual(exec_ctx["query_text"], "تست_ایمپلنت")
        self.assertEqual(exec_ctx["comments_found"], 25)
        self.assertEqual(exec_ctx["top_post_code"], "POST_ABC123")
        self.assertEqual(exec_ctx["top_post_comment_count"], 25)

        scrapers_ctx = response.context["scrapers"]
        self.assertEqual(len(scrapers_ctx), 1)
        self.assertEqual(scrapers_ctx[0]["username"], "scraper_acc_test")
        self.assertEqual(scrapers_ctx[0]["searches_24h"], 1)
        self.assertEqual(scrapers_ctx[0]["last_comments_count"], 25)

        posts_ctx = response.context["recent_posts"]
        self.assertEqual(len(posts_ctx), 1)
        self.assertEqual(posts_ctx[0]["media_code"], "POST_ABC123")
        self.assertEqual(posts_ctx[0]["comment_count"], 25)

        leads_ctx = response.context["recent_leads"]
        self.assertEqual(len(leads_ctx), 1)
        self.assertEqual(leads_ctx[0]["username"], "lead_customer_1")
        self.assertEqual(leads_ctx[0]["outreach_score"], 38)

        # JSON endpoint test (?format=json)
        json_resp = self.client.get(self.url, {"format": "json"})
        self.assertEqual(json_resp.status_code, 200)
        self.assertEqual(json_resp["content-type"], "application/json")
        data = json.loads(json_resp.content.decode("utf-8"))
        self.assertTrue(data["success"])
        self.assertEqual(data["latest_execution"]["top_post_code"], "POST_ABC123")
        self.assertEqual(len(data["scrapers"]), 1)
        self.assertEqual(len(data["recent_posts"]), 1)
        self.assertEqual(len(data["recent_leads"]), 1)

    def test_lead_ordering_comment_created_at_nulls_last(self):
        """
        Verify that leads are ordered by comment_created_at DESC with NULLS LAST,
        and historical leads with null comment_created_at are never dropped.
        """
        now = timezone.now()
        # Lead 1: 2 days old
        lead_old = InstagramEngineLead.objects.create(
            ig_user_pk="user_pk_101",
            username="lead_older",
            comment_text="قدیمی ۲ روز پیش",
            comment_intent_type="QUESTION_INQUIRY",
            comment_created_at=now - timedelta(days=2),
            outreach_score=20,
        )
        # Lead 2: 1 hour old (should be first)
        lead_recent = InstagramEngineLead.objects.create(
            ig_user_pk="user_pk_102",
            username="lead_newer",
            comment_text="جدید ۱ ساعت پیش",
            comment_intent_type="QUESTION_INQUIRY",
            comment_created_at=now - timedelta(hours=1),
            outreach_score=25,
        )
        # Lead 3: Historical lead with NULL comment_created_at (must appear last, not dropped)
        lead_null = InstagramEngineLead.objects.create(
            ig_user_pk="user_pk_103",
            username="lead_historical_null",
            comment_text="تاریخی بدون تاریخ کامنت",
            comment_intent_type="QUESTION_INQUIRY",
            comment_created_at=None,
            outreach_score=30,
        )

        self.client.force_login(self.staff_user)
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)

        leads = response.context["recent_leads"]
        self.assertEqual(len(leads), 3)
        self.assertEqual(leads[0]["username"], lead_recent.username)
        self.assertEqual(leads[1]["username"], lead_old.username)
        self.assertEqual(leads[2]["username"], lead_null.username)

    def test_scraper_query_isolation_no_state_leak(self):
        """
        Verify scraper state isolation: crawl_comments, top_code, top_url
        do not leak from one query to the next even if an error occurs.
        """
        from unittest.mock import MagicMock
        from apps.discovery.management.commands.run_instagram_scraper import Command

        cmd = Command()
        scraper = InstagramEngineAccount.objects.create(
            username="scraper_isolation_test",
            role="SCRAPER",
            status="ACTIVE",
        )

        mock_media = MagicMock()
        mock_media.id = "media_iso_1"
        mock_media.pk = "media_iso_1"
        mock_media.code = "CODE_ISO_1"
        mock_media.taken_at = timezone.now()
        mock_media.caption_text = "کپشن تست ایزولاسیون"
        mock_media.comment_count = 50

        mock_comment = MagicMock()
        mock_comment.text = "قیمت چنده لطفا؟"
        mock_comment.created_at_utc = timezone.now()
        mock_comment.created_at = timezone.now()
        mock_comment.user = MagicMock(pk="user_iso_1", username="user_iso_1", full_name="کاربر تست", is_private=False)

        cl = MagicMock()
        cl.media_comments.return_value = [mock_comment]

        target = {"tenant_id": "tenant_iso", "name": "تست کسب‌وکار", "business": None}
        seen = set()

        # Query 1: Processes media successfully
        p_insp1, p_qual1, l_sav1, ret1, c_com1, t_code1, t_url1 = cmd._inspect_and_process_medias(
            cl=cl, account=scraper, target=target, medias=[mock_media],
            min_comments=10, seen_media_ids=seen, query_label="query_1",
            check_domain_relevance=False,
        )
        self.assertEqual(c_com1, 1)
        self.assertEqual(t_code1, "CODE_ISO_1")
        self.assertIn("CODE_ISO_1", t_url1)

        # Query 2: Empty media / failed fetch
        p_insp2, p_qual2, l_sav2, ret2, c_com2, t_code2, t_url2 = cmd._inspect_and_process_medias(
            cl=cl, account=scraper, target=target, medias=[],
            min_comments=10, seen_media_ids=seen, query_label="query_2",
            check_domain_relevance=False,
        )
        self.assertEqual(c_com2, 0)
        self.assertEqual(t_code2, "")
        self.assertEqual(t_url2, "")
        self.assertEqual(cmd._last_crawl_comments, 0)
        self.assertEqual(cmd._last_top_code, "")
        self.assertEqual(cmd._last_top_url, "")

    def test_query_count_bounded_telemetry(self):
        """
        Verify that per-account telemetry query count is strictly bounded
        and does not scale with the number of scraper accounts (O(1) queries).
        """
        now = timezone.now()
        for i in range(5):
            acc = InstagramEngineAccount.objects.create(
                username=f"scraper_bulk_{i}",
                role="SCRAPER",
                status="ACTIVE",
            )
            InstagramCrawlExecution.objects.create(
                account=acc,
                account_username=acc.username,
                query_text=f"#tag_{i}",
                status="SUCCESS",
                crawled_at=now,
                leads_found=1,
            )
            InstagramCrawledPost.objects.create(
                post_hash=f"post_hash_bulk_{i}",
                media_id=f"media_bulk_{i}",
                media_code=f"CODE_{i}",
                account_username=acc.username,
                posted_at=now,
                crawled_at=now,
            )

        self.client.force_login(self.staff_user)
        # Verifies bounded query count (17 queries total) for 5 scraper accounts without N+1 per account
        with self.assertNumQueries(17):
            response = self.client.get(self.url)
            self.assertEqual(response.status_code, 200)
            self.assertEqual(len(response.context["scrapers"]), 5)

    def test_direct_sender_lead_qualification_pipeline(self):
        """
        Regression test: Verify that scraper discovered leads with status='NEW'
        are properly identified and selected as eligible candidates by Direct Sender outreach.
        """
        from apps.businesses.models import Business
        from apps.discovery.outreach_service import select_outreach_candidates

        business = Business.objects.create(
            user=self.staff_user,
            name="کسب‌وکار کلینیک دندانپزشکی",
            business_domain="دندانپزشکی و ایمپلنت",
        )
        tenant_id = f"tenant_{business.pk}"

        lead = InstagramEngineLead.objects.create(
            tenant_id=tenant_id,
            ig_user_pk="user_pk_implant_1",
            username="implant_lead_candidate",
            full_name="متقاضی ایمپلنت",
            comment_text="هزینه کاشت دندان چقدر است؟",
            comment_intent_type="QUESTION_INQUIRY",
            outreach_score=35,
            success_probability=90,
            status="NEW",
            comment_created_at=timezone.now() - timedelta(hours=2),
        )

        candidates = select_outreach_candidates(
            tenant_ids=[tenant_id],
            slots=5,
        )
        candidate_usernames = [c.username for c in candidates]
        self.assertIn("implant_lead_candidate", candidate_usernames)

