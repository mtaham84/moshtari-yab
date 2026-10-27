from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from unittest import IsolatedAsyncioTestCase
from unittest.mock import AsyncMock

from django.contrib.auth import get_user_model
from django.db import close_old_connections, connections
from django.test import Client, SimpleTestCase, TestCase, TransactionTestCase, override_settings
from django.template.loader import render_to_string
from django.urls import reverse
from django.utils import timezone
from telethon import errors, types

from apps.businesses.models import Business
from apps.discovery.models import AIAnalysis, Customer, Opportunity, TelegramOutboundMessage
from apps.discovery.retention import scrub_telegram_outbox
from apps.discovery.telegram_outbox import OutboxError, claim_next, enqueue, finish
from telegram_crawler.outreach import deliver


class OutboxFixture:
    def setUp(self):
        self.user = get_user_model().objects.create_user(username="sender", password="test-password")
        self.business = Business.objects.create(user=self.user, name="seller", business_domain="test")
        self.customer = Customer.objects.create(
            business=self.business, external_user_id="telegram_11", source_username="buyer",
        )
        self.opportunity = Opportunity.objects.create(
            business=self.business, customer=self.customer, source_raw_message="نیاز دارم",
        )
        self.url = reverse("discovery:telegram_message", args=[self.opportunity.pk])

    def queue(self, text="سلام، این متن تأیید شده است"):
        return enqueue(self.opportunity.pk, self.business, self.user, text)


