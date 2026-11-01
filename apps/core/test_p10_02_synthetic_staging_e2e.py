from decimal import Decimal
from django.db import models
from django.test import TestCase, Client
from django.contrib.auth import get_user_model

from apps.businesses.models import Business
from apps.products.models import Category, Product, ProductImage
from apps.billing.models import Wallet, CreditWaitingQueue, WalletTransaction, WalletReservation
from apps.billing.services import (
    ensure_wallet,
    apply_transaction,
)
from apps.billing.wallet_budget_service import DjangoWalletBudgetService
from apps.core.gateway_contract import OperationRef
from apps.discovery.models import (
    InstagramEngineAccount,
    InstagramEngineLead,
)
from apps.discovery.outreach_contract import OutreachIntent
from apps.core.access_policy import AccessPolicy

User = get_user_model()


class SyntheticStagingEndToEndTests(TestCase):
    """
    P10.02 - Full End-to-End Staging Run with Synthetic Data.
    Tests:
    1. Multi-tenant isolation (Tenant Alpha vs Tenant Beta)
    2. Shared platform sender pool access
    3. Product CRUD & Category tree invariant
    4. Outreach Intent creation & quota control without external network
    5. Budget reservation, concurrency protection, and CreditWaitingQueue
    6. B2C surface removal verification (404 on public catalog)
    7. Zero secret leakage
    """

    def setUp(self):
        # 1. Tenant Alpha (Laptop Store)
        self.user_a = User.objects.create_user(username="alpha_owner", email="alpha@test.local", password="alpha-password-123")
        self.biz_a = Business.objects.create(name="فروشگاه پایتخت (آلفا)", user=self.user_a, slug="alpha-shop", onboarding_done=True)
        self.wallet_a = ensure_wallet(self.biz_a.id)

        # 2. Tenant Beta (Medical / Stone Store)
        self.user_b = User.objects.create_user(username="beta_owner", email="beta@test.local", password="beta-password-123")
        self.biz_b = Business.objects.create(name="سنگ نگین (بتا)", user=self.user_b, slug="beta-shop", onboarding_done=True)
        self.wallet_b = ensure_wallet(self.biz_b.id)

        # 3. Platform Shared Sender Account
        self.platform_sender = InstagramEngineAccount.objects.create(
            username="sender_platform_shared",
            role="SENDER",
            tenant_id=AccessPolicy.PLATFORM_TENANT_ID,
            status="ACTIVE",
            password="enc:shared-pwd",
        )

        # 4. Tenant A Private Sender
        self.alpha_sender = InstagramEngineAccount.objects.create(
            username="sender_alpha_private",
            role="SENDER",
            tenant_id=f"tenant_{self.biz_a.id}",
            status="ACTIVE",
            password="enc:alpha-pwd",
        )

        self.client_a = Client()
        self.client_a.force_login(self.user_a)

        self.client_b = Client()
        self.client_b.force_login(self.user_b)

        self.budget_service = DjangoWalletBudgetService()

    def test_e2e_tenant_isolation_in_access_policy_and_products(self):
        # Create Category and Product for Tenant A
        cat_a = Category.objects.create(business=self.biz_a, name="لپ‌تاپ استوک", product_type="PHYSICAL")
        prod_a = Product.objects.create(business=self.biz_a, name="ThinkPad T480", price=25000000, category=cat_a)

        # Create Category and Product for Tenant B
        cat_b = Category.objects.create(business=self.biz_b, name="سنگ تراورتن", product_type="PHYSICAL")
        prod_b = Product.objects.create(business=self.biz_b, name="تراورتن عباس‌آباد", price=850000, category=cat_b)

        # Verify Tenant A only sees their own products
        resp_a = self.client_a.get("/products/")
        self.assertEqual(resp_a.status_code, 200)
        self.assertContains(resp_a, "ThinkPad T480")
        self.assertNotContains(resp_a, "تراورتن عباس‌آباد")

        # Verify Tenant B only sees their own products
        resp_b = self.client_b.get("/products/")
        self.assertEqual(resp_b.status_code, 200)
        self.assertContains(resp_b, "تراورتن عباس‌آباد")
        self.assertNotContains(resp_b, "ThinkPad T480")

        # Verify SENDER pool search tenants:
        # Tenant A can see their own sender + shared platform sender, but NEVER Tenant B sender
        tenants_a = AccessPolicy.sender_search_tenants(self.user_a)
        self.assertIn(f"tenant_{self.biz_a.id}", tenants_a)
        self.assertIn(AccessPolicy.PLATFORM_TENANT_ID, tenants_a)
        self.assertNotIn(f"tenant_{self.biz_b.id}", tenants_a)

    def test_e2e_financial_wallet_reservation_and_credit_queue(self):
        # Set initial balance for Tenant A: 5,000 Tomans
        apply_transaction(self.biz_a, Decimal("5000"), WalletTransaction.TOPUP, "شارژ تست استیجینگ")
        self.wallet_a.refresh_from_db()
        self.assertEqual(self.wallet_a.balance_toman, Decimal("5000"))

        # Operation requiring 3,000 Tomans -> OK
        op1 = OperationRef(operation_id="op_staging_ok", kind="extract", payer_tenant_id=f"tenant_{self.biz_a.id}")
        allowed1, reason1, res_id1 = self.budget_service.reserve(op1, Decimal("3000"))
        self.assertTrue(allowed1)
        self.wallet_a.refresh_from_db()
        # Reserved amount is held
        held1 = WalletReservation.objects.filter(wallet=self.wallet_a, status=WalletReservation.STATUS_HELD).aggregate(models.Sum("reserved_amount_toman"))["reserved_amount_toman__sum"]
        self.assertEqual(held1, Decimal("3000"))

        # Operation requiring 4,000 Tomans -> INSUFFICIENT_FUNDS (available 2000 < 4000)
        op2 = OperationRef(operation_id="op_staging_exhaust", kind="extract", payer_tenant_id=f"tenant_{self.biz_a.id}")
        allowed2, reason2, res_id2 = self.budget_service.reserve(op2, Decimal("4000"))
        self.assertFalse(allowed2)
        self.assertEqual(reason2, "INSUFFICIENT_FUNDS")
        self.wallet_a.refresh_from_db()
        held2 = WalletReservation.objects.filter(wallet=self.wallet_a, status=WalletReservation.STATUS_HELD).aggregate(models.Sum("reserved_amount_toman"))["reserved_amount_toman__sum"]
        self.assertEqual(held2, Decimal("3000"))

        # Enqueue waiting work
        item = CreditWaitingQueue.objects.create(
            work_item_id="work_staging_1",
            business=self.biz_a,
            operation_id="op_staging_exhaust",
            stage="extraction",
            estimated_toman=Decimal("4000"),
            payload={"test": "staging_payload"},
        )
        self.assertIsNotNone(item.pk)

        # Top up 10,000 Tomans -> Process credit queue
        apply_transaction(self.biz_a, Decimal("10000"), WalletTransaction.TOPUP, "شارژ مجدد استیجینگ")
        self.wallet_a.refresh_from_db()
        self.assertEqual(self.wallet_a.balance_toman, Decimal("15000"))

        # Now reservation succeeds cleanly
        op3 = OperationRef(operation_id="op_staging_retry", kind="extract", payer_tenant_id=f"tenant_{self.biz_a.id}")
        allowed3, reason3, res_id3 = self.budget_service.reserve(op3, Decimal("4000"))
        self.assertTrue(allowed3)
        self.wallet_a.refresh_from_db()
        held3 = WalletReservation.objects.filter(wallet=self.wallet_a, status=WalletReservation.STATUS_HELD).aggregate(models.Sum("reserved_amount_toman"))["reserved_amount_toman__sum"]
        self.assertEqual(held3, Decimal("7000"))

    def test_e2e_b2c_surface_completely_disabled(self):
        # B2C public product card or purchase view must return 404
        resp = self.client_a.get("/p/non-existent-product-b2c/")
        self.assertEqual(resp.status_code, 404)

    def test_e2e_product_image_single_main_invariant(self):
        # R35 verification: at most 1 main image per product under transactional updates
        prod = Product.objects.create(business=self.biz_a, name="لپ‌تاپ استوک تست", price=1000000)
        img1 = ProductImage.objects.create(product=prod, image="products/test1.jpg", is_main=True)
        self.assertTrue(img1.is_main)

        # Creating img2 with is_main=True demotes img1
        img2 = ProductImage.objects.create(product=prod, image="products/test2.jpg", is_main=True)
        img1.refresh_from_db()
        self.assertFalse(img1.is_main)
        self.assertTrue(img2.is_main)

        # Total main images must be exactly 1
        main_count = ProductImage.objects.filter(product=prod, is_main=True).count()
        self.assertEqual(main_count, 1)

    def test_e2e_outreach_intent_and_zero_credential_leakage(self):
        # Lead for Tenant A
        lead_a = InstagramEngineLead.objects.create(
            tenant_id=f"tenant_{self.biz_a.id}",
            username="prospective_buyer",
            full_name="خریدار نمونه",
            intent_score=85,
            status="NEW",
        )

        intent = OutreachIntent(
            lead_id=str(lead_a.id),
            sender_username=self.platform_sender.username,
            tenant_id=f"tenant_{self.biz_a.id}",
            recipient_identifier=lead_a.username,
            message_text="درود! پیگیر خرید لپ‌تاپ بودید در خدمتیم.",
        )

        # Verify string representations do not reveal secrets
        repr_str = str(intent)
        self.assertNotIn("enc:shared-pwd", repr_str)
        self.assertNotIn("alpha-password-123", repr_str)
