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
from .models import Customer, InstagramCategoryTarget, InstagramPage, Opportunity, ProductOrder
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
    return user, Business.objects.create(user=user, name=name, business_type="PHYSICAL", business_domain="عمومی", onboarding_done=True)


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
        customer_message = opportunity.evidence_items.get(evidence_type="customer_message")
        self.assertIn("https://x.com/buyer/status/1990000000000001001", customer_message.source_reference)

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
        from unittest.mock import patch

        self.mine.customer.source_username = ""
        self.mine.customer.external_user_id = ""
        self.mine.customer.phone_number = None
        self.mine.customer.name = ""
        self.mine.customer.source_profile_url = ""
        self.mine.customer.save()
        self.mine.source_raw_message = ""
        self.mine.save()
        self.mine.product_matches.all().delete()
        self.mine.evidence_items.all().delete()
        self.mine.ai_analysis.need = ""
        self.mine.ai_analysis.suggested_reply = ""
        self.mine.ai_analysis.save(update_fields=["need", "suggested_reply"])

        with patch("apps.discovery.views.drafts_for", return_value={}):
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
        messages = [str(message) for message in get_messages(response.wsgi_request)]
        self.assertIn("لینک باید با http یا https باشد؛ می‌توانید فقط آیدی صفحه را هم وارد کنید.", messages)
        theirs = InstagramPage.objects.create(business=self.other, handle="other_shop")
        self.assertNotContains(self.client.get(url), "@other_shop")
        self.assertEqual(self.client.post(url, {"action": "remove", "page_id": theirs.pk}).status_code, 404)
        self.assertTrue(InstagramPage.objects.filter(pk=theirs.pk).exists())

    def test_instagram_category_target_is_scoped_and_saves_seed_limits(self):
        category = Category.objects.create(business=self.biz, name="کفش پایش")
        url = reverse("discovery:instagram_pages")
        response = self.client.post(url, {
            "action": "save_category_target", "category_id": str(category.pk),
            "terms": "کفش چرمی\nسایز", "hashtags": "#کفش، #کفش_چرمی",
            "max_posts": "12", "window_days": "21", "is_active": "on",
        })

        self.assertEqual(response.status_code, 302)
        target = InstagramCategoryTarget.objects.get(business=self.biz, category=category)
        self.assertEqual(target.terms, ["کفش چرمی", "سایز"])
        self.assertEqual(target.hashtags, ["کفش", "کفش_چرمی"])
        self.assertEqual((target.max_posts, target.window_days, target.is_active), (12, 21, True))
        other_category = Category.objects.create(business=self.other, name="کفش متعلق به کسب‌وکار دیگر")
        response = self.client.post(url, {
            "action": "save_category_target", "category_id": str(other_category.pk),
            "terms": "ignored", "hashtags": "#other", "is_active": "on",
        })
        self.assertEqual(response.status_code, 302)
        self.assertFalse(InstagramCategoryTarget.objects.filter(business=self.biz, category=other_category).exists())

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

    def test_instagram_comment_detail_escapes_comment_and_scopes_opportunity(self):
        customer = Customer.objects.create(business=self.biz, source_platform="instagram",
                                           external_user_id="7105", source_username="buyer",
                                           source_profile_url="https://www.instagram.com/buyer/")
        opportunity = Opportunity.objects.create(
            business=self.biz, customer=customer, source_platform="instagram",
            source_message_id="ig:IGAd102:cc-5", source_query="shop_page",
            source_raw_message="<script>alert(1)</script>",
            trace_metadata={"instagram": {"post_url": "https://www.instagram.com/p/IGAd102/"}},
        )
        response = self.client.get(reverse("discovery:instagram_comment_detail", args=[opportunity.pk]))

        self.assertContains(response, "&lt;script&gt;alert(1)&lt;/script&gt;")
        self.assertNotContains(response, "<script>alert(1)</script>")
        self.assertContains(self.client.get(reverse("discovery:opportunity_list")),
                            "&lt;script&gt;alert(1)&lt;/script&gt;")
        detail = self.client.get(reverse("discovery:opportunity_detail", args=[opportunity.pk]))
        self.assertContains(detail, "https://www.instagram.com/buyer/")
        self.assertNotContains(detail, "javascript:")
        self.assertContains(self.client.get(reverse("discovery:opportunity_list")),
                            reverse("discovery:instagram_comment_detail", args=[opportunity.pk]))
        export = self.client.get(reverse("discovery:opportunity_export"))
        exported = list(csv.reader(io.StringIO(export.content.decode("utf-8-sig"))))
        self.assertTrue(any(row[3] == "<script>alert(1)</script>" for row in exported[1:]))
        self.assertEqual(self.client.get(reverse("discovery:instagram_comment_detail", args=[999999])).status_code, 404)
        theirs = Opportunity.objects.get(business=self.other)
        self.assertEqual(self.client.get(reverse("discovery:instagram_comment_detail", args=[theirs.pk])).status_code, 404)
        from unittest.mock import patch
        with patch("apps.discovery.views.rewrite_reply", side_effect=AssertionError("must not call the LLM")) as rewrite:
            response = self.client.post(reverse("discovery:opportunity_rewrite", args=[opportunity.pk]))
        self.assertEqual(response.status_code, 400)
        rewrite.assert_not_called()
        self.assertNotContains(detail, 'id="btn-rewrite-reply"')

    def test_instagram_comment_report_api_is_business_scoped_and_denominator_explicit(self):
        from unittest.mock import patch

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            rows = [
                {"id": "ig:p:c1", "business_id": str(self.biz.pk), "commenter_id": "u1",
                 "prefilter": {"status": "eligible"}, "jev": {"status": "probably a buyer", "score": 0.9}},
                {"id": "ig:p:c2", "business_id": str(self.biz.pk), "commenter_id": "u1", "commenter_username": "u1",
                 "prefilter": {"status": "set_aside"}, "jev": None},
                {"id": "ig:q:c1", "business_id": str(self.other.pk), "commenter_id": "other",
                 "prefilter": {"status": "eligible"}, "jev": {"status": "probably a buyer", "score": 0.99}},
            ]
            (root / "comment-evaluations.jsonl").write_text(
                "".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8",
            )
            with patch.dict("os.environ", {"IG_COLLECT_OUTPUT_DIR": str(root)}):
                response = self.client.get(reverse("discovery:instagram_comment_report_api"))

        self.assertEqual(response.status_code, 200)
        report = response.json()["data"]
        self.assertEqual(report["denominators"], {"N_total": 2, "N_eval": 1, "N_pass": 1})
        self.assertEqual(report["shares"]["evaluated_comments"]["denominator"], 1)
        self.assertEqual(report["shares"]["evaluated_comments"]["percent"], 100)
        self.assertEqual(report["comments"]["total_received"]["unique_users"]["unique_users"], 1)
        self.assertEqual(report["shares"]["evaluated_comments"]["label"],
                         "share of comments that passed the selection threshold")

    def test_instagram_report_ui_allows_operator_labels_after_jev(self):
        from unittest.mock import patch

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            row = {"id": "ig:p:c1", "business_id": str(self.biz.pk), "comment_text": "قیمت؟",
                   "commenter_id": "buyer", "prefilter": {"status": "eligible"},
                   "jev": {"status": "uncertain", "score": 0.4}}
            (root / "comment-evaluations.jsonl").write_text(json.dumps(row) + "\n", encoding="utf-8")
            with patch.dict("os.environ", {"IG_COLLECT_OUTPUT_DIR": str(root)}):
                page = self.client.get(reverse("discovery:instagram_pages"))
                self.assertEqual(page.status_code, 200)
                self.assertContains(page, "برچسب‌گذاری انسانی پس از ارزیابی Jev")
                response = self.client.post(reverse("discovery:instagram_pages"), {
                    "action": "label_comment", "comment_id": "ig:p:c1", "label": "approve",
                })
                self.assertEqual(response.status_code, 302)
                saved = json.loads((root / "human-labels.jsonl").read_text(encoding="utf-8").splitlines()[0])

        self.assertEqual(saved["comment_id"], "ig:p:c1")
        self.assertEqual(saved["business_id"], str(self.biz.pk))
        self.assertEqual(saved["operator_id"], str(self.user.pk))
        self.assertEqual(saved["label"], "approve")

    def test_instagram_ingest_is_idempotent_and_fans_out(self):
        import json
        import tempfile
        from pathlib import Path
        from django.core.management import call_command
        from need_engine.text import tokens

        caption = "کفش چرمی مردانه"
        caption_tokens = set(tokens(caption))
        products = Product.objects.filter(business__in=[self.biz, self.other], status="ACTIVE",
                                           is_discovery_active=True)
        product_tokens = {name: set(tokens(name)) for name in products.values_list("name", flat=True)}
        if not any(caption_tokens & item_tokens for item_tokens in product_tokens.values()):
            for business in (self.biz, self.other):
                Product.objects.create(business=business, name=caption, description="x",
                                       status="ACTIVE", is_discovery_active=True)

        InstagramPage.objects.create(business=self.biz, handle="shop_page")
        InstagramPage.objects.create(business=self.other, handle="SHOP_PAGE")
        record = {
            "id": "ig:IGAd102:cc-5", "source": "instagram", "kind": "comment",
            "text": "قیمت؟", "author_handle": "@buyer", "author_id": "7105",
            "created_at": "2026-10-12T09:00:00+00:00",
            "url": "https://www.instagram.com/p/IGAd102/",
            "metadata": {
                "seed": "account:shop_page",
                "parent_post": {"id": "ig:IGAd102", "caption": caption,
                                 "url": "javascript:alert(1)"},
                "intent": {"label": "price_question", "cue": "قیمت؟"},
            },
        }
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "leads.jsonl"
            path.write_text(json.dumps(record, ensure_ascii=False) + "\n", encoding="utf-8")
            call_command("ig_ingest", path=str(path), stdout=StringIO())
            call_command("ig_ingest", path=str(path), stdout=StringIO())

        opportunities = Opportunity.objects.filter(source_platform="instagram", source_message_id=record["id"])
        self.assertEqual(opportunities.count(), 2)
        self.assertEqual(Customer.objects.filter(source_platform="instagram").count(), 2)
        self.assertEqual(set(opportunities.values_list("trace_metadata__instagram__post_url", flat=True)),
                         {"https://www.instagram.com/p/IGAd102/"})
        self.assertTrue(all(item.customer.source_username == "buyer" for item in opportunities.select_related("customer")))
        self.assertEqual(list(opportunities.values_list("trace_metadata__instagram__product_match", flat=True)),
                         ["caption_tokens", "caption_tokens"])

    def test_instagram_ingest_uses_default_product_when_caption_does_not_match(self):
        import json
        import tempfile
        from pathlib import Path
        from django.core.management import call_command

        InstagramPage.objects.create(business=self.biz, handle="shop_page", default_product=self.p1)
        record = {"id": "ig:IGAd102:c8", "source": "instagram", "kind": "comment",
                  "text": "قیمت؟", "author_handle": "@buyer", "author_id": "7108",
                  "created_at": None, "url": "https://www.instagram.com/p/IGAd102/",
                  "metadata": {"seed": "account:shop_page",
                               "parent_post": {"id": "ig:IGAd102", "caption": "وسیله ناشناخته"},
                               "intent": {"label": "price_question", "cue": "قیمت"}}}
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "leads.jsonl"
            path.write_text(json.dumps(record, ensure_ascii=False) + "\n", encoding="utf-8")
            call_command("ig_ingest", path=str(path), stdout=StringIO())

        opportunity = Opportunity.objects.get(source_platform="instagram", source_message_id=record["id"])
        self.assertEqual(opportunity.trace_metadata["instagram"]["product_match"], "default_product")
        self.assertEqual(opportunity.product_matches.get().product, self.p1)

    def test_hashtag_comment_with_confident_category_creates_scoped_opportunity(self):
        import json
        import tempfile
        from pathlib import Path
        from django.core.management import call_command

        category = Category.objects.create(business=self.biz, name="کفش اینستاگرام", keywords=["کفش چرمی"])
        product = Product.objects.create(
            business=self.biz, category=category, name="کفش چرمی مردانه", description="x",
            status="ACTIVE", is_discovery_active=True, instagram_discovery_enabled=True,
        )
        InstagramCategoryTarget.objects.create(
            business=self.biz, category=category, terms=["کفش چرمی"], hashtags=["کفش_چرمی"],
            is_active=True,
        )
        decision = {
            "result": "confident_match", "category_id": category.pk,
            "category": category.name, "category_path": category.full_path, "score": 0.9,
            "product_id": product.pk, "product": product.name,
            "reason": {"code": "matched", "evidence": {"caption_terms": ["کفش چرمی"],
                                                  "post_hashtags": ["کفش_چرمی"]}},
            "knowledge_source": {"kind": "seller_category_seed", "business_id": self.biz.pk,
                                    "category_id": category.pk},
        }
        record = {
            "id": "ig:HashtagPost:c9", "source": "instagram", "kind": "comment",
            "text": "قیمت این کفش چنده؟", "author_handle": "@buyer", "author_id": "9901",
            "created_at": None, "url": "https://www.instagram.com/p/HashtagPost/",
            "metadata": {"seed": "hashtag:کفش_چرمی",
                "parent_post": {"id": "ig:HashtagPost", "caption": "برای خرید کفش چرمی مردانه لطفاً سایز را بفرمایید.",
                                "category_decision": decision},
                "intent": {"label": "price_question", "cue": "قیمت"}},
        }
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "leads.jsonl"
            path.write_text(json.dumps(record, ensure_ascii=False) + "\n", encoding="utf-8")
            call_command("ig_ingest", path=str(path), stdout=StringIO())

        opportunity = Opportunity.objects.get(source_platform="instagram", source_message_id=record["id"])
        self.assertEqual(opportunity.business, self.biz)
        self.assertEqual(opportunity.category, category)
        self.assertEqual(opportunity.category_confidence, 0.9)
        self.assertEqual(opportunity.trace_metadata["instagram"]["category_decision"]["result"], "confident_match")
        self.assertEqual(opportunity.product_matches.get().product, product)

    def test_instagram_owner_comment_and_invalid_intent_are_skipped(self):
        import json
        import tempfile
        from pathlib import Path

        InstagramPage.objects.create(business=self.biz, handle="shop_page")
        records = []
        for comment_id, author, label in (("1", "shop_page", "interest"),
                                          ("2", "buyer", "follow_for_follow")):
            records.append({
                "id": f"ig:IGAd102:c{comment_id}", "source": "instagram", "kind": "comment",
                "text": "میخوام", "author_handle": f"@{author}", "author_id": None,
                "created_at": None, "url": "https://www.instagram.com/p/IGAd102/",
                "metadata": {"seed": "account:shop_page",
                               "parent_post": {"id": "ig:IGAd102", "caption": "کفش"},
                               "intent": {"label": label, "cue": "میخوام"}},
            })
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "leads.jsonl"
            path.write_text("\n".join(json.dumps(row, ensure_ascii=False) for row in records) + "\n",
                           encoding="utf-8")
            call_command("ig_ingest", path=str(path), stdout=StringIO())

        self.assertFalse(Opportunity.objects.filter(source_platform="instagram").exists())

    def test_instagram_outcomes_only_apply_to_queued_run_and_all_watching_businesses(self):
        import json
        import tempfile
        from pathlib import Path
        from django.core.management import call_command
        from django.utils import timezone

        pages = [InstagramPage.objects.create(business=business, handle=handle)
                 for business, handle in ((self.biz, "shop_page"), (self.other, "SHOP_PAGE"))]
        queued = timezone.now() - timedelta(minutes=2)
        InstagramPage.objects.filter(pk__in=[page.pk for page in pages]).update(queued_at=queued)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "status.json").write_text(json.dumps({
                "started_at": queued.isoformat(),
                "updated_at": timezone.now().isoformat(),
                "handles": {"shop_page": {"status": "not_found", "posts": 0, "comments": 0}},
            }), encoding="utf-8")
            from unittest.mock import patch
            with patch.dict("os.environ", {"IG_COLLECT_OUTPUT_DIR": str(root)}):
                call_command("ig_sync_seeds", apply_status=True)
        for page in pages:
            page.refresh_from_db()
            self.assertEqual(page.last_status, "not_found")
            self.assertIsNone(page.queued_at)
            self.assertEqual(page.consecutive_failures, 1)

    def test_instagram_seed_file_is_deduplicated_and_fair(self):
        import json
        import tempfile
        from pathlib import Path
        from django.core.management import call_command
        from unittest.mock import patch
        from django.test import override_settings

        first = InstagramPage.objects.create(business=self.biz, handle="shop_page")
        InstagramPage.objects.create(business=self.other, handle="SHOP_PAGE")
        second = InstagramPage.objects.create(business=self.other, handle="second_shop")
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            output = root / "collector"
            with patch.dict("os.environ", {"IG_MAX_HANDLES_PER_CYCLE": "1", "IG_CYCLE_MINUTES": "60",
                                             "IG_COLLECT_OUTPUT_DIR": str(output)}):
                with override_settings(BASE_DIR=root):
                    call_command("ig_sync_seeds", stdout=StringIO())
                    payload = json.loads((root / "data" / "ig_seeds.json").read_text(encoding="utf-8"))

        self.assertEqual([item["username"] for item in payload["accounts"]], ["shop_page"])
        first.refresh_from_db()
        second.refresh_from_db()
        self.assertIsNotNone(first.queued_at)
        self.assertIsNone(second.queued_at)

    def test_instagram_seed_file_keeps_account_seeds_and_maps_hashtags_to_categories(self):
        import json
        import tempfile
        from pathlib import Path
        from django.core.management import call_command
        from django.test import override_settings
        from unittest.mock import patch

        page = InstagramPage.objects.create(business=self.biz, handle="shop_page")
        category = Category.objects.create(business=self.biz, name="کفش چرمی", keywords=["کفش چرمی", "سایز"])
        product = Product.objects.create(
            business=self.biz, category=category, name="کفش مردانه", description="کفش چرمی",
            instagram_terms=["مردانه"], instagram_hashtags=["#کفش_مردانه"],
            instagram_discovery_enabled=True,
        )
        InstagramCategoryTarget.objects.create(
            business=self.biz, category=category, terms=["چرم طبیعی"], hashtags=["#کفش_چرمی"],
            max_posts=9, window_days=14, is_active=True,
        )
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with patch.dict("os.environ", {"IG_COLLECT_OUTPUT_DIR": str(root / "collector")}):
                with override_settings(BASE_DIR=root):
                    call_command("ig_sync_seeds", stdout=StringIO())
                    payload = json.loads((root / "data" / "ig_seeds.json").read_text(encoding="utf-8"))

        self.assertEqual(payload["accounts"], [{"username": "shop_page", "limit": 5}])
        self.assertEqual(payload["hashtags"], [
            {
                "tag": "کفش_چرمی", "limit": 9, "window_days": 14,
                "business_id": self.biz.pk, "category_id": category.pk,
                "category": "کفش چرمی", "category_path": "کفش چرمی",
                "product_ids": [product.pk], "products": ["کفش مردانه"],
                "terms": ["کفش چرمی", "سایز", "چرم طبیعی", "مردانه"],
            },
            {
                "tag": "کفش_مردانه", "limit": 5, "window_days": 7,
                "business_id": self.biz.pk, "category_id": category.pk,
                "category": "کفش چرمی", "category_path": "کفش چرمی",
                "product_ids": [product.pk], "products": ["کفش مردانه"],
                "terms": ["کفش چرمی", "سایز", "مردانه"],
            },
        ])
        self.assertTrue(payload["comments"]["read_hashtag_posts"])
        page.refresh_from_db()
        self.assertIsNotNone(page.queued_at)

    def test_instagram_private_page_is_not_seeded_again_for_24_hours(self):
        import json
        import tempfile
        from pathlib import Path
        from django.core.management import call_command
        from django.test import override_settings
        from django.utils import timezone
        from unittest.mock import patch

        page = InstagramPage.objects.create(business=self.biz, handle="private_shop", last_status="private",
                                            last_run_at=timezone.now() - timedelta(hours=1))
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with patch.dict("os.environ", {"IG_COLLECT_OUTPUT_DIR": str(root / "collector")}):
                with override_settings(BASE_DIR=root):
                    call_command("ig_sync_seeds", stdout=StringIO())
                    payload = json.loads((root / "data" / "ig_seeds.json").read_text(encoding="utf-8"))
        self.assertEqual(payload["accounts"], [])
        page.refresh_from_db()
        self.assertIsNone(page.queued_at)

    def test_instagram_ignores_old_outcome_and_pauses_after_three_not_found(self):
        import json
        import tempfile
        from pathlib import Path
        from django.core.management import call_command
        from django.utils import timezone
        from unittest.mock import patch

        page = InstagramPage.objects.create(business=self.biz, handle="shop_page", consecutive_failures=2)
        queued = timezone.now() - timedelta(minutes=2)
        page.queued_at = queued
        page.save(update_fields=["queued_at"])
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "status.json").write_text(json.dumps({
                "started_at": (queued - timedelta(seconds=1)).isoformat(),
                "updated_at": timezone.now().isoformat(),
                "handles": {"shop_page": {"status": "not_found", "posts": 0, "comments": 0}},
            }), encoding="utf-8")
            with patch.dict("os.environ", {"IG_COLLECT_OUTPUT_DIR": str(root)}):
                call_command("ig_sync_seeds", apply_status=True)
        page.refresh_from_db()
        self.assertTrue(page.is_active)
        self.assertEqual(page.consecutive_failures, 2)

        page.queued_at = queued
        page.save(update_fields=["queued_at"])
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "status.json").write_text(json.dumps({
                "started_at": queued.isoformat(),
                "updated_at": timezone.now().isoformat(),
                "handles": {"shop_page": {"status": "not_found", "posts": 0, "comments": 0}},
            }), encoding="utf-8")
            with patch.dict("os.environ", {"IG_COLLECT_OUTPUT_DIR": str(root)}):
                call_command("ig_sync_seeds", apply_status=True)
        page.refresh_from_db()
        self.assertFalse(page.is_active)
        self.assertEqual(page.consecutive_failures, 3)

    def test_instagram_ingest_counts_bad_lines_and_rejects_invalid_owner(self):
        import json
        import tempfile
        from pathlib import Path
        from django.core.management import call_command

        InstagramPage.objects.create(business=self.biz, handle="shop_page")
        valid = {
            "id": "ig:IGAd102:c5", "source": "instagram", "kind": "comment",
            "text": "قیمت؟", "author_handle": "javascript:alert(1)", "author_id": None,
            "created_at": None, "url": "javascript:alert(1)",
            "metadata": {"seed": "account:shop_page", "parent_post": {"id": "ig:IGAd102"},
                         "intent": {"label": "price_question", "cue": "قیمت"}},
        }
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "leads.jsonl"
            path.write_text("not json\n" + json.dumps(valid, ensure_ascii=False) + "\n", encoding="utf-8")
            output = StringIO()
            call_command("ig_ingest", path=str(path), stdout=output)

        self.assertIn('"invalid_json":1', output.getvalue())
        self.assertFalse(Opportunity.objects.filter(source_platform="instagram").exists())

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