@override_settings(TELEGRAM_OUTREACH_ENABLED=True)
class OutboxTests(OutboxFixture, TestCase):
    def test_manual_post_snapshots_exact_approved_text(self):
        self.client.force_login(self.user)
        response = self.client.post(self.url, {"text": "متن ویرایش‌شده", "recipient_user_id": "99"})
        self.assertEqual(response.status_code, 202)
        job = TelegramOutboundMessage.objects.get()
        self.assertEqual(job.body, "متن ویرایش‌شده")
        self.assertEqual(job.recipient_user_id, 11)
        self.assertEqual(job.requested_by, self.user)
        self.opportunity.refresh_from_db()
        self.assertEqual(self.opportunity.status, "NEW")

    def test_get_does_not_enqueue(self):
        self.client.force_login(self.user)
        self.assertEqual(self.client.get(self.url).json()["state"], "READY")
        self.assertFalse(TelegramOutboundMessage.objects.exists())

    def test_detail_and_list_render_button_beneath_draft(self):
        AIAnalysis.objects.create(opportunity=self.opportunity, need="نیاز", why_selected="مرتبط",
                                  suggested_reply="متن پیشنهادی")
        self.client.force_login(self.user)
        detail = self.client.get(reverse("discovery:opportunity_detail", args=[self.opportunity.pk]))
        self.assertContains(detail, 'data-text-target="suggested-reply-box"')
        listing = self.client.get(reverse("discovery:opportunity_list"))
        self.assertContains(listing, f'data-text-target="reply-text-{self.opportunity.pk}"')
        self.assertFalse(TelegramOutboundMessage.objects.exists())

    def test_anonymous_request_cannot_enqueue(self):
        self.assertEqual(self.client.post(self.url, {"text": "x"}).status_code, 302)
        self.assertFalse(TelegramOutboundMessage.objects.exists())

    def test_other_seller_cannot_read_or_send(self):
        other = get_user_model().objects.create_user(username="other")
        Business.objects.create(user=other, name="other", business_domain="test")
        self.client.force_login(other)
        self.assertEqual(self.client.get(self.url).status_code, 404)
        self.assertEqual(self.client.post(self.url, {"text": "x"}).status_code, 404)

    def test_user_without_business_cannot_send(self):
        other = get_user_model().objects.create_user(username="no-business")
        self.client.force_login(other)
        self.assertEqual(self.client.post(self.url, {"text": "x"}).status_code, 404)

    def test_csrf_is_required(self):
        client = Client(enforce_csrf_checks=True)
        client.force_login(self.user)
        self.assertEqual(client.post(self.url, {"text": "x"}).status_code, 403)

    def test_put_is_not_allowed(self):
        self.client.force_login(self.user)
        self.assertEqual(self.client.put(self.url).status_code, 405)

    def test_duplicate_post_preserves_first_approved_message(self):
        job, created = self.queue("first")
        again, second_created = self.queue("different")
        self.assertTrue(created)
        self.assertFalse(second_created)
        self.assertEqual(again.pk, job.pk)
        self.assertEqual(again.body, "first")
        self.assertEqual(TelegramOutboundMessage.objects.count(), 1)

    def test_sent_message_cannot_be_queued_again(self):
        job, _ = self.queue()
        finish(job.pk, "SENT", message_id=123)
        again, created = self.queue()
        self.assertFalse(created)
        self.assertEqual(again.status, "SENT")

    def test_empty_and_oversized_body_rejected(self):
        for body in [" ", "a" * 4097, "😀" * 2049]:
            with self.subTest(body_length=len(body)), self.assertRaises(OutboxError):
                self.queue(body)
        self.assertFalse(TelegramOutboundMessage.objects.exists())

    def test_maximum_body_allowed(self):
        job, _ = self.queue("a" * 4096)
        self.assertEqual(len(job.body), 4096)

    def test_non_telegram_opportunity_rejected(self):
        self.opportunity.source_platform = "x"
        self.opportunity.save()
        with self.assertRaises(OutboxError):
            self.queue()

    def test_invalid_customer_identity_rejected(self):
        for value in ["", "telegram_-100123", "telegram_abc", "https://t.me/buyer"]:
            self.customer.external_user_id = value
            self.customer.save()
            with self.subTest(value=value), self.assertRaises(OutboxError):
                self.queue()

    def test_closed_or_expired_opportunity_rejected(self):
        for status in ["EXPIRED", "RESOLVED", "REJECTED", "CONVERTED"]:
            self.opportunity.status = status
            self.opportunity.save()
            with self.subTest(status=status), self.assertRaises(OutboxError):
                self.queue()
        self.opportunity.status = "NEW"
        self.opportunity.expires_at = timezone.now() - timedelta(seconds=1)
        self.opportunity.save()
        with self.assertRaises(OutboxError):
            self.queue()

    def test_scrubbed_opportunity_rejected(self):
        self.opportunity.trace_metadata = {"retention_scrubbed": True}
        self.opportunity.save()
        with self.assertRaises(OutboxError):
            self.queue()

    def test_claim_and_success_update_only_after_send(self):
        job, _ = self.queue()
        payload = claim_next()
        self.assertEqual(payload["id"], job.pk)
        self.assertEqual(payload["random_id"], job.random_id)
        self.assertIsNone(claim_next())
        finish(job.pk, "SENT", message_id=123)
        job.refresh_from_db()
        self.opportunity.refresh_from_db()
        self.assertEqual(job.telegram_message_id, 123)
        self.assertIsNotNone(job.sent_at)
        self.assertEqual(self.opportunity.status, "CONTACTED")

    def test_closed_opportunity_is_cancelled_before_send(self):
        job, _ = self.queue()
        self.opportunity.status = "RESOLVED"
        self.opportunity.save()
        self.assertIsNone(claim_next())
        job.refresh_from_db()
        self.assertEqual(job.status, "FAILED")

    def test_changed_recipient_is_cancelled_before_send(self):
        job, _ = self.queue()
        self.customer.external_user_id = "telegram_99"
        self.customer.save()
        self.assertIsNone(claim_next())
        job.refresh_from_db()
        self.assertEqual(job.error_code, "STALE_OPPORTUNITY")

    def test_flood_wait_is_durable(self):
        job, _ = self.queue()
        claim_next()
        finish(job.pk, "PENDING", "RATE_LIMIT", wait=300)
        self.assertIsNone(claim_next())
        job.refresh_from_db()
        self.assertEqual(job.status, "PENDING")
        self.assertGreater(job.next_attempt_at, timezone.now())

    def test_interrupted_send_is_not_retried(self):
        job, _ = self.queue()
        TelegramOutboundMessage.objects.filter(pk=job.pk).update(
            status="SENDING", updated_at=timezone.now() - timedelta(minutes=11),
        )
        self.assertIsNone(claim_next())
        job.refresh_from_db()
        self.assertEqual(job.status, "UNKNOWN")

    def test_retention_removes_text_and_identity_but_keeps_tombstone(self):
        job, _ = self.queue()
        scrub_telegram_outbox([self.opportunity.pk])
        job.refresh_from_db()
        self.assertEqual((job.body, job.recipient_username, job.recipient_user_id), ("", "", 0))
        self.assertEqual(job.status, "FAILED")
        self.assertIsNone(claim_next())
        again, created = self.queue()
        self.assertFalse(created)
        self.assertEqual(job.pk, again.pk)

    def test_engine_retention_tombstone_scrubs_outbox(self):
        from apps.discovery.engine_bridge import scrub_opportunity
        self.opportunity.engine_opportunity_id = "need_1"
        self.opportunity.save()
        job, _ = self.queue()
        scrub_opportunity({"opportunity_id": "need_1"})
        job.refresh_from_db()
        self.assertEqual(job.body, "")
        self.assertEqual(job.status, "FAILED")

    def test_panel_retention_scrubs_outbox(self):
        from telegram_crawler.retention import scrub_panel_opportunities
        job, _ = self.queue()
        scrub_panel_opportunities(timezone.now() + timedelta(days=1), delete=True)
        job.refresh_from_db()
        self.assertEqual(job.body, "")
        self.assertEqual(job.recipient_user_id, 0)
        self.assertIsNone(claim_next())

    @override_settings(TELEGRAM_OUTREACH_ENABLED=False)
    def test_disabled_sender_is_fail_closed(self):
        self.client.force_login(self.user)
        self.assertEqual(self.client.get(self.url).json()["state"], "DISABLED")
        self.assertEqual(self.client.post(self.url, {"text": "x"}).status_code, 403)
        self.assertFalse(TelegramOutboundMessage.objects.exists())


