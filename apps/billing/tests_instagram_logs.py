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
    InstagramAccountEvent,
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
            comment_intent_type="CONSULTATION",
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
