"""Panel features of this patch: message style + «نمونه بساز», «دوباره بنویس», /r/ click links, product autofill
(safe fetch), the editable agent card, dashboard/settings/login clean-up and the new-opportunities check."""
import json
import os
from unittest import mock

from django.contrib.auth import get_user_model
from django.test import TestCase, TransactionTestCase, override_settings
from django.urls import reverse

from apps.businesses.models import Business, MessageStyle
from apps.discovery.engine_bridge import import_opportunity
from apps.discovery.models import Opportunity, ProductDailyMetric
from apps.discovery.tests import EngineSchemaMixin, engine_payload, global_source
from apps.products import autofill
from apps.products.models import Product

User = get_user_model()


def make_seller(email="s@example.com", name="فروشگاه آفتاب"):
    user = User.objects.create_user(username=email, email=email, password="pass12345", first_name="سارا")
    biz = Business.objects.create(user=user, name=name, business_type="PHYSICAL", business_domain="پوشاک",
                                  description="دستکش و لوازم گرم زمستانی برای موتورسواران")
    return user, biz


@override_settings(PANEL_MOCK_LLM=True, PUBLIC_BASE_URL="https://panel.example.ir")
class PanelAgentTests(EngineSchemaMixin, TestCase):
    def setUp(self):
        self.user, self.biz = make_seller()
        self.client.force_login(self.user)
        self.store()   # throw-away need_engine schema (cost ledger of the panel's calls)
        self.with_url = Product.objects.create(business=self.biz, name="دستکش گرم", description="ضدآب", price=900000,
                                               url="https://shop.example.ir/gloves")
        self.no_url = Product.objects.create(business=self.biz, name="کلاه بافت", description="گرم", price=300000)

    # ── message style ──────────────────────────────────────────────────────
    def test_style_is_saved_from_settings(self):
        r = self.client.post(reverse("accounts:settings"), {
            "section": "style", "tone": "FORMAL", "max_sentences": "9", "use_emoji": "on", "signature": "فروشگاه آفتاب",
            "extra_instructions": "x" * 800})
        self.assertRedirects(r, reverse("accounts:settings") + "?tab=style")
        st = MessageStyle.objects.get(business=self.biz)
        self.assertEqual((st.tone, st.max_sentences, st.use_emoji, st.include_link), ("FORMAL", 5, True, False))
        self.assertEqual(len(st.extra_instructions), 500)
        self.biz.refresh_from_db()
        self.assertEqual(self.biz.name, "فروشگاه آفتاب")          # other sections untouched

    def test_sample_uses_the_unsaved_form_values_and_is_charged_to_the_seller(self):
        r = self.client.post(reverse("accounts:style_sample"), {
            "product_id": self.with_url.pk, "tone": "CASUAL", "max_sentences": "2", "include_link": "on",
            "signature": "— تیم آفتاب @evil"})
        data = r.json()
        self.assertEqual(data["status"], "success", data)
        self.assertIn(f"https://panel.example.ir/r/{self.with_url.pk}/?ref=sample", data["reply"])
        self.assertTrue(data["reply"].endswith("— تیم آفتاب"))     # @handle removed from the signature
        self.assertFalse(MessageStyle.objects.filter(business=self.biz).exists())   # nothing saved
        rows = self.store()._all("SELECT stage, business_id FROM {s}.costs")
        self.assertIn(("reply_sample", str(self.biz.pk)), [(x["stage"], x["business_id"]) for x in rows])

        r = self.client.post(reverse("accounts:style_sample"), {"product_id": self.no_url.pk, "include_link": "on"})
        self.assertNotIn("http", r.json()["reply"])                 # product without a page → no link

    @override_settings(PANEL_MOCK_LLM=False)
    def test_sample_without_api_key_gives_a_persian_error(self):
        with mock.patch.dict(os.environ, {"NE_LLM_API_KEY": "", "GEMINI_API_KEY": ""}):
            r = self.client.post(reverse("accounts:style_sample"), {"product_id": self.with_url.pk})
        self.assertEqual(r.status_code, 503)
        self.assertIn("NE_LLM_API_KEY", r.json()["message"])

    def test_settings_hide_settings_that_do_nothing(self):
        html = self.client.get(reverse("accounts:settings")).content.decode()
        for gone in ("telegram_session_or_bot", "daily_discovery_limit", "telegram_account_handle"):
            self.assertNotIn(gone, html)
        self.assertIn("سبک پیام", html)
        self.assertIn("نمونه بساز", html)

    # ── opportunities ─────────────────────────────────────────────────────
    def _opportunity(self) -> Opportunity:
        global_source()
        return import_opportunity(engine_payload("need_9", [self.with_url.pk, self.no_url.pk]))[0]

    def test_rewrite_keeps_the_seller_draft_after_a_later_engine_update(self):
        opp = self._opportunity()
        url = reverse("discovery:opportunity_rewrite", args=[opp.pk])
        first = self.client.post(url, {"product_id": self.with_url.pk}).json()
        self.assertEqual(first["status"], "success")
        self.assertIn(f"https://panel.example.ir/r/{self.with_url.pk}/?ref=need_9", first["reply"])
        second = self.client.post(url, {"product_id": self.no_url.pk}).json()
        self.assertNotIn("http", second["reply"])
        opp.refresh_from_db()
        self.assertEqual(opp.trace_metadata["seller_drafts_n"], {str(self.with_url.pk): 1, str(self.no_url.pk): 1})
        self.assertEqual(opp.ai_analysis.suggested_reply, first["reply"])

        import_opportunity(engine_payload("need_9", [self.with_url.pk, self.no_url.pk]))   # engine re-publishes
        opp.refresh_from_db()
        self.assertEqual(opp.trace_metadata["seller_drafts"][str(self.with_url.pk)], first["reply"])
        self.assertEqual(opp.ai_analysis.suggested_reply, first["reply"])
        page = self.client.get(reverse("discovery:opportunity_detail", args=[opp.pk])).content.decode()
        self.assertIn("دوباره بنویس", page)
        self.assertIn(second["reply"], page)                         # drafts of every product are on the page

    def test_other_sellers_cannot_rewrite(self):
        opp = self._opportunity()
        other, _ = make_seller("o@example.com", "دیگری")
        self.client.force_login(other)
        r = self.client.post(reverse("discovery:opportunity_rewrite", args=[opp.pk]), {"product_id": self.with_url.pk})
        self.assertEqual(r.status_code, 404)

    def test_new_opportunity_count(self):
        url = reverse("discovery:opportunity_new_count")
        self.assertEqual(self.client.get(url, {"after": 0}).json()["count"], 0)
        opp = self._opportunity()
        self.assertEqual(self.client.get(url, {"after": 0}).json()["count"], 1)
        self.assertEqual(self.client.get(url, {"after": opp.pk}).json()["count"], 0)
        page = self.client.get(reverse("discovery:opportunity_list")).content.decode()
        self.assertIn(f'data-after="{opp.pk}"', page)
        self.assertIn("20000", page)

    # ── /r/ click link ────────────────────────────────────────────────────
    def test_click_link_counts_and_goes_to_the_sellers_page(self):
        r = self.client.get(f"/r/{self.with_url.pk}/?ref=need_9")
        self.assertEqual((r.status_code, r["Location"]), (302, "https://shop.example.ir/gloves"))
        self.client.get(f"/r/{self.with_url.pk}/")
        self.assertEqual(ProductDailyMetric.objects.get(product=self.with_url).clicks_count, 2)
        r = self.client.get(f"/r/{self.no_url.pk}/?ref=need_9")
        self.assertEqual(r["Location"], f"/p/{self.no_url.pk}/?ref=need_9")

    # ── agent card ────────────────────────────────────────────────────────
    def test_agent_card_edit_and_revert(self):
        from apps.products.agent_card import card_state

        self.assertEqual(card_state(self.with_url)["state"], "pending")
        from need_engine.schemas import Product as EP, ProductCard
        import numpy as np
        ep = EP(product_id=str(self.with_url.pk), title="دستکش گرم")
        self.store().save_product(ep, ProductCard(product_id=ep.product_id, what_it_is="دستکش", aliases=["دستکش زمستانی"],
                                                  use="personal"), np.zeros((1, 4), dtype=np.float32), ["what"])
        state = card_state(self.with_url)
        self.assertEqual((state["state"], state["card"]["what_it_is"]), ("agent", "دستکش"))
        page = self.client.get(reverse("products:detail", args=[self.with_url.pk])).content.decode()
        self.assertIn("ایجنت محصول شما را این‌طور فهمیده", page)

        url = reverse("products:agent_card", args=[self.with_url.pk])
        self.client.post(url, {"action": "save", "what_it_is": "دستکش موتورسواری ضدآب", "aliases": "دستکش موتور، دستکش گرم",
                               "problems_solved": "سرمای دست\nخیس شدن دست", "audience": "پیک‌ها"})
        self.with_url.refresh_from_db()
        self.assertEqual(self.with_url.agent_card_override, {
            "what_it_is": "دستکش موتورسواری ضدآب", "aliases": ["دستکش موتور", "دستکش گرم"],
            "problems_solved": ["سرمای دست", "خیس شدن دست"], "audience": "پیک‌ها", "use": "personal", "level": None})
        self.assertEqual(card_state(self.with_url)["state"], "seller")
        page = self.client.get(reverse("products:detail", args=[self.with_url.pk])).content.decode()
        self.assertIn(">سرمای دست\nخیس شدن دست</textarea>", page)
        self.assertNotIn("problems_solved|join", page)

        self.client.post(url, {"action": "revert"})
        self.with_url.refresh_from_db()
        self.assertIsNone(self.with_url.agent_card_override)
        self.assertEqual(card_state(self.with_url)["state"], "agent")

    # ── autofill ──────────────────────────────────────────────────────────
    def test_autofill_endpoint_uses_structured_data_first(self):
        html = """<html><head><script type="application/ld+json">{"@context":"https://schema.org","@graph":[{"@type":"Product",
        "name":"دستکش موتور زمستانی","description":"دستکش ضدآب","offers":{"@type":"Offer","price":"9000000","priceCurrency":"IRR"},
        "additionalProperty":[{"name":"جنس","value":"چرم"}]}]}</script></head><body>...</body></html>"""
        with mock.patch.object(autofill, "fetch", return_value=("https://shop.example.ir/p/1", html)):
            r = self.client.post(reverse("products:api_autofill"), {"url": "shop.example.ir/p/1"})
        data = r.json()
        self.assertEqual(data["url"], "https://shop.example.ir/p/1")
        self.assertEqual(data["data"], {"name": "دستکش موتور زمستانی", "price": 900000, "description": "دستکش ضدآب",
                                        "features": [{"name": "جنس", "value": "چرم"}], "source": "structured"})

    def test_autofill_falls_back_to_the_llm(self):
        html = "<html><head><title>فروشگاه</title></head><body><p>" + "متن صفحه‌ی محصول " * 10 + "</p></body></html>"
        with mock.patch.object(autofill, "fetch", return_value=("https://shop.example.ir/p/2", html)):
            data = self.client.post(reverse("products:api_autofill"), {"url": "https://shop.example.ir/p/2"}).json()
        self.assertEqual(data["data"]["source"], "llm")
        self.assertEqual(data["data"]["price"], 1200000)
        rows = self.store()._all("SELECT business_id FROM {s}.costs WHERE stage = 'product_extract'")
        self.assertEqual([x["business_id"] for x in rows], [str(self.biz.pk)])

    def test_autofill_rejects_internal_addresses(self):
        for url in ("http://127.0.0.1:8000/admin/", "http://localhost/", "http://10.0.0.5/x", "http://169.254.169.254/latest",
                    "http://[::1]/", "ftp://example.com/x", "http://user:pw@example.com/"):
            with self.assertRaises(autofill.FetchError, msg=url):
                autofill.fetch(url)
        r = self.client.post(reverse("products:api_autofill"), {"url": "http://127.0.0.1/"})
        self.assertEqual(r.status_code, 400)
        self.assertIn("داخلی", r.json()["message"])

    def test_redirect_to_an_internal_address_is_rejected(self):
        class Resp:
            status = 302

            def getheader(self, k, default=None):
                return "http://127.0.0.1/secret" if k == "Location" else default

        with mock.patch.object(autofill, "_public_ips", side_effect=[["93.184.216.34"], autofill.FetchError("داخلی")]), \
             mock.patch.object(autofill._PinnedHTTPS, "request"), \
             mock.patch.object(autofill._PinnedHTTPS, "getresponse", return_value=Resp()):
            with self.assertRaises(autofill.FetchError):
                autofill.fetch("https://example.com/")

    # ── dashboard / login ─────────────────────────────────────────────────
    def test_dashboard_shows_real_data_only(self):
        html = self.client.get(reverse("accounts:dashboard")).content.decode()
        for fake in ("سارا امیری", "علی رضایی", "محسن کریمی", "leadsData", "تأیید شناخت ایجنت", "کاهش خستگی مفرط"):
            self.assertNotIn(fake, html)
        self.assertIn("هنوز فرصتی پیدا نشده", html)
        self.assertIn("دستکش و لوازم گرم زمستانی برای موتورسواران", html)   # ICP from the business profile
        opp = self._opportunity()
        html = self.client.get(reverse("accounts:dashboard")).content.decode()
        self.assertIn(reverse("discovery:opportunity_detail", args=[opp.pk]), html)
        self.assertIn("دستکش گرم موتور", html)                               # the need of the real opportunity