class InstagramPostReviewViewTests(TestCase):
    def setUp(self):
        import shutil
        import uuid
        from django.conf import settings
        from django.core.cache import cache

        self.user, self.biz = seller("review-a@example.com", "فروشگاه بازبینی الف")
        self.other_user, self.other_biz = seller("review-b@example.com", "فروشگاه بازبینی ب")
        self.category = Category.objects.create(business=self.biz, name="دستهٔ خودم")
        self.product = Product.objects.create(
            business=self.biz, category=self.category, name="محصول خودم", description="محصول آزمایشی",
        )
        self.other_category = Category.objects.create(business=self.other_biz, name="دستهٔ دیگری")
        self.other_product = Product.objects.create(
            business=self.other_biz, category=self.other_category,
            name="محصول دیگری", description="محصول آزمایشی دیگر",
        )
        self.output_root = Path(settings.BASE_DIR) / f".instagram-post-review-tests-{uuid.uuid4().hex}"
        self.output_root.mkdir(parents=True)
        self.addCleanup(shutil.rmtree, self.output_root, True)
        self.client = Client()
        self.client.force_login(self.user)
        cache.delete(f"ig-page-change:{self.user.pk}")

    def queue_post(self, *, code="PostAbc1", business_id=None, result="human_review",
                   reason="caption_too_short", audit=False, caption="کپشن آزمایشی",
                   candidate_category_id=None):
        business_id = business_id or self.biz.pk
        decision = {
            "result": result, "score": 0.42 if result == "human_review" else 0.93,
            "category_candidate_id": candidate_category_id or self.category.pk,
            "category_candidate": "دستهٔ خصوصی پیشنهادی", "product_candidate_id": None,
            "product_candidate": "محصول خصوصی پیشنهادی",
            "reason": {"code": reason, "evidence": {"caption_excerpt": caption}},
            "knowledge_source": {"kind": "seller_category_seed", "category_id": self.category.pk},
        }
        row = {
            "id": f"ig:{code}", "source": "instagram", "kind": "post", "text": caption,
            "author_handle": "@shop", "author_id": None, "created_at": "2026-10-23T10:00:00+00:00",
            "url": f"https://www.instagram.com/p/{code}/",
            "metadata": {
                "hashtags": ["کفش"], "collection_source": {"kind": "hashtag", "business_id": business_id},
                "suggested_target": {"business_id": business_id},
                "audit": audit, "category_decision": decision,
            },
        }
        queue = self.output_root / "needs-review.jsonl"
        with queue.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(row, ensure_ascii=False) + "\n")
        return row

    def output_env(self):
        import os
        from unittest.mock import patch

        return patch.dict(os.environ, {"IG_COLLECT_OUTPUT_DIR": str(self.output_root)})

    def test_post_review_requires_login_and_csrf(self):
        from django.urls import reverse

        url = reverse("discovery:instagram_post_review")
        self.assertEqual(Client().get(url).status_code, 302)
        self.queue_post()
        csrf_client = Client(enforce_csrf_checks=True)
        csrf_client.force_login(self.user)
        with self.output_env():
            self.assertEqual(csrf_client.get(url).status_code, 200)
            response = csrf_client.post(url, {
                "action": "label_post", "post_id": "ig:PostAbc1", "decision": "reject",
            })
        self.assertEqual(response.status_code, 403)

    def test_review_queue_is_scoped_paginated_and_xss_escaped(self):
        from django.urls import reverse

        malicious = self.queue_post(caption='<script>alert("caption-xss")</script>')
        self.queue_post(code="PostAbc2", business_id=self.other_biz.pk)
        with self.output_env():
            response = self.client.get(reverse("discovery:instagram_post_review"))

        html = response.content.decode()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["pending_total"], 1)
        self.assertContains(response, "&lt;script&gt;")
        self.assertNotIn('<script>alert("caption-xss")</script>', html)
        self.assertNotContains(response, "ig:PostAbc2")
        self.assertContains(response, "rel=\"noopener noreferrer\"")
        self.assertEqual(response.context["pending_posts"][0]["post_id"], malicious["id"])

        for index in range(21):
            self.queue_post(code=f"Page{index:04d}")
        with self.output_env():
            page_two = self.client.get(reverse("discovery:instagram_post_review"), {"page": 2})
        self.assertEqual(page_two.status_code, 200)
        self.assertEqual(page_two.context["page"], 2)
        self.assertEqual(len(page_two.context["pending_posts"]), 2)

    def test_audit_verdict_is_hidden_until_operator_submits(self):
        from django.urls import reverse

        self.queue_post(code="AuditAbc", result="confident_match", reason="matched", audit=True,
                        caption="متن ممیزی بدون برچسب افشاگر")
        with self.output_env():
            response = self.client.get(reverse("discovery:instagram_post_review"))

        html = response.content.decode()
        self.assertContains(response, "نمونهٔ ممیزی کور")
        self.assertNotIn("confident_match", html)
        self.assertNotIn("دستهٔ خصوصی پیشنهادی", html)

    def test_approval_validates_business_targets_and_displays_analyzer_result_after_submit(self):
        from django.urls import reverse

        row = self.queue_post(code="Approve1")
        queue_path = self.output_root / "needs-review.jsonl"
        queue_before = queue_path.read_bytes()
        with self.output_env():
            invalid_category = self.client.post(reverse("discovery:instagram_post_review"), {
                "action": "label_post", "post_id": row["id"], "decision": "approve",
                "category_id": self.other_category.pk,
            })
            invalid_product = self.client.post(reverse("discovery:instagram_post_review"), {
                "action": "label_post", "post_id": row["id"], "decision": "approve",
                "category_id": self.category.pk, "product_id": self.other_product.pk,
            })
            saved = self.client.post(reverse("discovery:instagram_post_review"), {
                "action": "label_post", "post_id": row["id"], "decision": "approve",
                "category_id": self.category.pk, "product_id": self.product.pk,
            })
            after_submit = self.client.get(saved.url)

        self.assertEqual(invalid_category.status_code, 400)
        self.assertEqual(invalid_product.status_code, 400)
        self.assertEqual(saved.status_code, 302)
        self.assertContains(after_submit, "human_review")
        self.assertContains(after_submit, "uncertain_score")
        self.assertEqual(queue_path.read_bytes(), queue_before)
        label_row = json.loads((self.output_root / "human-post-labels.jsonl").read_text(encoding="utf-8").splitlines()[0])
        self.assertEqual(label_row["decision"], "approve")
        self.assertEqual(label_row["business_id"], str(self.biz.pk))
        self.assertEqual(label_row["category_id"], str(self.category.pk))
        self.assertEqual(label_row["product_id"], str(self.product.pk))
        self.assertEqual(label_row["operator_id"], str(self.user.pk))
        self.assertEqual(label_row["analyzer_snapshot"]["result"], "human_review")

    def test_foreign_business_post_cannot_be_seen_or_labeled_and_skip_stays_pending(self):
        from django.urls import reverse

        row = self.queue_post(code="OtherB12", business_id=self.other_biz.pk)
        url = reverse("discovery:instagram_post_review")
        with self.output_env():
            invalid_id = self.client.post(url, {
                "action": "label_post", "post_id": "not-a-post-id", "decision": "reject",
            })
            page = self.client.get(url)
            denied = self.client.post(url, {
                "action": "label_post", "post_id": row["id"], "decision": "reject",
            })
            skipped = self.client.post(url, {
                "action": "skip_post", "post_id": row["id"], "page": "1",
            })
            again = self.client.get(url)

        self.assertEqual(invalid_id.status_code, 400)
        self.assertEqual(page.context["pending_total"], 0)
        self.assertEqual(denied.status_code, 404)
        self.assertEqual(skipped.status_code, 302)
        self.assertEqual(again.context["pending_total"], 0)
        self.assertFalse((self.output_root / "human-post-labels.jsonl").exists())

    def test_post_label_actions_use_the_existing_hourly_limit(self):
        from django.urls import reverse
        from unittest.mock import patch

        self.queue_post()
        with self.output_env(), patch("apps.discovery.views._instagram_change_allowed", return_value=False):
            response = self.client.post(reverse("discovery:instagram_post_review"), {
                "action": "label_post", "post_id": "ig:PostAbc1", "decision": "reject",
            })
        self.assertEqual(response.status_code, 429)
        self.assertFalse((self.output_root / "human-post-labels.jsonl").exists())

    def test_post_report_api_is_business_scoped_and_zero_denominators_are_explicit(self):
        from django.urls import reverse

        with self.output_env():
            response = self.client.get(reverse("discovery:instagram_post_report_api"))

        self.assertEqual(response.status_code, 200)
        report = response.json()["data"]
        self.assertEqual(report["denominators"]["N_posts_analyzed"], 0)
        self.assertIsNone(report["confident_match_agreement"]["percent"])
        self.assertEqual(report["confident_match_agreement"]["denominator"], 0)
