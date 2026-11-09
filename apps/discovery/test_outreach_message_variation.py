import re
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from django.test import SimpleTestCase

from apps.discovery.outreach.journey import perform_organic_warmup
from apps.discovery.outreach.messages import build_bulletproof_message

APPROVED = (
    "درود بر شما \nبنده رها هستم مشاور و پشتیبانی تخصصی دندان پزشکی ، متوجه شدم پیگیر امور زیبایی و سلامتی دندونتون هستید\n"
    "میتونید هر سوالی دارید از بنده به عنوان متخصص بپرسید."
)
EMOJI_RE = re.compile("[\U0001F300-\U0001FAFF\u2600-\u27BF]")


class StaticTemplateVariationTests(SimpleTestCase):
    def setUp(self):
        self.lead = SimpleNamespace(full_name="", username="buyer", comment_text="قیمت لمینت؟", post_caption_snippet="")

    def test_static_template_keeps_body_and_varies_greeting(self):
        body = APPROVED.split("\n", 1)[1]
        outputs = set()
        for seed in range(40):
            with patch("apps.discovery.outreach.messages.random", __import__("random").Random(seed)):
                msg = build_bulletproof_message(pitch_template=APPROVED, lead=self.lead, consultant_name="رها")
            self.assertIn(body, msg)
            self.assertEqual(len(re.findall(r"درود|سلام", msg.split("\n", 1)[0])), 1)
            self.assertNotIn("{", msg)
            self.assertIsNone(EMOJI_RE.search(msg))
            outputs.add(msg.split("\n", 1)[0])
        self.assertGreater(len(outputs), 3)

    def test_spintax_template_is_used_as_is(self):
        msg = build_bulletproof_message(pitch_template="{الف|الف} {consultant_name}", lead=self.lead, consultant_name="رها")
        self.assertTrue(msg.startswith("الف رها"))


class OrganicWarmupStoryTests(SimpleTestCase):
    @patch("apps.discovery.outreach.journey.time.sleep")
    def test_views_up_to_two_stories_then_likes_post(self, _sleep):
        cl = MagicMock()
        cl.user_stories.return_value = [SimpleNamespace(pk="11"), SimpleNamespace(pk="12"), SimpleNamespace(pk="13")]
        cl.user_medias.return_value = [SimpleNamespace(id="m1")]
        actions = perform_organic_warmup(cl, 99, "buyer")
        cl.story_seen.assert_called_once_with([11, 12])
        cl.media_like.assert_called_once_with("m1")
        self.assertEqual(actions, {"liked_post": True, "viewed_story": True})

    @patch("apps.discovery.outreach.journey.time.sleep")
    def test_story_failure_does_not_block_like(self, _sleep):
        cl = MagicMock()
        cl.user_stories.side_effect = RuntimeError("stories unavailable")
        cl.user_medias.return_value = [SimpleNamespace(id="m1")]
        actions = perform_organic_warmup(cl, 99, "buyer")
        self.assertEqual(actions, {"liked_post": True, "viewed_story": False})