class EngineProductSourceTests(TransactionTestCase):
    """The engine reads the Django product table on its own read-only connection (needs committed rows)."""

    def test_engine_reads_url_and_override_from_the_product_table(self):
        from need_engine.config import EngineConfig
        from need_engine.sources import SQLProductSource

        _, biz = make_seller()
        self.with_url = Product.objects.create(business=biz, name="دستکش", description="x", url="https://shop.example.ir/gloves",
                                               agent_card_override={"what_it_is": "دستکش موتور"})
        self.no_url = Product.objects.create(business=biz, name="کلاه", description="x")
        src = SQLProductSource(EngineConfig(), dsn=EngineSchemaMixin.dsn(self))
        got = {p.product_id: p for p in src.all()}
        self.assertEqual(got[str(self.with_url.pk)].url, "https://shop.example.ir/gloves")
        self.assertEqual(got[str(self.with_url.pk)].card_override, {"what_it_is": "دستکش موتور"})
        self.assertIsNone(got[str(self.no_url.pk)].url)



class AutofillParsingTests(TestCase):
    def test_open_graph_and_currency(self):
        s, _ = autofill.structured('<meta property="og:title" content="کیف چرمی"><meta name="description" content="کیف دست‌دوز">'
                                   '<meta property="product:price:amount" content="۱,۲۰۰,۰۰۰"><meta property="product:price:currency" content="IRT">')
        self.assertEqual((s.name, s.description, s.price_toman), ("کیف چرمی", "کیف دست‌دوز", 1200000))
        self.assertTrue(s.enough())
        self.assertIsNone(autofill.to_toman(10, "USD"))
        self.assertEqual(autofill.to_toman(50000, "IRR"), 5000)
