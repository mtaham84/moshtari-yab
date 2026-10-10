from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase, TransactionTestCase
from django.urls import reverse

from apps.discovery.models import EngineSyncCursor, MonitoredCommunity
from apps.discovery.tests import EngineSchemaMixin, seller

from . import services
from .models import AIModel, BillingSettings, Provider, Wallet, WalletTransaction

User = get_user_model()


class WalletTests(EngineSchemaMixin, TestCase):
    def setUp(self):
        self.user, self.biz = seller("a@x.com", "فروشگاه آ")
        _, self.other = seller("b@x.com", "فروشگاه ب")
        s = BillingSettings.get()
        s.usd_to_toman, s.markup_percent = Decimal("100000"), Decimal("50")
        s.save()

    def test_every_business_gets_a_wallet_with_signup_gift(self):
        self.assertEqual(self.biz.wallet.balance_toman, 0)
        s = BillingSettings.get()
        s.signup_credit_toman = Decimal("20000")
        s.save()
        _, biz = seller("c@x.com", "سومی")
        self.assertEqual(Wallet.objects.get(business=biz).balance_toman, Decimal("20000"))
        self.assertEqual(biz.wallet.transactions.get().kind, WalletTransaction.GIFT)

    def test_usage_is_charged_once_with_markup(self):
        services.apply_transaction(self.biz, 10000, WalletTransaction.TOPUP)
        st = self.store()
        st.add_cost("verify", "m", 100, 10, False, 0.02, 2000, businesses=[str(self.biz.pk), str(self.other.pk)])
        st.add_cost("need_extraction", "m", 100, 10, False, 0.5, 50000)              # platform: nobody pays
        st.add_cost("reply", "m", 100, 10, True, 0.04, 4000, businesses=[str(self.biz.pk)])   # cached: free
        r = services.charge_pending_usage(grace_seconds=0)
        self.assertEqual((r["rows"], r["businesses"]), (4, 2))
        # $0.01 share × 100,000 × 1.5 = 1,500 toman
        self.assertEqual(Wallet.objects.get(business=self.biz).balance_toman, Decimal("8500"))
        self.assertEqual(Wallet.objects.get(business=self.other).balance_toman, Decimal("-1500"))
        self.assertTrue(services.is_blocked(self.other))
        self.assertEqual(services.charge_pending_usage(grace_seconds=0)["rows"], 0)   # idempotent
        self.assertEqual(EngineSyncCursor.objects.get(name=services.BILLING_CURSOR).position, 4)

    def test_charge_without_engine_schema_is_a_noop(self):
        self.assertEqual(services.charge_pending_usage()["rows"], 0)


