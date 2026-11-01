import importlib
from pathlib import Path
from django.test import TestCase
from django.conf import settings


class RuntimeLockAndDependencyTests(TestCase):
    """
    Test suite for P09.01 - Runtime Lock and Reproducible Dependencies (R26).
    """

    def setUp(self):
        self.base_dir = Path(settings.BASE_DIR)
        # Handle container mount paths
        if not (self.base_dir / "requirements.lock").exists():
            candidate = Path(__file__).resolve().parent.parent.parent / "requirements.lock"
            if candidate.exists():
                self.base_dir = candidate.parent

    def test_requirements_lock_exists_and_pinned(self):
        lock_path = self.base_dir / "requirements.lock"
        self.assertTrue(lock_path.exists(), "requirements.lock must exist at repo root")

        lines = [line.strip() for line in lock_path.read_text(encoding="utf-8").splitlines()]
        packages = [l for l in lines if l and not l.startswith("#")]
        self.assertGreater(len(packages), 25, "Lock file must contain pinned transitive dependencies")

        for pkg in packages:
            self.assertIn("==", pkg, f"Package specification in lock file must be pinned with '==': {pkg}")

    def test_legacy_requirements_preserved(self):
        legacy_req = self.base_dir / "instagram_engine" / "requirements.txt"
        self.assertTrue(legacy_req.exists(), "Legacy instagram_engine/requirements.txt must exist")
        content = legacy_req.read_text(encoding="utf-8")
        self.assertIn("SQLAlchemy", content)
        self.assertIn("psycopg2-binary", content)

    def test_dockerfile_uses_lock_file(self):
        dockerfile = self.base_dir / "Dockerfile"
        self.assertTrue(dockerfile.exists(), "Dockerfile must exist")
        content = dockerfile.read_text(encoding="utf-8")
        self.assertIn("requirements.lock", content, "Dockerfile must reference requirements.lock")

    def test_active_runtime_core_packages_importable(self):
        core_modules = [
            "django",
            "psycopg",
            "pydantic",
            "numpy",
            "telethon",
            "whitenoise",
            "gunicorn",
            "instagrapi",
            "cryptography",
            "PIL",
        ]
        for mod_name in core_modules:
            with self.subTest(module=mod_name):
                mod = importlib.import_module(mod_name)
                self.assertIsNotNone(mod)
