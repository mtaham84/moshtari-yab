import yaml
from pathlib import Path
from django.test import TestCase
from django.conf import settings


class ProductionComposeAndImmutableImageTests(TestCase):
    """
    Test suite for P09.04 - Production Compose Profile & Immutable Image Invariants (R34, R35).
    """

    def setUp(self):
        self.base_dir = Path(settings.BASE_DIR)
        # Handle container mount paths
        candidate_prod = self.base_dir / "docker-compose.prod.yml"
        if not candidate_prod.exists():
            candidate = Path(__file__).resolve().parent.parent.parent / "docker-compose.prod.yml"
            if candidate.exists():
                self.base_dir = candidate.parent

    def test_prod_compose_file_exists_and_valid(self):
        prod_compose = self.base_dir / "docker-compose.prod.yml"
        self.assertTrue(prod_compose.exists(), "docker-compose.prod.yml must exist at repo root")
        content = prod_compose.read_text(encoding="utf-8")

        # Must enforce DEBUG=False
        self.assertIn('DJANGO_DEBUG: "False"', content)

        # Must configure log rotation
        self.assertIn("max-size", content)
        self.assertIn("max-file", content)

    def test_prod_compose_excludes_code_bind_mounts(self):
        prod_compose = self.base_dir / "docker-compose.prod.yml"
        content = prod_compose.read_text(encoding="utf-8")
        active_lines = "\n".join(line.split("#")[0] for line in content.splitlines())

        # Forbidden code bind mounts in production
        forbidden_mounts = [
            "./apps",
            "./templates",
            "./workers",
            "./need_engine",
            "./shared",
            "./x_ingest",
            "./instagram_engine",
        ]
        for mount in forbidden_mounts:
            with self.subTest(mount=mount):
                self.assertNotIn(mount, active_lines, f"Production compose must not bind-mount code: {mount}")

    def test_prod_compose_services_covered(self):
        prod_compose = self.base_dir / "docker-compose.prod.yml"
        content = prod_compose.read_text(encoding="utf-8")

        expected_services = ["web", "sync", "engine", "crawler", "x", "ig", "ig-outreach"]
        for svc in expected_services:
            with self.subTest(service=svc):
                self.assertIn(f"{svc}:", content, f"Service {svc} must be defined in docker-compose.prod.yml")
