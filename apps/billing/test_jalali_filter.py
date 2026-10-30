from datetime import date, datetime, timezone

from django.test import SimpleTestCase

from apps.billing.templatetags.ops import jalali_dt


class JalaliFilterTests(SimpleTestCase):
    def test_datetime_iso_string_and_date(self):
        stamp = datetime(2026, 10, 29, 20, 30, tzinfo=timezone.utc)          # 00:00 next day in Tehran (UTC+3:30)
        self.assertEqual(jalali_dt(stamp), "۱۴۰۵/۰۸/۰۸ ساعت ۰۰:۰۰")
        self.assertEqual(jalali_dt("2026-10-29T20:30:00+00:00"), "۱۴۰۵/۰۸/۰۸ ساعت ۰۰:۰۰")
        self.assertEqual(jalali_dt(date(2026, 10, 29)), "۱۴۰۵/۰۸/۰۷")
        self.assertEqual(jalali_dt("2026-10-29"), "۱۴۰۵/۰۸/۰۷")
        self.assertEqual(jalali_dt(None), "—")

    def test_no_gregorian_digits_in_output(self):
        out = jalali_dt(datetime(2026, 1, 5, 9, 7, tzinfo=timezone.utc))
        self.assertFalse(any(ch in out for ch in "0123456789"))
