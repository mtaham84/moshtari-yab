import time
from io import StringIO
from pathlib import Path
from django.test import TestCase
from django.core.management import call_command
from django.conf import settings

from apps.discovery.models import EngineSyncCursor
from apps.billing.services import heartbeat


class WorkerSupervisionAndHealthTests(TestCase):
    def test_check_service_health_healthy(self):
        # Record fresh heartbeat
        heartbeat("test_svc")

        out = StringIO()
        err = StringIO()
        # Should exit 0
        try:
            call_command("check_service_health", service="test_svc", max_age=60, stdout=out, stderr=err)
            success = True
        except SystemExit as exc:
            success = (exc.code == 0)

        self.assertTrue(success, "Health check should succeed for fresh heartbeat")
        self.assertIn("HEALTHY", out.getvalue())

    def test_check_service_health_stale_or_missing(self):
        # Missing service
        try:
            call_command("check_service_health", service="non_existent_service", max_age=10)
            failed_missing = False
        except SystemExit as exc:
            failed_missing = (exc.code != 0)

        self.assertTrue(failed_missing, "Health check must fail for non-existent heartbeat")

        # Stale service
        EngineSyncCursor.objects.update_or_create(
            name="heartbeat:stale_svc",
            defaults={"position": int(time.time()) - 500},
        )
        try:
            call_command("check_service_health", service="stale_svc", max_age=60)
            failed_stale = False
        except SystemExit as exc:
            failed_stale = (exc.code != 0)

        self.assertTrue(failed_stale, "Health check must fail for stale heartbeat")

    def test_entrypoint_no_background_ampersand_in_sync(self):
        candidate_paths = [
            Path(settings.BASE_DIR) / "docker" / "entrypoint.sh",
            Path(__file__).resolve().parent.parent.parent.parent / "docker" / "entrypoint.sh",
        ]
        entrypoint = next((p for p in candidate_paths if p.exists()), None)
        self.assertIsNotNone(entrypoint)

        content = entrypoint.read_text(encoding="utf-8")
        # Ensure sync branch does not use unmanaged & backgrounding
        sync_section = content.split("sync)")[1].split(";;")[0]
        self.assertNotIn("&", sync_section, "Sync branch in entrypoint must not use background '&'")
        self.assertIn("sync_supervisor", sync_section)

    def test_docker_compose_sync_healthcheck_defined(self):
        candidate_paths = [
            Path(settings.BASE_DIR) / "docker-compose.yml",
            Path(__file__).resolve().parent.parent.parent.parent / "docker-compose.yml",
        ]
        compose_file = next((p for p in candidate_paths if p.exists()), None)
        if compose_file:
            content = compose_file.read_text(encoding="utf-8")
            self.assertIn("check_service_health", content)
