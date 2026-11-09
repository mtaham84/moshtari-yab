"""Scraper behavior that the ig_scraper modules must keep: family target order, source retirement, media fetching."""
from datetime import timedelta
from io import StringIO
from types import SimpleNamespace
from unittest.mock import Mock, patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone

from apps.businesses.models import Business
from apps.discovery.instagram_families import DiscoverySource
from apps.discovery.models import InstagramEngineAccount, InstagramFamilyPlan, InstagramPage, InstagramSourceStat
from apps.products.models import Product


def _command():
    from apps.discovery.management.commands.run_instagram_scraper import Command
    return Command(stdout=StringIO(), stderr=StringIO())


def _targets() -> list[dict]:
    """Targets of one non-loop cycle, in the order they are scraped."""
    from apps.discovery.management.commands import run_instagram_scraper as mod

    seen = []
    account = InstagramEngineAccount.objects.create(username="scr", password="x", role="SCRAPER",
                                                    tenant_id="tenant_default", session_json={})
    with patch.object(mod.Command, "_available_scraper_accounts", return_value=[account]), \
            patch.object(mod.Command, "_is_usable", return_value=True), \
            patch.object(mod.Command, "_client_or_rest", return_value=object()), \
            patch.object(mod.Command, "_persist_session"), \
            patch.object(mod.Command, "_scrape_family_target",
                         side_effect=lambda cl, acc, target, min_comments: seen.append(target) or 0), \
            patch("apps.discovery.anti_ban.InstagramAntiBanManager.sleep_after_business"):
        _command().handle(min_comments=10, loop=False, interval=5, force_hours=True)
    return seen


def _retire(business, source_type, label, family=None, plan=None):
    _command()._retire_and_delete_source(business=business, source_type=source_type, label=label,
                                         family=family, plan=plan)


def _fetch(cl, account, source_type, label, **kw):
    command = _command()
    command._instagram_api_call = lambda _account, method, *a, **k: method(*a, **k)
    command._request_budget_available = lambda _account: True
    command._note_account_error = Mock()
    return command._fetch_source_medias(cl=cl, account=account, source_type=source_type, source_label=label, **kw)


def _business(name="کلینیک", domain="دندانپزشکی"):
    user = get_user_model().objects.create_user(username=f"u-{name}", password="x")
    return Business.objects.create(user=user, name=name, business_type="PHYSICAL", business_domain=domain)


class FamilyTargetOrderTests(TestCase):
    def test_tenants_take_turns_and_each_family_is_one_target(self):
        a, b = _business("الف"), _business("ب")
        fam = {a.pk: [SimpleNamespace(product_id="a1", category_path="x", title="x"),
                      SimpleNamespace(product_id="a2", category_path="y", title="y")],
               b.pk: [SimpleNamespace(product_id="b1", category_path="z", title="z")]}
        plan = SimpleNamespace(hashtags=[], keywords=[])
        with patch("apps.discovery.instagram_families.families_for_business", side_effect=lambda biz: fam[biz.pk]), \
                patch("apps.discovery.instagram_families.ensure_family_plan", return_value=(plan, False)), \
                patch("apps.discovery.instagram_families.discover_sources",
                      side_effect=lambda biz, family, p: [DiscoverySource(family.product_id, "keyword", "k")]), \
                patch("apps.discovery.instagram_families.sample_sources",
                      side_effect=lambda biz, sources, limit: sources[:limit]) as sample:
            targets = _targets()
        self.assertEqual([(t["business"].pk, t["family"].product_id) for t in targets],
                         [(a.pk, "a1"), (b.pk, "b1"), (a.pk, "a2")])
        self.assertEqual(targets[0]["tenant_id"], f"tenant_{a.pk}")
        self.assertEqual([s.label for s in targets[0]["sources"]], ["k"])
        self.assertEqual(sample.call_args_list[0].args[2], 4)

    def test_family_without_plan_is_skipped(self):
        a = _business("الف")
        fam = [SimpleNamespace(product_id="a1", category_path="x", title="x")]
        with patch("apps.discovery.instagram_families.families_for_business", return_value=fam), \
                patch("apps.discovery.instagram_families.ensure_family_plan", return_value=(None, False)), \
                patch("apps.discovery.instagram_families.sample_sources", return_value=[]):
            self.assertEqual(_targets(), [])
        self.assertTrue(a.pk)


