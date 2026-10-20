"""Panel side of the pipeline: need_engine.opportunities → panel records → views, dashboard and product-card tracking."""
import io
import csv
import json
import tempfile
import uuid
from datetime import datetime, timedelta, timezone
from io import StringIO
from pathlib import Path

from django.db import connection

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.test import Client, TestCase, override_settings
from django.urls import reverse

from apps.businesses.models import Business
from apps.products.models import Category, Product

from .engine_bridge import engine_totals, import_opportunity
from .models import Customer, InstagramPage, Opportunity, ProductOrder
from .views import _safe_csv_cell

User = get_user_model()
T0 = datetime(2026, 10, 8, 10, 0, tzinfo=timezone.utc)


class EngineSchemaMixin:
    """A throw-away need_engine schema inside Django's test database (same DB, like production)."""

    def tearDown(self):
        st = getattr(self, "_ne_store", None)
        if st is not None:               # schema stays until the test database is destroyed (Django holds locks on it)
            st.close()
            self._override.disable()
        super().tearDown()

    def dsn(self) -> str:
        from psycopg.conninfo import make_conninfo

        sd = connection.settings_dict
        params = {"host": sd["HOST"], "port": sd["PORT"], "dbname": sd["NAME"], "user": sd["USER"], "password": sd["PASSWORD"]}
        return make_conninfo(**{k: v for k, v in params.items() if v})

    def store(self):
        """Engine state in a fresh schema; the panel (NEED_ENGINE_SCHEMA) reads the same schema."""
        from need_engine.store import Store

        if getattr(self, "_ne_store", None) is None:
            schema = f"ne_{uuid.uuid4().hex[:10]}"
            self._override = override_settings(NEED_ENGINE_SCHEMA=schema)
            self._override.enable()
            self._ne_store = Store(self.dsn(), schema)
        return self._ne_store


def seller(email: str, name: str) -> tuple:
    user = User.objects.create_user(username=email, email=email, password="pass12345", first_name=name)
    return user, Business.objects.create(user=user, name=name, business_type="PHYSICAL", business_domain="عمومی")


def global_source(chat_id: int = -1001):
    """The engine payloads below come from chat -1001: make it a source every seller sees."""
    from .models import MonitoredCommunity
    return MonitoredCommunity.objects.create(business=None, handle_or_link="@moto_global_src", telegram_chat_id=chat_id,
                                             sync_status="ACTIVE")


def engine_payload(opp_id: str, product_ids: list[int], status: str = "open") -> dict:
    return {
        "opportunity_id": opp_id, "status": status,
        "created_at": T0.isoformat(), "expires_at": (T0 + timedelta(days=7)).isoformat(),
        "candidate": {"external_user_id": "telegram_11", "customer_name": "علی", "username": "ali",
                      "profile_url": "https://t.me/ali", "phone_number": None},
        "source": {"platform": "telegram", "chat_id": "-1001", "chat_title": "گروه موتورسواران", "evidence": [
            {"message_id": "2", "timestamp": T0.isoformat(), "author": "علی", "text": "دستام تو موتور یخ می‌زنه",
             "url": "https://t.me/moto/2"}]},
        "need": {"label": "implicit_need", "strength": "strong", "situation": "در سرما موتور سواری می‌کند",
                 "summary": "دستکش گرم موتور", "requirements": [], "constraints": {"budget_toman": 1000000}, "priority": 0.8},
        "matched_products": [
            {"product_id": str(pid), "match_score": score, "similarity": 0.7,
             "verdict": {"solves": "yes", "met": 1, "unmet": 0, "unknown": 0, "total": 1, "conflicts": [], "checks": [],
                         "reason": "گرم و ضدآب"}, "reply_draft": f"سلام! https://x/p/{pid}?ref={opp_id}"}
            for pid, score in zip(product_ids, (0.9, 0.6, 0.5))],
        "cost": {"toman": 12.4, "llm_calls": 3},
    }


