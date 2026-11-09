"""What the /manage/ dashboard view hands its template. Pins the context while the view is split into sections."""
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone

from apps.billing.models import WalletTransaction
from apps.billing.services import apply_transaction
from apps.businesses.models import Business
from apps.discovery.models import (AIAnalysis, Customer, InstagramCrawledPost, InstagramCrawlExecution,
                                   InstagramEngineAccount, InstagramEngineLead, InstagramSourceStat, Opportunity,
                                   ProxyConfig, TwitterEngineAccount, XCrawledPost)

CONTEXT_KEYS = {
    "accounts_with_pitches", "all_ig_accounts", "approved_ig_posts", "approved_x_tweets", "billing_settings",
    "businesses", "businesses_for_outreach", "crawl_executions", "default_consultant", "default_pitch",
    "dms_replied_total", "dms_sent_total", "financial_rows", "funnel_24h_conv_rate", "funnel_24h_leads",
    "funnel_24h_posts", "funnel_24h_searches", "funnel_total_conv_rate", "funnel_total_leads", "funnel_total_posts",
    "funnel_total_searches", "high_intent_leads", "ig_accounts_total", "ig_active_queries_count",
    "ig_comments_approved", "ig_comments_rejected", "ig_comments_total", "ig_conversion_rate_24h",
    "ig_conversion_rate_total", "ig_leads_24h", "ig_leads_total", "ig_min_comments", "ig_posts_24h",
    "ig_posts_approved", "ig_posts_rejected", "ig_posts_total", "ig_queries_list", "ig_searches_24h",
    "ig_searches_total", "ig_sleep_desc", "ig_window_label", "instagram_source_stats", "is_ig_night_sleep",
    "latest_lead_at", "latest_scraper_action_at", "leads", "markup_percent", "matrix_totals", "nav", "now",
    "outreach_dms_list", "outreach_failed_count", "outreach_pending_count", "outreach_replied_count",
    "outreach_replied_leads", "proxy_channels", "proxy_configs", "proxy_configs_active", "proxy_configs_total",
    "queries_top_gainers", "queries_top_losers", "queries_top_rated", "scrapers", "scrapers_active",
    "scrapers_banned", "scrapers_challenged", "scrapers_cooldown", "scrapers_total", "selected_business_id",
    "selected_outreach_biz", "senders", "senders_active", "senders_total", "tenant_matrix", "total_balance_all",
    "total_calls_all", "total_cost_usd_all", "total_leads", "total_raw_toman_all", "total_spent_all",
    "twitter_crawl_runs", "twitter_scrapers", "twitter_scrapers_active", "twitter_scrapers_banned",
    "twitter_scrapers_challenged", "twitter_scrapers_cooldown", "twitter_scrapers_total", "usd_rate",
    "verified_x_leads", "x_active_queries_count", "x_conversion_rate_24h", "x_conversion_rate_total",
    "x_leads_24h", "x_leads_count", "x_leads_total", "x_posts_24h", "x_posts_total", "x_searches_24h",
    "x_searches_total", "x_tweets_approved", "x_tweets_inspected", "x_tweets_rejected",
}


