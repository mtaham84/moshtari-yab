from django.test import TestCase, RequestFactory
from django.contrib.auth import get_user_model
from django.test.utils import CaptureQueriesContext
from django.db import connection

from apps.businesses.models import Business
from apps.products.models import Category, Product
from apps.products.taxonomy import get_subtree_ids, rename_node, delete_node, _refresh_paths
from apps.accounts.shop import shop_view

User = get_user_model()


class CategoryTreeNPlusOneOptimizationTests(TestCase):
    def setUp(self):
        self.user = User.objects.create(username="cat_tester", email="cat@test.local")
        self.business = Business.objects.create(name="Tree Business", user=self.user, slug="tree-shop", onboarding_done=True)

        self.other_user = User.objects.create(username="other_tester", email="other@test.local")
        self.other_biz = Business.objects.create(name="Other Business", user=self.other_user, slug="other-shop", onboarding_done=True)

        # Create multi-tier category hierarchy for self.business
        # Level 1
        self.root = Category.objects.create(business=self.business, name="پوشاک", product_type="PHYSICAL")
        # Level 2
        self.child1 = Category.objects.create(business=self.business, name="مردانه", parent=self.root, product_type="PHYSICAL")
        self.child2 = Category.objects.create(business=self.business, name="زنانه", parent=self.root, product_type="PHYSICAL")
        # Level 3
        self.grandchild1 = Category.objects.create(business=self.business, name="شلوار", parent=self.child1, product_type="PHYSICAL")
        self.grandchild2 = Category.objects.create(business=self.business, name="پیراهن", parent=self.child1, product_type="PHYSICAL")
        self.grandchild3 = Category.objects.create(business=self.business, name="مانتو", parent=self.child2, product_type="PHYSICAL")

        # Create category for other business
        self.other_root = Category.objects.create(business=self.other_biz, name="لوازم خانگی", product_type="PHYSICAL")

    def test_get_full_path_cached_zero_queries(self):
        # Fetch categories from DB
        cats = list(Category.objects.filter(business=self.business))
        self.assertGreaterEqual(len(cats), 6)

        # Calling get_full_path() on pre-loaded categories must execute ZERO extra queries
        with CaptureQueriesContext(connection) as ctx:
            paths = [c.get_full_path() for c in cats]

        self.assertEqual(len(ctx.captured_queries), 0, "get_full_path must use cached full_path column and execute 0 queries")
        self.assertIn("پوشاک > مردانه > شلوار", paths)

    def test_get_subtree_ids_constant_single_query(self):
        # Add 10 additional subcategories across allowed depths to prove query count is strictly invariant to tree size
        for i in range(10):
            Category.objects.create(
                business=self.business,
                name=f"زیردسته {i}",
                parent=self.grandchild1,
                product_type="PHYSICAL"
            )

        # Calling get_subtree_ids must run exactly 1 query to fetch the tenant tree regardless of depth/width
        with CaptureQueriesContext(connection) as ctx:
            subtree = get_subtree_ids(self.business, self.root.pk)

        self.assertEqual(len(ctx.captured_queries), 1, "get_subtree_ids must execute exactly 1 query regardless of tree size")
        self.assertGreater(len(subtree), 15)

        # Ensure other business categories are completely isolated
        self.assertNotIn(self.other_root.pk, subtree)

    def test_shop_view_category_filtering_bounded_queries(self):
        rf = RequestFactory()
        req = rf.get(f"/shop/{self.business.slug}/?c={self.root.pk}")
        req.user = self.user

        # Execute shop_view and check that query count does not expand with category tree nodes
        with CaptureQueriesContext(connection) as ctx:
            resp = shop_view(req, self.business.slug)

        self.assertEqual(resp.status_code, 200)
        # Should be bounded: business lookup, roots lookup, category filter lookup, single batch get_subtree_ids, products query, count query
        # Previously c.children.all() ran N queries in while loop
        self.assertLessEqual(len(ctx.captured_queries), 15, f"Shop view query count must be strictly bounded: got {len(ctx.captured_queries)}")

    def test_refresh_paths_updates_descendants_cleanly(self):
        # Rename root and ensure descendants update their full_path
        rename_node(self.business, self.root.pk, "البسه و پوشاک")

        self.grandchild1.refresh_from_db()
        self.assertEqual(self.grandchild1.get_full_path(), "البسه و پوشاک > مردانه > شلوار")
        self.assertEqual(self.grandchild1.depth, 3)

    def test_delete_node_executes_batch_cleanup(self):
        prod = Product.objects.create(
            business=self.business,
            name="شلوار لی مردانه",
            category=self.grandchild1,
            price=150000,
        )

        deleted_count = delete_node(self.business, self.child1.pk)
        self.assertEqual(deleted_count, 3) # child1, grandchild1, grandchild2

        prod.refresh_from_db()
        # Product moved to parent (root)
        self.assertEqual(prod.category_id, self.root.pk)
