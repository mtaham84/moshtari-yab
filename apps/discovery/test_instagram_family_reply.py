import random
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import Mock, patch

from django.contrib.auth import get_user_model
from django.test import SimpleTestCase, TestCase
from django.utils import timezone

from apps.businesses.models import Business
from apps.billing.models import AIModel, Provider, Wallet, WalletTransaction
from apps.discovery.instagram_families import (
    DiscoverySource, _validate_plan, cheap_buyer_match, ensure_family_plan, lead_quality_score, sample_sources,
)
from apps.discovery.models import (AIAnalysis, Customer, InstagramCrawledPost, InstagramFamilyPlan,
                                   InstagramReplySuggestion, Opportunity, OpportunityProductMatch)
from need_engine.reply import MessageStyle, validate_outreach_result


def _valid_plan():
    return {
        "hashtags": ["پیراهن_مردانه", "پوشاک_مردانه", "لباس_مجلسی_مردانه", "شومیز_زنانه", "جوراب_ورزشی", "لباس_بچگانه"],
        "keywords": ["لباس خنک", "لباس راحت", "پیراهن مردانه", "لباس بچگانه", "خرید عمده جوراب", "قیمت پیراهن"],
        "page_search_terms": ["فروشگاه پوشاک", "تولیدی جوراب", "مزون زنانه", "پوشاک مردانه"],
        "buyer_signals": ["قیمت", "موجوده", "سایز", "عمده", "دایرکت", "ارسال دارید"],
        "negative_signals": ["تبلیغات", "فالوور"],
    }


