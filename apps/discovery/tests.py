from django.test import TestCase, Client
from django.contrib.auth import get_user_model
from apps.businesses.models import Business
from apps.products.models import Category, Product
from apps.discovery.models import CategoryBranchMemory, ProcessedMessageHash, DiscoveredLead
from apps.discovery.services import evaluate_and_discover_leads, send_lead_outreach, extract_keywords_from_product

User = get_user_model()

class DiscoveryPipelineTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="seller@example.com",
            email="seller@example.com",
            password="StrongPassword123!",
            first_name="فروشنده",
            last_name="تست"
        )
        self.business = Business.objects.create(
            user=self.user,
            name="بوتیک شیک‌پوش",
            business_type="PHYSICAL",
            business_domain="پوشاک و مد",
            daily_discovery_limit=3,
            telegram_account_handle="@shikpoosh_bot",
            x_account_handle="@shikpoosh_x",
            preferred_outreach_mode="DIRECT"
        )

        # Build custom category tree up to 3 levels: پوشاک > زنانه > شلوار کارگو
        self.cat_clothing = Category.objects.create(
            business=self.business,
            name="پوشاک",
            product_type="PHYSICAL"
        )
        self.cat_women = Category.objects.create(
            business=self.business,
            name="زنانه",
            parent=self.cat_clothing,
            product_type="PHYSICAL"
        )
        self.cat_cargo = Category.objects.create(
            business=self.business,
            name="شلوار کارگو",
            parent=self.cat_women,
            product_type="PHYSICAL"
        )

        # Create active product with dynamic custom attributes
        self.product = Product.objects.create(
            business=self.business,
            name="شلوار کارگو زنانه باکیفیت",
            category=self.cat_cargo,
            description="شلوار شش جیب راسته با دوخت صنعتی، مناسب استایل کژوال و راحتی",
            price=850000,
            attributes={"رنگ": "زیتونی، مشکی", "جنس": "کتان", "سایز": "38, 40"},
            status="ACTIVE",
            is_discovery_active=True,
            discovery_priority=3,
            telegram_outreach_enabled=True,
            x_outreach_enabled=True
        )

    def test_custom_category_tree_hierarchy(self):
        self.assertEqual(
            self.cat_cargo.get_full_path(),
            "پوشاک > زنانه > شلوار کارگو"
        )
        self.assertEqual(len(self.cat_cargo.get_ancestors()), 2)

    def test_keywords_extraction_includes_tree_and_product(self):
        keywords, negatives = extract_keywords_from_product(self.product)
        self.assertIn("کارگو", keywords)
        self.assertIn("شلوار", keywords)
        self.assertIn("پوشاک", keywords)
        self.assertIn("زنانه", keywords)
        self.assertIn("مشکی", keywords)
        self.assertIn("کتان", keywords)
        self.assertIn("استخدام", negatives)

    def test_discovery_pipeline_captures_all_leads_above_30_percent(self):
        """
        CRITICAL TEST: Ensures user instruction is strictly respected:
        Every potential customer with intent score >= 30% MUST NOT be dropped.
        """
        result = evaluate_and_discover_leads(self.business)
        self.assertEqual(result["status"], "success")
        self.assertGreater(result["leads_created"], 0)

        # Check in DB
        leads = DiscoveredLead.objects.filter(business=self.business)
        self.assertTrue(leads.exists())
        for lead in leads:
            self.assertGreaterEqual(lead.intent_score, 30, "No lead under 30% should be preserved")
            self.assertTrue(lead.outreach_message, "Outreach message must be generated")
            self.assertEqual(lead.status, "CONTACTED", "Outreach should be sent automatically without requiring confirmation")
            self.assertEqual(lead.outreach_status, "SENT")
            self.assertEqual(lead.product, self.product)

    def test_deduplication_prevents_duplicate_processing(self):
        # Run first pass
        res1 = evaluate_and_discover_leads(self.business)
        count1 = DiscoveredLead.objects.filter(business=self.business).count()

        # Run second pass - hashes must prevent duplicate insertion
        res2 = evaluate_and_discover_leads(self.business)
        count2 = DiscoveredLead.objects.filter(business=self.business).count()
        self.assertEqual(count1, count2, "Duplicate messages should not create new leads")

    def test_send_lead_outreach_uses_seller_account(self):
        evaluate_and_discover_leads(self.business)
        lead = DiscoveredLead.objects.filter(business=self.business).first()
        self.assertIsNotNone(lead)

        outreach_res = send_lead_outreach(lead.id, self.business)
        self.assertEqual(outreach_res["status"], "success")

        lead.refresh_from_db()
        self.assertEqual(lead.status, "CONTACTED")
        self.assertEqual(lead.outreach_status, "SENT")
        if lead.channel == "TELEGRAM":
            self.assertEqual(lead.sent_from_handle, self.business.telegram_account_handle)
        elif lead.channel == "X":
            self.assertEqual(lead.sent_from_handle, self.business.x_account_handle)

    def test_category_api_creation_and_tree(self):
        client = Client()
        client.force_login(self.user)

        # 1. Adding child under Level 3 (cat_cargo) creates Level 4 (allowed)
        res_lvl4 = client.post(
            "/products/categories/api/add/",
            data={"name": "کارگو کتان بهاره", "parent_id": self.cat_cargo.id},
            content_type="application/json"
        )
        self.assertEqual(res_lvl4.status_code, 200)
        data_lvl4 = res_lvl4.json()
        self.assertEqual(data_lvl4["status"], "success")
        lvl4_id = data_lvl4["category"]["id"]

        # 2. Adding child under Level 4 creates Level 5 (allowed, maximum depth)
        res_lvl5 = client.post(
            "/products/categories/api/add/",
            data={"name": "مدل بگ راسته", "parent_id": lvl4_id},
            content_type="application/json"
        )
        self.assertEqual(res_lvl5.status_code, 200)
        data_lvl5 = res_lvl5.json()
        self.assertEqual(data_lvl5["status"], "success")
        lvl5_id = data_lvl5["category"]["id"]

        # 3. Adding child under Level 5 (attempting Level 6) must FAIL due to max 5-level constraint
        res_lvl6 = client.post(
            "/products/categories/api/add/",
            data={"name": "طرح جیب پاکتی", "parent_id": lvl5_id},
            content_type="application/json"
        )
        self.assertEqual(res_lvl6.status_code, 400)
        err_data = res_lvl6.json()
        self.assertEqual(err_data["status"], "error")
        self.assertIn("حداکثر عمق مجاز درخت‌واره ۵ سطح است", err_data["message"])

        # 4. Check tree endpoint contains depth information
        tree_res = client.get("/products/categories/api/tree/")
        self.assertEqual(tree_res.status_code, 200)
        tree_data = tree_res.json()
        self.assertEqual(tree_data["status"], "success")
        self.assertTrue(any(node["name"] == "پوشاک" and node["depth"] == 0 for node in tree_data["tree"]))

    def test_daily_limit_quota_toggle(self):
        client = Client()
        client.force_login(self.user)

        # Business has limit of 1
        self.business.daily_discovery_limit = 1
        self.business.save()

        # Product 1 is already active
        p2 = Product.objects.create(
            business=self.business,
            name="تیشرت مردانه نخی",
            status="ACTIVE",
            is_discovery_active=False
        )

        # Attempt to toggle p2 to active should fail because quota (1) is reached
        res = client.post(f"/discovery/products/{p2.id}/toggle-discovery/")
        self.assertEqual(res.status_code, 400)
        data = res.json()
        self.assertEqual(data["status"], "error")
        self.assertIn("سقف سهمیه روزانه پایش", data["message"])
