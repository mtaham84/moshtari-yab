"""Tests for safe, POST-only, dispatcher-served inbox reply sync (Roadmap 7.2 / D-2)."""
import json
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch
from zoneinfo import ZoneInfo

from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.auth.models import AnonymousUser
from django.contrib.messages.storage.fallback import FallbackStorage
from django.contrib.sessions.middleware import SessionMiddleware
from django.test import RequestFactory, SimpleTestCase, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from apps.accounts import outreach_views
from apps.businesses.models import Business
from apps.discovery.models import OutreachTenantProfile

User = get_user_model()
TEHRAN_TZ = ZoneInfo("Asia/Tehran")


def _attach_messages(req):
    SessionMiddleware(lambda r: None).process_request(req)
    req._messages = FallbackStorage(req)
    return req


class SyncRepliesViewUnitTests(SimpleTestCase):
    """Fast unit tests (no DB required) for sync_replies_view and UI callers."""

    def setUp(self):
        self.rf = RequestFactory()
        self.url = reverse("accounts:sync_replies")

    def _seller_user(self, biz_id=12, is_staff=False):
        biz = SimpleNamespace(id=biz_id, pk=biz_id, name="آکادمی تست")
        return SimpleNamespace(
            pk=10,
            is_authenticated=True,
            is_staff=is_staff,
            is_superuser=False,
            business=biz,
            groups=SimpleNamespace(filter=lambda **kw: SimpleNamespace(exists=lambda: False)),
        )

    def test_get_sync_replies_returns_405(self):
        req = self.rf.get(self.url)
        req.user = self._seller_user()
        with patch("apps.discovery.outreach.replies.check_and_sync_lead_replies") as ig_sync:
            resp = outreach_views.sync_replies_view(req)
        self.assertEqual(resp.status_code, 405)
        ig_sync.assert_not_called()

    def test_anonymous_post_redirects_to_login(self):
        req = self.rf.post(self.url)
        req.user = AnonymousUser()
        resp = outreach_views.sync_replies_view(req)
        self.assertEqual(resp.status_code, 302)
        self.assertIn("/login", resp["Location"])

    def test_post_queues_for_dispatcher_without_touching_instagram(self):
        req = self.rf.post(self.url, HTTP_X_REQUESTED_WITH="XMLHttpRequest")
        req.user = self._seller_user(biz_id=12)
        queued = {"status": "queued", "queued": True, "allowed": True, "wait_seconds": 0, "message": "ثبت شد"}
        with patch("apps.discovery.outreach.replies.request_reply_sync", return_value=queued) as req_sync, \
             patch("apps.discovery.outreach.replies.check_and_sync_lead_replies") as ig_sync:
            resp = outreach_views.sync_replies_view(req)
        self.assertEqual(resp.status_code, 200)
        data = json.loads(resp.content)
        self.assertTrue(data["success"])
        self.assertTrue(data["queued"])
        self.assertEqual(data["status"], "queued")
        req_sync.assert_called_once_with("tenant_12")
        ig_sync.assert_not_called()

    def test_post_outside_shift_returns_when_it_will_run(self):
        req = _attach_messages(self.rf.post(self.url))
        req.user = self._seller_user(biz_id=12)
        outside = {
            "status": "outside_shift",
            "queued": True,
            "allowed": False,
            "wait_seconds": 3600,
            "next_run_at": "09:45",
            "message": "ربات خارج از ساعت کاری است؛ در شروع شیفت بعدی (09:45) انجام می‌شود.",
        }
        with patch("apps.discovery.outreach.replies.request_reply_sync", return_value=outside):
            resp = outreach_views.sync_replies_view(req)
        self.assertEqual(resp.status_code, 302)
        stored = [m.message for m in req._messages]
        self.assertTrue(any("09:45" in m for m in stored), stored)

    def test_second_click_inside_spacing_returns_429(self):
        req = self.rf.post(self.url, HTTP_X_REQUESTED_WITH="XMLHttpRequest")
        req.user = self._seller_user(biz_id=12)
        throttled = {
            "status": "throttled",
            "queued": False,
            "allowed": True,
            "wait_seconds": 240,
            "next_run_at": "11:05",
            "message": "به‌تازگی بررسی شده است؛ ۴ دقیقه دیگر مجاز است.",
        }
        with patch("apps.discovery.outreach.replies.request_reply_sync", return_value=throttled), \
             patch("apps.discovery.outreach.replies.check_and_sync_lead_replies") as ig_sync:
            resp = outreach_views.sync_replies_view(req)
        self.assertEqual(resp.status_code, 429)
        data = json.loads(resp.content)
        self.assertFalse(data["success"])
        self.assertFalse(data["queued"])
        self.assertEqual(data["status"], "throttled")
        ig_sync.assert_not_called()

    def test_seller_cannot_target_other_business_via_post_param(self):
        req = self.rf.post(self.url, {"biz": "999"}, HTTP_X_REQUESTED_WITH="XMLHttpRequest")
        req.user = self._seller_user(biz_id=12, is_staff=False)
        queued = {"status": "queued", "queued": True, "allowed": True, "wait_seconds": 0, "message": "ok"}
        with patch("apps.discovery.outreach.replies.request_reply_sync", return_value=queued) as req_sync:
            resp = outreach_views.sync_replies_view(req)
        self.assertEqual(resp.status_code, 200)
        req_sync.assert_called_once_with("tenant_12")

    def test_operator_can_sync_selected_business_from_manage(self):
        req = _attach_messages(self.rf.post(self.url, {"biz": "99", "next": "manage"}))
        req.user = self._seller_user(biz_id=12, is_staff=True)
        req.user.is_staff = True
        selected_biz = SimpleNamespace(id=99, pk=99, name="مشتری ۹۹")
        queued = {"status": "queued", "queued": True, "allowed": True, "wait_seconds": 0, "message": "ok"}
        with patch("apps.businesses.models.Business.objects") as mock_biz_qs, \
             patch("apps.discovery.outreach.replies.request_reply_sync", return_value=queued) as req_sync:
            mock_biz_qs.filter.return_value.first.return_value = selected_biz
            resp = outreach_views.sync_replies_view(req)
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(resp["Location"], reverse("manage:dashboard"))
        req_sync.assert_called_once_with("tenant_99")

    def test_templates_and_js_use_post_for_sync_replies(self):
        root = Path(settings.BASE_DIR)
        tab_html = (root / "templates" / "ops" / "manage" / "tab_outreach.html").read_text(encoding="utf-8")
        self.assertNotIn("<a href=\"{% url 'accounts:sync_replies' %}\"", tab_html)
        self.assertIn('method="post"', tab_html.lower())
        outreach_js = (root / "static" / "js" / "outreach.js").read_text(encoding="utf-8")
        sync_fn_block = outreach_js[outreach_js.index("window.syncChatReplies"):outreach_js.index("window.filterChatLeads")]
        self.assertIn("POST", sync_fn_block)
        self.assertIn("X-CSRFToken", sync_fn_block)


