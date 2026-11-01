import re
from pathlib import Path
from django.conf import settings
from django.test import SimpleTestCase


class SecureDOMTemplateTests(SimpleTestCase):
    """
    Tests ensuring templates eliminate dangerous innerHTML sinks and inline onclick handlers
    with raw user/model/LLM data (Finding R29).
    """

    def setUp(self):
        self.templates_dir = Path(settings.BASE_DIR) / "templates"

    def test_ops_manage_dashboard_pitch_rendering_is_safe(self):
        """
        Ops dashboard must NOT inject p.text or p.title into raw innerHTML or inline template-literal onclick.
        """
        template_file = self.templates_dir / "ops" / "manage_dashboard.html"
        content = template_file.read_text(encoding="utf-8")

        # Must not contain dangerous inline onclick with template literals
        self.assertNotIn(
            "onclick=\"document.getElementById('adm-pitch-text').value",
            content,
            "Ops dashboard contains dangerous inline onclick for AI pitch insertion."
        )

        # Must use textContent for title and text
        self.assertIn("titleEl.textContent = p.title", content)
        self.assertIn("textEl.textContent = p.text", content)
        self.assertIn("addEventListener('click'", content)

    def test_accounts_outreach_pitch_rendering_is_safe(self):
        """
        Outreach template must NOT inject unescaped AI title/badge into innerHTML or use inline onclick.
        """
        template_file = self.templates_dir / "accounts" / "outreach.html"
        content = template_file.read_text(encoding="utf-8")

        # Must not have inline onclick for useAiPitch
        self.assertNotIn(
            "onclick=\"useAiPitch(",
            content,
            "Outreach template contains inline onclick for useAiPitch."
        )

        # Must use DOM elements with textContent
        self.assertIn("titleStrong.textContent =", content)
        self.assertIn("badgeSpan.textContent =", content)
        self.assertIn("textDiv.textContent =", content)
        self.assertIn("actionBtn.addEventListener('click'", content)

    def test_accounts_dashboard_analytics_table_escapes_data(self):
        """
        Dashboard analytics table must escape all dynamic fields (name, category, date, url).
        """
        template_file = self.templates_dir / "accounts" / "dashboard.html"
        content = template_file.read_text(encoding="utf-8")

        # escapeHtml helper must be defined
        self.assertIn("function escapeHtml(str)", content)

        # Tr innerHTML must use sanitized / escaped variables
        self.assertIn("safeName", content)
        self.assertIn("safeCategory", content)
        self.assertIn("safeImgUrl", content)
        self.assertIn("<span>${safeName}</span>", content)
        self.assertNotIn("<span>${p.name}</span>", content)

    def test_public_product_card_escapes_order_response_data(self):
        """
        Public product card checkout modal must escape customer_name and tracking_code.
        """
        template_file = self.templates_dir / "products" / "public_product_card.html"
        if not template_file.exists():
            return
        content = template_file.read_text(encoding="utf-8")

        self.assertIn("escapeHtml(data.customer_name)", content)
        self.assertIn("escapeHtml(data.tracking_code)", content)
        self.assertNotIn("<strong>${data.customer_name}</strong>", content)

    def test_discovery_feedback_toast_uses_dom_text_content(self):
        """
        Discovery feedback toasts must use textContent rather than raw innerHTML string interpolation.
        """
        list_file = self.templates_dir / "discovery" / "opportunities_list.html"
        list_content = list_file.read_text(encoding="utf-8")
        self.assertIn("msgSpan.textContent = msg;", list_content)
        self.assertNotIn("toast.innerHTML = `<span>✓</span> <span>${msg}</span>`", list_content)

        detail_file = self.templates_dir / "discovery" / "opportunity_detail.html"
        detail_content = detail_file.read_text(encoding="utf-8")
        self.assertIn("badge.textContent = data.status_display", detail_content)
        self.assertNotIn("statusBadge.innerHTML = '<span class=\"p-badge\"", detail_content)
