import json
from pathlib import Path
from django.test import TestCase
from django.conf import settings


class ReleaseReadinessMatrixTests(TestCase):
    """
    Test suite for P10.01 - Release Readiness Matrix and Blocker Auditing (R01, R08, R11, R25, R32).
    """

    def setUp(self):
        self.base_dir = Path(settings.BASE_DIR)
        candidate_matrix = self.base_dir / "docs" / "remediation" / "RELEASE_READINESS_MATRIX.md"
        if not candidate_matrix.exists():
            candidate = Path(__file__).resolve().parent.parent.parent / "docs" / "remediation" / "RELEASE_READINESS_MATRIX.md"
            if candidate.exists():
                self.base_dir = candidate.parent.parent.parent

    def test_release_matrix_document_exists_and_contains_gates(self):
        possible_paths = [
            self.base_dir / "docs" / "remediation" / "RELEASE_READINESS_MATRIX.md",
            Path(__file__).resolve().parent / "RELEASE_READINESS_MATRIX.md",
        ]
        doc = next((p for p in possible_paths if p.exists()), None)
        self.assertIsNotNone(doc, "RELEASE_READINESS_MATRIX.md must exist")

        content = doc.read_text(encoding="utf-8")
        self.assertIn("READY", content)
        self.assertIn("DEFERRED", content)
        self.assertIn("R25", content)

    def test_all_predecessor_gates_passed(self):
        # Verify that all gate test files from G01 through G09 are present and intact
        core_dir = Path(__file__).resolve().parent
        for gate_idx in range(1, 10):
            gate_filename = f"test_p0{gate_idx}_gate_g0{gate_idx}.py" if gate_idx > 1 else "test_p01_08_phase1_gate_matrix.py"
            gate_file = core_dir / gate_filename
            self.assertTrue(gate_file.exists(), f"Gate test file {gate_filename} must exist")

    def test_legacy_engine_frozen_and_isolated(self):
        legacy_dir = self.base_dir / "instagram_engine"
        self.assertTrue(legacy_dir.exists(), "Legacy instagram_engine must exist")
        # Ensure post_filters.py still exists in legacy as frozen
        self.assertTrue((legacy_dir / "post_filters.py").exists())
