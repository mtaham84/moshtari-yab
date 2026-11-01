import os
from pathlib import Path
from django.conf import settings
from django.test import TestCase

from apps.core.boundary_checker import (
    BASELINE_EXCEPTIONS,
    check_source_file_imports,
    is_excluded_path,
    scan_project_boundaries,
)


class TestArchitectureBoundaries(TestCase):
    def setUp(self):
        self.project_root = str(settings.BASE_DIR)

    def test_current_repository_adheres_to_baseline_boundaries(self):
        """
        Scans entire active codebase to guarantee no unlisted boundary violations exist (R39, R40, R42).
        """
        violations = scan_project_boundaries(self.project_root)
        if violations:
            msg = "\n".join(
                f"- {v.source_file}:{v.line_number} -> {v.imported_module} ({v.rule_broken})"
                for v in violations
            )
            self.fail(f"Found {len(violations)} architecture boundary violation(s):\n{msg}")

    def test_synthetic_forbidden_import_fails(self):
        """
        Acceptance Criteria: Synthetic forbidden import must FAIL boundary check.
        """
        synthetic_xray_file = os.path.join(self.project_root, "xray", "leak_component.py")
        bad_code = """
import os
from apps.discovery.models import Opportunity

def leak():
    return Opportunity.objects.all()
"""
        violations = check_source_file_imports(
            synthetic_xray_file,
            self.project_root,
            custom_content=bad_code,
        )
        self.assertEqual(len(violations), 1)
        self.assertEqual(violations[0].imported_module, "apps.discovery.models")
        self.assertIn("forbidden from importing 'apps'", violations[0].rule_broken)

    def test_synthetic_allowed_import_passes(self):
        """
        Acceptance Criteria: Allowed imports pass without violations.
        """
        synthetic_app_file = os.path.join(self.project_root, "apps", "products", "helper.py")
        good_code = """
import logging
from need_engine.schemas import ProductCard
from .models import Category

logger = logging.getLogger(__name__)
"""
        violations = check_source_file_imports(
            synthetic_app_file,
            self.project_root,
            custom_content=good_code,
        )
        self.assertEqual(len(violations), 0)

    def test_relative_import_resolution(self):
        """
        Relative imports resolve properly without triggering false positives.
        """
        file_path = os.path.join(self.project_root, "apps", "core", "sample.py")
        code = """
from .typed_errors import sanitize_text
from ..billing.models import Wallet
"""
        violations = check_source_file_imports(file_path, self.project_root, custom_content=code)
        # apps importing apps is permitted
        self.assertEqual(len(violations), 0)

    def test_test_and_migration_files_are_excluded(self):
        """
        Acceptance Criteria: Test, migration, and framework files are not confused with runtime code.
        """
        self.assertTrue(is_excluded_path("apps/discovery/tests.py"))
        self.assertTrue(is_excluded_path("apps/discovery/tests/test_foo.py"))
        self.assertTrue(is_excluded_path("apps/discovery/migrations/0001_initial.py"))
        self.assertTrue(is_excluded_path("instagram_engine/models.py"))
        self.assertFalse(is_excluded_path("apps/discovery/views.py"))
        self.assertFalse(is_excluded_path("need_engine/store.py"))

    def test_baseline_exceptions_integrity(self):
        """
        Ensures baseline exceptions are minimal, explicit, and tracked.
        """
        self.assertTrue(len(BASELINE_EXCEPTIONS) <= 10)
        for path, target in BASELINE_EXCEPTIONS:
            self.assertTrue(isinstance(path, str))
            self.assertTrue(isinstance(target, str))
