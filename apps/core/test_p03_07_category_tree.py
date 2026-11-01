import logging
from django.test import TestCase
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from apps.businesses.models import Business
from apps.products.models import Category
from apps.products.taxonomy import move_node, TaxonomyError

User = get_user_model()


class CategoryTreeDefenseTestCase(TestCase):
    def setUp(self):
        self.user1 = User.objects.create_user(username="tree_owner_1", password="password123")
        self.business1 = Business.objects.create(user=self.user1, name="فروشگاه یک", slug="shop-1")

        self.user2 = User.objects.create_user(username="tree_owner_2", password="password123")
        self.business2 = Business.objects.create(user=self.user2, name="فروشگاه دو", slug="shop-2")

        # System root category
        self.system_root = Category.objects.create(name="پوشاک", product_type="PHYSICAL", business=None)

        # Tenant 1 categories
        self.cat_l1 = Category.objects.create(business=self.business1, name="مردانه", parent=self.system_root, product_type="PHYSICAL")
        self.cat_l2 = Category.objects.create(business=self.business1, name="پیراهن", parent=self.cat_l1, product_type="PHYSICAL")
        self.cat_l3 = Category.objects.create(business=self.business1, name="رسمی", parent=self.cat_l2, product_type="PHYSICAL")

    def test_self_parent_is_rejected(self):
        """A category cannot be its own parent."""
        self.cat_l1.parent = self.cat_l1
        with self.assertRaises(ValidationError):
            self.cat_l1.clean()

        with self.assertRaises(ValidationError):
            self.cat_l1.save()

        with self.assertRaises(TaxonomyError):
            move_node(self.business1, self.cat_l1.id, self.cat_l1.id)

    def test_two_node_cycle_is_rejected(self):
        """A -> B: Setting A's parent to B must be rejected."""
        self.cat_l1.parent = self.cat_l2
        with self.assertRaises(ValidationError):
            self.cat_l1.clean()

        with self.assertRaises(ValidationError):
            self.cat_l1.save()

        with self.assertRaises(TaxonomyError):
            move_node(self.business1, self.cat_l1.id, self.cat_l2.id)

    def test_multi_node_cycle_is_rejected(self):
        """A -> B -> C: Setting A's parent to C must be rejected."""
        self.cat_l1.parent = self.cat_l3
        with self.assertRaises(ValidationError):
            self.cat_l1.clean()

        with self.assertRaises(ValidationError):
            self.cat_l1.save()

        with self.assertRaises(TaxonomyError):
            move_node(self.business1, self.cat_l1.id, self.cat_l3.id)

    def test_cross_tenant_parent_is_rejected(self):
        """Tenant 1 category cannot have Tenant 2 category as parent."""
        cat_tenant2 = Category.objects.create(business=self.business2, name="لوازم جانبی", product_type="PHYSICAL")
        self.cat_l1.parent = cat_tenant2
        with self.assertRaises(ValidationError):
            self.cat_l1.clean()

        with self.assertRaises(ValidationError):
            self.cat_l1.save()

    def test_system_category_cannot_have_tenant_parent(self):
        """A global system category cannot have a tenant category as parent."""
        sys_cat = Category.objects.create(name="کالای عمومی", product_type="PHYSICAL", business=None)
        sys_cat.parent = self.cat_l1
        with self.assertRaises(ValidationError):
            sys_cat.clean()

        with self.assertRaises(ValidationError):
            sys_cat.save()

    def test_max_depth_exceeded_is_rejected(self):
        """Depth beyond 5 levels must be rejected."""
        cat_l4 = Category.objects.create(business=self.business1, name="آستین بلند", parent=self.cat_l3, product_type="PHYSICAL")
        self.assertEqual(cat_l4.depth, 5)

        # Level 6 should fail
        with self.assertRaises(ValidationError):
            Category.objects.create(business=self.business1, name="سفید", parent=cat_l4, product_type="PHYSICAL")

    def test_get_ancestors_infinite_loop_defense_with_logging(self):
        """If corrupt cycle data exists in DB, get_ancestors terminates safely and logs error."""
        # Force a cycle directly in DB using update() to bypass clean()
        Category.objects.filter(pk=self.cat_l1.pk).update(parent=self.cat_l2)
        self.cat_l1.refresh_from_db()
        self.cat_l2.refresh_from_db()

        # Both cat_l1 and cat_l2 now point to each other in DB
        with self.assertLogs("apps.products", level="ERROR") as cm:
            ancestors = self.cat_l1.get_ancestors()
            self.assertTrue(any("CATEGORY_CYCLE_DETECTED" in log for log in cm.output))

        # Must terminate without infinite loop and return a finite list
        self.assertIsInstance(ancestors, list)
        self.assertLessEqual(len(ancestors), 10)

    def test_move_node_updates_depth_and_full_path_for_descendants(self):
        """Moving a parent node updates depth and full_path of all descendants."""
        new_parent = Category.objects.create(business=self.business1, name="پوشاک اداری", product_type="PHYSICAL")
        self.assertEqual(new_parent.depth, 1)

        # Move cat_l1 under new_parent
        move_node(self.business1, self.cat_l1.id, new_parent.id)

        self.cat_l1.refresh_from_db()
        self.cat_l2.refresh_from_db()
        self.cat_l3.refresh_from_db()

        self.assertEqual(self.cat_l1.parent_id, new_parent.id)
        self.assertEqual(self.cat_l1.depth, 2)
        self.assertEqual(self.cat_l2.depth, 3)
        self.assertEqual(self.cat_l3.depth, 4)

        self.assertIn("پوشاک اداری > مردانه", self.cat_l1.full_path)
        self.assertIn("پوشاک اداری > مردانه > پیراهن", self.cat_l2.full_path)
        self.assertIn("پوشاک اداری > مردانه > پیراهن > رسمی", self.cat_l3.full_path)

    def test_move_node_to_root(self):
        """Moving a child node to root (new_parent_id=None) resets depth and updates children."""
        move_node(self.business1, self.cat_l2.id, None)

        self.cat_l2.refresh_from_db()
        self.cat_l3.refresh_from_db()

        self.assertIsNone(self.cat_l2.parent)
        self.assertEqual(self.cat_l2.depth, 1)
        self.assertEqual(self.cat_l2.full_path, "پیراهن")

        self.assertEqual(self.cat_l3.parent_id, self.cat_l2.id)
        self.assertEqual(self.cat_l3.depth, 2)
        self.assertEqual(self.cat_l3.full_path, "پیراهن > رسمی")
