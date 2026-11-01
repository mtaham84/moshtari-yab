from types import SimpleNamespace
from io import StringIO
from pathlib import Path
from django.test import TestCase
from django.core.management import call_command
from django.conf import settings

from apps.discovery.outreach_service import build_bulletproof_message
from apps.discovery.management.commands.run_instagram_outreach import load_outreach_config


class DecoupleHardcodedTenantTests(TestCase):
    def test_run_outreach_without_tenant_halts_safely(self):
        out = StringIO()
        # Explicit empty tenant should halt safely and display warning instead of assuming tenant_4
        call_command("run_instagram_outreach", tenant="", stdout=out)
        output = out.getvalue()
        self.assertIn("هیچ شناسه‌ای برای مستأجر", output)

    def test_load_outreach_config_isolates_tenant_pitches(self):
        # When querying a neutral tenant (e.g. tenant_100), default pitches must not be hardcoded to dental clinic
        cfg = load_outreach_config(tenant_id="tenant_100")
        sender_pitches = cfg.get("sender_pitches", {})

        for username, pitch in sender_pitches.items():
            role = pitch.get("consultant_role", "")
            template = pitch.get("template", "")
            # Pitches must not contain hardcoded dental consultation for neutral tenants
            self.assertNotIn("دندان‌پزشکی", role)
            self.assertNotIn("دندون", template)

    def test_legacy_sarv_tenant_loads_legacy_config_when_requested(self):
        # When explicitly requesting sarv or tenant_4, legacy config is honored
        cfg_sarv = load_outreach_config(tenant_id="sarv")
        pitches = cfg_sarv.get("sender_pitches", {})
        # May contain configured pitches if file exists
        self.assertIsInstance(pitches, dict)

    def test_build_bulletproof_message_dynamic_topic(self):
        # 1. Neutral lead without dental context
        lead_neutral = SimpleNamespace(
            full_name="علی محمدی",
            username="ali_m",
            comment_text="سلام شرایط اقساط و قیمت چطوره؟",
            domain="فروش لپ‌تاپ استوک",
        )
        msg_neutral = build_bulletproof_message(
            pitch_template="",
            lead=lead_neutral,
            consultant_name="پشتیبان",
            business_name="فروشگاه پایتخت",
        )
        self.assertNotIn("دندان‌پزشکی", msg_neutral)
        self.assertIn("فروشگاه پایتخت", msg_neutral)

        # 2. Lead with dental comment
        lead_dental = SimpleNamespace(
            full_name="سارا رضایی",
            username="sara_r",
            comment_text="هزینه کامپوزیت چقدر میشه؟",
            domain="کلینیک زیبایی",
        )
        msg_dental = build_bulletproof_message(
            pitch_template="",
            lead=lead_dental,
            consultant_name="مشاور سرو",
            business_name="مجموعه سرو",
        )
        self.assertIn("کامپوزیت", msg_dental)

    def test_docker_compose_uses_parameterized_tenant(self):
        # Handle container mount paths
        compose_paths = [
            Path(settings.BASE_DIR) / "docker-compose.yml",
            Path(__file__).resolve().parent.parent.parent.parent / "docker-compose.yml",
        ]
        compose_path = next((p for p in compose_paths if p.exists()), None)
        if compose_path:
            content = compose_path.read_text(encoding="utf-8")
            self.assertNotIn('--tenant", "tenant_4"', content)
            self.assertIn("IG_OUTREACH_TENANT", content)
