import io
from unittest.mock import patch, MagicMock
from django.contrib.auth import get_user_model
from django.test import TestCase, Client
from django.urls import reverse
from django.core.files.uploadedfile import SimpleUploadedFile

from apps.businesses.models import Business
from apps.discovery.models import InstagramEngineAccount, InstagramEngineLead, ProductOrder
from apps.products.models import Product, ProductImage


class ContainmentGatesTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.user1 = User.objects.create_user("user1", "u1@x.ir", "pw1")
        self.biz1 = Business.objects.create(user=self.user1, name="Biz 1")
        
        self.user2 = User.objects.create_user("user2", "u2@x.ir", "pw2")
        self.biz2 = Business.objects.create(user=self.user2, name="Biz 2")

    def test_cross_tenant_followup_blocked(self):
        """R01 containment: User 1 cannot send follow-up to a lead belonging to Biz 2."""
        lead = InstagramEngineLead.objects.create(
            tenant_id=f"tenant_{self.biz2.id}",
            username="buyer_target",
            status="DM_SENT"
        )
        sender = InstagramEngineAccount.objects.create(
            tenant_id=f"tenant_{self.biz1.id}",
            username="sender1",
            password="pw",
            role="SENDER",
            status="ACTIVE"
        )
        self.client.force_login(self.user1)
        resp = self.client.post(
            reverse("accounts:send_followup"),
            {
                "lead_id": lead.id,
                "sender_username": sender.username,
                "message_text": "Follow up message"
            },
            HTTP_X_REQUESTED_WITH="XMLHttpRequest"
        )
        # Should be rejected with 403 or error, not success
        self.assertNotEqual(resp.status_code, 200, "Foreign lead follow-up must be rejected!")

    def test_reassign_foreign_account_blocked(self):
        """R02 containment: User 2 cannot take over sender account belonging to Biz 1."""
        sender1 = InstagramEngineAccount.objects.create(
            tenant_id=f"tenant_{self.biz1.id}",
            username="exclusive_sender",
            password="pw",
            role="SENDER",
            status="ACTIVE"
        )
        self.client.force_login(self.user2)
        # Session id >= 32 chars to pass format check if it gets there
        valid_looking_session = "a" * 35
        resp = self.client.post(
            reverse("accounts:add_outreach_account"),
            {
                "username": "exclusive_sender",
                "sessionid": valid_looking_session,
                "consultant_name": "Attacker"
            }
        )
        sender1.refresh_from_db()
        self.assertEqual(
            sender1.tenant_id,
            f"tenant_{self.biz1.id}",
            "Foreign sender account tenant_id must NOT be reassigned to attacker tenant!"
        )

    def test_upload_html_file_as_image_rejected(self):
        """R05 containment: Uploading HTML file as product image must be rejected."""
        self.client.force_login(self.user1)
        fake_html = SimpleUploadedFile("proof.html", b"<html><body>xss</body></html>", content_type="text/html")
        resp = self.client.post(
            reverse("products:add"),
            {
                "name": "Test Product",
                "product_type": "PHYSICAL",
                "images": [fake_html]
            }
        )
        # Ensure no product image with .html was saved
        saved_imgs = ProductImage.objects.filter(product__business=self.biz1)
        for img in saved_imgs:
            self.assertFalse(img.image.name.endswith(".html"), "HTML file should not be stored as ProductImage!")

    def test_public_product_card_order_disabled(self):
        """R06 containment: Public B2C card order creation must be disabled."""
        product = Product.objects.create(business=self.biz1, name="Public Item", price=1000)
        resp = self.client.post(
            reverse("products:public_card", kwargs={"pk": product.pk}),
            {
                "customer_name": "Test Buyer",
                "customer_phone": "09120000000",
                "quantity": "1"
            }
        )
        # Must return 403 or 410 or redirect without creating order
        self.assertEqual(ProductOrder.objects.count(), 0, "Fake PAID order must NOT be created!")
        self.assertIn(resp.status_code, [403, 404, 405, 410])

    def test_api_suggest_category_requires_login(self):
        """R07 containment: Anonymous calls to category suggestion must be blocked."""
        client = Client() # anonymous
        resp = client.get(reverse("products:api_suggest_category"), {"name": "کفش ورزشی"})
        # Must require login (redirect to login or 401/403)
        self.assertIn(resp.status_code, [302, 401, 403], "Anonymous access to category API must be blocked!")

    def test_dry_run_does_not_mutate_lead_or_sender(self):
        """R10 containment: run_instagram_outreach in dry-run mode must NOT alter database state."""
        from django.core.management import call_command
        sender = InstagramEngineAccount.objects.create(
            tenant_id=f"tenant_{self.biz1.id}",
            username="sender_dry",
            password="pw",
            role="SENDER",
            status="ACTIVE",
            daily_action_count=0
        )
        lead = InstagramEngineLead.objects.create(
            tenant_id=f"tenant_{self.biz1.id}",
            username="lead_dry",
            status="NEW",
            comment_text="دنبال مانتو هستم"
        )
        with patch("apps.discovery.management.commands.run_instagram_outreach.time.sleep", return_value=None):
            call_command("run_instagram_outreach", "--dry-run", "--once", "--ignore-circadian", f"--tenant=tenant_{self.biz1.id}")
        
        lead.refresh_from_db()
        sender.refresh_from_db()
        self.assertEqual(lead.status, "NEW", "Dry-run must NOT mutate lead status to DM_SENT!")
        self.assertEqual(sender.daily_action_count, 0, "Dry-run must NOT increment sender daily_action_count!")