class OpsPanelTests(EngineSchemaMixin, TestCase):
    def setUp(self):
        self.staff = User.objects.create_user(username="ops@x.com", email="ops@x.com", password="pass12345", is_staff=True)
        self.seller_user, self.biz = seller("s@x.com", "فروشنده")
        self.store().add_cost("verify", "gem", 1000, 100, False, 0.001, 100, businesses=[str(self.biz.pk)])

    def test_only_staff(self):
        self.assertEqual(self.client.get(reverse("ops:dashboard")).status_code, 302)
        self.client.force_login(self.seller_user)
        self.assertEqual(self.client.get(reverse("ops:dashboard")).status_code, 403)

    def test_pages_render(self):
        self.client.force_login(self.staff)
        p = Provider.objects.create(name="Gemini", base_url="https://g.example/v1", api_key="secret-key-1234")
        AIModel.objects.create(provider=p, name="gem", use_extract=True)
        MonitoredCommunity.objects.create(handle_or_link="@global_group")
        for name, kw in [("dashboard", {}), ("costs", {}), ("providers", {}), ("wallets", {}), ("communities", {}),
                         ("settings", {}), ("model_new", {}), ("provider_new", {}),
                         ("wallet_detail", {"business_id": self.biz.pk})]:
            resp = self.client.get(reverse(f"ops:{name}", kwargs=kw))
            self.assertEqual(resp.status_code, 200, name)
        page = self.client.get(reverse("ops:providers")).content.decode()
        self.assertIn("1234", page)
        self.assertNotIn("secret-key-1234", page)            # key is masked
        self.assertIn("موتور تحلیل", self.client.get(reverse("ops:dashboard")).content.decode())

    def test_provider_and_model_crud(self):
        self.client.force_login(self.staff)
        self.client.post(reverse("ops:provider_new"), {"name": "OpenRouter", "base_url": "https://openrouter.ai/api/v1/",
                                                       "api_key": "k1", "is_active": "on"})
        p = Provider.objects.get(name="OpenRouter")
        self.assertEqual(p.base_url, "https://openrouter.ai/api/v1")
        self.client.post(reverse("ops:provider_edit", args=[p.pk]), {"name": "OpenRouter", "base_url": p.base_url,
                                                                     "api_key": "", "is_active": "on"})
        p.refresh_from_db()
        self.assertEqual(p.api_key, "k1")                    # empty field keeps the key
        self.client.post(reverse("ops:model_new"), {"provider": p.pk, "name": "gpt-x", "input_price_usd": "0.5",
                                                    "output_price_usd": "1.5", "use_verify": "on", "priority": 5,
                                                    "is_active": "on"})
        m = AIModel.objects.get(name="gpt-x")
        self.assertEqual((m.input_price_usd, m.use_verify), (Decimal("0.5"), True))
        self.client.post(reverse("ops:model_toggle", args=[m.pk]))
        m.refresh_from_db()
        self.assertFalse(m.is_active)
        self.client.post(reverse("ops:model_delete", args=[m.pk]))
        self.assertFalse(AIModel.objects.exists())
        self.client.post(reverse("ops:provider_delete", args=[p.pk]))
        self.assertFalse(Provider.objects.exists())

    def test_topup_and_global_source(self):
        self.client.force_login(self.staff)
        self.client.post(reverse("ops:wallet_detail", args=[self.biz.pk]), {"kind": "TOPUP", "amount_toman": "50000"})
        self.assertEqual(Wallet.objects.get(business=self.biz).balance_toman, Decimal("50000"))
        resp = self.client.post(reverse("ops:wallet_detail", args=[self.biz.pk]), {"kind": "TOPUP", "amount_toman": "-5"})
        self.assertEqual(resp.status_code, 200)              # negative top-up rejected
        self.client.post(reverse("ops:charge_now"))
        self.assertLess(Wallet.objects.get(business=self.biz).balance_toman, Decimal("50000"))
        self.client.post(reverse("ops:communities"), {"handle_or_link": "https://t.me/Some_Group"})
        self.client.post(reverse("ops:communities"), {"handle_or_link": "@some_group"})
        self.assertEqual(MonitoredCommunity.objects.filter(business__isnull=True).count(), 1)

    def test_x_page(self):
        import json
        import os
        import tempfile
        import uuid
        from unittest.mock import patch

        from django.db import connection
        from django.test import override_settings

        self.client.force_login(self.staff)
        self.assertEqual(self.client.get(reverse("ops:x")).status_code, 200)          # nothing collected yet
        st = self.store()
        st.add_cost("need_extraction_x_batch", "gem", 1000, 100, False, 0.002, 200, ref="x:1-9")
        crawler = f"cr_{uuid.uuid4().hex[:8]}"
        with connection.cursor() as c:
            c.execute(f"CREATE SCHEMA {crawler}")
            c.execute(f"""CREATE TABLE {crawler}.x_posts (row_id BIGSERIAL PRIMARY KEY, tweet_id BIGINT UNIQUE NOT NULL,
                          author_id TEXT NOT NULL, author_handle TEXT NOT NULL, text TEXT NOT NULL, created_at TIMESTAMPTZ,
                          url TEXT, query TEXT, loaded_at TIMESTAMPTZ NOT NULL DEFAULT now())""")
            c.execute(f"""INSERT INTO {crawler}.x_posts (tweet_id, author_id, author_handle, text, query)
                          VALUES (1, 'a', 'buyer1', 'هندزفری خوب چی بخرم', 'هندزفری lang:fa')""")
        status = {"status": "COMPLETED", "collected": 3, "collected_today": 7, "daily_cap": 200, "failures": 0,
                  "failed_queries": 0, "updated_at": "2026-01-01T00:00:00+00:00",
                  "queries": [{"query": "(هندزفری) (بخرم) lang:fa", "kind": "intent", "last_run_at": None, "fetched": 4,
                               "new_posts": 3}]}
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False, encoding="utf-8") as f:
            json.dump(status, f, ensure_ascii=False)
        try:
            with override_settings(TG_DB_SCHEMA=crawler), patch.dict(os.environ, {"X_STATUS_FILE": f.name}):
                resp = self.client.get(reverse("ops:x"))
                page = resp.content.decode()
                dash = self.client.get(reverse("ops:dashboard")).content.decode()
        finally:
            os.unlink(f.name)
        self.assertIn("buyer1", page)
        self.assertIn("(هندزفری) (بخرم) lang:fa", page)
        o = resp.context["o"]
        self.assertEqual((o["posts"], o["posts_total"], o["cost"]["extract_toman"], o["cost"]["extract_calls"]), (1, 1, 200.0, 1))
        self.assertEqual(o["cost"]["per_post"], 200.0)
        self.assertIn("جمع‌آور X", dash)

    def test_seller_header_shows_balance(self):
        self.client.force_login(self.seller_user)
        page = self.client.get(reverse("accounts:dashboard")).content.decode()
        self.assertIn("اعتبار:", page)


class EngineReadsPanelTests(EngineSchemaMixin, TransactionTestCase):
    """need_engine/registry.py against the real Django tables (committed rows, separate read-only connection)."""

    def test_registry_from_panel_tables(self):
        from need_engine.config import EngineConfig
        from need_engine.registry import ModelRegistry

        _, biz = seller("r@x.com", "ثبت")
        p = Provider.objects.create(name="P", base_url="https://p.example/v1", api_key="PK")
        AIModel.objects.create(provider=p, name="mx", input_price_usd=Decimal("1.25"), output_price_usd=3, use_reply=True)
        cfg = EngineConfig()
        cfg.database_url, cfg.products_source = self.dsn(), "db"
        reg = ModelRegistry(cfg)
        self.assertEqual(reg.model_for("reply"), "mx")
        self.assertEqual(reg.endpoint("mx"), ("https://p.example/v1", "PK"))
        self.assertEqual(reg.price("mx"), (1.25, 3.0))
        self.assertIn(str(biz.pk), reg.blocked())            # zero balance + enforcement on
        services.apply_transaction(biz, 1000, WalletTransaction.TOPUP)
        self.assertNotIn(str(biz.pk), reg.refresh(force=True) and reg.blocked())
