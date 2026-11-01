import io
import os
import shutil
import tempfile
import time
from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.files.base import ContentFile
from django.core.files.storage import default_storage
from django.core.management import call_command
from django.db import transaction
from django.test import TransactionTestCase, override_settings
from django.urls import reverse
from PIL import Image

from apps.businesses.models import Business
from apps.products.models import Product, ProductImage
from apps.core.image_service import (
    schedule_file_cleanup,
    secure_serve_media,
    get_all_referenced_media_paths,
    sweep_orphan_media,
)

User = get_user_model()


def create_test_image_bytes(fmt="JPEG", color=(100, 150, 200)) -> bytes:
    buf = io.BytesIO()
    img = Image.new("RGB", (50, 50), color=color)
    img.save(buf, format=fmt)
    return buf.getvalue()


class FileLifecycleAndServingTests(TransactionTestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="seller_media", password="Password123!")
        self.business = Business.objects.create(name="فروشگاه مدیا", user=self.user)
        self.product = Product.objects.create(
            business=self.business,
            name="کالای تست مدیا",
            description="شرح کالا",
            price=150000,
        )

    def test_product_image_delete_keeps_file_on_transaction_rollback(self):
        """
        If a ProductImage is deleted within an atomic block that rolls back,
        the underlying file on storage must NOT be deleted.
        """
        img_content = ContentFile(create_test_image_bytes("JPEG"), name="test_rollback.jpg")
        prod_img = ProductImage.objects.create(product=self.product, image=img_content)
        original_pk = prod_img.pk
        file_name = prod_img.image.name
        self.assertTrue(default_storage.exists(file_name))

        # Perform deletion inside a failing atomic transaction
        try:
            with transaction.atomic():
                prod_img.delete()
                # Row is deleted inside the atomic block
                self.assertFalse(ProductImage.objects.filter(pk=original_pk).exists())
                # Trigger rollback
                raise RuntimeError("Simulated DB failure")
        except RuntimeError:
            pass

        # Verify that rollback restored the row AND kept the file on storage
        self.assertTrue(ProductImage.objects.filter(pk=original_pk).exists())
        self.assertTrue(default_storage.exists(file_name))

        # Cleanup
        if default_storage.exists(file_name):
            default_storage.delete(file_name)

    def test_product_image_delete_removes_file_on_transaction_commit(self):
        """
        If a ProductImage is deleted within an atomic block that commits,
        the underlying file on storage is deleted after commit.
        """
        img_content = ContentFile(create_test_image_bytes("JPEG"), name="test_commit.jpg")
        prod_img = ProductImage.objects.create(product=self.product, image=img_content)
        file_name = prod_img.image.name
        self.assertTrue(default_storage.exists(file_name))

        with transaction.atomic():
            prod_img.delete()

        # After commit, file must be removed
        self.assertFalse(ProductImage.objects.filter(pk=prod_img.pk).exists())
        self.assertFalse(default_storage.exists(file_name))

    def test_secure_serve_media_security_headers_and_cookie_stripping(self):
        """
        secure_serve_media must return nosniff, sandbox CSP, CORP, no-referrer headers,
        and must not send Set-Cookie headers.
        """
        temp_dir = tempfile.mkdtemp()
        try:
            # Place a real image in temp_dir
            img_path = os.path.join(temp_dir, "test_pic.jpg")
            with open(img_path, "wb") as f:
                f.write(create_test_image_bytes("JPEG"))

            with override_settings(MEDIA_ROOT=temp_dir, SERVE_MEDIA=True):
                res = self.client.get("/media/test_pic.jpg")
                self.assertEqual(res.status_code, 200)
                self.assertEqual(res.headers.get("X-Content-Type-Options"), "nosniff")
                self.assertIn("sandbox", res.headers.get("Content-Security-Policy", ""))
                self.assertEqual(res.headers.get("Cross-Origin-Resource-Policy"), "same-site")
                self.assertEqual(res.headers.get("Referrer-Policy"), "no-referrer")
                self.assertNotIn("Set-Cookie", res.headers)
        finally:
            shutil.rmtree(temp_dir, ignore_errors=True)

    def test_secure_serve_media_forces_download_and_octet_stream_for_non_image(self):
        """
        If an HTML or script file exists in media, secure_serve_media must force
        attachment disposition and override content type to application/octet-stream.
        """
        temp_dir = tempfile.mkdtemp()
        try:
            html_path = os.path.join(temp_dir, "malicious.html")
            with open(html_path, "w", encoding="utf-8") as f:
                f.write("<html><body><script>alert(1)</script></body></html>")

            with override_settings(MEDIA_ROOT=temp_dir, SERVE_MEDIA=True):
                res = self.client.get("/media/malicious.html")
                self.assertEqual(res.status_code, 200)
                self.assertEqual(res.headers.get("Content-Type"), "application/octet-stream")
                self.assertIn("attachment", res.headers.get("Content-Disposition", ""))
                self.assertIn("filename=\"malicious.html\"", res.headers.get("Content-Disposition", ""))
        finally:
            shutil.rmtree(temp_dir, ignore_errors=True)

    def test_secure_serve_media_path_traversal_defense(self):
        """
        Path traversal attempts like ../file must be rejected with 404.
        """
        temp_dir = tempfile.mkdtemp()
        try:
            from django.test import RequestFactory
            from django.http import Http404
            rf = RequestFactory()
            req = rf.get("/media/../etc/passwd")
            with self.assertRaises(Http404):
                secure_serve_media(req, "../etc/passwd", document_root=temp_dir)
            with self.assertRaises(Http404):
                secure_serve_media(req, "sub/../../etc/passwd", document_root=temp_dir)
        finally:
            shutil.rmtree(temp_dir, ignore_errors=True)

    def test_sweep_orphan_media_dry_run_and_grace_period(self):
        """
        sweep_orphan_media correctly identifies active, in-grace, and orphan files
        without touching any files when dry_run=True.
        """
        temp_dir = tempfile.mkdtemp()
        try:
            # 1. Active referenced file
            prod_img = ProductImage.objects.create(
                product=self.product,
                image=ContentFile(create_test_image_bytes("JPEG"), name="active_ref.jpg"),
            )
            rel_active = os.path.normpath(str(prod_img.image.name))
            full_active = os.path.join(temp_dir, rel_active)
            os.makedirs(os.path.dirname(full_active), exist_ok=True)
            with open(full_active, "wb") as f:
                f.write(create_test_image_bytes("JPEG"))

            # 2. In-grace file (modified 1 hour ago < 24h grace)
            in_grace_path = os.path.join(temp_dir, "products", "new_upload.jpg")
            os.makedirs(os.path.dirname(in_grace_path), exist_ok=True)
            with open(in_grace_path, "wb") as f:
                f.write(create_test_image_bytes("JPEG"))
            now = time.time()
            os.utime(in_grace_path, (now - 3600, now - 3600))

            # 3. Orphan file (modified 48 hours ago > 24h grace)
            orphan_path = os.path.join(temp_dir, "products", "abandoned.jpg")
            with open(orphan_path, "wb") as f:
                f.write(create_test_image_bytes("JPEG"))
            os.utime(orphan_path, (now - 48 * 3600, now - 48 * 3600))

            stats = sweep_orphan_media(grace_hours=24, dry_run=True, media_root=temp_dir)
            self.assertEqual(stats["scanned"], 3)
            self.assertEqual(stats["referenced"], 1)
            self.assertEqual(stats["in_grace"], 1)
            self.assertEqual(stats["orphans"], 1)
            self.assertEqual(stats["quarantined"], 0)
            self.assertEqual(stats["deleted"], 0)

            # In dry-run, all 3 files still exist in original locations
            self.assertTrue(os.path.exists(full_active))
            self.assertTrue(os.path.exists(in_grace_path))
            self.assertTrue(os.path.exists(orphan_path))

            # Now run with quarantine=True, dry_run=False
            action_stats = sweep_orphan_media(grace_hours=24, dry_run=False, quarantine=True, media_root=temp_dir)
            self.assertEqual(action_stats["orphans"], 1)
            self.assertEqual(action_stats["quarantined"], 1)

            # Orphan file was moved to quarantine/
            self.assertFalse(os.path.exists(orphan_path))
            self.assertTrue(os.path.exists(full_active))
            self.assertTrue(os.path.exists(in_grace_path))
            quarantine_dir = os.path.join(temp_dir, "quarantine")
            self.assertTrue(os.path.isdir(quarantine_dir))
            self.assertEqual(len(os.listdir(quarantine_dir)), 1)
        finally:
            shutil.rmtree(temp_dir, ignore_errors=True)

    def test_cleanup_orphan_media_management_command(self):
        """
        Management command cleanup_orphan_media runs cleanly with --grace-hours.
        """
        out = io.StringIO()
        call_command("cleanup_orphan_media", "--grace-hours", "12", stdout=out)
        output = out.getvalue()
        self.assertIn("آغاز بررسی فایل‌های رهاشده در مدیا", output)
        self.assertIn("DRY-RUN", output)
        self.assertIn("تعداد کل فایل‌های اسکن‌شده", output)
