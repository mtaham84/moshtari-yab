import json
from datetime import date, timedelta
from django.utils import timezone
from django.test import TestCase, Client
from django.contrib.auth import get_user_model
from apps.businesses.models import Business
from apps.products.models import Category, Product
from apps.discovery.models import (
    CategoryBranchMemory,
    ProcessedMessageHash,
    DiscoveredLead,
    ProductDailyMetric,
    ProductOrder,
    Customer,
    Opportunity,
    AIAnalysis,
    OpportunityProductMatch,
    Evidence
)
from apps.discovery.ai_contracts import (
    CandidateInputSchema,
    CandidateProductItem,
    ProductMatchOutput,
    EvidenceOutput,
    AIAnalysisOutputSchema,
    LLMOutputValidator
)
from apps.discovery.llm_provider import (
    MockLLMProvider,
    get_llm_provider
)
from apps.discovery.opportunity_service import OpportunityService
from apps.core.jalali import format_jalali_date, parse_jalali_date
from apps.discovery.services import (
    evaluate_and_discover_leads,
    send_lead_outreach,
    extract_keywords_from_product,
    get_agent_discovery_feed,
    validate_message_in_product_domain,
    check_account_message_cap,
    generate_smart_outreach_message,
    get_intent_rank,
    get_performance_analytics,
    DAILY_OUTREACH_CAP_PER_PRODUCT
)

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

    def test_agent_discovery_feed_service_and_api(self):
        # 1. Test Python service directly
        feed = get_agent_discovery_feed(self.business)
        self.assertEqual(feed["status"], "success")
        self.assertGreaterEqual(feed["total_active_products"], 1)

        product_data = feed["products"][0]
        self.assertEqual(product_data["name"], self.product.name)
        self.assertIn("priority", product_data)
        self.assertEqual(product_data["priority"]["score"], 3)
        self.assertEqual(product_data["priority"]["level"], "HIGH")
        self.assertEqual(product_data["attributes"]["رنگ"], "زیتونی، مشکی")
        self.assertEqual(product_data["attributes"]["جنس"], "کتان")
        self.assertIn("outreach_config", product_data)
        self.assertEqual(product_data["outreach_config"]["seller_telegram_handle"], "@shikpoosh_bot")
        self.assertEqual(product_data["category"]["full_path"], "پوشاک > زنانه > شلوار کارگو")

        # 2. Test REST API endpoint
        client = Client()
        client.force_login(self.user)
        api_res = client.get("/discovery/api/agent/feed/")
        self.assertEqual(api_res.status_code, 200)
        api_data = api_res.json()
        self.assertEqual(api_data["status"], "success")
        self.assertEqual(len(api_data["products"]), 1)
        self.assertIn("priority_definitions", api_data)

    def test_comment_first_x_outreach_includes_direct_product_link_and_dm_invite(self):
        post = {
            "channel": "X",
            "lead_handle": "@test_user",
            "lead_display_name": "کاربر تستی",
            "text": "دنبال شلوار کارگو کتان زیتونی خوش دوخت هستم."
        }
        msg, link = generate_smart_outreach_message(self.business, self.product, post, mode="COMMENT")
        self.assertIn(str(self.product.id), link)
        self.assertIn(link, msg)
        self.assertIn("دایرکت", msg)
        self.assertIn("شلوار کارگو زنانه باکیفیت", msg)

    def test_strict_product_domain_guardrail_blocks_off_topic_queries(self):
        # 1. Product-related inquiry should pass
        valid, status = validate_message_in_product_domain("سلام، آیا سایز ۴۲ از این شلوار کارگو موجود هست؟", self.product, self.business)
        self.assertTrue(valid)
        self.assertEqual(status, "SAFE_IN_DOMAIN")

        # 2. Irrelevant / cooking recipe question must be blocked politely
        invalid, response = validate_message_in_product_domain("من چجوری قرمه سبزی درست کنم؟", self.product, self.business)
        self.assertFalse(invalid)
        self.assertIn("دستیار تخصصی خرید", response)
        self.assertIn(self.product.name, response)

    def test_account_message_cap_enforces_10_message_daily_limit_and_resets_next_day(self):
        lead = DiscoveredLead.objects.create(
            business=self.business,
            product=self.product,
            channel="X",
            lead_handle="@chatty_user",
            content_snippet="دنبال لباس هستم",
            intent_score=80,
            message_count=9,
            last_message_date=timezone.now().date()
        )
        # Message 9 is allowed today
        allowed, status = check_account_message_cap(lead)
        self.assertTrue(allowed)
        self.assertFalse(lead.is_conversation_capped)

        # Message 10 reaches daily cap and blocks further answers today
        lead.message_count = 10
        allowed, status = check_account_message_cap(lead)
        self.assertFalse(allowed)
        self.assertTrue(lead.is_conversation_capped)
        self.assertIn("سقف مجاز روزانه تبادل پیام (۱۰ پیام در روز)", status)

        # On the next day, counter resets automatically
        yesterday = timezone.now().date() - timedelta(days=1)
        lead.last_message_date = yesterday
        lead.save()

        allowed, status = check_account_message_cap(lead)
        self.assertTrue(allowed)
        lead.refresh_from_db()
        self.assertEqual(lead.message_count, 0)
        self.assertFalse(lead.is_conversation_capped)
        self.assertEqual(lead.last_message_date, timezone.now().date())

    def test_api_submit_lead_endpoint(self):
        payload = {
            "channel": "TELEGRAM",
            "lead_handle": "@telegram_shopper",
            "lead_display_name": "خریدار تستی",
            "content_snippet": "سلام شلوار کارگو باکیفیت برای سایز ۳۸ دارید؟",
            "product_id": self.product.id,
            "intent_score": 88,
            "intent_reasoning": "نیاز فوری به شلوار کارگو با ذکر سایز",
            "outreach_mode": "DIRECT",
            "outreach_message": "سلام، شلوار کارگو کتان بگ با کیفیت عالی در کاتالوگ ما موجود است.",
            "tokens_used": 540,
            "cost_usd": 0.00034,
            "cost_toman": 24,
        }
        response = self.client.post(
            "/discovery/api/leads/submit/",
            data=json.dumps(payload),
            content_type="application/json"
        )
        self.assertEqual(response.status_code, 201)
        data = response.json()
        self.assertEqual(data["status"], "success")
        self.assertEqual(data["cost_toman"], 24)
        self.assertEqual(data["tokens_used"], 540)

        # Test deduplication
        dup_response = self.client.post(
            "/discovery/api/leads/submit/",
            data=json.dumps(payload),
            content_type="application/json"
        )
        self.assertEqual(dup_response.status_code, 200)
        self.assertEqual(dup_response.json()["status"], "duplicate")

    def test_two_way_communication_records_customer_reply_and_peyda_bot_identity(self):
        result = evaluate_and_discover_leads(self.business)
        self.assertEqual(result["status"], "success")

        x_lead = DiscoveredLead.objects.filter(business=self.business, channel="X").first()
        self.assertIsNotNone(x_lead)
        self.assertEqual(x_lead.bot_agent_name, "بات پیدا (@peyda_bot)")
        self.assertTrue(x_lead.direct_link_sent)
        self.assertTrue(x_lead.customer_reply, "Customer reply should be captured for two-way communication")
        self.assertIsNotNone(x_lead.customer_reply_at)

    def test_jalali_date_conversion_and_formatting(self):
        d = date(2026, 10, 6)
        shamsi_str = format_jalali_date(d)
        self.assertIn("۱۴۰۵/۰۷/۱۴", shamsi_str)
        parsed = parse_jalali_date(shamsi_str)
        self.assertEqual(parsed, d)

    def test_business_flexible_targeting_constraints(self):
        # 31 provincial cities or custom city
        self.business.target_locations = "شیراز"
        self.business.target_min_age = 22
        self.business.target_max_age = None
        self.business.save()
        self.business.refresh_from_db()
        self.assertEqual(self.business.target_locations, "شیراز")
        self.assertEqual(self.business.target_min_age, 22)
        self.assertIsNone(self.business.target_max_age)

    def test_intent_prioritization_hierarchy(self):
        # 1: Ready to buy (>= 85)
        # 2: Comparing (60 - 84)
        # 3: Initial need (30 - 59)
        self.assertEqual(get_intent_rank(95), 1)
        self.assertEqual(get_intent_rank(85), 1)
        self.assertEqual(get_intent_rank(75), 2)
        self.assertEqual(get_intent_rank(60), 2)
        self.assertEqual(get_intent_rank(45), 3)
        self.assertEqual(get_intent_rank(30), 3)

    def test_daily_outreach_cap_per_product_rule(self):
        """
        Tests that when > 100 leads exist for a product, exactly 100 with highest
        purchase chance are messaged, and the rest remain in status 'NEW'.
        """
        candidate_leads = []
        for i in range(105):
            score = 30 + (i % 70)
            lead = DiscoveredLead.objects.create(
                business=self.business,
                product=self.product,
                channel="X",
                lead_handle=f"@lead_{i}",
                content_snippet=f"درخواست شماره {i}",
                intent_score=score,
                status="NEW",
                outreach_status="DRAFT"
            )
            candidate_leads.append(lead)

        candidate_leads.sort(key=lambda l: (l.intent_priority_rank, -l.intent_score))

        for idx, l in enumerate(candidate_leads):
            if idx < DAILY_OUTREACH_CAP_PER_PRODUCT:
                l.status = "CONTACTED"
                l.outreach_status = "SENT"
                l.save()

        sent_count = DiscoveredLead.objects.filter(product=self.product, status="CONTACTED").count()
        unsent_count = DiscoveredLead.objects.filter(product=self.product, status="NEW").count()
        self.assertEqual(sent_count, 100)
        self.assertEqual(unsent_count, 5)

    def test_analytics_aggregation_and_persian_date_filtering(self):
        today = date(2026, 10, 6)
        ProductDailyMetric.objects.create(
            product=self.product,
            date=today,
            outreach_sent_count=20,
            clicks_count=15,
            views_count=35,
            orders_count=2,
            sales_amount=1700000
        )

        analytics = get_performance_analytics(self.business, start_date=today, end_date=today)
        summary = analytics["summary"]
        self.assertEqual(summary["total_sales"], 1700000)
        self.assertEqual(summary["total_orders"], 2)
        self.assertEqual(summary["total_clicks"], 15)
        self.assertEqual(summary["total_views"], 35)
        self.assertEqual(summary["total_outreach"], 20)
        self.assertAlmostEqual(summary["conversion_rate"], 13.3, places=1)

    def test_public_product_card_and_quick_order(self):
        client = Client()
        resp = client.get(f"/p/{self.product.id}/?src=agent")
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, self.product.name)
        self.assertContains(resp, "خرید مستقیم و سفارش سریع")

        order_resp = client.post(
            f"/p/{self.product.id}/",
            {
                "customer_name": "مریم احمدی",
                "customer_phone": "09121234567",
                "shipping_address": "تهران، میدان ونک",
                "quantity": "2"
            },
            HTTP_X_REQUESTED_WITH="XMLHttpRequest"
        )
        self.assertEqual(order_resp.status_code, 200)
        data = order_resp.json()
        self.assertEqual(data["status"], "success")
        self.assertTrue(data["tracking_code"].startswith("ORD-"))

        order = ProductOrder.objects.filter(product=self.product, customer_name="مریم احمدی").first()
        self.assertIsNotNone(order)
        self.assertEqual(order.quantity, 2)
        self.assertEqual(order.total_price, self.product.price * 2)


class UnifiedOpportunityModelTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="seller2@example.com",
            email="seller2@example.com",
            password="StrongPassword123!"
        )
        self.business = Business.objects.create(
            user=self.user,
            name="کالای دیجیتال پیشرو",
            business_type="PHYSICAL"
        )
        self.category = Category.objects.create(
            business=self.business,
            name="لوازم جانبی موبایل"
        )
        self.product1 = Product.objects.create(
            business=self.business,
            name="پاوربانک ۲۰۰۰۰ فست شارژ",
            category=self.category,
            price=1200000
        )
        self.product2 = Product.objects.create(
            business=self.business,
            name="کابل شارژ تایپ سی انکر",
            category=self.category,
            price=350000
        )

    def test_customer_creation_nullable_fields_and_identifier(self):
        # Customer without phone or real name
        c1 = Customer.objects.create(
            business=self.business,
            source_platform="telegram",
            source_username="tehran_shopper",
            external_user_id="tg_123456"
        )
        self.assertIsNone(c1.phone_number)
        self.assertEqual(c1.name, "")
        self.assertEqual(c1.display_identifier, "@tehran_shopper")

        # Customer with phone and name
        c2 = Customer.objects.create(
            business=self.business,
            name="علی محمدی",
            phone_number="09120000000",
            source_platform="divar"
        )
        self.assertEqual(c2.display_identifier, "علی محمدی")

        # Customer with only external_user_id
        c3 = Customer.objects.create(
            business=self.business,
            external_user_id="user_9876",
            source_platform="x"
        )
        self.assertEqual(c3.display_identifier, "کاربر user_9876")

    def test_opportunity_and_aianalysis_relationships(self):
        customer = Customer.objects.create(
            business=self.business,
            source_platform="telegram",
            source_username="buyer_ali"
        )
        opportunity = Opportunity.objects.create(
            business=self.business,
            customer=customer,
            category=self.category,
            category_name_snapshot="لوازم جانبی موبایل",
            source_platform="telegram",
            source_message_id="msg_999",
            source_raw_message="سلام پاوربانک فست شارژ خوب چی پیشنهاد میدید؟",
            status="QUALIFIED"
        )

        analysis = AIAnalysis.objects.create(
            opportunity=opportunity,
            need="درخواست پیشنهاد خرید پاوربانک فست شارژ",
            intent_score=0.92,
            product_fit_score=0.88,
            confidence=0.95,
            why_selected="کاربر مشخصاً به دنبال پاوربانک فست شارژ با کیفیت است که دقیقاً با کاتالوگ فروشگاه تطابق دارد.",
            suggested_reply="سلام علی عزیز، پاوربانک ۲۰۰۰۰ فست شارژ ما با فناوری PD و توان ۲۲.۵ وات کاملاً مناسب نیاز شماست.",
            model_name="llama-3.3-70b-versatile",
            tokens_used=420,
            cost_usd=0.00035,
            cost_toman=25
        )

        self.assertEqual(opportunity.intent_score_percentage, 92)
        self.assertEqual(opportunity.product_fit_score_percentage, 88)
        self.assertEqual(opportunity.confidence_percentage, 95)
        self.assertEqual(opportunity.ai_analysis.why_selected, analysis.why_selected)

    def test_multi_product_matches_and_primary_match(self):
        customer = Customer.objects.create(
            business=self.business,
            source_platform="x",
            source_username="tech_geek"
        )
        opportunity = Opportunity.objects.create(
            business=self.business,
            customer=customer,
            category=self.category,
            source_platform="x",
            source_raw_message="یه پاوربانک و کابل خوب چی بخرم؟"
        )

        match1 = OpportunityProductMatch.objects.create(
            opportunity=opportunity,
            product=self.product1,
            match_score=0.95,
            recommendation_reason="تطابق با بخش پاوربانک درخواست",
            rank=1
        )
        match2 = OpportunityProductMatch.objects.create(
            opportunity=opportunity,
            product=self.product2,
            match_score=0.85,
            recommendation_reason="تطابق با بخش کابل شارژ",
            rank=2
        )

        self.assertEqual(opportunity.primary_product_match, match1)
        self.assertEqual(opportunity.product_matches.count(), 2)
        self.assertEqual(match1.match_score_percentage, 95)

    def test_evidence_citations(self):
        customer = Customer.objects.create(
            business=self.business,
            source_platform="divar"
        )
        opportunity = Opportunity.objects.create(
            business=self.business,
            customer=customer,
            source_platform="divar",
            source_raw_message="سلام قیمت عمده کابل شارژ چنده؟"
        )

        ev1 = Evidence.objects.create(
            opportunity=opportunity,
            evidence_type="customer_message",
            content="سلام قیمت عمده کابل شارژ چنده؟",
            source_reference="پیام چت دیوار"
        )
        ev2 = Evidence.objects.create(
            opportunity=opportunity,
            evidence_type="need_signal",
            content="نیاز به استعلام قیمت کابل شارژ",
            source_reference="تحلیل نیت هوش مصنوعی"
        )

        evidence_items = list(opportunity.evidence_items.all())
        self.assertEqual(len(evidence_items), 2)
        self.assertEqual(evidence_items[0], ev1)
        self.assertEqual(evidence_items[1], ev2)