class ReplySyncDispatcherUnitTests(SimpleTestCase):
    """Tests request_reply_sync + serve_reply_syncs logic with an in-memory profile stub."""

    def _fake_profile_store(self):
        store = {}

        class _QuerySet:
            def __init__(self, rows):
                self._rows = rows

            def filter(self, **kw):
                rows = list(self._rows)
                if "tenant_id" in kw:
                    rows = [r for r in rows if r.tenant_id == kw["tenant_id"]]
                if "tenant_id__in" in kw:
                    rows = [r for r in rows if r.tenant_id in kw["tenant_id__in"]]
                if kw.get("reply_sync_requested_at__isnull") is False:
                    rows = [r for r in rows if r.reply_sync_requested_at is not None]
                return _QuerySet(rows)

            def select_for_update(self):
                return self

            def get(self, tenant_id):
                return store[tenant_id]

            def exists(self):
                return bool(self._rows)

        class _Objects:
            def get_or_create(self, tenant_id):
                if tenant_id not in store:
                    store[tenant_id] = SimpleNamespace(
                        tenant_id=tenant_id,
                        reply_sync_requested_at=None,
                        last_reply_sync_at=None,
                        save=lambda update_fields=None: None,
                    )
                    return store[tenant_id], True
                return store[tenant_id], False

            def filter(self, **kw):
                return _QuerySet(list(store.values())).filter(**kw)

            def select_for_update(self):
                return _QuerySet(list(store.values()))

        return store, _Objects()

    @override_settings(INSTAGRAM_REPLY_SYNC_MIN_SECONDS=300)
    def test_second_click_inside_spacing_does_not_touch_instagram(self):
        from apps.discovery.outreach import replies

        store, fake_objects = self._fake_profile_store()
        sync_fn = MagicMock(return_value={"status": "success", "replies_found": 0, "details": []})
        t0 = datetime(2026, 11, 10, 10, 30, tzinfo=TEHRAN_TZ)

        with patch.object(OutreachTenantProfile, "objects", fake_objects), \
             patch("apps.discovery.outreach.replies.transaction.atomic", side_effect=lambda: MagicMock(__enter__=lambda s: None, __exit__=lambda s, *a: False)), \
             patch("apps.discovery.outreach_engine.reply_sync_window", return_value=(True, "", 0)):
            # 1. First click -> queued -> dispatcher serves it once
            res1 = replies.request_reply_sync("tenant_7", now=t0)
            self.assertEqual(res1["status"], "queued")
            self.assertTrue(res1["queued"])
            replies.serve_reply_syncs([["tenant_7"]], requested_only=True, sync_fn=sync_fn, now=t0 + timedelta(seconds=10))
            self.assertEqual(sync_fn.call_count, 1)

            # 2. Second click 60s later (inside 300s spacing) -> throttled, dispatcher does NOT call Instagram
            t1 = t0 + timedelta(seconds=60)
            res2 = replies.request_reply_sync("tenant_7", now=t1)
            self.assertEqual(res2["status"], "throttled")
            self.assertFalse(res2["queued"])
            replies.serve_reply_syncs([["tenant_7"]], requested_only=False, sync_fn=sync_fn, now=t1 + timedelta(seconds=5))
            self.assertEqual(sync_fn.call_count, 1)

            # 3. Third click after spacing elapsed (315s after first sync) -> queued & served
            t2 = t0 + timedelta(seconds=325)
            res3 = replies.request_reply_sync("tenant_7", now=t2)
            self.assertEqual(res3["status"], "queued")
            replies.serve_reply_syncs([["tenant_7"]], requested_only=True, sync_fn=sync_fn, now=t2 + timedelta(seconds=5))
            self.assertEqual(sync_fn.call_count, 2)

    @override_settings(INSTAGRAM_REPLY_SYNC_MIN_SECONDS=300)
    def test_outside_shift_tells_next_run_and_waits_for_shift_to_open(self):
        from apps.discovery.outreach import replies

        store, fake_objects = self._fake_profile_store()
        sync_fn = MagicMock(return_value={"status": "success", "replies_found": 0, "details": []})
        t_night = datetime(2026, 11, 10, 22, 0, tzinfo=TEHRAN_TZ)

        with patch.object(OutreachTenantProfile, "objects", fake_objects), \
             patch("apps.discovery.outreach.replies.transaction.atomic", side_effect=lambda: MagicMock(__enter__=lambda s: None, __exit__=lambda s, *a: False)):
            with patch("apps.discovery.outreach_engine.reply_sync_window",
                       return_value=(False, "پایان شیفت‌های کاری امروز (شروع شیفت فردا در 09:50)", 42600)):
                res = replies.request_reply_sync("tenant_7", now=t_night)
                self.assertEqual(res["status"], "outside_shift")
                self.assertTrue(res["queued"])
                self.assertIn("09:50", res["message"])
                self.assertIsNotNone(store["tenant_7"].reply_sync_requested_at)

                # Dispatcher outside shift does NOT touch Instagram
                replies.serve_reply_syncs([["tenant_7"]], requested_only=False, sync_fn=sync_fn, now=t_night)
                sync_fn.assert_not_called()

            # Once shift opens, dispatcher serves the queued request once
            t_morning = datetime(2026, 11, 11, 9, 51, tzinfo=TEHRAN_TZ)
            with patch("apps.discovery.outreach_engine.reply_sync_window", return_value=(True, "", 0)):
                replies.serve_reply_syncs([["tenant_7"]], requested_only=True, sync_fn=sync_fn, now=t_morning)
                sync_fn.assert_called_once_with(["tenant_7"])
                self.assertIsNone(store["tenant_7"].reply_sync_requested_at)

    def test_celery_beat_serves_pending_panel_request_even_on_random_skip(self):
        from apps.discovery.tasks import task_sync_lead_replies

        with patch("apps.discovery.tasks.reply_sync_allowed", return_value=(True, "")), \
             patch("apps.discovery.tenants.outreach_tenants", return_value=["tenant_7"]), \
             patch("apps.discovery.tenants.tenant_ids", side_effect=lambda t: [t]), \
             patch("apps.discovery.tasks.random.random", return_value=0.05), \
             patch("apps.discovery.tasks.has_pending_reply_sync_requests", return_value=True), \
             patch("apps.discovery.tasks.serve_reply_syncs", return_value=[{"replies_found": 2, "details": []}]) as serve:
            res = task_sync_lead_replies()
        self.assertEqual(res["replies_found"], 2)
        serve.assert_called_once()
        self.assertTrue(serve.call_args.kwargs["requested_only"])

    def test_outreach_command_sync_replies_routes_through_serve_reply_syncs(self):
        from io import StringIO
        from apps.discovery.management.commands.run_instagram_outreach import Command

        cmd = Command(stdout=StringIO(), stderr=StringIO())
        with patch("apps.discovery.management.commands.run_instagram_outreach.serve_reply_syncs",
                   return_value=[{"replies_found": 1, "details": [{"lead": "ali", "reply_text": "سلام"}]}]) as serve:
            cmd._sync_replies([["tenant_7"]], requested_only=True)
        serve.assert_called_once()
        self.assertTrue(serve.call_args.kwargs["requested_only"])


