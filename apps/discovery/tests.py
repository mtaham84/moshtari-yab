"""Panel side of the pipeline: need_engine JSONL → panel records → views, dashboard and product-card tracking."""
import json
import tempfile
from datetime import datetime, timedelta, timezone
from io import StringIO
from pathlib import Path

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.test import Client, TestCase, override_settings
from django.urls import reverse

from apps.businesses.models import Business
from apps.products.models import Category, Product

from .engine_bridge import engine_totals, import_opportunity
from .models import Customer, Opportunity, ProductOrder

User = get_user_model()
T0 = datetime(2026, 10, 8, 10, 0, tzinfo=timezone.utc)


def seller(email: str, name: str) -> tuple:
    user = User.objects.create_user(username=email, email=email, password="pass12345", first_name=name)
    return user, Business.objects.create(user=user, name=name, business_type="PHYSICAL", business_domain="عمومی")


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


class EngineImportTests(TestCase):
    def setUp(self):
        self.user, self.biz = seller("a@example.com", "فروشگاه الف")
        _, self.other = seller("b@example.com", "فروشگاه ب")
        self.cat = Category.objects.create(business=self.biz, name="دستکش")
        self.p1 = Product.objects.create(business=self.biz, category=self.cat, name="دستکش گرم", description="x", price=900000)
        self.p2 = Product.objects.create(business=self.biz, name="دستکش چرمی", description="x", price=1200000)
        self.q1 = Product.objects.create(business=self.other, name="دستکش موتور", description="x", price=800000)

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

    def test_unknown_products_are_skipped(self):
        self.assertEqual(import_opportunity(engine_payload("need_000003_ab", [999999])), [])
        self.assertFalse(Opportunity.objects.exists())

    def test_sync_command_reads_new_complete_lines_only(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "opps.jsonl"
            line = json.dumps(engine_payload("need_000004_ab", [self.p1.id]), ensure_ascii=False)
            path.write_text(line + "\n" + line[:20], encoding="utf-8")      # second line still being written
            call_command("sync_opportunities", path=str(path), stdout=StringIO())
            self.assertEqual(Opportunity.objects.count(), 1)
            offset = int(Path(str(path) + ".offset").read_text())
            self.assertEqual(offset, len((line + "\n").encode()))
            with path.open("a", encoding="utf-8") as fh:                   # finish line 2 (a status update)
                fh.write(json.dumps(engine_payload("need_000004_ab", [self.p1.id], "expired"), ensure_ascii=False)[20:] + "\n")
            call_command("sync_opportunities", path=str(path), stdout=StringIO())
            self.assertEqual(Opportunity.objects.get().status, "EXPIRED")

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
            cfg.state_path, cfg.output_path = str(d / "state.db"), str(d / "opps.jsonl")
            cfg.messages_dsn, cfg.products_source = f"jsonl:{d / 'chats.jsonl'}", f"jsonl:{d / 'products.jsonl'}"
            report = NeedEngine(cfg, mock_llm=mock_llm).run_once(flush=True)
            self.assertGreaterEqual(len(report.opportunities), 1)

            call_command("sync_opportunities", path=cfg.output_path, stdout=StringIO())
            self.assertTrue(Opportunity.objects.filter(source_platform="telegram").exists())
            with override_settings(NEED_ENGINE_STATE_PATH=cfg.state_path):
                totals = engine_totals()
            self.assertEqual(totals["messages_analysed"], 1)
            self.assertGreater(totals["cost_toman"], 0)


class PanelViewTests(TestCase):
    def setUp(self):
        self.user, self.biz = seller("a@example.com", "فروشگاه الف")
        self.other_user, self.other = seller("b@example.com", "فروشگاه ب")
        self.p1 = Product.objects.create(business=self.biz, name="دستکش گرم", description="x", price=900000)
        self.q1 = Product.objects.create(business=self.other, name="دستکش موتور", description="x", price=800000)
        import_opportunity(engine_payload("need_000010_ab", [self.p1.id, self.q1.id]))
        self.mine = Opportunity.objects.get(business=self.biz)
        self.client = Client()
        self.client.force_login(self.user)

    def test_list_and_detail_are_scoped_to_the_seller(self):
        r = self.client.get(reverse("discovery:opportunity_list"))
        self.assertEqual(r.status_code, 200)
        self.assertEqual(list(r.context["opportunities"]), [self.mine])
        self.assertEqual(self.client.get(reverse("discovery:opportunity_detail", args=[self.mine.pk])).status_code, 200)
        theirs = Opportunity.objects.get(business=self.other)
        self.assertEqual(self.client.get(reverse("discovery:opportunity_detail", args=[theirs.pk])).status_code, 404)

    def test_status_update(self):
        r = self.client.post(reverse("discovery:opportunity_status_update", args=[self.mine.pk]), {"status": "CONTACTED"})
        self.assertEqual(r.json()["new_status"], "CONTACTED")
        r = self.client.post(reverse("discovery:opportunity_status_update", args=[self.mine.pk]), {"status": "MATCHED"})
        self.assertEqual(r.status_code, 400)

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
