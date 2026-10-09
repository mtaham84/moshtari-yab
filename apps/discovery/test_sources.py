import tempfile
from io import StringIO

from django.core.management import call_command
from django.db import IntegrityError, transaction
from django.test import TestCase

from telegram_crawler.panel import normalize_link as crawler_normalize

from .models import MonitoredCommunity
from .sources import GLOBAL, PRIVATE, normalize_link, sync_error_message
from .tests import seller


class SourceModelTests(TestCase):
    def setUp(self):
        _, self.biz = seller("a@example.com", "فروشگاه الف")

    def test_scope_follows_owner_and_link_is_normalized(self):
        g = MonitoredCommunity.objects.create(business=None, handle_or_link="https://t.me/Moto_Group/")
        p = MonitoredCommunity.objects.create(business=self.biz, handle_or_link="moto_group")
        inv = MonitoredCommunity.objects.create(business=self.biz, handle_or_link="t.me/+AbCdEf")
        self.assertEqual((g.scope, g.handle_or_link, g.normalized_link), (GLOBAL, "@moto_group", "moto_group"))
        self.assertEqual((p.scope, p.normalized_link), (PRIVATE, "moto_group"))
        self.assertEqual(inv.handle_or_link, "https://t.me/+AbCdEf")
        p.business = None
        p.save(update_fields=["business"])
        p.refresh_from_db()
        self.assertEqual(p.scope, GLOBAL)

    def test_database_rejects_inconsistent_scope(self):
        c = MonitoredCommunity.objects.create(business=self.biz, handle_or_link="@x_group")
        with self.assertRaises(IntegrityError), transaction.atomic():
            MonitoredCommunity.objects.filter(id=c.id).update(scope=GLOBAL)

    def test_normalization_matches_the_crawler(self):
        for link in ["@Moto", "moto", "https://t.me/moto/", "t.me/Moto", "telegram.me/moto", "https://t.me/+AbCd", "t.me/joinchat/XyZ"]:
            self.assertEqual(normalize_link(link), crawler_normalize(link), link)

    def test_error_codes_are_shown_in_persian(self):
        self.assertIn("۲", sync_error_message("FLOOD_WAIT:120").replace("2", "۲"))
        self.assertIn("وجود ندارد", sync_error_message("INVALID_LINK"))
        self.assertEqual(sync_error_message("No user has \"x\" as username"), sync_error_message("UNKNOWN:x"))
        self.assertEqual(sync_error_message(""), "")

    def test_seed_global_sources(self):
        with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False, encoding="utf-8") as f:
            f.write("# comment\n@moto_iran  موتورسواران ایران\nhttps://t.me/moto_iran/\nt.me/bikers\n")
        MonitoredCommunity.objects.create(business=None, handle_or_link="@old_group")
        out = StringIO()
        call_command("seed_global_sources", f.name, "--deactivate-missing", stdout=out)
        self.assertIn("2 created", out.getvalue())
        globals_ = {c.normalized_link: c for c in MonitoredCommunity.objects.filter(scope=GLOBAL)}
        self.assertEqual(globals_["moto_iran"].name, "موتورسواران ایران")
        self.assertTrue(globals_["bikers"].is_active)
        self.assertFalse(globals_["old_group"].is_active)
        call_command("seed_global_sources", f.name, stdout=StringIO())             # idempotent
        self.assertEqual(MonitoredCommunity.objects.filter(scope=GLOBAL).count(), 3)
