from django.test import TestCase

from apps.discovery.sources import display_link as app_display, normalize_link as app_normalize
from shared.links import display_telegram_link, normalize_telegram_link
from telegram_crawler.panel import normalize_link as crawler_normalize


class TestNormalizeLinkCanonicalization(TestCase):
    def test_canonical_equivalences(self):
        cases = [
            ("@Motor", "motor"),
            ("motor", "motor"),
            ("https://t.me/motor/", "motor"),
            ("http://t.me/motor", "motor"),
            ("t.me/Motor", "motor"),
            ("telegram.me/motor", "motor"),
            ("  https://www.t.me/My_Channel  ", "my_channel"),
        ]
        for raw, expected in cases:
            self.assertEqual(normalize_telegram_link(raw), expected)
            # Equivalence across both callers
            self.assertEqual(app_normalize(raw), expected)
            self.assertEqual(crawler_normalize(raw), expected)

    def test_invite_links_preserve_casing(self):
        invite_plus = "https://t.me/+AbCdEf_123"
        self.assertEqual(normalize_telegram_link(invite_plus), "+AbCdEf_123")
        self.assertEqual(app_normalize(invite_plus), "+AbCdEf_123")
        self.assertEqual(crawler_normalize(invite_plus), "+AbCdEf_123")

        invite_join = "t.me/joinchat/XyZ_987"
        self.assertEqual(normalize_telegram_link(invite_join), "joinchat/XyZ_987")
        self.assertEqual(app_normalize(invite_join), "joinchat/XyZ_987")
        self.assertEqual(crawler_normalize(invite_join), "joinchat/XyZ_987")

    def test_empty_and_whitespace(self):
        self.assertEqual(normalize_telegram_link(None), "")
        self.assertEqual(normalize_telegram_link(""), "")
        self.assertEqual(normalize_telegram_link("   "), "")

    def test_display_link_formatting(self):
        self.assertEqual(display_telegram_link("@test"), "@test")
        self.assertEqual(display_telegram_link("https://t.me/test"), "@test")
        self.assertEqual(display_telegram_link("https://t.me/+InviteKey"), "https://t.me/+InviteKey")
        self.assertEqual(display_telegram_link("joinchat/InviteKey"), "https://t.me/joinchat/InviteKey")
        self.assertEqual(display_telegram_link(""), "")
        self.assertEqual(app_display("https://t.me/test"), "@test")
