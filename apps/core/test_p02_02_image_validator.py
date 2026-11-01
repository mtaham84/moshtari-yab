import io
from PIL import Image
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client, TestCase
from django.urls import reverse
from apps.businesses.models import Business
from apps.core.image_service import (
    MAX_IMAGE_FILE_SIZE_BYTES,
    MAX_IMAGE_DIMENSION,
    validate_and_sanitize_image,
    is_valid_image,
)
from apps.products.models import Product, ProductImage

User = get_user_model()


def create_test_image_bytes(format="JPEG", size=(100, 100), color=(255, 0, 0), with_exif=False) -> bytes:
    """Helper to generate in-memory valid image bytes."""
    img = Image.new("RGB", size, color=color)
    output = io.BytesIO()
    if with_exif and format == "JPEG":
        exif = img.getexif()
        exif[0x010E] = "Sensitive User Description"
        img.save(output, format=format, exif=exif)
    else:
        img.save(output, format=format)
    return output.getvalue()


class ImageValidatorTestCase(TestCase):
    """
    P02.02 Test Suite:
    Validates common image validator and sanitization service:
    - Rejects HTML disguised as images, corrupted files, oversized dimensions, and excessive file sizes (R05).
    - Strips EXIF/metadata and assigns safe server-side UUID filenames.
    - Seamlessly integrates with product create and edit forms.
    """

    def setUp(self):
        self.client = Client()
        self.user = User.objects.create_user(username="seller@example.com", password="password123")
        self.business = Business.objects.create(user=self.user, name="فروشگاه تست تصویر", onboarding_done=True)

    def test_disguised_html_rejected(self):
        """
        File disguised as an image containing HTML/Script tags must be rejected with Persian error.
        """
        html_content = b"<!DOCTYPE html><html><head><script>alert('xss')</script></head><body>evil</body></html>"
        fake_file = SimpleUploadedFile("avatar.png", html_content, content_type="image/png")

        with self.assertRaises(ValidationError) as cm:
            validate_and_sanitize_image(fake_file)
        self.assertIn("اسکریپت یا HTML", str(cm.exception))
        self.assertFalse(is_valid_image(fake_file))

    def test_corrupt_image_rejected(self):
        """
        Corrupted file with invalid header/structure must be rejected.
        """
        corrupted_bytes = b"\xFF\xD8\xFF\xE0\x00\x10JFIF" + b"\x00" * 50  # Truncated broken JPEG
        fake_file = SimpleUploadedFile("broken.jpg", corrupted_bytes, content_type="image/jpeg")

        with self.assertRaises(ValidationError) as cm:
            validate_and_sanitize_image(fake_file)
        self.assertIn("معتبر نیست یا آسیب دیده", str(cm.exception))

    def test_oversize_file_size_rejected(self):
        """
        Image exceeding MAX_IMAGE_FILE_SIZE_BYTES (5MB) must be rejected.
        """
        oversize_bytes = b"\x00" * (MAX_IMAGE_FILE_SIZE_BYTES + 1024)
        fake_file = SimpleUploadedFile("huge.jpg", oversize_bytes, content_type="image/jpeg")

        with self.assertRaises(ValidationError) as cm:
            validate_and_sanitize_image(fake_file)
        self.assertIn("حجم فایل تصویر بیش از حد مجاز", str(cm.exception))

    def test_decompression_bomb_dimension_rejected(self):
        """
        Image with dimension exceeding 4096px must be rejected to prevent Decompression Bomb attacks.
        """
        huge_dim_bytes = create_test_image_bytes("JPEG", size=(MAX_IMAGE_DIMENSION + 100, 50))
        fake_file = SimpleUploadedFile("wide.jpg", huge_dim_bytes, content_type="image/jpeg")

        with self.assertRaises(ValidationError) as cm:
            validate_and_sanitize_image(fake_file)
        self.assertIn("ابعاد تصویر", str(cm.exception))

    def test_undersize_dimension_rejected(self):
        """
        Tiny image (< 10px) must be rejected.
        """
        tiny_bytes = create_test_image_bytes("PNG", size=(5, 5))
        fake_file = SimpleUploadedFile("tiny.png", tiny_bytes, content_type="image/png")

        with self.assertRaises(ValidationError) as cm:
            validate_and_sanitize_image(fake_file)
        self.assertIn("بسیار کوچک است", str(cm.exception))

    def test_valid_image_sanitized_and_exif_stripped(self):
        """
        Valid JPEG with sensitive EXIF metadata is re-encoded, stripped of EXIF, and given a UUID name.
        """
        img_bytes = create_test_image_bytes("JPEG", size=(200, 200), with_exif=True)
        orig_file = SimpleUploadedFile("original_name.jpg", img_bytes, content_type="image/jpeg")

        sanitized = validate_and_sanitize_image(orig_file)
        self.assertIsNotNone(sanitized)
        # Name is converted to random UUID with extension
        self.assertNotEqual(sanitized.name, "original_name.jpg")
        self.assertTrue(sanitized.name.endswith(".jpg"))

        # Verify EXIF is completely stripped in sanitized output
        reloaded = Image.open(io.BytesIO(sanitized.read()))
        exif = reloaded.getexif()
        self.assertNotIn(0x010E, exif)

    def test_create_product_accepts_valid_image_and_ignores_disguised_html(self):
        """
        Product create view accepts valid image and silently discards disguised HTML without crashing or saving unsafe files.
        """
        self.client.force_login(self.user)
        url = reverse("products:add")

        valid_img = SimpleUploadedFile("photo.jpg", create_test_image_bytes("JPEG"), content_type="image/jpeg")
        disguised_html = SimpleUploadedFile("malicious.png", b"<html><body>attack</body></html>", content_type="image/png")

        post_data = {
            "name": "محصول تست آپلود امن",
            "description": "توضیحات تست آپلود امن محصول",
            "price": "250000",
            "product_type": "PHYSICAL",
            "selected_main_index": "0",
            "images": [valid_img, disguised_html],
        }

        res = self.client.post(url, post_data)
        self.assertEqual(res.status_code, 302)

        product = Product.objects.filter(name="محصول تست آپلود امن").first()
        self.assertIsNotNone(product)

        # Only the valid sanitized image must be saved; disguised HTML must be rejected
        images = list(product.images.all())
        self.assertEqual(len(images), 1)
        self.assertTrue(images[0].image.name.endswith(".jpg"))

        # Verify disk content is valid image bytes, NOT HTML
        file_bytes = images[0].image.read()
        self.assertNotIn(b"<html>", file_bytes)

    def test_edit_product_ignores_corrupt_image(self):
        """
        Product edit view rejects corrupt image and keeps existing valid images intact.
        """
        product = Product.objects.create(
            business=self.business,
            name="کالای قابل ویرایش",
            price=100000,
        )
        self.client.force_login(self.user)
        url = reverse("products:edit", kwargs={"pk": product.pk})

        corrupted_file = SimpleUploadedFile("bad.jpg", b"corrupted random data not an image", content_type="image/jpeg")
        res = self.client.post(url, {
            "name": "کالای قابل ویرایش (بروزرسانی)",
            "price": "120000",
            "images": [corrupted_file],
        })
        self.assertEqual(res.status_code, 302)

        product.refresh_from_db()
        self.assertEqual(product.name, "کالای قابل ویرایش (بروزرسانی)")
        # No corrupted image was attached
        self.assertEqual(product.images.count(), 0)
