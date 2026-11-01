"""
Unit and integration test suite for Ops Priority Snapshot and ModelRef Registry (P05.02 / R17).
"""

from decimal import Decimal
from django.test import TestCase

from apps.billing.models import Provider, AIModel
from apps.core.gateway_contract import ModelRef
from need_engine.config import EngineConfig
from need_engine.registry import ModelRegistry, ModelEntry, Snapshot


class FakeOpsDB:
    """Simulates database returns for ModelRegistry."""

    def __init__(self, rows):
        self.rows = rows

    def all(self, sql, params=()):
        if "to_regclass" in sql:
            return [{"t": "exists"}]
        if "billing_aimodel" in sql:
            # Simulate SQL ORDER BY m.priority, m.id
            return sorted(self.rows, key=lambda r: int(r.get("priority", 100)))
        return []


class OpsPrioritySnapshotTestCase(TestCase):
    """
    Validates P05.02:
    - Provider+Model disambiguation
    - Ops priority preservation
    - Ambiguous name defense
    - Immutable operational snapshot freezing
    - AIModel ORM properties
    """

    def test_provider_model_disambiguation_and_priority_ordering(self):
        """
        R17: Two providers offering the same model name MUST have distinct entries,
        and priority must dictate role selection.
        """
        rows = [
            # Luna provider offers gpt-4o-mini at priority 10
            {
                "provider_name": "Luna",
                "name": "gpt-4o-mini",
                "base_url": "https://luna.example/v1",
                "api_key": "luna-key",
                "input_price_usd": 0.15,
                "output_price_usd": 0.60,
                "priority": 10,
                "use_extract": True,
                "use_verify": False,
                "use_reply": True,
                "rpm": 60,
                "tpm": 100000,
                "rpd": 5000,
            },
            # OpenAI provider offers gpt-4o-mini at priority 30
            {
                "provider_name": "OpenAI",
                "name": "gpt-4o-mini",
                "base_url": "https://api.openai.com/v1",
                "api_key": "openai-key",
                "input_price_usd": 0.15,
                "output_price_usd": 0.60,
                "priority": 30,
                "use_extract": True,
                "use_verify": True,
                "use_reply": False,
                "rpm": 100,
                "tpm": 200000,
                "rpd": None,
            },
            # Dedicated deep model
            {
                "provider_name": "OpenAI",
                "name": "o3-mini",
                "base_url": "https://api.openai.com/v1",
                "api_key": "openai-key",
                "input_price_usd": 1.10,
                "output_price_usd": 4.40,
                "priority": 20,
                "use_extract": False,
                "use_verify": True,
                "use_reply": False,
                "rpm": 20,
                "tpm": 50000,
                "rpd": 500,
            },
        ]

        cfg = EngineConfig()
        cfg.products_source = "db"
        cfg.model_registry = True
        registry = ModelRegistry(cfg, db=FakeOpsDB(rows))

        # Distinct identities exist
        luna_entry = registry.entry("luna:gpt-4o-mini")
        openai_entry = registry.entry("openai:gpt-4o-mini")
        self.assertIsNotNone(luna_entry)
        self.assertIsNotNone(openai_entry)
        self.assertEqual(luna_entry.provider, "Luna")
        self.assertEqual(openai_entry.provider, "OpenAI")
        self.assertNotEqual(luna_entry.identity, openai_entry.identity)

        # ModelRef properties
        self.assertEqual(luna_entry.model_ref, ModelRef("Luna", "gpt-4o-mini"))
        self.assertEqual(openai_entry.model_ref, ModelRef("OpenAI", "gpt-4o-mini"))

        # Primary model for 'extract' should be Luna (priority 10 < priority 30)
        self.assertEqual(registry.model_for("need_extraction"), "luna:gpt-4o-mini")
        self.assertEqual(registry.model_ref_for("need_extraction"), ModelRef("Luna", "gpt-4o-mini"))

        # Fallback candidates for 'extract' must strictly preserve priority order [10, 30]
        candidates = registry.candidate_models_for("need_extraction")
        self.assertEqual(candidates, ["luna:gpt-4o-mini", "openai:gpt-4o-mini"])

        # Role 'verify' has o3-mini (priority 20) and openai:gpt-4o-mini (priority 30)
        self.assertEqual(registry.model_for("verify"), "openai:o3-mini")
        verify_candidates = registry.candidate_models_for("verify")
        self.assertEqual(verify_candidates, ["openai:o3-mini", "openai:gpt-4o-mini"])

    def test_ambiguous_plain_model_name_raises_clear_error(self):
        """
        R17: Querying ambiguous model name without provider raises an informative ValueError.
        """
        rows = [
            {"provider_name": "ProviderA", "name": "claude-3-haiku", "base_url": "https://a.test", "api_key": "k",
             "input_price_usd": 0.2, "output_price_usd": 1.0, "priority": 1, "use_extract": True, "use_verify": False,
             "use_reply": False, "rpm": 10, "tpm": 1000, "rpd": 100},
            {"provider_name": "ProviderB", "name": "claude-3-haiku", "base_url": "https://b.test", "api_key": "k",
             "input_price_usd": 0.25, "output_price_usd": 1.25, "priority": 2, "use_extract": True, "use_verify": False,
             "use_reply": False, "rpm": 10, "tpm": 1000, "rpd": 100},
        ]
        cfg = EngineConfig()
        cfg.products_source = "db"
        cfg.model_registry = True
        registry = ModelRegistry(cfg, db=FakeOpsDB(rows))

        with self.assertRaises(ValueError) as ctx:
            registry.entry("claude-3-haiku")

        err_msg = str(ctx.exception)
        self.assertIn("Ambiguous model name 'claude-3-haiku'", err_msg)
        self.assertIn("providera:claude-3-haiku", err_msg)
        self.assertIn("providerb:claude-3-haiku", err_msg)

    def test_frozen_snapshot_immutability_during_operation(self):
        """
        R17: A frozen snapshot maintains immutable state across potential registry reloads or config changes.
        """
        initial_rows = [
            {"provider_name": "Luna", "name": "gemini-flash", "base_url": "https://luna.test", "api_key": "k1",
             "input_price_usd": 0.1, "output_price_usd": 0.4, "priority": 1, "use_extract": True, "use_verify": False,
             "use_reply": False, "rpm": 50, "tpm": 50000, "rpd": 1000},
        ]
        db = FakeOpsDB(initial_rows)
        cfg = EngineConfig()
        cfg.products_source = "db"
        cfg.model_registry = True
        registry = ModelRegistry(cfg, db=db)

        # Freeze snapshot for operation
        frozen_snap = registry.freeze_snapshot()
        self.assertEqual(frozen_snap.by_role.get("extract"), "luna:gemini-flash")
        initial_version = frozen_snap.version

        # Simulate Ops reconfiguring models in DB mid-operation (priority change)
        db.rows = [
            {"provider_name": "Luna", "name": "gemini-flash", "base_url": "https://luna.test", "api_key": "k1",
             "input_price_usd": 0.1, "output_price_usd": 0.4, "priority": 99, "use_extract": True, "use_verify": False,
             "use_reply": False, "rpm": 50, "tpm": 50000, "rpd": 1000},
            {"provider_name": "NewProvider", "name": "gemini-pro", "base_url": "https://new.test", "api_key": "k2",
             "input_price_usd": 1.0, "output_price_usd": 3.0, "priority": 1, "use_extract": True, "use_verify": False,
             "use_reply": False, "rpm": 10, "tpm": 10000, "rpd": 100},
        ]

        # Force refresh on registry
        new_snap = registry.refresh(force=True)
        self.assertEqual(new_snap.by_role.get("extract"), "newprovider:gemini-pro")
        self.assertGreater(new_snap.version, initial_version)

        # The frozen snapshot held by the in-flight operation remains 100% UNCHANGED
        self.assertEqual(frozen_snap.by_role.get("extract"), "luna:gemini-flash")
        self.assertEqual(frozen_snap.version, initial_version)
        self.assertNotIn("newprovider:gemini-pro", frozen_snap.by_identity)

    def test_aimodel_orm_model_ref_and_identity_properties(self):
        """
        R17: Django AIModel provides transparent model_ref and identity properties.
        """
        provider = Provider.objects.create(name="Avalai", base_url="https://api.avalai.ir/v1")
        model = AIModel.objects.create(
            provider=provider,
            name="gpt-4o",
            label="GPT 4o Avalai",
            priority=5,
            use_reply=True,
        )

        self.assertEqual(model.identity, "avalai:gpt-4o")
        self.assertEqual(model.model_ref, ModelRef("Avalai", "gpt-4o"))
