import io
import threading
from django.test import TransactionTestCase, Client
from django.contrib.auth import get_user_model
from django.core.files.base import ContentFile
from django.core.files.storage import default_storage
from django.db import connection, transaction, IntegrityError
from django.urls import reverse
from PIL import Image

from apps.businesses.models import Business
from apps.products.models import Product, ProductImage

User = get_user_model()


def make_dummy_image(name="test.jpg", color=(200, 50, 50)) -> ContentFile:
    buf = io.BytesIO()
    img = Image.new("RGB", (30, 30), color=color)
    img.save(buf, format="JPEG")
    return ContentFile(buf.getvalue(), name=name)


class ProductImageInvariantsTestCase(TransactionTestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="img_user", password="Password123!")
        self.business = Business.objects.create(name="فروشگاه عکس", user=self.user, slug="photo-shop", onboarding_done=True)
        self.product = Product.objects.create(
            business=self.business,
            name="کالای تصویری",
            price=200000,
        )
        self.client = Client()
        self.client.force_login(self.user)

    def tearDown(self):
        # Purge any test files created on storage
        for pi in ProductImage.objects.all():
            if pi.image and default_storage.exists(pi.image.name):
                try:
                    default_storage.delete(pi.image.name)
                except Exception:
                    pass

    def test_unique_constraint_enforced_at_db_level(self):
        """Direct bulk insertion of two main images for same product must raise IntegrityError."""
        pi1 = ProductImage.objects.create(
            product=self.product,
            image=make_dummy_image("img1.jpg"),
            is_main=True,
            order=0,
        )
        self.assertTrue(pi1.is_main)

        # Attempting to force another row with is_main=True bypassing model save()
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                ProductImage.objects.bulk_create([
                    ProductImage(
                        product=self.product,
                        image=make_dummy_image("img2.jpg"),
                        is_main=True,
                        order=1,
                    )
                ])

    def test_concurrent_set_main_leaves_exactly_one_main(self):
        """Concurrent threads attempting to set different images as main must serialize safely."""
        images = []
        for i in range(5):
            img = ProductImage.objects.create(
                product=self.product,
                image=make_dummy_image(f"concurrent_{i}.jpg"),
                is_main=(i == 0),
                order=i,
            )
            images.append(img)

        self.assertEqual(ProductImage.objects.filter(product=self.product, is_main=True).count(), 1)

        errors = []

        def worker(img_to_promote):
            try:
                img_to_promote.is_main = True
                img_to_promote.save()
            except Exception as e:
                errors.append(e)
            finally:
                connection.close()

        threads = [threading.Thread(target=worker, args=(img,)) for img in images]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        self.assertEqual(len(errors), 0, f"Concurrent workers encountered errors: {errors}")
        mains_count = ProductImage.objects.filter(product=self.product, is_main=True).count()
        self.assertEqual(mains_count, 1, "Exactly one image must be is_main=True")

    def test_delete_main_promotes_next_image(self):
        """Deleting the primary image must automatically promote the first remaining image."""
        img1 = ProductImage.objects.create(
            product=self.product,
            image=make_dummy_image("del1.jpg"),
            is_main=True,
            order=0,
        )
        img2 = ProductImage.objects.create(
            product=self.product,
            image=make_dummy_image("del2.jpg"),
            is_main=False,
            order=1,
        )
        img3 = ProductImage.objects.create(
            product=self.product,
            image=make_dummy_image("del3.jpg"),
            is_main=False,
            order=2,
        )

        self.assertTrue(img1.is_main)
        self.assertFalse(img2.is_main)
        self.assertFalse(img3.is_main)

        # Delete img1 via view or delete()
        with transaction.atomic():
            img1.delete()

        img2.refresh_from_db()
        img3.refresh_from_db()

        self.assertTrue(img2.is_main, "Next image by order must be promoted to main")
        self.assertFalse(img3.is_main)
        self.assertEqual(ProductImage.objects.filter(product=self.product, is_main=True).count(), 1)

    def test_delete_last_remaining_image_leaves_zero_images(self):
        """Deleting the only remaining image leaves 0 images without crash."""
        img = ProductImage.objects.create(
            product=self.product,
            image=make_dummy_image("only.jpg"),
            is_main=True,
            order=0,
        )
        img.delete()
        self.assertEqual(ProductImage.objects.filter(product=self.product).count(), 0)

    def test_auto_promote_first_image_when_added_without_main(self):
        """When the first image is added with is_main=False, it is automatically promoted to main."""
        img = ProductImage.objects.create(
            product=self.product,
            image=make_dummy_image("first.jpg"),
            is_main=False,
            order=0,
        )
        img.refresh_from_db()
        self.assertTrue(img.is_main, "First image must automatically become main")

    def test_set_main_image_view_serializes_and_succeeds(self):
        """Calling set_main_image_view updates the main image atomically."""
        img1 = ProductImage.objects.create(
            product=self.product,
            image=make_dummy_image("v1.jpg"),
            is_main=True,
            order=0,
        )
        img2 = ProductImage.objects.create(
            product=self.product,
            image=make_dummy_image("v2.jpg"),
            is_main=False,
            order=1,
        )

        url = reverse("products:set_main_image", args=[self.product.id, img2.id])
        res = self.client.post(url)
        self.assertEqual(res.status_code, 302)

        img1.refresh_from_db()
        img2.refresh_from_db()
        self.assertFalse(img1.is_main)
        self.assertTrue(img2.is_main)
        self.assertEqual(ProductImage.objects.filter(product=self.product, is_main=True).count(), 1)

    def test_delete_image_view_with_promotion(self):
        """Calling delete_image_view deletes image and promotes next image."""
        img1 = ProductImage.objects.create(
            product=self.product,
            image=make_dummy_image("del_v1.jpg"),
            is_main=True,
            order=0,
        )
        img2 = ProductImage.objects.create(
            product=self.product,
            image=make_dummy_image("del_v2.jpg"),
            is_main=False,
            order=1,
        )

        url = reverse("products:delete_image", args=[self.product.id, img1.id])
        res = self.client.post(url)
        self.assertEqual(res.status_code, 302)

        self.assertFalse(ProductImage.objects.filter(id=img1.id).exists())
        img2.refresh_from_db()
        self.assertTrue(img2.is_main)
