from django.test import TestCase

from apps.core.form_helpers import parse_bounded_int, parse_form_list
from apps.discovery.views import _instagram_list
from apps.products.views import _form_list, _form_list_int


class TestFormHelpers(TestCase):
    def test_parse_form_list_empty_and_none(self):
        self.assertEqual(parse_form_list(None), [])
        self.assertEqual(parse_form_list(""), [])
        self.assertEqual(parse_form_list("   \n\r\t  "), [])

    def test_parse_form_list_multiline_and_mixed_separators(self):
        raw = "laptop\nموبایل، تبلت; ساعت هوشمند,هدفون\r\nاسپیکر"
        res = parse_form_list(raw)
        self.assertEqual(res, ["laptop", "موبایل", "تبلت", "ساعت هوشمند", "هدفون", "اسپیکر"])

    def test_parse_form_list_hashtags_and_deduplication(self):
        raw = "#Tehran, #iran, tehran, #IRAN, #Tabriz"
        res = parse_form_list(raw, hashtags=True)
        # Should strip '#' and deduplicate case-insensitively
        self.assertEqual(res, ["Tehran", "iran", "Tabriz"])

    def test_parse_form_list_limits(self):
        raw = "item1, item2, item3, item4, item5"
        res = parse_form_list(raw, max_items=3)
        self.assertEqual(len(res), 3)
        self.assertEqual(res, ["item1", "item2", "item3"])

        long_item = "a" * 150
        res_long = parse_form_list(long_item, max_item_length=10)
        self.assertEqual(res_long, ["a" * 10])

    def test_parse_bounded_int_valid_and_bounds(self):
        self.assertEqual(parse_bounded_int("25", default=10, minimum=1, maximum=100), 25)
        # Lower bound clamp
        self.assertEqual(parse_bounded_int("-5", default=10, minimum=1, maximum=100), 1)
        # Upper bound clamp
        self.assertEqual(parse_bounded_int("500", default=10, minimum=1, maximum=100), 100)

    def test_parse_bounded_int_invalid_inputs(self):
        self.assertEqual(parse_bounded_int(None, default=10, minimum=1, maximum=100), 10)
        self.assertEqual(parse_bounded_int("", default=10, minimum=1, maximum=100), 10)
        self.assertEqual(parse_bounded_int("invalid_num", default=10, minimum=1, maximum=100), 10)
        self.assertEqual(parse_bounded_int("12.5", default=10, minimum=1, maximum=100), 10)

    def test_backwards_compatibility_smoke(self):
        # Smoke test caller imports and aliases
        prod_list = _form_list("کفش, لباس\nشلوار")
        self.assertEqual(prod_list, ["کفش", "لباس", "شلوار"])

        prod_int = _form_list_int("150", 10, 1, 100)
        self.assertEqual(prod_int, 100)

        ig_list = _instagram_list("#summer, #SUMMER, #winter", hashtags=True)
        self.assertEqual(ig_list, ["summer", "winter"])