class EngineImportTests(EngineSchemaMixin, TestCase):
    def setUp(self):
        self.user, self.biz = seller("a@example.com", "فروشگاه الف")
        _, self.other = seller("b@example.com", "فروشگاه ب")
        self.cat = Category.objects.create(business=self.biz, name="دستکش")
        self.p1 = Product.objects.create(business=self.biz, category=self.cat, name="دستکش گرم", description="x", price=900000)
        self.p2 = Product.objects.create(business=self.biz, name="دستکش چرمی", description="x", price=1200000)
        self.q1 = Product.objects.create(business=self.other, name="دستکش موتور", description="x", price=800000)
        self.global_src = global_source()

    def test_private_chat_only_reaches_its_owners(self):
        from .models import MonitoredCommunity

        self.global_src.delete()
        self.assertEqual(import_opportunity(engine_payload("need_000020_ab", [self.p1.id, self.q1.id])), [])  # no source
        MonitoredCommunity.objects.create(business=self.other, handle_or_link="@moto", telegram_chat_id=-1001)
        out = import_opportunity(engine_payload("need_000020_ab", [self.p1.id, self.q1.id]))
        self.assertEqual([o.business_id for o in out], [self.other.id])

    def test_one_opportunity_per_seller_with_ranked_matches_and_evidence(self):
        opps = import_opportunity(engine_payload("need_000001_ab", [self.p1.id, self.q1.id, self.p2.id]))
        self.assertEqual(len(opps), 2)
        mine = Opportunity.objects.get(business=self.biz)
        self.assertEqual(mine.status, "NEW")
        self.assertEqual([m.product_id for m in mine.product_matches.order_by("rank")], [self.p1.id, self.p2.id])
        self.assertEqual(mine.category, self.cat)
        self.assertEqual(mine.customer.source_username, "ali")
        self.assertEqual(mine.ai_analysis.need, "دستکش گرم موتور")
        self.assertEqual(mine.ai_analysis.cost_toman, 12)
        self.assertIn("https://t.me/moto/2", mine.evidence_items.values_list("source_reference", flat=True))
        self.assertEqual(Opportunity.objects.get(business=self.other).product_matches.get().product, self.q1)

    def test_x_opportunity_keeps_x_customer_and_evidence_links(self):
        payload = engine_payload("need_x_000001", [self.p1.id])
        payload["candidate"].update(external_user_id="81001", username="buyer", profile_url="https://x.com/buyer")
        payload["source"].update(platform="x", chat_id="x:public", evidence=[
            {"message_id": "1990000000000001001", "timestamp": T0.isoformat(), "author": "خریدار",
             "text": "دنبال دستگاه هستم", "url": "https://x.com/buyer/status/1990000000000001001"}])
        import_opportunity(payload)
        opportunity = Opportunity.objects.get(business=self.biz)
        self.assertEqual(opportunity.source_platform, "x")
        self.assertEqual(opportunity.customer.source_profile_url, "https://x.com/buyer")
        self.assertIn("https://x.com/buyer/status/1990000000000001001",
                      opportunity.evidence_items.get().source_reference)

    def test_reimport_is_idempotent_and_status_updates_respect_seller_choice(self):
        import_opportunity(engine_payload("need_000002_ab", [self.p1.id, self.q1.id]))
        import_opportunity(engine_payload("need_000002_ab", [self.p1.id, self.q1.id]))
        self.assertEqual(Opportunity.objects.count(), 2)
        self.assertEqual(Customer.objects.count(), 2)
        self.assertEqual(Opportunity.objects.get(business=self.biz).product_matches.count(), 1)

        Opportunity.objects.filter(business=self.other).update(status="CONTACTED")
        import_opportunity(engine_payload("need_000002_ab", [self.p1.id, self.q1.id], status="resolved"))
        self.assertEqual(Opportunity.objects.get(business=self.biz).status, "RESOLVED")
        self.assertEqual(Opportunity.objects.get(business=self.other).status, "CONTACTED")  # seller's status kept

    def test_new_opportunity_counts_as_a_lead_of_the_sellers_community(self):
        from .models import MonitoredCommunity

        mine = MonitoredCommunity.objects.create(business=self.biz, name="موتورسواران", handle_or_link="@moto",
                                                 telegram_chat_id=-1001, sync_status="ACTIVE")
        theirs = MonitoredCommunity.objects.create(business=self.other, name="x", handle_or_link="@moto")  # not joined yet
        import_opportunity(engine_payload("need_000004_ab", [self.p1.id, self.q1.id]))
        import_opportunity(engine_payload("need_000004_ab", [self.p1.id, self.q1.id]))   # update ≠ new lead
        mine.refresh_from_db(); theirs.refresh_from_db()
        self.assertEqual((mine.leads_discovered_count, theirs.leads_discovered_count), (1, 0))

    def test_communities_page_shows_crawler_status_and_reactivation_clears_error(self):
        from .models import MonitoredCommunity

        c = MonitoredCommunity.objects.create(business=self.biz, name="گروه بد", handle_or_link="@bad", is_active=False,
                                              sync_status="ERROR", sync_error="No user has \"bad\" as username")
        MonitoredCommunity.objects.create(business=self.biz, name="گروه تازه", handle_or_link="https://t.me/+AbCd")
        client = Client()
        client.force_login(self.user)
        html = client.get(reverse("discovery:communities_list")).content.decode()
        self.assertIn("در صف اتصال", html)
        self.assertIn('href="https://t.me/+AbCd"', html)
        client.post(reverse("discovery:community_toggle", args=[c.id]))
        c.refresh_from_db()
        self.assertEqual((c.is_active, c.sync_status, c.sync_error), (True, "PENDING", ""))
        MonitoredCommunity.objects.filter(id=c.id).update(sync_status="ERROR", sync_error="No user has bad")
        self.assertIn("خطا در اتصال", client.get(reverse("discovery:communities_list")).content.decode())

    def test_unknown_products_are_skipped(self):
        self.assertEqual(import_opportunity(engine_payload("need_000003_ab", [999999])), [])
        self.assertFalse(Opportunity.objects.exists())

    def test_cost_per_message_is_per_seller(self):
        from .models import MonitoredCommunity

        st = self.store()
        MonitoredCommunity.objects.create(business=self.biz, handle_or_link="@my_private", telegram_chat_id=-1002)
        st.add_cost("need_extraction", "m", 100, 10, False, 0.0, 30.0, businesses=[str(self.biz.id), str(self.other.id)])
        st.add_cost("reply", "m", 10, 10, False, 0.0, 5.0, businesses=[str(self.biz.id)])
        st.add_cost("need_extraction", "m", 10, 10, False, 0.0, 100.0)                      # global chat: platform
        st._exec("INSERT INTO {s}.chat_analysed VALUES ('-1001', 10), ('-1002', 10), ('-1003', 50)")
        mine = engine_totals(self.biz)
        self.assertEqual((mine["messages_analysed"], mine["cost_toman"], mine["llm_calls"]), (20, 20.0, 2))
        self.assertEqual(mine["cost_per_message_toman"], 1.0)
        self.assertEqual(engine_totals(self.other)["cost_toman"], 15.0)
        everything = engine_totals()
        self.assertEqual((everything["cost_toman"], everything["llm_calls"]), (135.0, 3))   # a shared call counts once

    def test_sync_command_imports_published_versions_once(self):
        from need_engine.schemas import Opportunity as EngineOpportunity

        st = self.store()
        call_command("sync_opportunities", stdout=StringIO())             # engine tables not there yet → no error
        st.publish(EngineOpportunity.model_validate(engine_payload("need_000004_ab", [self.p1.id])))
        call_command("sync_opportunities", stdout=StringIO())
        self.assertEqual(Opportunity.objects.count(), 1)
        st.publish(EngineOpportunity.model_validate(engine_payload("need_000004_ab", [self.p1.id], "expired")))
        out = StringIO()
        call_command("sync_opportunities", stdout=out)
        self.assertIn("imported 1", out.getvalue())                         # only the new version is read
        self.assertEqual(Opportunity.objects.get().status, "EXPIRED")
        call_command("sync_opportunities", "--from-start", stdout=StringIO())   # re-import is idempotent
        self.assertEqual(Opportunity.objects.count(), 1)

    def test_engine_end_to_end_with_mock_llm(self):
        """Real need_engine run (offline mock LLM) on chat JSON + this panel's products → panel records."""
        from need_engine.config import EngineConfig
        from need_engine.engine import NeedEngine
        from need_engine.mock import mock_llm

        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            (d / "chats.jsonl").write_text(json.dumps({"chat_id": "C1", "group_title": "موتورسواران", "messages": [
                {"message_id": 1, "date": T0.isoformat(), "author_id": "u1", "author_name": "علی",
                 "text": "دنبال دستکش گرم برای موتورم بودجه 1 میلیون"}]}, ensure_ascii=False), encoding="utf-8")
            (d / "products.jsonl").write_text("\n".join(json.dumps(
                {"product_id": str(p.id), "seller_id": str(p.business_id), "title": p.name, "description": p.description,
                 "price_toman": int(p.price)}, ensure_ascii=False) for p in Product.objects.all()), encoding="utf-8")
            cfg = EngineConfig()
            cfg.embed_backend, cfg.max_workers = "hash", 1
            cfg.messages_source, cfg.products_source = f"jsonl:{d / 'chats.jsonl'}", f"jsonl:{d / 'products.jsonl'}"
            report = NeedEngine(cfg, mock_llm=mock_llm, store=self.store()).run_once(flush=True)
            self.assertGreaterEqual(len(report.opportunities), 1)

            call_command("sync_opportunities", stdout=StringIO())
            self.assertTrue(Opportunity.objects.filter(source_platform="telegram").exists())
            totals = engine_totals()
            self.assertEqual(totals["messages_analysed"], 1)
            self.assertGreater(totals["cost_toman"], 0)


