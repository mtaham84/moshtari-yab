import unittest
from instagram_engine.analyzer import (
    extract_caption_cta,
    is_buyer_comment,
    clean_trigger_word,
    validate_and_sanitize_dm,
)



class TestInstagramAnalyzer(unittest.TestCase):

    def test_clean_trigger_word(self):
        self.assertEqual(clean_trigger_word("«قیمت»"), "قیمت")
        self.assertEqual(clean_trigger_word('"تخفیف"'), "تخفیف")
        self.assertEqual(clean_trigger_word("سفارش! "), "سفارش")

    def test_extract_caption_cta_regex(self):
        # Case 1: کلمه X رو کامنت کنید
        cap1 = "برای دریافت لینک خرید تخفیف‌دار، کلمه «مانتو» رو کامنت کنید."
        self.assertEqual(extract_caption_cta(cap1), "مانتو")

        # Case 2: عبارت X را کامنت کنید
        cap2 = "جهت دریافت کاتالوگ و قیمت عبارت سنگ را کامنت کنید"
        self.assertEqual(extract_caption_cta(cap2), "سنگ")

        # Case 3: عدد X رو کامنت کن
        cap3 = "برای راهنمایی عدد 20 رو کامنت کن برات دایرکت بفرستم"
        self.assertEqual(extract_caption_cta(cap3), "20")

        # Case 4: کامنت کنید X
        cap4 = "سایزبندی محدوده؛ کامنت کنید ارسال تا جزئیات بیاد براتون"
        self.assertEqual(extract_caption_cta(cap4), "ارسال")

        # Case 5: No CTA
        cap5 = "یک روز خوب در کارگاه تولیدی ما با همکاران عزیز."
        self.assertIsNone(extract_caption_cta(cap5))

    def test_is_buyer_comment(self):
        # CTA Match
        matched, reason = is_buyer_comment("سلام مانتو میخواستم", cta_word="مانتو")
        self.assertTrue(matched)
        self.assertEqual(reason, "cta_match:مانتو")

        # Keyword Match
        matched, reason = is_buyer_comment("سلام قیمت این کار چنده؟")
        self.assertTrue(matched)
        self.assertTrue("keyword_match" in reason)

        # Keyword Match with 'موجوده'
        matched, reason = is_buyer_comment("سایز ۴۰ موجوده؟")
        self.assertTrue(matched)
        self.assertTrue("keyword_match" in reason)

        # Non-buyer comment
        matched, reason = is_buyer_comment("خیلی عالی بود دستتون درد نکنه")
        self.assertFalse(matched)
        self.assertIsNone(reason)

    def test_outreach_dm_guardrails(self):
        # Case 1: Clean conversational message
        clean_dm = "سلام وقت بخیر، کامنتتون رو در مورد مانتو دیدم، سایز مورد نظرتون چنده؟"
        valid, _ = validate_and_sanitize_dm(clean_dm)
        self.assertTrue(valid)

        # Case 2: Contains URL -> MUST REJECT
        url_dm = "سلام برای خرید به سایت ما https://example.com سر بزنید"
        valid, reason = validate_and_sanitize_dm(url_dm)
        self.assertFalse(valid)
        self.assertIn("URL", reason)

        # Case 3: Contains .ir domain -> MUST REJECT
        ir_dm = "سلام میتونید از myshop.ir خرید کنید"
        valid, reason = validate_and_sanitize_dm(ir_dm)
        self.assertFalse(valid)

        # Case 4: Contains @mention -> MUST REJECT
        mention_dm = "سلام لطفا پیج دوممون @shop_support رو فالو کنید"
        valid, reason = validate_and_sanitize_dm(mention_dm)
        self.assertFalse(valid)
        self.assertIn("@mention", reason)

        # Case 5: Contains Phone number -> MUST REJECT
        phone_dm = "سلام به شماره 09123456789 در واتساپ پیام بدید"
        valid, reason = validate_and_sanitize_dm(phone_dm)
        self.assertFalse(valid)
        self.assertIn("phone", reason)


if __name__ == "__main__":
    unittest.main()
