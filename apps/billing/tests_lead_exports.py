import csv
import io
import json

from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse

from apps.discovery.exports import safe_csv_cell
from apps.discovery.models import InstagramEngineLead


class LeadExportTests(TestCase):
    """ADM-13: admin exports share one row mapping and are formula-safe."""

    def setUp(self):
        self.user = User.objects.create_superuser(username="ops", email="ops@test.com", password="x")
        self.client.force_login(self.user)
        InstagramEngineLead.objects.create(tenant_id="tenant_1", username="evil", ig_user_pk="1",
                                           comment_text='=HYPERLINK("http://x","click")', status="NEW")

    def test_csv_neutralises_formulas_and_has_one_bom(self):
        res = self.client.get(reverse("manage:leads_export_csv"))
        self.assertEqual(res.status_code, 200)
        raw = b"".join(res.streaming_content).decode("utf-8")
        self.assertTrue(raw.startswith("\ufeff"))
        self.assertFalse(raw.startswith("\ufeff\ufeff"))
        rows = list(csv.reader(io.StringIO(raw[1:])))
        self.assertEqual(rows[1][4], '\'=HYPERLINK("http://x","click")')

    def test_json_keeps_raw_values(self):
        res = self.client.get(reverse("manage:leads_export_json"))
        data = json.loads(res.content)
        self.assertEqual(data[0]["comment_text"], '=HYPERLINK("http://x","click")')
        self.assertNotIn("status_display", data[0])

    def test_safe_csv_cell(self):
        self.assertEqual(safe_csv_cell("-"), "-")
        self.assertEqual(safe_csv_cell("+98"), "'+98")
        self.assertEqual(safe_csv_cell(None), "")
        self.assertEqual(safe_csv_cell(12), "12")