@override_settings(INSTAGRAM_REPLY_SYNC_MIN_SECONDS=300)
class SyncRepliesIntegrationTests(TestCase):
    """Full PostgreSQL integration tests for /accounts/outreach/sync-replies/ + dispatcher."""

    def setUp(self):
        self.user = User.objects.create_user(username="reply_seller@test.ir", password="secret")
        self.business = Business.objects.create(user=self.user, name="آموزشگاه تست", onboarding_done=True)
        self.tenant = f"tenant_{self.business.id}"
        self.client.force_login(self.user)
        self.url = reverse("accounts:sync_replies")

    def test_get_rejected_and_second_click_inside_spacing_does_not_touch_instagram(self):
        self.assertEqual(self.client.get(self.url).status_code, 405)
        from apps.discovery.outreach.replies import serve_reply_syncs

        ig_sync = MagicMock(return_value={"status": "success", "replies_found": 1, "details": []})
        now = timezone.now()
        with patch("apps.discovery.outreach_engine.reply_sync_window", return_value=(True, "", 0)), \
             patch("apps.discovery.outreach.replies.check_and_sync_lead_replies", ig_sync):
            r1 = self.client.post(self.url, HTTP_X_REQUESTED_WITH="XMLHttpRequest")
            self.assertEqual(r1.status_code, 200)
            self.assertTrue(r1.json()["queued"])
            ig_sync.assert_not_called()  # web process never calls Instagram

            serve_reply_syncs([[self.tenant]], requested_only=True, now=now)
            self.assertEqual(ig_sync.call_count, 1)

            r2 = self.client.post(self.url, HTTP_X_REQUESTED_WITH="XMLHttpRequest")
            self.assertEqual(r2.status_code, 429)
            self.assertFalse(r2.json()["queued"])
            serve_reply_syncs([[self.tenant]], requested_only=False, now=now + timedelta(seconds=30))
            self.assertEqual(ig_sync.call_count, 1)