class InstagramPureTests(SimpleTestCase):
    def test_plan_rejects_handles_and_urls_and_requires_minimum_phrases(self):
        plan = _valid_plan()
        plan["page_search_terms"][0] = "@fake_shop"
        plan["page_search_terms"].append("تولیدی لباس")
        clean = _validate_plan(plan)
        self.assertNotIn("@fake_shop", clean["page_search_terms"])
        self.assertFalse(any("خرید" in term or "قیمت" in term for term in clean["keywords"]))
        plan = _valid_plan()
        plan["page_search_terms"] = ["instagram.com/fake", "example.com"]
        with self.assertRaises(ValueError):
            _validate_plan(plan)

    def test_cheap_regex_funnel_and_quality_are_family_aware(self):
        self.assertTrue(cheap_buyer_match("سلام قیمتش چنده؟", ["عمده"]))
        self.assertFalse(cheap_buyer_match("تبلیغات فروش ویژه", ["قیمت"], ["تبلیغات"]))
        wholesale = lead_quality_score(.8, is_business=True, follower_count=5000, bio="تولیدی جوراب",
                                       seller_notes="فروش عمده", family_path="پوشاک > جوراب", product_titles=["جوراب"])
        consumer = lead_quality_score(.8, is_business=False, follower_count=None, bio="علاقه‌مند به پوشاک",
                                      seller_notes="فروش تکی", family_path="پوشاک", product_titles=[])
        self.assertGreater(wholesale, .8)
        self.assertGreater(consumer, .8)

    def test_regex_matched_comments_do_not_call_llm_but_ambiguous_comments_are_batched(self):
        from instagram_engine.analyzer import evaluate_comment_smart, llm_evaluate_family_batch
        fake_llm = Mock(return_value={"results": [{"id": "c2", "is_buyer": True,
                                                   "intent_score": .82, "reason": "درباره خرید سوال دارد"}]})
        with patch("apps.core.llm.chat_json", fake_llm):
            direct = evaluate_comment_smart("قیمت لطفا", buyer_signals=["قیمت"])
            self.assertTrue(direct["is_lead"])
            fake_llm.assert_not_called()
            ambiguous = [{"id": "c2", "username": "buyer", "text": "برای تابستان دنبال لباس مناسبم"}]
            result = llm_evaluate_family_batch(ambiguous, business_id=4,
                                               family_context={"category_path": "پوشاک"}, call_llm=fake_llm)
        self.assertTrue(result["c2"]["is_lead"])
        self.assertEqual(fake_llm.call_count, 1)

    def test_source_bandit_reward_and_exploration_budget(self):
        sources = [DiscoverySource("f1", "page", "@shop", True),
                   DiscoverySource("f1", "hashtag", "پیراهن_مردانه"),
                   DiscoverySource("f2", "keyword", "قیمت پیراهن"),
                   DiscoverySource("f2", "hashtag", "لباس_مجلسی"),
                   DiscoverySource("f3", "keyword", "خرید عمده")]
        with patch("apps.discovery.models.InstagramSourceStat.objects.filter", return_value=[]):
            selected = sample_sources(SimpleNamespace(), sources, 4, rng=random.Random(4))
        self.assertEqual(len(selected), 4)
        self.assertIn(sources[0], selected)  # a seller-managed page is pinned
        self.assertTrue(any(s.source_type == "keyword" for s in selected))

    def test_reply_json_validation_channel_limits_emojis_and_not_a_buyer(self):
        raw = {"channel": "public_reply", "why": "سؤال درباره سایز بود.", "warnings": [], "variants": [
            {"tone": "short", "text": "سلام، سایز ۳۸ را پرسیدید 😊. #پیراهن"},
            {"tone": "friendly", "text": "در مورد سایز ۳۸ راهنمایی‌تان می‌کنم. چه مدلی می‌پسندید؟"},
            {"tone": "consultative", "text": "برای سایز ۳۸، قد و مدل مدنظرتان را بفرمایید تا گزینه مناسب را معرفی کنم."},
        ]}
        result = validate_outreach_result(raw, platform="x", style=MessageStyle(use_emoji=False, max_sentences=4))
        self.assertEqual(len(result["variants"]), 3)
        self.assertNotIn("😊", result["variants"][0]["text"])
        self.assertNotIn("#پیراهن", result["variants"][0]["text"])
        self.assertLessEqual(__import__("need_engine.x_replies", fromlist=["x_weighted_length"]).x_weighted_length(
            result["variants"][0]["text"]), 280)
        none = validate_outreach_result({"channel": "none", "variants": [], "why": "شوخی بود", "warnings": []},
                                        platform="instagram", style=MessageStyle())
        self.assertEqual(none["warnings"], ["not_a_buyer"])

    def test_similar_suggestion_rejected(self):
        raw = {"channel": "dm", "why": "پرسش قیمت", "warnings": [], "variants": [
            {"tone": "short", "text": "برای قیمت پیراهن، مدل مدنظرتان را بفرمایید."},
            {"tone": "friendly", "text": "سلام، برای قیمت پیراهن چه مدلی را در نظر دارید؟"},
            {"tone": "consultative", "text": "برای اینکه قیمت پیراهن دقیق باشد، لطفاً مدل و سایز را بفرمایید."},
        ]}
        with self.assertRaises(ValueError):
            validate_outreach_result(raw, platform="instagram", style=MessageStyle(),
                                     prior_suggestions=[raw["variants"][0]["text"]])
        raw["variants"][1]["text"] = raw["variants"][0]["text"]
        with self.assertRaises(ValueError):
            validate_outreach_result(raw, platform="instagram", style=MessageStyle())


