from datetime import datetime, timezone
from pathlib import Path
import unittest
import shutil

from .core import ProductProfile, SourceRecord, extract_quantity, local_classify, normalize_text, rank_analysis, export_result, JobResult
from .engine import run_job


class BuyerEngineTests(unittest.TestCase):
    def test_persian_normalization_and_digits(self):
        self.assertEqual(normalize_text("مي‌خوام كالا ۲۰۰ عدد"), "می خوام کالا 200 عدد")

    def test_quantity_ranges_and_single(self):
        self.assertEqual(extract_quantity("بین ۲۰۰ تا ۴۰۰ عدد"), (200, 400))
        self.assertEqual(extract_quantity("۳ دستگاه می‌خواهم"), (3, 3))
        self.assertEqual(extract_quantity("تعداد نامشخص"), (None, None))

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

    def test_rank_and_reject_seller(self):
        profile = ProductProfile("Espresso Machine", quantity=5)
        buyer = SourceRecord("x", "1", "I need to buy an Espresso Machine 5 unit", created_at=datetime.now(timezone.utc).isoformat())
        result = local_classify(buyer, profile)
        self.assertEqual(rank_analysis(result, buyer, profile).category, "HOT")
        seller = local_classify(SourceRecord("divar", "2", "Espresso Machine for sale"), profile)
        self.assertEqual(rank_analysis(seller, SourceRecord("divar", "2", "Espresso Machine for sale"), profile).category, "REJECTED")

    def test_mock_end_to_end_and_exports(self):
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


if __name__ == "__main__":
    unittest.main()
