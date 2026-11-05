import re
from pathlib import Path

from django.test import SimpleTestCase

ROOT = Path(__file__).resolve().parents[2]


class NoRemovedDjangoUtcAliasTests(SimpleTestCase):
    """django.utils.timezone.utc was removed in Django 5; lint can't catch it, so guard the source."""

    def test_no_django_timezone_utc_usage(self):
        offenders = []
        for path in (ROOT / "apps").rglob("*.py"):
            text = path.read_text(encoding="utf-8")
            names = set(re.findall(r"^\s*from django\.utils import timezone as (\w+)", text, re.M))
            datetime_tz = re.search(r"^\s*from datetime import [^\n]*\btimezone\b", text, re.M)
            if re.search(r"^\s*from django\.utils import timezone\s*$", text, re.M) and not datetime_tz:
                names.add("timezone")
            for name in names:
                if re.search(rf"\b{name}\.utc\b", text):
                    offenders.append(f"{path.relative_to(ROOT)}: {name}.utc")
        self.assertEqual(offenders, [])
