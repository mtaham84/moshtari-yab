from datetime import datetime, timezone
from pathlib import Path
import unittest
import shutil
import os
from unittest.mock import patch

from .core import ProductProfile, SourceRecord, extract_quantity, local_classify, normalize_text, rank_analysis, export_result, JobResult
from .engine import run_job
from .cache import DiscoveryCache
from .evaluation import PERSIAN_CASES, evaluate_persian_rules
from .ai import GrokAnalyzer
from .jobs import JobStore
from . import engine as engine_module


class BuyerEngineTests(unittest.TestCase):
    def test_persian_normalization_and_digits(self):
        self.assertEqual(normalize_text("مي‌خوام كالا ۲۰۰ عدد"), "می خوام کالا 200 عدد")

    def test_quantity_ranges_and_single(self):
        self.assertEqual(extract_quantity("بین ۲۰۰ تا ۴۰۰ عدد"), (200, 400))
        self.assertEqual(extract_quantity("۳ دستگاه می‌خواهم"), (3, 3))
        self.assertEqual(extract_quantity("تعداد نامشخص"), (None, None))
        self.assertEqual(extract_quantity("دویست عدد می‌خوام"), (200, 200))
        self.assertEqual(extract_quantity("بین دویست تا چهارصد دستگاه"), (200, 400))
        self.assertEqual(extract_quantity("صد و پنجاه عدد"), (150, 150))

    def test_seller_never_classified_as_buyer(self):
        profile = ProductProfile("دستگاه اسپرسوساز")
        result = local_classify(SourceRecord("divar", "1", "فروش دستگاه اسپرسوساز با تخفیف"), profile)
        self.assertEqual(result.lead_direction, "seller")
        self.assertEqual(result.intent, "sell")

    def test_explicit_buy_intent(self):
        profile = ProductProfile("دستگاه اسپرسوساز")
        result = local_classify(SourceRecord("x", "1", "برای کافه دستگاه اسپرسوساز نیاز دارم"), profile)
        self.assertEqual(result.lead_direction, "buyer")
        self.assertEqual(result.intent, "buy")

    def test_question_is_not_automatic_buy(self):
        result = local_classify(SourceRecord("x", "1", "قیمت دستگاه اسپرسوساز چنده؟"), ProductProfile("دستگاه اسپرسوساز"))
        self.assertEqual(result.intent, "question")
        self.assertEqual(result.lead_direction, "unknown")

    def test_seller_account_with_buy_evidence_is_retained_as_mixed_intent(self):
        result = local_classify(SourceRecord("x", "1", "دستگاه اسپرسوساز می‌فروشم؛ برای کافه هم یک دستگاه می‌خرم"), ProductProfile("دستگاه اسپرسوساز"))
        self.assertEqual(result.intent, "buy")
        self.assertEqual(result.lead_direction, "buyer")
        self.assertLess(result.confidence, 0.8)

    def test_persian_quality_report_covers_at_least_thirty_examples(self):
        report = evaluate_persian_rules()
        self.assertGreaterEqual(len(PERSIAN_CASES), 30)
        self.assertEqual(report["examples"], len(PERSIAN_CASES))
        self.assertIn("precision", report["per_class"]["buy"])
        self.assertIn("recall", report["per_class"]["sell"])
        self.assertIn("macro_f1", report)

    def test_discovery_cache_round_trip_expiration_and_context(self):
        path = Path.cwd() / ".tmp-discovery-cache.sqlite3"
        profile = ProductProfile("اسپرسوساز", category="تجهیزات کافه")
        cache = DiscoveryCache(path, ttl_seconds=60)
        cache.set(profile, {"terms": ["اسپرسوساز صنعتی"]}, context={"kind": "vocabulary"})
        self.assertEqual(cache.get(profile, context={"kind": "vocabulary"}), {"terms": ["اسپرسوساز صنعتی"]})
        self.assertIsNone(cache.get(profile, context={"kind": "queries"}))
        del cache
        path.unlink()

    def test_grok_structured_result_schema_rejects_malformed_rows(self):
        valid = [{"id": "1", "intent": "buy", "lead_direction": "buyer", "confidence": 0.8}]
        enums = {"intent": {"buy", "sell"}, "lead_direction": {"buyer", "seller", "unknown"}}
        self.assertEqual(GrokAnalyzer._validate_items(valid, ["1"], ("intent", "lead_direction", "confidence"), enums), valid)
        malformed = [{"id": "1", "intent": "purchase", "lead_direction": "buyer", "confidence": 4}]
        with self.assertRaises(ValueError):
            GrokAnalyzer._validate_items(malformed, ["1"], ("intent", "lead_direction", "confidence"), enums)

    def test_rank_and_reject_seller(self):
        profile = ProductProfile("Espresso Machine", quantity=5)
        buyer = SourceRecord("x", "1", "I need to buy an Espresso Machine 5 unit", created_at=datetime.now(timezone.utc).isoformat())
        result = local_classify(buyer, profile)
        self.assertEqual(rank_analysis(result, buyer, profile).category, "HOT")
        seller = local_classify(SourceRecord("divar", "2", "Espresso Machine for sale"), profile)
        self.assertEqual(rank_analysis(seller, SourceRecord("divar", "2", "Espresso Machine for sale"), profile).category, "REJECTED")

    def test_mock_end_to_end_and_exports(self):
        cache_path = Path.cwd() / ".tmp-engine-cache.sqlite3"
        with patch.dict(os.environ, {"DISCOVERY_CACHE_PATH": str(cache_path)}):
            result = run_job(ProductProfile("دستگاه اسپرسوساز", city="تهران"), ["x", "divar"], "mock")
        self.assertIn(result.status, {"COMPLETED", "PARTIAL_SUCCESS"})
        self.assertGreater(result.buyers_found, 0)
        self.assertGreater(result.sellers_filtered, 0)
        output = Path.cwd() / ".tmp-engine-output"
        export_result(result, output)
        self.assertTrue((output / "buyers.json").exists())
        self.assertTrue((output / "buyers.csv").exists())
        self.assertTrue((output / "run_summary.json").exists())
        self.assertTrue((output / "raw" / "records.json").exists())
        self.assertTrue((output / "analyzed" / "buyers.json").exists())
        shutil.rmtree(output)
        cache_path.unlink(missing_ok=True)

    def test_job_state_persists_and_can_pause_then_resume(self):
        job_path = Path.cwd() / ".tmp-jobs.sqlite3"
        cache_path = Path.cwd() / ".tmp-jobs-cache.sqlite3"
        store = JobStore(job_path)
        profile = ProductProfile("دستگاه اسپرسوساز")
        original_search = engine_module.MockAdapter.search
        did_request_stop = False

        def search_and_request_stop(adapter, query, **kwargs):
            nonlocal did_request_stop
            records = original_search(adapter, query, **kwargs)
            if not did_request_stop:
                did_request_stop = True
                store.request_stop(job_id)
            return records

        job_id = store.create({"profile": profile.__dict__, "sources": ["x", "divar"], "mode": "mock", "max_queries": 8, "stage": "SELLER", "query_index": 0, "records": [], "vocabulary": [], "queries": []})
        with patch.dict(os.environ, {"DISCOVERY_CACHE_PATH": str(cache_path)}), patch.object(engine_module.MockAdapter, "search", search_and_request_stop):
            paused = run_job(profile, ["x", "divar"], "mock", job_id=job_id, job_store=store)
        self.assertEqual(paused.status, "PAUSED")
        persisted = store.get(job_id)
        self.assertEqual(persisted["status"], "PAUSED")
        self.assertGreater(persisted["records_collected"], 0)
        with patch.dict(os.environ, {"DISCOVERY_CACHE_PATH": str(cache_path)}):
            resumed = run_job(profile, ["x", "divar"], "mock", job_id=job_id, job_store=store)
        self.assertEqual(resumed.status, "COMPLETED")
        self.assertEqual(resumed.job_id, job_id)
        self.assertGreater(resumed.buyers_found, 0)
        del store
        job_path.unlink(missing_ok=True)
        cache_path.unlink(missing_ok=True)

    def test_job_store_lists_jobs_and_rejects_stop_after_completion(self):
        path = Path.cwd() / ".tmp-job-list.sqlite3"
        store = JobStore(path)
        job_id = store.create({"profile": {"product_name": "demo"}, "records": []})
        self.assertEqual(store.list()[0]["job_id"], job_id)
        store.update(job_id, status="COMPLETED", phase="COMPLETED")
        self.assertFalse(store.request_stop(job_id))
        del store
        path.unlink(missing_ok=True)


if __name__ == "__main__":
    unittest.main()