class ConcurrentOutboxTests(OutboxFixture, TransactionTestCase):
    def test_concurrent_clicks_create_only_one_message(self):
        def click(_):
            close_old_connections()
            try:
                return self.queue()[1]
            finally:
                connections.close_all()
        with ThreadPoolExecutor(max_workers=2) as pool:
            created = list(pool.map(click, range(2)))
        self.assertEqual(sorted(created), [False, True])
        self.assertEqual(TelegramOutboundMessage.objects.count(), 1)


class ButtonTemplateTests(SimpleTestCase):
    def test_button_present_only_for_telegram_and_text_escaped(self):
        for platform in ["telegram", "x", "instagram"]:
            html = render_to_string("discovery/partials/telegram_send_button.html", {
                "opportunity": {"id": 1, "source_platform": platform},
                "text_target": "suggested-reply-box", "csrf_token": "test",
            })
            self.assertEqual("telegram-send-button" in html, platform == "telegram")


class DeliveryTests(IsolatedAsyncioTestCase):
    def setUp(self):
        self.client = AsyncMock()
        self.client.get_entity.return_value = types.User(id=11, first_name="buyer")
        self.client.get_input_entity.return_value = types.InputPeerUser(11, 456)
        self.client.return_value = types.UpdateShortSentMessage(
            id=123, pts=1, pts_count=1, date=timezone.now(), out=True,
        )
        self.finish = AsyncMock()
        self.job = {
            "id": 7, "body": "<b>متن تأییدشده</b>",
            "recipient_user_id": 11, "recipient_username": "buyer", "random_id": 999,
        }

    async def test_send_uses_exact_text_and_persisted_random_id(self):
        await deliver(self.client, self.job, self.finish)
        request = self.client.call_args.args[0]
        self.assertEqual(request.message, self.job["body"])
        self.assertEqual(request.random_id, 999)
        self.assertTrue(request.no_webpage)
        self.assertIsNone(request.entities)
        self.finish.assert_awaited_once_with(7, "SENT", message_id=123)

    async def test_username_owner_mismatch_is_never_messaged(self):
        self.client.get_entity.side_effect = [ValueError(), types.User(id=99)]
        await deliver(self.client, self.job, self.finish)
        self.client.assert_not_awaited()
        self.finish.assert_awaited_once_with(7, "FAILED", "PEER_UNAVAILABLE")

    async def test_unknown_peer_does_not_fall_back_to_arbitrary_recipient(self):
        self.client.get_entity.side_effect = ValueError()
        self.job["recipient_username"] = ""
        await deliver(self.client, self.job, self.finish)
        self.client.assert_not_awaited()

    async def test_bots_and_groups_are_rejected(self):
        for entity in [types.User(id=11, bot=True), types.ChatEmpty(id=11)]:
            self.client.get_entity.return_value = entity
            await deliver(self.client, self.job, self.finish)
        self.client.assert_not_awaited()

    async def test_flood_wait_requeues_and_pauses_sender(self):
        self.client.side_effect = errors.FloodWaitError(request=None, capture=7)
        wait = await deliver(self.client, self.job, self.finish)
        self.assertEqual(wait, 8)
        self.finish.assert_awaited_once_with(7, "PENDING", "RATE_LIMIT", wait=8)

    async def test_resolution_flood_wait_also_requeues(self):
        self.client.get_entity.side_effect = errors.FloodWaitError(request=None, capture=7)
        await deliver(self.client, self.job, self.finish)
        self.finish.assert_awaited_once_with(7, "PENDING", "RATE_LIMIT", wait=8)

    async def test_privacy_rejection_is_not_retried(self):
        self.client.side_effect = errors.UserPrivacyRestrictedError(request=None)
        await deliver(self.client, self.job, self.finish)
        self.finish.assert_awaited_once_with(7, "FAILED", "PRIVACY")

    async def test_peer_flood_pauses_shared_sender(self):
        self.client.side_effect = errors.PeerFloodError(request=None)
        self.assertEqual(await deliver(self.client, self.job, self.finish), 3600)
        self.finish.assert_awaited_once_with(7, "FAILED", "ACCOUNT_LIMITED")

    async def test_timeout_has_unknown_outcome_no_retry(self):
        self.client.side_effect = TimeoutError()
        await deliver(self.client, self.job, self.finish)
        self.client.assert_awaited_once()
        self.finish.assert_awaited_once_with(7, "UNKNOWN", "NETWORK_UNKNOWN")

    async def test_missing_ack_is_unknown_not_success(self):
        self.client.return_value = types.Updates(updates=[], users=[], chats=[], date=timezone.now(), seq=0)
        await deliver(self.client, self.job, self.finish)
        self.finish.assert_awaited_once_with(7, "UNKNOWN", "NETWORK_UNKNOWN")

    async def test_updates_ack_matches_persisted_random_id(self):
        self.client.return_value = types.Updates(
            updates=[types.UpdateMessageID(id=123, random_id=999)],
            users=[], chats=[], date=timezone.now(), seq=0,
        )
        await deliver(self.client, self.job, self.finish)
        self.finish.assert_awaited_once_with(7, "SENT", message_id=123)