class ManageDashboardContextTests(TestCase):
    def setUp(self):
        users = get_user_model().objects
        self.staff = users.create_user("dash-staff", "d@x.ir", "pw", is_staff=True, is_superuser=True)
        owners = [users.create_user(f"dash-owner{i}", f"o{i}@x.ir", "pw") for i in range(2)]
        self.biz = [Business.objects.create(user=u, name=f"کسب‌وکار {i}", business_type="PHYSICAL",
                                            business_domain="پوشاک") for i, u in enumerate(owners)]
        apply_transaction(self.biz[0], 30000, WalletTransaction.TOPUP)
        now = timezone.now()
        scr = InstagramEngineAccount.objects.create(username="dash-scr", password="x", role="SCRAPER",
                                                    last_action_at=now)
        InstagramEngineAccount.objects.create(username="dash-scr2", password="x", role="SCRAPER", status="COOLDOWN",
                                              cooldown_until=now + timedelta(minutes=30))
        for i, b in enumerate(self.biz):
            InstagramEngineAccount.objects.create(username=f"dash-snd{i}", password="x", role="SENDER",
                                                  tenant_id=f"tenant_{b.pk}")
            InstagramEngineLead.objects.create(tenant_id=f"tenant_{b.pk}", ig_user_pk=str(i), username=f"l{i}",
                                               comment_text="قیمت؟", intent_score=.9, status="DM_SENT",
                                               has_replied=bool(i), reply_at=now)
        InstagramCrawlExecution.objects.create(business=self.biz[0], tenant_id=f"tenant_{self.biz[0].pk}",
                                               query_text="#x", target_type="HASHTAG", account_username=scr.username,
                                               account=scr, status="SUCCESS", posts_inspected=10, leads_found=2)
        InstagramCrawledPost.objects.create(post_hash="h1", media_id="m1", media_code="C1", account_username="s",
                                            posted_at=now, comment_count=50)
        for i, (alpha, beta) in enumerate(((3.0, 1.0), (1.0, 3.0))):
            InstagramSourceStat.objects.create(business=self.biz[0], family_id="f", source_type="hashtag",
                                               label=f"tag{i}", alpha=alpha, beta=beta, requests=2, leads=i)
        TwitterEngineAccount.objects.create(username="dash-tw", auth_token="t", ct0="c", total_action_count=10,
                                            daily_action_count=2, posts_found_count=4)
        XCrawledPost.objects.create(post_hash="xh", tweet_id="1", author_handle="a")
        for platform in ("x", "instagram", "telegram"):
            cust = Customer.objects.create(business=self.biz[0], name="c", source_username="buyer",
                                           external_user_id=platform, source_platform=platform)
            opp = Opportunity.objects.create(business=self.biz[0], customer=cust, source_platform=platform,
                                             source_message_id=f"{platform}-1", source_raw_message="نیاز")
            AIAnalysis.objects.create(opportunity=opp, need="n", intent_score=.95, product_fit_score=.9,
                                      confidence=.9, why_selected="w")
        ProxyConfig.objects.create(raw_url="vless://a@1.1.1.1:443", protocol="vless", local_port=1086,
                                   status="ACTIVE")
        self.client.force_login(self.staff)

    def test_context_keys_and_totals(self):
        ctx = self.client.get("/manage/").context
        self.assertEqual(set(ctx.keys()) & CONTEXT_KEYS, CONTEXT_KEYS)
        self.assertEqual((ctx["scrapers_total"], ctx["scrapers_active"], ctx["scrapers_cooldown"]), (2, 1, 1))
        self.assertEqual((ctx["senders_total"], ctx["total_leads"], ctx["dms_sent_total"]), (2, 2, 2))
        self.assertEqual(ctx["matrix_totals"], {"instagram": 1, "telegram": 1, "x": 1, "total": 3,
                                                "high_intent": 3, "dms_sent": 2})
        self.assertEqual([r["has_credit"] for r in ctx["financial_rows"]], [True, False])
        self.assertEqual(ctx["total_balance_all"], ctx["financial_rows"][0]["balance"])
        self.assertEqual((ctx["x_posts_total"], ctx["x_leads_total"], ctx["x_searches_total"]), (1, 1, 10))
        self.assertEqual((ctx["ig_searches_total"], ctx["ig_posts_total"], ctx["ig_posts_approved"]), (1, 1, 1))
        self.assertEqual((ctx["funnel_total_posts"], ctx["funnel_total_leads"]), (2, 2))
        self.assertEqual([q["label"] for q in ctx["queries_top_rated"]], ["tag0", "tag1"])
        self.assertEqual(([q["label"] for q in ctx["queries_top_gainers"]], [q["label"] for q in ctx["queries_top_losers"]]),
                         (["tag0"], ["tag1"]))
        self.assertEqual(ctx["verified_x_leads"][0]["tweet_url"], "https://x.com/buyer/status/x-1")
        tw = ctx["twitter_scrapers"][0]
        self.assertEqual((tw.live_status_key, tw.posts_total, tw.leads_total), ("ACTIVE", 4, 4))
        self.assertEqual((ctx["proxy_configs_total"], ctx["proxy_configs_active"]), (1, 1))
        self.assertEqual(ctx["outreach_replied_count"], 1)
        self.assertEqual(len(ctx["all_ig_accounts"]), 4)

    def test_outreach_business_filter_narrows_senders_and_leads(self):
        ctx = self.client.get(f"/manage/?outreach_biz={self.biz[1].pk}").context
        self.assertEqual(ctx["selected_outreach_biz"], self.biz[1])
        self.assertEqual((ctx["senders_total"], ctx["total_leads"], ctx["outreach_replied_count"]), (1, 1, 1))
        self.assertEqual([a["obj"].username for a in ctx["accounts_with_pitches"]], ["dash-snd1"])
        self.assertEqual(len(ctx["all_ig_accounts"]), 4)   # computed before the filter, as before
