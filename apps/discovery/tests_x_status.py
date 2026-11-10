import json
import tempfile
from pathlib import Path

from django.test import TestCase
from django.utils import timezone

from apps.discovery.engine_bridge import sync_x_status_file
from apps.discovery.models import XCollectorState


class SyncXStatusFileTests(TestCase):
    def write(self, **payload):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        path = Path(folder.name) / "status.json"
        base = {"status": "COMPLETED", "collected": 3, "failures": 0, "cooldown_until": None,
                "updated_at": timezone.now().isoformat(), "collected_today": 7, "daily_cap": 2500,
                "last_cycle_at": timezone.now().isoformat(), "queries": []}
        path.write_text(json.dumps({**base, **payload}), encoding="utf-8")
        return str(path)

    def test_file_written_by_the_collector_is_read(self):
        # the collector writes "cooldown_until": null and upper-case statuses such as COMPLETED
        self.assertTrue(sync_x_status_file(self.write()))
        state = XCollectorState.objects.get(id=1)
        self.assertEqual((state.status, state.collected_today, state.cooldown_until), ("idle", 7, None))

    def test_collector_statuses_map_to_panel_statuses(self):
        for written, shown in (("PARTIAL", "idle"), ("DAILY_CAP_REACHED", "daily_cap"),
                               ("RATE_LIMITED", "rate_limited"), ("CIRCUIT_OPEN", "circuit_open")):
            self.assertTrue(sync_x_status_file(self.write(status=written)))
            self.assertEqual(XCollectorState.objects.get(id=1).status, shown, written)

    def test_broken_file_marks_the_status_unknown(self):
        self.assertFalse(sync_x_status_file(self.write(updated_at=None)))
        self.assertEqual(XCollectorState.objects.get(id=1).status, "unknown")
