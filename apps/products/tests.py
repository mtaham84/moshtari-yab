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
            business_domain="آموزش و مد"
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