class PanelViewTests(TestCase):
    def setUp(self):
        self.user, self.biz = seller("a@example.com", "فروشگاه الف")
        self.other_user, self.other = seller("b@example.com", "فروشگاه ب")
        self.p1 = Product.objects.create(business=self.biz, name="دستکش گرم", description="x", price=900000)
        self.q1 = Product.objects.create(business=self.other, name="دستکش موتور", description="x", price=800000)
        global_source()
        import_opportunity(engine_payload("need_000010_ab", [self.p1.id, self.q1.id]))
        self.mine = Opportunity.objects.get(business=self.biz)
        self.client = Client()
        self.client.force_login(self.user)

    def test_list_and_detail_are_scoped_to_the_seller(self):
        r = self.client.get(reverse("discovery:opportunity_list"))
        self.assertEqual(r.status_code, 200)
        self.assertEqual(list(r.context["opportunities"]), [self.mine])
        self.assertEqual(self.client.get(reverse("discovery:opportunity_detail", args=[self.mine.pk])).status_code, 200)
        self.assertContains(self.client.get(reverse("discovery:opportunity_list")), "مشتری واقعی")
        self.assertContains(self.client.get(reverse("discovery:opportunity_list")), "نامرتبط")
        theirs = Opportunity.objects.get(business=self.other)
        self.assertEqual(self.client.get(reverse("discovery:opportunity_detail", args=[theirs.pk])).status_code, 404)

    def test_filters_sort_and_csv_export(self):
        url = reverse("discovery:opportunity_list")
        for params in ({}, {"product_id": self.p1.id, "sort": "intent_desc"}, {"status": "RESOLVED"}, {"min_score": "50", "q": "دستکش"},
                       {"date_from": "1400/01/01", "date_to": "1499/12/29", "sort": "oldest", "view": "cards"}):
            self.assertEqual(self.client.get(url, params).status_code, 200, params)
        self.assertNotContains(self.client.get(url, {"status": "RESOLVED"}), "dummy-never")
        resp = self.client.get(reverse("discovery:opportunity_export"))
        self.assertEqual(resp.status_code, 200)
        body = resp.content.decode("utf-8-sig")
        self.assertIn("دستکش گرم", body)
        self.assertNotIn("دستکش موتور", body)            # only the seller's own opportunities

    def test_csv_export_escapes_formulas_and_keeps_persian_and_empty_marker(self):
        self.mine.customer.source_username = ""
        self.mine.customer.external_user_id = "telegram_=HYPERLINK(\"https://bad.invalid\")"
        self.mine.customer.source_profile_url = "https://t.me/ali"
        self.mine.customer.save()
        self.mine.source_raw_message = "=HYPERLINK(\"https://bad.invalid\")"
        self.mine.save()
        match = self.mine.product_matches.first()
        match.product.name = "+1+1"
        match.product.save(update_fields=["name"])
        self.mine.ai_analysis.suggested_reply = "@SUM(1,1)"
        self.mine.ai_analysis.save(update_fields=["suggested_reply"])

        response = self.client.get(reverse("discovery:opportunity_export"))

        self.assertEqual(response.content[:3], b"\xef\xbb\xbf")
        rows = list(csv.reader(io.StringIO(response.content.decode("utf-8-sig"))))
        self.assertEqual(rows[1][0], "'=HYPERLINK(\"https://bad.invalid\")")
        self.assertEqual(rows[1][2], "https://t.me/ali")
        self.assertEqual(rows[1][3], "'=HYPERLINK(\"https://bad.invalid\")")
        self.assertEqual(rows[1][4], "'+1+1")
        self.assertEqual(rows[1][5], "'@SUM(1,1)")
        self.assertEqual(rows[1][1], "تلگرام")

    def test_csv_cell_helper_escapes_formula_prefixes(self):
        for prefix in ("=", "+", "-", "@", "\t", "\r"):
            with self.subTest(prefix=repr(prefix)):
                self.assertEqual(_safe_csv_cell(prefix + "1"), "'" + prefix + "1")
        self.assertEqual(_safe_csv_cell("-"), "-")
        self.assertEqual(_safe_csv_cell("متن فارسی"), "متن فارسی")

    def test_csv_export_keeps_dash_placeholder(self):
        self.mine.customer.source_username = ""
        self.mine.customer.external_user_id = ""
        self.mine.customer.phone_number = None
        self.mine.customer.name = ""
        self.mine.customer.source_profile_url = ""
        self.mine.customer.save()
        self.mine.source_raw_message = ""
        self.mine.save()
        self.mine.product_matches.all().delete()
        self.mine.ai_analysis.suggested_reply = ""
        self.mine.ai_analysis.save(update_fields=["suggested_reply"])

        response = self.client.get(reverse("discovery:opportunity_export"))
        rows = list(csv.reader(io.StringIO(response.content.decode("utf-8-sig"))))

        self.assertEqual(rows[1][0], "-")
        self.assertEqual(rows[1][2], "-")
        self.assertEqual(rows[1][3], "-")
        self.assertEqual(rows[1][4], "-")
        self.assertEqual(rows[1][5], "-")

    def test_csv_strips_platform_prefix_for_id_and_profile_link(self):
        self.mine.customer.source_username = ""
        self.mine.customer.external_user_id = "telegram_12345"
        self.mine.customer.source_profile_url = ""
        self.mine.customer.save()

        response = self.client.get(reverse("discovery:opportunity_export"))
        rows = list(csv.reader(io.StringIO(response.content.decode("utf-8-sig"))))

        self.assertEqual(rows[1][0], "12345")
        self.assertEqual(rows[1][2], "https://t.me/c/12345")

    def test_csv_draft_failure_is_logged_and_keeps_fallback(self):
        from unittest.mock import patch

        self.mine.ai_analysis.suggested_reply = ""
        self.mine.ai_analysis.save(update_fields=["suggested_reply"])
        with patch("apps.discovery.views.drafts_for", side_effect=RuntimeError("offline")):
            with self.assertLogs("apps.discovery.views", level="WARNING") as logs:
                response = self.client.get(reverse("discovery:opportunity_export"))

        self.assertEqual(response.status_code, 200)
        self.assertIn(str(self.mine.id), logs.output[0])
        rows = list(csv.reader(io.StringIO(response.content.decode("utf-8-sig"))))
        self.assertIn("سلام وقت بخیر", rows[1][5])

    def test_status_update(self):
        r = self.client.post(reverse("discovery:opportunity_status_update", args=[self.mine.pk]), {"status": "CONTACTED"})
        self.assertEqual(r.json()["new_status"], "CONTACTED")
        r = self.client.post(reverse("discovery:opportunity_status_update", args=[self.mine.pk]), {"status": "MATCHED"})
        self.assertEqual(r.status_code, 400)

    def test_opportunity_feedback_is_saved_and_scoped_to_business(self):
        url = reverse("discovery:opportunity_feedback", args=[self.mine.pk])
        response = self.client.post(url, {"feedback": "relevant"})
        self.assertEqual(response.status_code, 200)
        self.mine.refresh_from_db()
        self.assertEqual(self.mine.feedback, "relevant")
        self.assertIsNotNone(self.mine.feedback_at)
        self.assertEqual(self.mine.trace_metadata["seller_feedback"], "relevant")
        timestamp = self.mine.feedback_at
        self.assertEqual(self.client.post(url, {"feedback": "relevant"}).status_code, 200)
        self.mine.refresh_from_db()
        self.assertGreaterEqual(self.mine.feedback_at, timestamp)
        response = self.client.post(url, {"feedback": "irrelevant", "reason": "spam"})
        self.assertEqual(response.status_code, 200)
        self.mine.refresh_from_db()
        self.assertEqual((self.mine.feedback, self.mine.feedback_reason), ("irrelevant", "spam"))
        payload = engine_payload("need_000010_ab", [self.p1.id])
        import_opportunity(payload)
        self.mine.refresh_from_db()
        self.assertEqual((self.mine.feedback, self.mine.feedback_reason), ("irrelevant", "spam"))
        self.assertEqual(self.client.post(url, {"feedback": "invalid"}).status_code, 400)
        self.assertEqual(self.client.post(url, {"feedback": "relevant", "reason": "other"}).status_code, 400)
        theirs = Opportunity.objects.get(business=self.other)
        self.assertEqual(self.client.post(reverse("discovery:opportunity_feedback", args=[theirs.pk]),
                                          {"feedback": "irrelevant"}).status_code, 404)

    def test_instagram_page_registration_is_validated_and_scoped(self):
        from django.contrib.messages import get_messages

        url = reverse("discovery:instagram_pages")
        response = self.client.post(url, {"page_ref": "https://www.instagram.com/Shop_Page/?igsh=abc",
                                          "kind": "watched", "default_product": self.p1.pk})
        self.assertEqual(response.status_code, 302)
        page = InstagramPage.objects.get(business=self.biz)
        self.assertEqual((page.handle, page.kind, page.default_product), ("shop_page", "watched", self.p1))
        self.assertEqual(self.client.post(url, {"page_ref": "@SHOP_PAGE"}).status_code, 200)
        self.assertEqual(InstagramPage.objects.filter(business=self.biz).count(), 1)
        response = self.client.post(url, {"page_ref": "javascript:alert(1)"})
        self.assertEqual(response.status_code, 200)
        self.assertTrue(any("معتبر" in str(message) for message in get_messages(response.wsgi_request)))
        theirs = InstagramPage.objects.create(business=self.other, handle="other_shop")
        self.assertNotContains(self.client.get(url), "@other_shop")
        self.assertEqual(self.client.post(url, {"action": "remove", "page_id": theirs.pk}).status_code, 404)
        self.assertTrue(InstagramPage.objects.filter(pk=theirs.pk).exists())

    def test_instagram_page_add_and_remove_are_throttled(self):
        from django.core.cache import cache
        from unittest.mock import patch

        cache.clear()
        with patch("apps.discovery.views.INSTAGRAM_PAGE_CHANGE_LIMIT", 1):
            url = reverse("discovery:instagram_pages")
            self.assertEqual(self.client.post(url, {"page_ref": "seller_page"}).status_code, 302)
            response = self.client.post(url, {"action": "remove", "page_id": 999999})
            self.assertEqual(response.status_code, 429)
            self.assertContains(response, "یک ساعت", status_code=429)
        cache.clear()

    def test_dashboard_shows_real_numbers_only(self):
        r = self.client.get(reverse("accounts:dashboard"))
        self.assertEqual(r.status_code, 200)
        summary = r.context["analytics"]["summary"]
        self.assertEqual(summary["total_leads_in_db"], 1)
        self.assertEqual(summary["high_intent_leads_in_db"], 1)
        self.assertEqual(r.context["analytics"]["products"][0]["discovered_leads_count"], 1)
        self.assertNotContains(r, "۱۳۳ سرنخ")
        api = self.client.get(reverse("discovery:api_analytics")).json()
        self.assertEqual(api["data"]["summary"]["total_leads_in_db"], 1)

    def test_reply_link_tracks_click_and_order_converts_the_opportunity(self):
        url = reverse("short_public_card", args=[self.p1.pk]) + "?ref=need_000010_ab"
        anon = Client()
        self.assertEqual(anon.get(url).status_code, 200)
        self.assertEqual(self.p1.daily_metrics.get().clicks_count, 1)
        r = anon.post(url, {"customer_name": "علی", "customer_phone": "09120000000", "quantity": "1"},
                      HTTP_X_REQUESTED_WITH="XMLHttpRequest")
        self.assertEqual(r.json()["status"], "success")
        self.assertEqual(ProductOrder.objects.get().opportunity, self.mine)
        self.mine.refresh_from_db()
        self.assertEqual(self.mine.status, "CONVERTED")

    def test_removed_legacy_endpoints_are_gone(self):
        for path in ("/discovery/leads/", "/discovery/api/leads/submit/", "/discovery/api/candidates/",
                     "/discovery/api/opportunities/process/", "/discovery/scan/trigger/"):
            self.assertEqual(self.client.post(path).status_code, 404, path)


