import csv
import io
from django.contrib.auth import get_user_model
from django.test import RequestFactory, TestCase

from apps.businesses.models import Business
from apps.discovery import export_service
from apps.discovery.models import (
    AIAnalysis,
    Customer,
    Evidence,
    Opportunity,
    OpportunityProductMatch,
)
from apps.products.models import Product

User = get_user_model()


class P0816ExportServiceTests(TestCase):
    def setUp(self):
        self.factory = RequestFactory()
        self.user_a = User.objects.create_user(username="seller_a", email="a@example.com", password="pass")
        self.biz_a = Business.objects.create(user=self.user_a, name="فروشگاه الف")
        self.prod_a = Product.objects.create(business=self.biz_a, name="کیف چرم الف")

        self.user_b = User.objects.create_user(username="seller_b", email="b@example.com", password="pass")
        self.biz_b = Business.objects.create(user=self.user_b, name="فروشگاه ب")
        self.prod_b = Product.objects.create(business=self.biz_b, name="کفش ورزشی ب")

        self.admin = User.objects.create_superuser(username="admin_user", email="admin@example.com", password="pass")

    def test_resolve_export_scope_for_seller(self):
        # Normal seller access
        biz, err_code, err_msg = export_service.resolve_export_scope(self.user_a, {})
        self.assertEqual(biz, self.biz_a)
        self.assertIsNone(err_code)

        # Seller requesting their own product
        biz, err_code, err_msg = export_service.resolve_export_scope(
            self.user_a, {"product_id": str(self.prod_a.id)}
        )
        self.assertEqual(biz, self.biz_a)
        self.assertIsNone(err_code)

        # Seller requesting another tenant's product -> 403 Forbidden
        biz, err_code, err_msg = export_service.resolve_export_scope(
            self.user_a, {"product_id": str(self.prod_b.id)}
        )
        self.assertIsNone(biz)
        self.assertEqual(err_code, 403)
        self.assertIn("محصول در این فروشگاه یافت نشد", err_msg)

    def test_resolve_export_scope_for_user_without_business(self):
        user_nobiz = User.objects.create_user(username="orphan", email="orphan@example.com", password="pass")
        biz, err_code, err_msg = export_service.resolve_export_scope(user_nobiz, {})
        self.assertIsNone(biz)
        self.assertEqual(err_code, 404)

    def test_resolve_export_scope_for_admin(self):
        # Admin scoping by business_id
        biz, err_code, _ = export_service.resolve_export_scope(
            self.admin, {"business_id": str(self.biz_b.id)}
        )
        self.assertEqual(biz, self.biz_b)
        self.assertIsNone(err_code)

        # Admin scoping by product_id
        biz, err_code, _ = export_service.resolve_export_scope(
            self.admin, {"product_id": str(self.prod_a.id)}
        )
        self.assertEqual(biz, self.biz_a)
        self.assertIsNone(err_code)

    def test_safe_csv_cell_escapes_formulas(self):
        # Formula injection triggers must be prepended with a single quote
        self.assertEqual(export_service.safe_csv_cell("=1+1"), "'=1+1")
        self.assertEqual(export_service.safe_csv_cell("+cmd"), "'+cmd")
        self.assertEqual(export_service.safe_csv_cell("-2"), "'-2")
        self.assertEqual(export_service.safe_csv_cell("@SUM(A1:A5)"), "'@SUM(A1:A5)")
        self.assertEqual(export_service.safe_csv_cell("\tTAB"), "'\tTAB")

        # Dash placeholder and regular Persian text must remain untouched
        self.assertEqual(export_service.safe_csv_cell("-"), "-")
        self.assertEqual(export_service.safe_csv_cell("متن فارسی مشتری"), "متن فارسی مشتری")

    def test_serialize_opportunity_row_and_csv_generation(self):
        cust = Customer.objects.create(
            business=self.biz_a,
            source_platform="instagram",
            source_username="buyer_ig",
            name="خریدار اینستاگرام",
        )
        opp = Opportunity.objects.create(
            business=self.biz_a,
            customer=cust,
            source_platform="instagram",
            source_raw_message="سلام، آیا کیف چرم مشکی موجود دارید؟\nقیمتش چنده؟",
        )
        OpportunityProductMatch.objects.create(
            opportunity=opp,
            product=self.prod_a,
            rank=1,
            match_score=0.95,
        )
        AIAnalysis.objects.create(
            opportunity=opp,
            need="کیف چرم مشکی",
            suggested_reply="سلام وقت بخیر، بله کیف چرم الف موجود است.",
        )

        row = export_service.serialize_opportunity_row(opp)
        self.assertEqual(len(row), 6)
        self.assertEqual(row[0], "'@buyer_ig")
        self.assertEqual(row[1], "اینستاگرام")
        self.assertEqual(row[2], "https://www.instagram.com/buyer_ig/")
        self.assertEqual(row[3], "سلام، آیا کیف چرم مشکی موجود دارید؟ قیمتش چنده؟")
        self.assertEqual(row[4], "کیف چرم الف")
        self.assertEqual(row[5], "سلام وقت بخیر، بله کیف چرم الف موجود است.")

        filename, csv_bytes = export_service.generate_opportunities_csv(
            Opportunity.objects.filter(id=opp.id)
        )
        self.assertTrue(csv_bytes.startswith(b"\xef\xbb\xbf"))  # UTF-8 BOM
        parsed_rows = list(csv.reader(io.StringIO(csv_bytes.decode("utf-8-sig"))))
        self.assertEqual(parsed_rows[0], export_service.CSV_HEADERS)
        self.assertEqual(parsed_rows[1], row)