class RetireSourceTests(TestCase):
    def setUp(self):
        self.business = _business()
        self.plan = InstagramFamilyPlan.objects.create(
            business=self.business, family_id="f1", content_hash="h",
            hashtags=["ایمپلنت", "لمینت"], keywords=["ایمپلنت دندان", "عصب کشی"],
            page_search_terms=["کلینیک دندان", "دندانپزشکی"])
        self.product = Product.objects.create(business=self.business, name="ایمپلنت", price=1,
                                              instagram_hashtags=["ایمپلنت", "ارتودنسی"],
                                              instagram_terms=["ایمپلنت دندان"])
        patcher = patch("apps.discovery.instagram_families.expand_queries_from_top_sources", return_value=[])
        self.expand = patcher.start()
        self.addCleanup(patcher.stop)

    def test_hashtag_is_removed_from_plan_product_and_stats(self):
        InstagramSourceStat.objects.create(business=self.business, family_id="f1", source_type="hashtag",
                                           label="ایمپلنت")
        _retire(self.business, "hashtag", "#ایمپلنت", plan=self.plan)
        self.plan.refresh_from_db()
        self.product.refresh_from_db()
        self.assertEqual(self.plan.hashtags, ["لمینت"])
        self.assertEqual(self.product.instagram_hashtags, ["ارتودنسی"])
        self.assertFalse(InstagramSourceStat.objects.exists())
        self.expand.assert_called_once()

    def test_keyword_and_page_search_term_are_removed(self):
        _retire(self.business, "keyword", "ایمپلنت دندان")
        self.plan.refresh_from_db()
        _retire(self.business, "page", "search:کلینیک دندان", plan=self.plan)
        self.plan.refresh_from_db()
        self.product.refresh_from_db()
        self.assertEqual(self.plan.keywords, ["عصب کشی"])
        self.assertEqual(self.plan.page_search_terms, ["دندانپزشکی"])
        self.assertEqual(self.product.instagram_terms, [])

    def test_watched_and_own_pages_stay_active(self):
        watched = InstagramPage.objects.create(business=self.business, handle="my_watch", kind="watched",
                                               discovered_family_id="f1")
        own = InstagramPage.objects.create(business=self.business, handle="my_own", kind="own")
        _retire(self.business, "page", "@my_watch")
        _retire(self.business, "page", "@my_own")
        watched.refresh_from_db()
        own.refresh_from_db()
        self.assertTrue(watched.is_active and own.is_active)


class FetchSourceMediasTests(TestCase):
    def _media(self, mid, days_old, comments=20):
        return SimpleNamespace(id=mid, pk=mid, taken_at=timezone.now() - timedelta(days=days_old),
                               comment_count=comments)

    def test_hashtag_medias_are_deduplicated_and_newest_first(self):
        old, new, mid = self._media("1", 9), self._media("2", 1), self._media("3", 4)
        cl = SimpleNamespace(hashtag_medias_recent=Mock(return_value=[old, new]),
                             hashtag_medias_top=Mock(return_value=[mid, new]))
        out = _fetch(cl, object(), "hashtag", "#ایمپلنت", max_total=30)
        self.assertEqual([m.id for m in out], ["2", "3", "1"])
        self.assertEqual(cl.hashtag_medias_recent.call_args.args[0], "ایمپلنت")

    def test_fetching_stops_once_enough_qualifying_posts_are_found(self):
        batch = [self._media(str(i), 1) for i in range(15)]
        cl = SimpleNamespace(media_search=Mock(return_value=batch))
        out = _fetch(cl, object(), "keyword", "ایمپلنت دندان", max_total=10)
        cl.media_search.assert_called_once()
        self.assertEqual(len(out), 10)

    def test_page_source_marks_the_page_ok(self):
        business = _business()
        page = InstagramPage.objects.create(business=business, handle="clinic", consecutive_failures=2)
        cl = SimpleNamespace(user_id_from_username=Mock(return_value="99"),
                             user_medias=Mock(return_value=[self._media("1", 1)]))
        out = _fetch(cl, object(), "page", "@clinic", business=business)
        page.refresh_from_db()
        self.assertEqual(len(out), 1)
        cl.user_id_from_username.assert_called_once_with("clinic")
        self.assertEqual((page.last_status, page.consecutive_failures), ("ok", 0))