class InstagramFamilyDatabaseTests(TestCase):
    def setUp(self):
        user = get_user_model().objects.create_user(username="ig-family-test", password="x")
        self.business = Business.objects.create(user=user, name="فروشگاه پوشاک", business_type="PHYSICAL",
                                                business_domain="پوشاک", search_notes="فروش عمده به فروشگاه‌ها")
        from apps.billing.models import Wallet, WalletTransaction
        from apps.billing.services import apply_transaction
        apply_transaction(self.business, 5000, WalletTransaction.TOPUP)
        self.family = SimpleNamespace(product_id=f"f{self.business.pk}-abc", business_id=str(self.business.pk),
                                      category_path="پوشاک > مردانه > پیراهن", titles=["پیراهن مردانه"],
                                      notes=self.business.search_notes)
        self.family.content_hash = lambda: "hash-a"

    def test_plan_cache_and_rebuild_on_family_hash_change(self):
        llm = Mock(return_value=_valid_plan())
        plan, rebuilt = ensure_family_plan(self.business, self.family, call_llm=llm)
        self.assertTrue(rebuilt)
        self.assertEqual(plan.status, "ready")
        again, rebuilt = ensure_family_plan(self.business, self.family, call_llm=llm)
        self.assertFalse(rebuilt)
        self.assertEqual(plan.pk, again.pk)
        self.assertEqual(llm.call_count, 1)
        self.family.content_hash = lambda: "hash-b"
        changed, rebuilt = ensure_family_plan(self.business, self.family, call_llm=llm)
        self.assertTrue(rebuilt)
        self.assertEqual(changed.content_hash, "hash-b")
        self.assertEqual(llm.call_count, 2)

    def test_zero_balance_skips_plan_llm_before_cache_lookup(self):
        from apps.billing.models import Wallet
        wallet = Wallet.objects.get(business=self.business)
        wallet.balance_toman = 0
        wallet.save(update_fields=["balance_toman"])
        llm = Mock(return_value=_valid_plan())
        plan, built = ensure_family_plan(self.business, self.family, call_llm=llm)
        self.assertIsNone(plan)
        self.assertFalse(built)
        llm.assert_not_called()

    def test_zero_balance_skips_instagram_scraping_before_api_calls(self):
        from apps.discovery.management.commands.run_instagram_scraper import Command
        command = Command()
        command._instagram_api_call = Mock(side_effect=AssertionError("Instagram API called"))
        target = {"business": self.business, "family": self.family, "plan": None, "sources": []}
        with patch("apps.billing.services.is_blocked", return_value=True):
            self.assertEqual(command._scrape_family_target(object(), object(), target, 20, 6), 0)
        command._instagram_api_call.assert_not_called()

    def test_daily_plan_cap(self):
        for i in range(8):
            InstagramFamilyPlan.objects.create(business=self.business, family_id=f"f{i}", content_hash=f"h{i}")
        llm = Mock(return_value=_valid_plan())
        plan, built = ensure_family_plan(self.business, self.family, call_llm=llm)
        self.assertIsNone(plan)
        self.assertFalse(built)
        llm.assert_not_called()

    def test_source_reward_and_empty_run_penalty_are_persisted(self):
        from apps.discovery.instagram_families import record_source_result
        from apps.discovery.models import InstagramSourceStat
        source = DiscoverySource(self.family.product_id, "keyword", "پیراهن مردانه")
        first = record_source_result(self.business, source, leads=2, status="SUCCESS")
        self.assertEqual((first.requests, first.leads, first.alpha, first.beta), (1, 2, 3.0, 1.0))
        second = record_source_result(self.business, source, leads=0, status="THROTTLED")
        self.assertEqual((second.requests, second.leads, second.alpha, second.beta), (2, 2, 3.0, 1.25))
        self.assertEqual(InstagramSourceStat.objects.count(), 1)

    def test_crawled_comments_are_reused_across_businesses(self):
        from apps.discovery.management.commands.run_instagram_scraper import Command
        from apps.discovery.instagram_families import DiscoverySource

        now = timezone.now()
        media = SimpleNamespace(id="media-1", pk="media-1", code="abc", caption_text="پیراهن موجود است",
                                comment_count=25, taken_at=now)
        user = SimpleNamespace(pk="user-1", id="user-1", username="buyer", full_name="Buyer",
                               is_business=False, follower_count=1500, biography="خریدار پوشاک")
        comment = SimpleNamespace(pk="comment-1", id="comment-1", user=user, text="قیمت لطفا")
        client = SimpleNamespace(media_comments=Mock(return_value=[comment]))
        account = SimpleNamespace(username="scraper")
        plan = SimpleNamespace(buyer_signals=["قیمت", "موجود"], negative_signals=[])
        source = DiscoverySource("f1", "keyword", "پیراهن مردانه")
        family = SimpleNamespace(product_id="f1", category_path="پوشاک > مردانه > پیراهن",
                                 titles=["پیراهن مردانه"], notes="فروش عمده")
        command = Command()
        command._request_budget_available = lambda _account: True
        command._current_api_requests = 0
        with patch("apps.discovery.services.sync_instagram_lead_to_opportunity") as sync:
            command._inspect_family_medias(client, account, {"business": self.business, "family": family,
                                      "plan": plan, "tenant_id": f"tenant_{self.business.pk}"}, source, [media], 20)
            second_user = get_user_model().objects.create_user(username="ig-family-test-2", password="x")
            second_business = Business.objects.create(user=second_user, name="پوشاک دوم", business_type="PHYSICAL",
                                                       business_domain="پوشاک")
            second_family = SimpleNamespace(product_id=f"f{second_business.pk}-def", category_path=family.category_path,
                                            titles=family.titles, notes="")
            source2 = DiscoverySource(second_family.product_id, "keyword", "پیراهن مردانه")
            command._inspect_family_medias(client, account, {"business": second_business, "family": second_family,
                                      "plan": plan, "tenant_id": f"tenant_{second_business.pk}"}, source2, [media], 20)
        client.media_comments.assert_called_once()
        self.assertEqual(InstagramCrawledPost.objects.count(), 1)
        self.assertEqual(sync.call_count, 2)


class OutreachBillingDatabaseTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(username="outreach-test", password="x")
        self.business = Business.objects.create(user=self.user, name="فروشگاه پوشاک", business_type="PHYSICAL",
                                                business_domain="پوشاک", search_notes="فروش عمده", onboarding_done=True)
        self.product = __import__("apps.products.models", fromlist=["Product"]).Product.objects.create(
            business=self.business, name="پیراهن مردانه", description="پیراهن نخی", price=500000,
        )
        self.customer = Customer.objects.create(business=self.business, name="مشتری", source_username="buyer",
                                                external_user_id="123", source_platform="instagram",
                                                metadata={"bio": "علاقه مند به پوشاک", "is_business": False})
        self.opportunity = Opportunity.objects.create(business=self.business, customer=self.customer,
            source_platform="instagram", source_message_id="ig:post:c1", source_raw_message="سلام، سایز ۳۸ موجوده؟",
            trace_metadata={"instagram": {"post_url": "https://www.instagram.com/p/abc/", "post_caption": "پیراهن"}})
        AIAnalysis.objects.create(opportunity=self.opportunity, need="پرسش درباره سایز", intent_score=.9,
                                  product_fit_score=.9, confidence=.9, why_selected="سؤال موجودی")
        self.match = OpportunityProductMatch.objects.create(opportunity=self.opportunity, product=self.product,
                                                            match_score=.9, rank=1)
        provider = Provider.objects.create(name="test-provider", base_url="https://api.example.test/v1",
                                           api_key="not-a-real-key")
        AIModel.objects.create(provider=provider, name="gpt-6-luna", input_price_usd=1,
                               output_price_usd=1, use_reply=True, is_active=True)

    def test_opportunity_detail_and_list_render_suggestion_cards(self):
        from django.test import Client
        from django.urls import reverse
        from apps.discovery.models import InstagramReplySuggestion
        variants = self._variants("درود")
        self.match.reply_variants = variants
        self.match.save(update_fields=["reply_variants"])
        InstagramReplySuggestion.objects.create(opportunity=self.opportunity, product=self.product,
                                                reply_variants=variants)
        client = Client()
        client.force_login(self.user)
        detail = client.get(reverse("discovery:opportunity_detail", args=[self.opportunity.pk]))
        listing = client.get(reverse("discovery:opportunity_list"))
        self.assertEqual(detail.status_code, 200)
        self.assertEqual(listing.status_code, 200)
        self.assertContains(detail, "تولید / بازنویسی")
        self.assertContains(listing, "تولید / بازنویسی")

    @staticmethod
    def _variants(prefix):
        return {"channel": "dm", "why": "سؤال درباره سایز بود.", "warnings": [], "variants": [
            {"tone": "short", "text": f"{prefix} برای سایز ۳۸ راهنمایی می‌کنم."},
            {"tone": "friendly", "text": f"{prefix} درباره سایز ۳۸ با کمال میل کمک می‌کنم، چه مدلی مدنظرتان است؟"},
            {"tone": "consultative", "text": f"{prefix} اگر قد و مدل مدنظر را بفرمایید، گزینه مناسب سایز ۳۸ را معرفی می‌کنم."},
        ]}

    def test_wallet_blocked_skips_generation_and_success_creates_billing_entry(self):
        from apps.discovery.services import generate_opportunity_reply_suggestions
        from apps.core.models import LLMUsageLog
        from apps.billing.models import Wallet
        from django.utils import timezone
        from unittest.mock import patch

        fake_chat = Mock(side_effect=lambda *args, **kwargs: self._variants("سلام"))
        with patch("apps.billing.services.is_blocked", return_value=True), patch("apps.core.llm.chat_json", fake_chat):
            self.assertIsNone(generate_opportunity_reply_suggestions(self.opportunity, self.match, force=True))
        fake_chat.assert_not_called()
        wallet = Wallet.objects.get(business=self.business)
        wallet.balance_toman = 5000
        wallet.save(update_fields=["balance_toman"])

        def log_and_reply(*args, **kwargs):
            LLMUsageLog.objects.create(model="gpt-6-luna", purpose="outreach_suggestion",
                                       business_id=self.business.pk, tokens_in=1000, tokens_out=1000, ok=True)
            return self._variants("درود")

        with patch("apps.billing.services.is_blocked", return_value=False), patch("apps.core.llm.chat_json", side_effect=log_and_reply):
            result = generate_opportunity_reply_suggestions(self.opportunity, self.match, force=True)
        self.assertEqual(len(result["variants"]), 3)
        self.assertTrue(Wallet.objects.filter(business=self.business).exists())
        self.assertTrue(WalletTransaction.objects.filter(wallet__business=self.business, kind="USAGE", calls=1).exists())

    def test_regeneration_keeps_previous_variants_in_history(self):
        from apps.discovery.services import generate_opportunity_reply_suggestions
        from apps.core.models import LLMUsageLog
        from unittest.mock import patch

        def reply_one(*args, **kwargs):
            LLMUsageLog.objects.create(model="gpt-6-luna", purpose="outreach_suggestion", business_id=self.business.pk,
                                       tokens_in=100, tokens_out=100, ok=True)
            return self._variants("درود و وقت بخیر")

        def reply_two(*args, **kwargs):
            LLMUsageLog.objects.create(model="gpt-6-luna", purpose="outreach_suggestion", business_id=self.business.pk,
                                       tokens_in=100, tokens_out=100, ok=True)
            return {"channel": "comment_reply", "why": "پرسش اندازه بود", "warnings": [], "variants": [
                {"tone": "short", "text": "درباره اندازه، مدل مورد نظرتان را بفرمایید."},
                {"tone": "friendly", "text": "برای انتخاب اندازه خوشحال می‌شوم راهنمایی کنم، چه مدلی می‌خواهید؟"},
                {"tone": "consultative", "text": "اگر اندازه و کاربرد مدنظر را بگویید، گزینه مناسب را پیشنهاد می‌دهم."},
            ]}

        with patch("apps.billing.services.is_blocked", return_value=False), patch("apps.core.llm.chat_json", side_effect=reply_one):
            generate_opportunity_reply_suggestions(self.opportunity, self.match, force=True)
        with patch("apps.billing.services.is_blocked", return_value=False), patch("apps.core.llm.chat_json", side_effect=reply_two):
            generate_opportunity_reply_suggestions(self.opportunity, self.match, force=True, instruction="کوتاه‌تر")
        saved = InstagramReplySuggestion.objects.get(opportunity=self.opportunity, product=self.product)
        self.assertEqual(len(saved.history), 1)
        self.assertEqual(saved.history[0]["reply_variants"]["variants"][0]["tone"], "short")

    def test_low_intent_opportunity_also_gets_human_review_drafts(self):
        from apps.discovery.services import generate_opportunity_reply_suggestions
        from unittest.mock import patch
        AIAnalysis.objects.filter(opportunity=self.opportunity).update(intent_score=.35)
        generator = Mock(return_value=(self._variants("پیشنهاد شخصی‌سازی‌شده"), {}))
        with patch("apps.billing.services.is_blocked", return_value=False):
            result = generate_opportunity_reply_suggestions(self.opportunity, self.match, generator=generator)
        self.assertEqual(len(result["variants"]), 3)
        generator.assert_called_once()