class LLMContractAndValidationTests(TestCase):
    def setUp(self):
        self.candidate = CandidateInputSchema(
            candidate_id="cand_123",
            source_platform="telegram",
            source_raw_message="سلام پاوربانک فست شارژ برای سفر میخوام. چه مدلی رو پیشنهاد میدید؟",
            source_message_id="tg_msg_777",
            sender_username="traveler_ali",
            candidate_products=[
                CandidateProductItem(id=10, name="پاوربانک ۲۰۰۰۰ میلی‌آمپر فست", price=1200000),
                CandidateProductItem(id=20, name="کابل تایپ سی انکر", price=300000),
            ]
        )

    def test_candidate_input_schema_serialization(self):
        cand_dict = self.candidate.to_dict()
        self.assertEqual(cand_dict["source_platform"], "telegram")
        self.assertEqual(len(cand_dict["candidate_products"]), 2)
        self.assertEqual(cand_dict["candidate_products"][0]["id"], 10)

        restored = CandidateInputSchema.from_dict(cand_dict)
        self.assertEqual(restored.candidate_id, "cand_123")
        self.assertEqual(restored.candidate_products[0].name, "پاوربانک ۲۰۰۰۰ میلی‌آمپر فست")

    def test_validator_strips_hallucinated_product_ids(self):
        """
        CRITICAL GUARDRAIL:
        If LLM invents product ID 9999 (not in candidate_products), it MUST be discarded.
        Valid product ID 10 must be preserved.
        """
        raw_output = AIAnalysisOutputSchema(
            need="پاوربانک مناسب سفر",
            intent_score=0.95,
            product_fit_score=0.90,
            confidence=0.92,
            why_selected="کاربر نیازمند شارژر همراه برای مسافرت است.",
            suggested_reply="سلام، پاوربانک ۲۰۰۰۰ فست شارژ برای مسافرت عالی است.",
            product_matches=[
                ProductMatchOutput(product_id=9999, match_score=0.99, recommendation_reason="کالای خیالی"),
                ProductMatchOutput(product_id=10, match_score=0.92, recommendation_reason="پاوربانک کاتالوگ"),
            ]
        )

        sanitized = LLMOutputValidator.validate_and_sanitize(raw_output, self.candidate)
        # Hallucinated product 9999 must NOT be in product_matches
        match_ids = [m.product_id for m in sanitized.product_matches]
        self.assertNotIn(9999, match_ids)
        self.assertIn(10, match_ids)
        self.assertEqual(len(sanitized.product_matches), 1)
        self.assertEqual(sanitized.product_matches[0].rank, 1)

    def test_validator_strips_chain_of_thought_and_clamps_scores(self):
        raw_output = AIAnalysisOutputSchema(
            need="خرید باتری",
            intent_score=1.5,  # Out of range > 1.0
            product_fit_score=-0.2,  # Out of range < 0.0
            confidence=0.8,
            why_selected="<think>User is asking about power bank. Step 1: analyze intent...</think>مشتری قصد خرید دارد.",
            suggested_reply="سلام، در خدمتیم."
        )

        sanitized = LLMOutputValidator.validate_and_sanitize(raw_output, self.candidate)
        self.assertEqual(sanitized.intent_score, 1.0)
        self.assertEqual(sanitized.product_fit_score, 0.0)
        self.assertNotIn("<think>", sanitized.why_selected)
        self.assertNotIn("Step 1", sanitized.why_selected)
        self.assertEqual(sanitized.why_selected, "مشتری قصد خرید دارد.")

    def test_validator_calculates_toman_cost(self):
        raw_output = AIAnalysisOutputSchema(
            need="تست هزینه",
            intent_score=0.8,
            product_fit_score=0.8,
            confidence=0.8,
            why_selected="تست",
            suggested_reply="تست",
            cost_usd=0.001,
            cost_toman=0
        )
        sanitized = LLMOutputValidator.validate_and_sanitize(raw_output, self.candidate)
        self.assertEqual(sanitized.cost_toman, 70)  # 0.001 * 70,000 = 70 Toman

    def test_mock_llm_provider_execution(self):
        provider = MockLLMProvider()
        result = provider.analyze_candidate(self.candidate)

        self.assertIsInstance(result, AIAnalysisOutputSchema)
        self.assertGreaterEqual(result.intent_score, 0.8)
        self.assertGreaterEqual(result.product_fit_score, 0.7)
        self.assertTrue(len(result.product_matches) >= 1)
        self.assertEqual(result.product_matches[0].product_id, 10)
        self.assertIn("پاوربانک", result.suggested_reply)
        self.assertTrue(len(result.evidence_items) >= 2)

    def test_get_llm_provider_factory(self):
        provider = get_llm_provider("mock")
        self.assertIsInstance(provider, MockLLMProvider)


class OpportunityServiceTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="seller3@example.com",
            email="seller3@example.com",
            password="StrongPassword123!"
        )
        self.business = Business.objects.create(
            user=self.user,
            name="کفش و پوشاک اسپرت",
            business_type="PHYSICAL"
        )
        self.category = Category.objects.create(
            business=self.business,
            name="کفش ورزشی"
        )
        self.product = Product.objects.create(
            business=self.business,
            name="کفش پیاده‌روی ریباک اصل",
            category=self.category,
            price=2400000,
            status="ACTIVE",
            is_discovery_active=True
        )

    def test_process_candidate_from_all_four_platforms(self):
        """
        Verifies source-agnostic support across Telegram, X, Instagram, and Divar.
        """
        platforms_data = [
            ("telegram", "tg_101", "@reza_run", "سلام قیمت کفش پیاده‌روی چنده؟ موجود دارید؟"),
            ("x", "x_202", "@sarah_fit", "دنبال یه جفت کفش پیاده‌روی راحت و اصل هستم. چی پیشنهاد میدید؟"),
            ("instagram", "ig_303", "lifestyle_iran", "سلام این مدل کفش پیاده‌روی رو چطور می‌تونم سفارش بدم؟"),
            ("divar", "divar_404", "divar_buyer_88", "سلام کفش پیاده‌روی ریباک سایز ۴۲ موجوده؟ تخفیف داره؟"),
        ]

        for platform, msg_id, username, msg_text in platforms_data:
            payload = {
                "source_platform": platform,
                "source_message_id": msg_id,
                "sender_username": username,
                "source_raw_message": msg_text,
                "category_id": self.category.id
            }

            opp = OpportunityService.process_candidate(
                candidate_payload=payload,
                business=self.business,
                llm_provider=MockLLMProvider()
            )

            self.assertIsNotNone(opp)
            self.assertEqual(opp.source_platform, platform)
            self.assertEqual(opp.source_message_id, msg_id)
            self.assertEqual(opp.customer.source_platform, platform)
            self.assertEqual(opp.customer.source_username, username.lstrip("@"))
            self.assertIsNone(opp.customer.phone_number)
            self.assertTrue(hasattr(opp, "ai_analysis"))
            self.assertGreaterEqual(opp.ai_analysis.intent_score, 0.7)
            self.assertTrue(opp.product_matches.exists())
            self.assertEqual(opp.product_matches.first().product, self.product)
            self.assertTrue(opp.evidence_items.exists())

    def test_deduplication_returns_existing_opportunity(self):
        payload = {
            "source_platform": "telegram",
            "source_message_id": "tg_duplicate_check",
            "sender_username": "buyer_check",
            "source_raw_message": "سلام کفش ورزشی موجود دارید؟",
            "category_id": self.category.id
        }

        opp1 = OpportunityService.process_candidate(payload, self.business, MockLLMProvider())
        opp2 = OpportunityService.process_candidate(payload, self.business, MockLLMProvider())

        self.assertEqual(opp1.id, opp2.id)
        self.assertEqual(Opportunity.objects.filter(source_message_id="tg_duplicate_check").count(), 1)


class OpportunityPanelViewsTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.user = User.objects.create_user(
            username="seller_panel@example.com",
            email="seller_panel@example.com",
            password="StrongPassword123!"
        )
        self.business = Business.objects.create(
            user=self.user,
            name="فروشگاه لپ‌تاپ پارس",
            business_type="PHYSICAL"
        )
        self.category = Category.objects.create(
            business=self.business,
            name="لپ‌تاپ گیمینگ"
        )
        self.product = Product.objects.create(
            business=self.business,
            name="لپ‌تاپ ایسوس ROG",
            category=self.category,
            price=68000000,
            status="ACTIVE",
            is_discovery_active=True
        )

        # Create two opportunities for testing
        payload1 = {
            "source_platform": "telegram",
            "source_message_id": "tg_view_1",
            "sender_username": "gamermaster",
            "source_raw_message": "سلام لپ‌تاپ گیمینگ ایسوس موجود دارید برای خرید نقدی؟",
            "category_id": self.category.id
        }
        self.opp1 = OpportunityService.process_candidate(payload1, self.business, MockLLMProvider())

        payload2 = {
            "source_platform": "x",
            "source_message_id": "x_view_2",
            "sender_username": "tech_fan",
            "source_raw_message": "دنبال یه لپ‌تاپ قوی برای رندر و بازی هستم. پیشنهادتون چیه؟",
            "category_id": self.category.id
        }
        self.opp2 = OpportunityService.process_candidate(payload2, self.business, MockLLMProvider())

    def test_opportunity_list_requires_login(self):
        resp = self.client.get("/discovery/opportunities/")
        self.assertEqual(resp.status_code, 302)
        self.assertIn("/accounts/login/", resp.url)

    def test_opportunity_list_authenticated_displays_items(self):
        self.client.force_login(self.user)
        resp = self.client.get("/discovery/opportunities/")
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "فرصت‌های کشف‌شده هوشمند")
        self.assertContains(resp, "gamermaster")
        self.assertContains(resp, "tech_fan")
        self.assertContains(resp, "لپ‌تاپ ایسوس ROG")

    def test_opportunity_list_filters_by_platform(self):
        self.client.force_login(self.user)
        # Filter for telegram
        resp_tg = self.client.get("/discovery/opportunities/?platform=telegram")
        self.assertEqual(resp_tg.status_code, 200)
        self.assertContains(resp_tg, "gamermaster")
        self.assertNotContains(resp_tg, "tech_fan")

        # Filter for X
        resp_x = self.client.get("/discovery/opportunities/?platform=x")
        self.assertEqual(resp_x.status_code, 200)
        self.assertContains(resp_x, "tech_fan")
        self.assertNotContains(resp_x, "gamermaster")

    def test_opportunity_detail_view(self):
        self.client.force_login(self.user)
        resp = self.client.get(f"/discovery/opportunities/{self.opp1.id}/")
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "چرا پیدا این مشتری را انتخاب کرد؟")
        self.assertContains(resp, "پیشنهاد پاسخ هوشمند")
        self.assertContains(resp, "لپ‌تاپ ایسوس ROG")
        self.assertContains(resp, self.opp1.source_raw_message)

    def test_opportunity_status_update_via_ajax(self):
        self.client.force_login(self.user)
        resp = self.client.post(
            f"/discovery/opportunities/{self.opp1.id}/status/",
            data={"status": "CONTACTED"}
        )
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["status"], "success")
        self.assertEqual(data["new_status"], "CONTACTED")

        self.opp1.refresh_from_db()
        self.assertEqual(self.opp1.status, "CONTACTED")

    def test_api_opportunity_process_endpoint(self):
        self.client.force_login(self.user)
        payload = {
            "source_platform": "instagram",
            "source_message_id": "ig_api_999",
            "sender_username": "insta_buyer",
            "source_raw_message": "سلام لپ تاپ برای گیم دارید قیمت چنده؟",
            "business_id": self.business.id
        }
        resp = self.client.post(
            "/discovery/api/opportunities/process/",
            data=json.dumps(payload),
            content_type="application/json"
        )
        self.assertEqual(resp.status_code, 201)
        data = resp.json()
        self.assertEqual(data["status"], "success")
        self.assertEqual(data["source_platform"], "instagram")
        self.assertIn("ai_analysis", data)
        self.assertGreaterEqual(data["ai_analysis"]["intent_score"], 0.7)
        self.assertTrue(len(data["product_matches"]) >= 1)






