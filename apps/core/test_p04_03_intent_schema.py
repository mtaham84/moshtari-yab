from datetime import datetime, timezone, timedelta
from django.test import TransactionTestCase
from django.db import IntegrityError, transaction
from apps.discovery.models import InstagramEngineAccount, InstagramEngineLead, OutreachDispatchIntent
from apps.discovery.outreach_contract import OutreachStateMachine, OutreachStatus


class OutreachDispatchIntentSchemaTestCase(TransactionTestCase):
    def setUp(self):
        self.tenant_id = "tenant_schema_p04"
        self.sender = InstagramEngineAccount.objects.create(
            tenant_id=self.tenant_id,
            username="sender.schema",
            role="SENDER",
            status="ACTIVE",
        )
        self.lead = InstagramEngineLead.objects.create(
            tenant_id=self.tenant_id,
            ig_user_pk="ig_pk_5555",
            username="schema_lead",
            comment_text="تست مدل قصد ارسال",
            intent_score=0.95,
            status="NEW",
        )

    def test_outreach_dispatch_intent_creation_and_defaults(self):
        """OutreachDispatchIntent initializes with UUID, generation=1, attempt=0, and PENDING status."""
        intent = OutreachDispatchIntent.objects.create(
            tenant_id=self.tenant_id,
            lead=self.lead,
            sender=self.sender,
            sender_username=self.sender.username,
            recipient_identifier=self.lead.username,
            message_type="initial",
            message_text="پیام تست برای اسکیما",
        )
        self.assertIsNotNone(intent.intent_id)
        self.assertEqual(intent.status, "PENDING")
        self.assertEqual(intent.generation, 1)
        self.assertEqual(intent.attempt_count, 0)
        self.assertIsNone(intent.worker_id)
        self.assertIsNone(intent.lease_expires_at)

    def test_active_intent_uniqueness_constraint_prevents_duplicate_active_intents(self):
        """Database constraint uq_active_intent_per_lead_type forbids two active intents for same lead & type."""
        OutreachDispatchIntent.objects.create(
            tenant_id=self.tenant_id,
            lead=self.lead,
            sender=self.sender,
            sender_username=self.sender.username,
            recipient_identifier=self.lead.username,
            message_type="initial",
            message_text="پیام اول",
            status="PENDING",
        )

        # Attempt to create duplicate active initial intent for same lead
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                OutreachDispatchIntent.objects.create(
                    tenant_id=self.tenant_id,
                    lead=self.lead,
                    sender=self.sender,
                    sender_username=self.sender.username,
                    recipient_identifier=self.lead.username,
                    message_type="initial",
                    message_text="پیام تکراری همزمان",
                    status="CLAIMED",
                )

    def test_distinct_message_type_is_permitted_concurrently(self):
        """Initial and followup message types can coexist without constraint collision."""
        init_intent = OutreachDispatchIntent.objects.create(
            tenant_id=self.tenant_id,
            lead=self.lead,
            message_type="initial",
            message_text="پیام اول",
            status="PENDING",
        )
        followup_intent = OutreachDispatchIntent.objects.create(
            tenant_id=self.tenant_id,
            lead=self.lead,
            message_type="followup",
            message_text="پیام پیگیری",
            status="PENDING",
        )
        self.assertNotEqual(init_intent.id, followup_intent.id)

    def test_terminal_intent_allows_subsequent_intent_creation(self):
        """Once an intent reaches SENT or FAILED, a new intent for the same lead and type is allowed."""
        intent1 = OutreachDispatchIntent.objects.create(
            tenant_id=self.tenant_id,
            lead=self.lead,
            message_type="initial",
            message_text="پیام ناموفق اول",
            status="FAILED",
            error_code="RATE_LIMIT",
        )
        # Creating a second intent after terminal status is permitted
        intent2 = OutreachDispatchIntent.objects.create(
            tenant_id=self.tenant_id,
            lead=self.lead,
            message_type="initial",
            message_text="پیام تلاش دوباره",
            status="PENDING",
        )
        self.assertNotEqual(intent1.id, intent2.id)

    def test_contract_sync_round_trip(self):
        """to_contract_intent() and sync_from_contract() seamlessly transition DB rows."""
        db_intent = OutreachDispatchIntent.objects.create(
            tenant_id=self.tenant_id,
            lead=self.lead,
            sender=self.sender,
            sender_username=self.sender.username,
            recipient_identifier=self.lead.username,
            message_type="initial",
            message_text="متن تست همگام‌سازی",
            status="PENDING",
        )

        contract_obj = db_intent.to_contract_intent()
        now = datetime.now(timezone.utc)

        # Claim via FSM
        OutreachStateMachine.claim(contract_obj, worker_id="worker_worker_42", lease_duration_sec=600, now=now)
        db_intent.sync_from_contract(contract_obj)

        db_intent.refresh_from_db()
        self.assertEqual(db_intent.status, "CLAIMED")
        self.assertEqual(db_intent.worker_id, "worker_worker_42")
        self.assertEqual(db_intent.generation, 2)

        # Start sending and mark sent
        OutreachStateMachine.start_sending(contract_obj, worker_id="worker_worker_42", now=now)
        OutreachStateMachine.mark_sent(contract_obj, worker_id="worker_worker_42", remote_message_id="ig_rm_777", now=now)
        db_intent.sync_from_contract(contract_obj)

        db_intent.refresh_from_db()
        self.assertEqual(db_intent.status, "SENT")
        self.assertEqual(db_intent.remote_message_id, "ig_rm_777")
        self.assertIsNone(db_intent.worker_id)
        self.assertEqual(db_intent.generation, 4)

    def test_frozen_lead_reader_remains_untouched(self):
        """Lead model queries operate normally and frozen fields are preserved."""
        lead = InstagramEngineLead.objects.get(pk=self.lead.pk)
        self.assertEqual(lead.status, "NEW")
        self.assertEqual(lead.intent_score, 0.95)
        # Relationship to dispatch_intents is cleanly accessible
        self.assertEqual(lead.dispatch_intents.count(), 0)
