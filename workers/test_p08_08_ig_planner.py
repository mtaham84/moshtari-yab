from unittest import TestCase

from workers.ig_collector.planner import (
    CollectionPlan,
    adjust_manual_comments_config,
    build_collection_plan,
)


class TestInstagramPlanner(TestCase):
    def test_seed_segregation(self):
        seeds = [
            {"kind": "account", "value": "store_page"},
            {"kind": "post", "value": "C12345", "max_comments": 80, "window_days": 14},
            {"kind": "hashtag", "value": "shoes"},
            {"kind": "account", "value": "competitor_page"},
        ]
        plan = build_collection_plan(seeds, {"policy": "buyer_request"})
        self.assertEqual(len(plan.manual_seeds), 1)
        self.assertEqual(plan.manual_seeds[0]["value"], "C12345")
        self.assertEqual(len(plan.account_seeds), 2)
        self.assertEqual([s["value"] for s in plan.account_seeds], ["store_page", "competitor_page"])
        self.assertEqual(len(plan.hashtag_seeds), 1)
        self.assertEqual(plan.hashtag_seeds[0]["value"], "shoes")

    def test_manual_comments_adjustment(self):
        comments_in = {
            "mode": "signals",
            "policy": "seller_post",
            "max_posts_per_run": 0,
            "max_per_post": 20,
            "window_days": 3,
        }
        manual_seeds = [
            {"kind": "post", "value": "C1", "max_comments": 150, "window_days": 10},
            {"kind": "post", "value": "C2", "max_comments": 200, "window_days": 5},
        ]
        adjusted = adjust_manual_comments_config(comments_in, manual_seeds)
        self.assertEqual(adjusted["mode"], "all")
        self.assertEqual(adjusted["max_posts_per_run"], 1)  # clamped to [1, 100]
        self.assertEqual(adjusted["max_per_post"], 200)      # max of (20, 150, 200)
        self.assertEqual(adjusted["window_days"], 10)        # max of (3, 10, 5)

    def test_daily_capacity_and_cooldown(self):
        # Case 1: Active cooldown
        plan_cooldown = build_collection_plan(
            [{"kind": "account", "value": "acc1"}],
            daily_cap=100,
            collected_today=40,
            cooldown_until="2026-11-01T23:00:00Z",
        )
        self.assertTrue(plan_cooldown.is_rate_limited)
        self.assertEqual(plan_cooldown.cooldown_until, "2026-11-01T23:00:00Z")
        self.assertEqual(plan_cooldown.available_capacity, 60)

        # Case 2: Cap exhausted
        plan_exhausted = build_collection_plan(
            [],
            daily_cap=50,
            collected_today=55,
            cooldown_until=None,
        )
        self.assertFalse(plan_exhausted.is_rate_limited)
        self.assertEqual(plan_exhausted.available_capacity, 0)

    def test_policy_fallback_to_buyer_request(self):
        plan = build_collection_plan(
            [],
            comments={"policy": "unsupported_weird_policy"},
            supported_policies={"buyer_request", "seller_post"},
        )
        self.assertEqual(plan.policy_name, "buyer_request")
        self.assertEqual(plan.effective_comments["policy"], "buyer_request")