class XSellerPagesTests(EngineSchemaMixin, TestCase):
    """«جستجوی مشتری در X»: hub + per-product report from crawler.x_search_runs / x_post_products / x_posts and
    need_engine.x_hit_results / costs; only the owner sees a product's report."""

    def test_humanize_query(self):
        from .x_activity import humanize_query

        h = humanize_query('(نوشن OR notion) (موجود OR "از کجا") -فروش -"فروش ویژه" lang:fa since_time:1700000000')
        self.assertEqual(h["groups"], [["نوشن", "notion"], ["موجود", "از کجا"]])
        self.assertEqual(h["excluded"], ["فروش", "فروش ویژه"])

    def test_hub_and_product_report(self):
        import os
        from unittest.mock import patch

        from x_ingest.__main__ import load, record_search_runs

        user, biz = seller("xs1@x.com", "فروشگاه نوشن")
        other_user, _ = seller("xs2@x.com", "دیگری")
        prod = Product.objects.create(business=biz, name="اکانت نوشن پلاس", description="x", price=100, x_search_enabled=True)
        idle = Product.objects.create(business=biz, name="هندزفری", description="x", price=100)
        st = self.store()
        pid = str(prod.pk)
        st.record_x_hit_results([(f"x:p:{pid}", 101, "customer", "دقیقاً همین اکانت را می‌خواهد", "need-1"),
                                 (f"x:p:{pid}", 102, "not_customer", "", None)])
        st.record_x_hit_results([(f"x:p:{pid}", 101, "not_fit", "x", None)])     # a customer is never downgraded
        st.add_cost("need_extraction_x_product", "gem", 1000, 100, False, 0.001, 70, ref=f"x:p:{pid}:101-103",
                    businesses=[str(biz.pk)])
        crawler = f"cr_{uuid.uuid4().hex[:8]}"
        now = datetime.now(timezone.utc)
        rows = [{"id": str(i), "source": "x", "author_id": f"a{i}", "author_handle": h, "text": t, "kind": "post",
                 "created_at": now.isoformat(), "url": f"https://x.com/{h}/status/{i}", "product_id": pid,
                 "metadata": {"lang": "fa", "query": "(نوشن OR notion) (موجود) -فروش lang:fa"}}
                for i, h, t in ((101, "buyer1", "اکانت نوشن موجود دارین؟"), (102, "shop1", "فروش ویژه اکانت نوشن"),
                                (103, "late1", "کسی نوشن پلاس داره؟"))]
        path = Path(tempfile.mkdtemp()) / "x.jsonl"
        path.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in rows), encoding="utf-8")
        with patch.dict(os.environ, {"NE_CRAWLER_SCHEMA": crawler}):
            self.assertEqual(load(path, self.dsn()), 3)
            self.assertEqual(record_search_runs([{"query": "(نوشن OR notion) (موجود) -فروش lang:fa", "product_ids": [pid],
                                                  "fetched": 9, "new_by_product": {pid: 3}, "ran_at": now.isoformat()}],
                                                self.dsn()), 1)
        self.client.force_login(user)
        with override_settings(TG_DB_SCHEMA=crawler):
            hub = self.client.get(reverse("discovery:x_search"))
            page = self.client.get(reverse("discovery:x_product", args=[prod.pk]))
            only_customers = self.client.get(reverse("discovery:x_product", args=[prod.pk]) + "?show=customer").content.decode()
            self.client.force_login(other_user)
            self.assertEqual(self.client.get(reverse("discovery:x_product", args=[prod.pk])).status_code, 404)
        self.assertEqual(hub.status_code, 200)
        row = next(r for r in hub.context["rows"] if r["product"].pk == prod.pk)
        self.assertEqual({k: row["st"][k] for k in ("found", "reviewed", "customers", "rejected", "queued", "seen")},
                         {"found": 3, "reviewed": 2, "customers": 1, "rejected": 1, "queued": 1, "seen": 9})
        self.assertGreater(row["st"]["cost_toman"], 0)
        hub_html = hub.content.decode()
        self.assertIn("چطور کار می‌کند؟", hub_html)
        self.assertIn("هندزفری", hub_html)
        self.assertEqual(page.status_code, 200)
        html = page.content.decode()
        for text in ("buyer1", "مشتری احتمالی", "دقیقاً همین اکانت را می‌خواهد", "خریدار نبود", "در صف بررسی",
                     "جستجو در X", "notion", "به‌جز: فروش"):
            self.assertIn(text, html)
        self.assertIn("buyer1", only_customers)
        self.assertNotIn("shop1", only_customers)
        self.client.force_login(user)
        for _ in range(2):   # explicit state from the switch: «off» twice stays off
            self.client.post(reverse("products:toggle_x_search", args=[prod.pk]), {"state": "off"})
            prod.refresh_from_db()
            self.assertFalse(prod.x_search_enabled)
        self.assertIn("جستجوی مشتری در X: خاموش", self.client.get(reverse("products:detail", args=[idle.pk])).content.decode())
