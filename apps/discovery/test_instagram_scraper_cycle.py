"""Scraper behavior that the ig_scraper modules must keep: family target order, source retirement, media fetching."""
from datetime import timedelta
from io import StringIO
from types import SimpleNamespace
from unittest.mock import Mock, patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone

from apps.businesses.models import Business
from apps.discovery.ig_scraper.context import Run
from apps.discovery.ig_scraper.sources import fetch_source_medias, retire_and_delete_source
from apps.discovery.instagram_families import DiscoverySource
from apps.discovery.models import InstagramFamilyPlan, InstagramPage, InstagramSourceStat
from apps.products.models import Product


def _targets() -> list[dict]:
    """Targets of one cycle, in the order they are scraped."""
    from apps.discovery.ig_scraper.cycle import build_targets
    return build_targets(Run(stdout=StringIO(), stderr=StringIO()))


def _retire(business, source_type, label, family=None, plan=None):
    retire_and_delete_source(Run(stdout=StringIO(), stderr=StringIO()), business=business, source_type=source_type,
                             label=label, family=family, plan=plan)


def _fetch(cl, account, source_type, label, **kw):
    with patch("apps.discovery.ig_scraper.accounts.instagram_api_call",
               side_effect=lambda _run, _account, method, *a, **k: method(*a, **k)), \
            patch("apps.discovery.ig_scraper.accounts.request_budget_available", return_value=True), \
            patch("apps.discovery.ig_scraper.accounts.note_account_error"):
        return fetch_source_medias(Run(stdout=StringIO(), stderr=StringIO()), cl=cl, account=account,
                                   source_type=source_type, source_label=label, **kw)


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

    def test_discovered_pages_are_deactivated_but_seller_pages_stay(self):
        discovered = InstagramPage.objects.create(business=self.business, handle="found_page", kind="watched",
                                                  discovered_family_id="f1", last_status="candidate")
        hand_added = InstagramPage.objects.create(business=self.business, handle="my_watch", kind="watched")
        own = InstagramPage.objects.create(business=self.business, handle="my_own", kind="own",
                                           discovered_family_id="f1")
        for handle in ("found_page", "my_watch", "my_own"):
            _retire(self.business, "page", f"@{handle}")
        for page in (discovered, hand_added, own):
            page.refresh_from_db()
        self.assertEqual((discovered.is_active, discovered.last_status), (False, "unqualified_retired"))
        self.assertTrue(hand_added.is_active and own.is_active)


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
