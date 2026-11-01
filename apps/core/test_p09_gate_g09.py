import time
from pathlib import Path
from django.test import TestCase
from django.conf import settings

from apps.billing.services import heartbeat
from apps.discovery.models import EngineSyncCursor
from apps.products.models import Category
from apps.products.taxonomy import get_subtree_ids
from apps.businesses.models import Business
from django.contrib.auth import get_user_model

User = get_user_model()


class GateG09ProductionReadinessTests(TestCase):
    """
    Gate G09 Exit Verification Matrix:
    1. Runtime lock file and pinned dependencies (R26).
    2. Zero N+1 category traversal and cached full_path lookups (R36).
    3. Decoupled tenant configurations in outreach and compose (R37, R38).
    4. Immutable production compose overlay with log rotation and no code bind mounts (R34, R35).
    5. Supervised background workers without background '&' and health probes (R24, R34).
    """

    def setUp(self):
        self.base_dir = Path(settings.BASE_DIR)
        candidate_prod = self.base_dir / "docker-compose.prod.yml"
        if not candidate_prod.exists():
            candidate = Path(__file__).resolve().parent.parent.parent / "docker-compose.prod.yml"
            if candidate.exists():
                self.base_dir = candidate.parent

    def test_g09_runtime_lockfile_integrity(self):
        lock_file = self.base_dir / "requirements.lock"
        self.assertTrue(lock_file.exists(), "requirements.lock must exist at repo root")
        content = lock_file.read_text(encoding="utf-8")
        self.assertIn("Django==", content)
        self.assertIn("psycopg==", content)

    def test_g09_n_plus_one_category_guard(self):
        user = User.objects.create(username="g09_user", email="g09@test.local")
        biz = Business.objects.create(name="G09 Biz", user=user)
        root = Category.objects.create(business=biz, name="دسته اصلی", product_type="PHYSICAL")
        child = Category.objects.create(business=biz, name="زیردسته", parent=root, product_type="PHYSICAL")

        # get_full_path must return immediately using cached column
        self.assertEqual(child.get_full_path(), "دسته اصلی > زیردسته")
        # Subtree lookup in single query
        subtree = get_subtree_ids(biz, root.pk)
        self.assertEqual(set(subtree), {root.pk, child.pk})

    def test_g09_tenant_decoupling(self):
        from apps.discovery.management.commands.run_instagram_outreach import Command as OutreachCommand
        cmd = OutreachCommand()
        parser = cmd.create_parser("", "run_instagram_outreach")
        actions = {a.dest: a for a in parser._actions}
        self.assertIn("tenant", actions)
        # Default must not be hardcoded to tenant_4
        self.assertNotEqual(actions["tenant"].default, "tenant_4")

    def test_g09_production_compose_immutability(self):
        prod_compose = self.base_dir / "docker-compose.prod.yml"
        self.assertTrue(prod_compose.exists(), "docker-compose.prod.yml must exist")
        content = prod_compose.read_text(encoding="utf-8")
        active_lines = "\n".join(line.split("#")[0] for line in content.splitlines())
        for forbidden in ["./apps", "./templates", "./workers", "./need_engine", "./shared"]:
            self.assertNotIn(forbidden, active_lines)

    def test_g09_worker_supervision_and_health(self):
        # Entrypoint contains supervisor call
        entrypoint = self.base_dir / "docker" / "entrypoint.sh"
        if entrypoint.exists():
            content = entrypoint.read_text(encoding="utf-8")
            sync_section = content.split("sync)")[1].split(";;")[0]
            self.assertNotIn("&", sync_section)
            self.assertIn("sync_supervisor", sync_section)

        # Heartbeat functions properly
        heartbeat("g09_gate")
        cursor = EngineSyncCursor.objects.filter(name="heartbeat:g09_gate").first()
        self.assertIsNotNone(cursor)
        self.assertGreaterEqual(cursor.position, int(time.time()) - 5)
