from unittest.mock import patch
from django.core.management import call_command
from django.test import TestCase

from apps.businesses.models import Business
from apps.discovery.models import EngineSyncCursor, Opportunity, QuarantinedSyncPayload
from apps.products.models import Product


class TestPoisonPayloadQuarantine(TestCase):
    def setUp(self):
        from django.contrib.auth import get_user_model
        from apps.discovery.models import Customer
        User = get_user_model()
        self.user, _ = User.objects.get_or_create(username="test_sync_user", defaults={"email": "sync@example.com"})
        self.biz, _ = Business.objects.get_or_create(user=self.user, name="Sync Store")
        self.customer = Customer.objects.create(
            business=self.biz,
            name="Test Customer",
        )
        self.cursor, _ = EngineSyncCursor.objects.get_or_create(name="opportunities", defaults={"position": 0})
        self.cursor.position = 0
        self.cursor.save()

    @patch("apps.discovery.management.commands.sync_opportunities.published_after")
    def test_poison_payload_does_not_block_batch_ingestion(self, mock_published):
        # 3 items in batch: valid_1 (seq=1), poison_2 (seq=2), valid_3 (seq=3)
        valid_payload_1 = {
            "opportunity_id": "opp_valid_1",
            "status": "open",
            "matched_products": [],
            "source": {"platform": "telegram", "chat_id": 100},
        }
        poison_payload_2 = {
            "opportunity_id": "opp_poison_2",
            "malformed_structure": True,
            # triggers error because it is missing required format or causes simulated crash
        }
        valid_payload_3 = {
            "opportunity_id": "opp_valid_3",
            "status": "open",
            "matched_products": [],
            "source": {"platform": "telegram", "chat_id": 100},
        }

        mock_published.return_value = [
            (1, valid_payload_1),
            (2, poison_payload_2),
            (3, valid_payload_3),
        ]

        def _mock_import(p):
            if p.get("opportunity_id") == "opp_poison_2":
                raise ValueError("Corrupted poison payload: unexpected null byte or schema violation")
            # Create or update dummy Opportunity
            opp, _ = Opportunity.objects.update_or_create(
                engine_opportunity_id=p["opportunity_id"],
                business=self.biz,
                customer=self.customer,
                defaults={"status": "NEW"},
            )
            return [opp]

        with patch("apps.discovery.management.commands.sync_opportunities.import_opportunity", side_effect=_mock_import):
            call_command("sync_opportunities", no_billing=True)

        # 1. Cursor must have advanced all the way to seq=3!
        self.cursor.refresh_from_db()
        self.assertEqual(self.cursor.position, 3)

        # 2. Valid items 1 and 3 are successfully imported
        self.assertTrue(Opportunity.objects.filter(engine_opportunity_id="opp_valid_1").exists())
        self.assertTrue(Opportunity.objects.filter(engine_opportunity_id="opp_valid_3").exists())

        # 3. Poison item 2 is safely quarantined in dead-letter table
        quarantined = QuarantinedSyncPayload.objects.filter(seq=2).first()
        self.assertIsNotNone(quarantined)
        self.assertEqual(quarantined.status, "QUARANTINED")
        self.assertEqual(quarantined.opportunity_id, "opp_poison_2")
        self.assertIn("Corrupted poison payload", quarantined.error_reason)
        self.assertEqual(quarantined.error_type, "ValueError")

    def test_replay_quarantined_resolves_and_prevents_duplicates(self):
        # Create a quarantined record
        q_item = QuarantinedSyncPayload.objects.create(
            seq=10,
            opportunity_id="opp_quarantine_replay",
            payload={"opportunity_id": "opp_quarantine_replay", "status": "open"},
            error_reason="Simulated temporary crash",
            error_type="OperationalError",
            status="QUARANTINED",
            max_retries=3,
        )

        call_count = 0

        def _mock_import(p):
            nonlocal call_count
            call_count += 1
            opp, _ = Opportunity.objects.update_or_create(
                engine_opportunity_id=p["opportunity_id"],
                business=self.biz,
                customer=self.customer,
                defaults={"status": "NEW"},
            )
            return [opp]

        with patch("apps.discovery.management.commands.sync_opportunities.import_opportunity", side_effect=_mock_import):
            # First replay: succeeds
            call_command("sync_opportunities", replay_quarantine=True)

            q_item.refresh_from_db()
            self.assertEqual(q_item.status, "RESOLVED")
            self.assertEqual(q_item.retry_count, 1)
            self.assertEqual(Opportunity.objects.filter(engine_opportunity_id="opp_quarantine_replay").count(), 1)

            # Replaying again does not re-import resolved records
            call_command("sync_opportunities", replay_quarantine=True)
            self.assertEqual(call_count, 1)
            self.assertEqual(Opportunity.objects.filter(engine_opportunity_id="opp_quarantine_replay").count(), 1)

    def test_quarantine_exhaustion_after_max_retries(self):
        q_item = QuarantinedSyncPayload.objects.create(
            seq=20,
            opportunity_id="opp_permanent_fail",
            payload={"opportunity_id": "opp_permanent_fail"},
            error_reason="Persistent failure",
            status="QUARANTINED",
            retry_count=2,
            max_retries=3,
        )

        with patch("apps.discovery.management.commands.sync_opportunities.import_opportunity", side_effect=RuntimeError("Still broken")):
            call_command("sync_opportunities", replay_quarantine=True)

            q_item.refresh_from_db()
            self.assertEqual(q_item.retry_count, 3)
            self.assertEqual(q_item.status, "FAILED_EXHAUSTED")
