import io
from PIL import Image
from django.core.files.uploadedfile import SimpleUploadedFile
from django.contrib.auth import get_user_model
from django.test import Client, TestCase
from django.urls import reverse
from apps.businesses.models import Business
from .models import Category, Product, ProductImage
from .services import suggest_category_for_product

User = get_user_model()

class ProductsAndTaxonomyTestCase(TestCase):
    def setUp(self):
        self.client = Client()
        self.user = User.objects.create_user(
            username="seller_test@example.com",
            email="seller_test@example.com",
            first_name="آرش",
            last_name="کاظمی",
            password="testpassword123"
        )
        self.business = Business.objects.create(
            user=self.user,
            name="آکادمی تست",
            business_type="HYBRID",
            business_domain="آموزش و مد",
            onboarding_done=True
        )
        self.client.force_login(self.user)

        # Build Category Hierarchy
        self.root_clothing = Category.objects.create(
            name="پوشاک",
            slug="clothing-test",
            product_type="PHYSICAL"
        )
        self.mens_clothing = Category.objects.create(
            name="مردانه",
            slug="mens-test",
            parent=self.root_clothing,
            product_type="PHYSICAL"
        )
        self.jeans = Category.objects.create(
            name="شلوار جین",
            slug="jeans-test",
            parent=self.mens_clothing,
            product_type="PHYSICAL",
            suggested_attributes=[{"key": "material", "label": "جنس"}, {"key": "size", "label": "سایز"}]
        )

        self.root_edu = Category.objects.create(
            name="آموزش",
            slug="edu-test",
            product_type="SERVICE"
        )
        self.python_cat = Category.objects.create(
            name="دوره پایتون",
            slug="python-test",
            parent=self.root_edu,
            product_type="SERVICE",
            suggested_attributes=[{"key": "duration", "label": "مدت زمان"}]
        )

    def test_category_hierarchy_path(self):
        self.assertEqual(self.jeans.get_full_path(), "پوشاک > مردانه > شلوار جین")
        self.assertEqual(len(self.jeans.get_ancestors()), 2)
        self.assertEqual(self.root_clothing.children.first(), self.mens_clothing)

    def test_product_creation_and_attributes(self):
        product = Product.objects.create(
            business=self.business,
            name="شلوار جین لی مدل راسته",
            product_type="PHYSICAL",
            category=self.jeans,
            description="شلوار جین باکیفیت",
            price=750000,
            attributes={"material": "کتان کشی", "size": "32"}
        )
        self.assertEqual(product.formatted_price(), "۷۵۰،۰۰۰ تومان")
        self.assertEqual(product.attributes.get("material"), "کتان کشی")
        self.assertEqual(product.category.name, "شلوار جین")

    def test_product_list_view(self):
        Product.objects.create(
            business=self.business,
            name="دوره پایتون پیشرفته",
            product_type="SERVICE",
            category=self.python_cat,
            description="آموزش عمیق پایتون",
            price=2500000
        )
        response = self.client.get(reverse("products:list"))
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "products/product_list.html")
        self.assertIn("دوره پایتون پیشرفته", response.content.decode("utf-8"))

    def test_product_add_view_post(self):
        data = {
            "name": "تیشرت نخی مردانه",
            "product_type": "PHYSICAL",
            "category_id": self.mens_clothing.id,
            "description": "تیشرت ۱۰۰ درصد پنبه",
            "price": "350000",
            "target_customer": "جوانان و نوجوانان",
            "status": "ACTIVE",
            "attr_material": "پنبه خالص",
            "attr_color": "مشکی",
        }
        response = self.client.post(reverse("products:add"), data, follow=True)
        self.assertEqual(response.status_code, 200)
        new_prod = Product.objects.filter(name="تیشرت نخی مردانه").first()
        self.assertIsNotNone(new_prod)
        self.assertEqual(new_prod.attributes.get("material"), "پنبه خالص")
        self.assertEqual(new_prod.price, 350000)

    def test_product_add_with_dynamic_custom_attributes(self):
        data = {
            "name": "شلوار کتان کارگو مردانه",
            "product_type": "PHYSICAL",
            "category_id": self.jeans.id,
            "description": "شلوار کارگو شیک و با دوام با پارچه کتان اعلا",
            "price": "620000",
            "custom_attr_key": ["سایز", "رنگ", "جنس", "گارانتی"],
            "custom_attr_value": ["XL, 38", "مشکی ذغالی", "کتان پنبه", "۱۸ ماه شرکتی"],
            "status": "ACTIVE",
        }
        response = self.client.post(reverse("products:add"), data, follow=True)
        self.assertEqual(response.status_code, 200)
        new_prod = Product.objects.filter(name="شلوار کتان کارگو مردانه").first()
        self.assertIsNotNone(new_prod)
        self.assertEqual(new_prod.attributes.get("سایز"), "XL, 38")
        self.assertEqual(new_prod.attributes.get("رنگ"), "مشکی ذغالی")
        self.assertEqual(new_prod.attributes.get("جنس"), "کتان پنبه")
        self.assertEqual(new_prod.attributes.get("گارانتی"), "۱۸ ماه شرکتی")

    def test_product_add_saves_instagram_discovery_settings(self):
        response = self.client.post(reverse("products:add"), {
            "name": "کفش چرمی", "product_type": "PHYSICAL",
            "category_id": self.jeans.pk, "description": "کفش چرمی مردانه",
            "instagram_discovery_enabled": "on",
            "instagram_terms": "کفش چرمی\nسایز مردانه",
            "instagram_hashtags": "#کفش_چرمی، #کفش",
            "instagram_max_posts": "16", "instagram_window_days": "28",
        })

        self.assertEqual(response.status_code, 302)
        product = Product.objects.get(name="کفش چرمی", business=self.business)
        self.assertTrue(product.instagram_discovery_enabled)
        self.assertEqual(product.instagram_terms, ["کفش چرمی", "سایز مردانه"])
        self.assertEqual(product.instagram_hashtags, ["کفش_چرمی", "کفش"])
        self.assertEqual((product.instagram_max_posts, product.instagram_window_days), (16, 28))

    def test_product_edit_saves_instagram_discovery_settings(self):
        product = Product.objects.create(
            business=self.business, name="کفش چرمی", category=self.jeans,
            description="کفش چرمی مردانه",
        )

        response = self.client.post(reverse("products:edit", kwargs={"pk": product.pk}), {
            "name": "کفش چرمی", "product_type": "PHYSICAL", "category_id": self.jeans.pk,
            "description": "کفش چرمی مردانه", "instagram_discovery_enabled": "on",
            "instagram_terms": "چرم\nسایز", "instagram_hashtags": "#کفش_چرمی",
            "instagram_max_posts": "20", "instagram_window_days": "30",
        })

        self.assertEqual(response.status_code, 302)
        product.refresh_from_db()
        self.assertTrue(product.instagram_discovery_enabled)
        self.assertEqual(product.instagram_terms, ["چرم", "سایز"])
        self.assertEqual(product.instagram_hashtags, ["کفش_چرمی"])
        self.assertEqual((product.instagram_max_posts, product.instagram_window_days), (20, 30))

    def test_ai_category_suggestion_service(self):
        suggestion = suggest_category_for_product(
            name="شلوار جین مردانه زاپ دار",
            description="شلوار زیبا با دوخت عالی"
        )
        self.assertIsNotNone(suggestion)
        self.assertEqual(suggestion["category_id"], self.jeans.id)
        self.assertGreaterEqual(suggestion["confidence"], 90)

    def test_api_suggest_category_endpoint(self):
        url = reverse("products:api_suggest_category") + "?name=دوره%20پایتون%20پروژه%20محور"
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["status"], "success")
        self.assertIn("پایتون", data["suggestion"]["full_path"])

    def test_product_detail_view_rendering(self):
        product = Product.objects.create(
            business=self.business,
            name="دوره آموزشی تست",
            product_type="SERVICE",
            category=self.python_cat,
            description="شرح کامل دوره",
            price=1200000
        )
        response = self.client.get(reverse("products:detail", kwargs={"pk": product.pk}))
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "products/product_detail.html")
        self.assertTemplateUsed(response, "panel/base_panel.html")
        self.assertIn("دوره آموزشی تست", response.content.decode("utf-8"))

    def test_product_edit_view_rendering(self):
        product = Product.objects.create(
            business=self.business,
            name="کالای قابل ویرایش",
            product_type="PHYSICAL",
            category=self.jeans,
            description="توضیحات",
            price=500000
        )
        response = self.client.get(reverse("products:edit", kwargs={"pk": product.pk}))
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "products/product_form.html")
        self.assertTemplateUsed(response, "panel/base_panel.html")
        self.assertIn("کالای قابل ویرایش", response.content.decode("utf-8"))

    def _create_test_image(self, name="test.jpg", color="blue"):
        file = io.BytesIO()
        img = Image.new("RGB", (100, 100), color=color)
        img.save(file, "jpeg")
        file.seek(0)
        return SimpleUploadedFile(name, file.read(), content_type="image/jpeg")

    def test_product_image_flow_add_set_main_and_delete(self):
        product = Product.objects.create(
            business=self.business,
            name="کالای تستی عکس دار",
            product_type="PHYSICAL",
            category=self.jeans,
            description="دارای تصاویر متعدد",
            price=300000
        )
        img1 = self._create_test_image("img1.jpg", "red")
        img2 = self._create_test_image("img2.jpg", "green")

        # Edit post with 2 images
        response = self.client.post(reverse("products:edit", kwargs={"pk": product.pk}), {
            "name": product.name,
            "product_type": product.product_type,
            "description": product.description,
            "category_id": self.jeans.id,
            "status": "ACTIVE",
            "images": [img1, img2],
        }, follow=True)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(product.images.count(), 2)

        first_img = product.images.first()
        second_img = product.images.last()
        self.assertTrue(first_img.is_main)
        self.assertFalse(second_img.is_main)
        self.assertEqual(product.main_image, first_img)

        # Set second image as main
        response = self.client.post(
            reverse("products:set_main_image", kwargs={"pk": product.pk, "image_pk": second_img.pk}),
            follow=True
        )
        self.assertEqual(response.status_code, 200)
        first_img.refresh_from_db()
        second_img.refresh_from_db()
        self.assertFalse(first_img.is_main)
        self.assertTrue(second_img.is_main)
        self.assertEqual(product.main_image, second_img)

        # Delete main image -> first image is promoted to main
        response = self.client.post(
            reverse("products:delete_image", kwargs={"pk": product.pk, "image_pk": second_img.pk}),
            follow=True
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(product.images.count(), 1)
        first_img.refresh_from_db()
        self.assertTrue(first_img.is_main)

    def test_max_10_images_limit_enforced(self):
        product = Product.objects.create(
            business=self.business,
            name="کالای ۱۰ عکسه",
            product_type="PHYSICAL",
            description="تست سقف ۱۰ عکس",
            price=100000
        )
        # Create 10 images directly
        for i in range(10):
            ProductImage.objects.create(
                product=product,
                image=self._create_test_image(f"img_{i}.jpg"),
                is_main=(i == 0),
                order=i
            )
        self.assertEqual(product.images.count(), 10)
        self.assertFalse(product.can_add_images)
        self.assertEqual(product.remaining_image_slots, 0)

        # Attempt to upload an 11th image
        extra_img = self._create_test_image("extra.jpg")
        response = self.client.post(reverse("products:edit", kwargs={"pk": product.pk}), {
            "name": product.name,
            "product_type": product.product_type,
            "description": product.description,
            "status": "ACTIVE",
            "images": [extra_img],
        })
        self.assertEqual(response.status_code, 200)
        self.assertIn("حداکثر ۱۰ تصویر", response.content.decode("utf-8"))
        self.assertEqual(product.images.count(), 10)



class XSearchToggleTests(TestCase):
    def test_seller_toggles_x_search_per_product_only_for_own_products(self):
        from django.urls import reverse

        from apps.discovery.tests import seller

        user, biz = seller("x1@x.com", "فروشنده یک")
        other_user, other = seller("x2@x.com", "فروشنده دو")
        product = Product.objects.create(business=biz, name="اکانت نوشن", description="x", price=100)
        self.assertFalse(product.x_search_enabled)                       # off by default: the seller pays for it
        self.client.force_login(user)
        self.assertIn("جستجوی مشتری در X: خاموش", self.client.get(reverse("products:list")).content.decode())
        self.client.post(reverse("products:toggle_x_search", args=[product.pk]), {"next": "https://evil.example/"})
        product.refresh_from_db()
        self.assertTrue(product.x_search_enabled)
        self.assertIn("جستجوی مشتری در X: روشن", self.client.get(reverse("products:list")).content.decode())
        self.client.force_login(other_user)
        self.assertEqual(self.client.post(reverse("products:toggle_x_search", args=[product.pk])).status_code, 404)
        product.refresh_from_db()
        self.assertTrue(product.x_search_enabled)


from unittest.mock import MagicMock
from apps.businesses.models import BusinessType
from apps.products import taxonomy


class TaxonomyCoreTestCase(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="seller_tax@test.ir",
            email="seller_tax@test.ir",
            password="testpass123",
            first_name="فروشنده"
        )
        self.business = Business.objects.create(
            user=self.user,
            name="پوشاک نمونه",
            slug="pooshak-test",
            business_type="PHYSICAL",
            onboarding_done=True
        )
        self.btype = BusinessType.objects.create(
            key="apparel_tax",
            name="پوشاک",
            kind="PHYSICAL",
            description="انواع البسه"
        )

    def test_ensure_type_tree_built_once_and_cached(self):
        fake_llm = MagicMock(return_value={
            "children": [
                {
                    "name": "مردانه",
                    "children": [
                        {"name": "پیراهن", "children": []},
                        {"name": "شلوار", "children": []}
                    ]
                },
                {
                    "name": "زنانه",
                    "children": [
                        {"name": "مانتو", "children": []}
                    ]
                }
            ]
        })

        # 1st call builds the tree
        res1 = taxonomy.ensure_type_tree(self.btype, llm=fake_llm)
        self.assertEqual(res1.tree_status, BusinessType.TREE_READY)
        self.assertEqual(fake_llm.call_count, 1)

        root = res1.root_category
        self.assertIsNotNone(root)
        self.assertEqual(root.name, "پوشاک")
        self.assertEqual(root.children.count(), 2)

        # 2nd call should use cache without calling LLM
        fail_llm = MagicMock(side_effect=Exception("LLM should not be called"))
        res2 = taxonomy.ensure_type_tree(self.btype, llm=fail_llm)
        self.assertEqual(res2.tree_status, BusinessType.TREE_READY)
        self.assertEqual(fail_llm.call_count, 0)

    def test_adopt_nodes_is_idempotent_and_retains_ancestors(self):
        root = Category.objects.create(name="پوشاک", product_type="PHYSICAL", source="system")
        dept = Category.objects.create(name="مردانه", parent=root, product_type="PHYSICAL", source="ai")
        leaf = Category.objects.create(name="پیراهن", parent=dept, product_type="PHYSICAL", source="ai")

        # Adopt only the leaf node
        count1 = taxonomy.adopt_nodes(self.business, [leaf.pk])
        # Root + dept + leaf should all be copied for the business
        self.assertEqual(count1, 3)

        own_leaf = Category.objects.get(business=self.business, origin=leaf)
        self.assertEqual(own_leaf.name, "پیراهن")
        self.assertIsNotNone(own_leaf.parent)
        self.assertEqual(own_leaf.parent.name, "مردانه")
        self.assertEqual(own_leaf.parent.origin, dept)
        self.assertIsNotNone(own_leaf.parent.parent)
        self.assertEqual(own_leaf.parent.parent.name, "پوشاک")
        self.assertEqual(own_leaf.parent.parent.origin, root)

        # Idempotency: second adopt call with same or ancestor node returns 0 new copies
        count2 = taxonomy.adopt_nodes(self.business, [leaf.pk, dept.pk])
        self.assertEqual(count2, 0)
        self.assertEqual(Category.objects.filter(business=self.business).count(), 3)

    def test_depth_limits_and_rules(self):
        # Build 3 levels for seller
        l1 = Category.objects.create(business=self.business, name="پوشاک", product_type="PHYSICAL")
        l2 = taxonomy.add_node(self.business, l1.pk, "مردانه")
        l3 = taxonomy.add_node(self.business, l2.pk, "پیراهن")
        self.assertEqual(l3.depth, 3)

        # Level 4 allowed for seller (user source)
        l4 = taxonomy.add_node(self.business, l3.pk, "پیراهن نخی", source="user")
        self.assertEqual(l4.depth, 4)

        # Level 5 not allowed (exceeds MAX_DEPTH 4)
        with self.assertRaises(taxonomy.TaxonomyError):
            taxonomy.add_node(self.business, l4.pk, "سایز لارج", source="user")

        # Source 'ai' cannot exceed AI_MAX_DEPTH (3)
        with self.assertRaises(taxonomy.TaxonomyError):
            taxonomy.add_node(self.business, l3.pk, "مدل آکسفورد", source="ai")

    def test_rename_node_syncs_descendant_paths(self):
        root = Category.objects.create(business=self.business, name="پوشاک", product_type="PHYSICAL")
        sub = Category.objects.create(business=self.business, name="مردانه", parent=root, product_type="PHYSICAL")
        leaf = Category.objects.create(business=self.business, name="کت و شلوار", parent=sub, product_type="PHYSICAL")

        self.assertEqual(leaf.full_path, "پوشاک > مردانه > کت و شلوار")

        # Rename root
        taxonomy.rename_node(self.business, root.pk, "لباس و پوشاک")
        root.refresh_from_db()
        sub.refresh_from_db()
        leaf.refresh_from_db()

        self.assertEqual(root.name, "لباس و پوشاک")
        self.assertEqual(sub.full_path, "لباس و پوشاک > مردانه")
        self.assertEqual(leaf.full_path, "لباس و پوشاک > مردانه > کت و شلوار")

        # Duplicate sibling name is rejected
        sub2 = Category.objects.create(business=self.business, name="زنانه", parent=root, product_type="PHYSICAL")
        with self.assertRaises(taxonomy.TaxonomyError):
            taxonomy.rename_node(self.business, sub2.pk, "مردانه")

    def test_delete_node_moves_products_to_parent(self):
        root = Category.objects.create(business=self.business, name="خدمات آموزش", product_type="SERVICE")
        child = Category.objects.create(business=self.business, name="پایتون", parent=root, product_type="SERVICE")
        p = Product.objects.create(
            business=self.business,
            name="دوره جنگو پیشرفته",
            category=child,
            product_type="SERVICE",
            description="توضیحات دوره جنگو"
        )

        deleted_count = taxonomy.delete_node(self.business, child.pk)
        self.assertEqual(deleted_count, 1)
        self.assertFalse(Category.objects.filter(pk=child.pk).exists())

        p.refresh_from_db()
        # Product moved to parent
        self.assertEqual(p.category, root)

        # Deleting root sets product category to None
        taxonomy.delete_node(self.business, root.pk)
        p.refresh_from_db()
        self.assertIsNone(p.category)

    def test_collapse_depth(self):
        l1 = Category.objects.create(business=self.business, name="دسته‌بندی ۱", product_type="PHYSICAL")
        l2 = Category.objects.create(business=self.business, name="دسته‌بندی ۲", parent=l1, product_type="PHYSICAL")
        l3 = Category.objects.create(business=self.business, name="دسته‌بندی ۳", parent=l2, product_type="PHYSICAL")
        l4 = Category.objects.create(business=self.business, name="دسته‌بندی ۴", parent=l3, product_type="PHYSICAL")

        self.assertEqual(taxonomy.collapse(l1), l1)
        self.assertEqual(taxonomy.collapse(l2), l2)
        self.assertEqual(taxonomy.collapse(l3), l3)
        # Depth 4 collapses to Depth 3
        self.assertEqual(taxonomy.collapse(l4), l3)
        self.assertIsNone(taxonomy.collapse(None))

    def test_maybe_organize_and_organize_products(self):
        cat1 = Category.objects.create(business=self.business, name="مردانه", product_type="PHYSICAL")

        # 4 products: maybe_organize should return False
        for i in range(4):
            Product.objects.create(
                business=self.business,
                name=f"کالای {i}",
                description="توضیح کالا",
                status="ACTIVE"
            )
        self.assertFalse(taxonomy.maybe_organize(self.business, background=False))

        # 5th product triggers organization
        p5 = Product.objects.create(
            business=self.business,
            name="پیراهن مردانه سفید",
            description="پیراهن کتان",
            status="ACTIVE"
        )
        fake_llm = MagicMock(return_value={
            "assignments": [
                {"product_id": p5.id, "category_id": cat1.id, "new_category": None}
            ]
        })
        stats = taxonomy.organize_products(self.business, llm=fake_llm)
        self.assertEqual(stats["checked"], 5)
        self.assertEqual(stats["placed"], 1)

        p5.refresh_from_db()
        self.assertEqual(p5.category, cat1)


class OnboardingAndStorefrontFlowTestCase(TestCase):
    def setUp(self):
        self.client = Client()
        self.user = User.objects.create_user(
            username="seller_flow@test.ir",
            email="seller_flow@test.ir",
            password="testpass123",
            first_name="سارا"
        )
        self.business = Business.objects.create(
            user=self.user,
            name="مزون سارا",
            business_type="PHYSICAL",
            onboarding_done=False
        )
        self.btype = BusinessType.objects.create(
            key="apparel_flow",
            name="پوشاک",
            kind="PHYSICAL"
        )
        self.client.force_login(self.user)

    def test_middleware_redirects_unonboarded_user(self):
        # Accessing guarded panel path redirects to onboarding
        res = self.client.get("/products/")
        self.assertEqual(res.status_code, 302)
        self.assertIn("/accounts/onboarding/", res.url)

        # Onboarding page is accessible (200)
        res_ob = self.client.get("/accounts/onboarding/")
        self.assertEqual(res_ob.status_code, 200)

        # Once onboarding_done is set, access is permitted
        self.business.onboarding_done = True
        self.business.save()
        res_after = self.client.get("/products/")
        self.assertEqual(res_after.status_code, 200)

    def test_onboarding_endpoints_flow(self):
        # 1. Select types
        res = self.client.post(
            reverse("accounts:ob_select_types"),
            data='{"keys": ["apparel_flow"]}',
            content_type="application/json"
        )
        self.assertEqual(res.status_code, 200)
        self.assertTrue(self.business.business_types.filter(key="apparel_flow").exists())

        # Cannot finish onboarding without categories
        res_fin_fail = self.client.post(
            reverse("accounts:ob_finish"),
            data='{"search_notes": "فروش عمده مانتو"}',
            content_type="application/json"
        )
        self.assertEqual(res_fin_fail.status_code, 400)
        self.assertIn("حداقل یک دسته‌بندی", res_fin_fail.json()["message"])

        # Add node to seller tree
        cat = Category.objects.create(business=self.business, name="مانتو مجلسی", product_type="PHYSICAL")

        # Finish onboarding with notes and category
        res_fin = self.client.post(
            reverse("accounts:ob_finish"),
            data='{"search_notes": "تولیدی مانتو هستم و مشتری عمده می‌خواهم.", "tagline": "بهترین مانتوهای پاییزه"}',
            content_type="application/json"
        )
        self.assertEqual(res_fin.status_code, 200)
        self.business.refresh_from_db()
        self.assertTrue(self.business.onboarding_done)
        self.assertTrue(bool(self.business.slug))
        self.assertEqual(self.business.tagline, "بهترین مانتوهای پاییزه")

    def test_shop_view_and_404_conditions(self):
        # Unknown slug -> 404
        res_404 = self.client.get("/shop/non-existent-shop-slug/")
        self.assertEqual(res_404.status_code, 404)

        # Business with onboarding_done=False -> 404
        self.business.slug = "mezon-sara"
        self.business.onboarding_done = False
        self.business.save()
        res_not_done = self.client.get(f"/shop/{self.business.slug}/")
        self.assertEqual(res_not_done.status_code, 404)

        # Once finished -> 200
        self.business.onboarding_done = True
        self.business.save()
        res_done = self.client.get(f"/shop/{self.business.slug}/")
        self.assertEqual(res_done.status_code, 200)
        self.assertContains(res_done, "مزون سارا")

    def test_shop_edit_slug_uniqueness_and_images(self):
        self.business.onboarding_done = True
        self.business.slug = "mezon-sara"
        self.business.save()

        other_user = User.objects.create_user(username="other@test.ir", password="p")
        Business.objects.create(user=other_user, name="فروشگاه دیگر", slug="other-shop", onboarding_done=True)

        # Try to take another store's slug
        res = self.client.post(reverse("accounts:shop_edit"), {
            "name": "مزون سارا جدید",
            "slug": "other-shop"
        })
        self.assertEqual(res.status_code, 200)
        self.business.refresh_from_db()
        self.assertEqual(self.business.slug, "mezon-sara")

        # Update with valid unique slug and image
        file_obj = io.BytesIO()
        img = Image.new("RGB", (20, 20), color=(100, 150, 200))
        img.save(file_obj, format="PNG")
        file_obj.seek(0)
        logo_img = SimpleUploadedFile("logo.png", file_obj.read(), content_type="image/png")

        res_ok = self.client.post(reverse("accounts:shop_edit"), {
            "name": "مزون سارا لوکس",
            "slug": "mezon-sara-lux",
            "tagline": "شیک و به‌روز",
            "logo": logo_img
        })
        self.assertEqual(res_ok.status_code, 302)
        self.business.refresh_from_db()
        self.assertEqual(self.business.slug, "mezon-sara-lux")
        self.assertEqual(self.business.name, "مزون سارا لوکس")
        self.assertTrue(bool(self.business.logo))

    def test_product_form_renders_own_categories_and_defaults_x_search(self):
        self.business.onboarding_done = True
        self.business.save()

        # Create own category and an unrelated business category
        own_cat = Category.objects.create(business=self.business, name="دسته‌بندی من", product_type="PHYSICAL")
        other_user = User.objects.create_user(username="other2@test.ir", password="p")
        other_biz = Business.objects.create(user=other_user, name="کسب‌وکار دیگر")
        Category.objects.create(business=other_biz, name="دسته‌بندی غیرمرتبط", product_type="PHYSICAL")

        # GET product add view
        res = self.client.get(reverse("products:add"))
        self.assertEqual(res.status_code, 200)
        self.assertContains(res, "دسته‌بندی من")
        self.assertNotContains(res, "دسته‌بندی غیرمرتبط")
        # x_search_enabled should be checked by default on new product form
        self.assertContains(res, 'name="x_search_enabled" value="on" checked')
        self.assertContains(res, reverse("accounts:categories"))

