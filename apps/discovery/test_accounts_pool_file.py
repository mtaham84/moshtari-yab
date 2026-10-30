import json
import os
from pathlib import Path
import stat
import subprocess
import tempfile
from unittest import mock

from django.test import TestCase


class PoolFileTests(TestCase):
    def test_export_import_roundtrip_and_permissions(self):
        from apps.discovery import accounts_pool_file as pool
        from apps.discovery.models import InstagramEngineAccount, TwitterEngineAccount

        tmp = Path(tempfile.mkdtemp())
        with mock.patch.dict(os.environ, {"ACCOUNTS_POOL_FILE": str(tmp / "pool.json")}):
            InstagramEngineAccount.objects.create(username="ig1", password="pw", session_json={"cookies": {"sessionid": "S"}})
            TwitterEngineAccount.objects.create(username="x1", auth_token="a" * 40, ct0="c" * 32)
            self.assertTrue(pool.export_pool(force=True))
            path = tmp / "pool.json"
            if os.name != "nt":
                self.assertEqual(stat.S_IMODE(os.stat(path).st_mode), 0o600)
            data = json.loads(path.read_text())
            self.assertEqual(data["instagram"][0]["session_json"]["cookies"]["sessionid"], "S")
            self.assertEqual(data["twitter"][0]["auth_token"], "a" * 40)

            InstagramEngineAccount.objects.all().delete()
            TwitterEngineAccount.objects.filter(username="x1").update(auth_token="")
            stats = pool.import_pool()
            self.assertEqual((stats["created"], stats["filled"]), (1, 1))
            self.assertEqual(TwitterEngineAccount.objects.get(username="x1").auth_token, "a" * 40)
            # a non-empty DB value is not overwritten without --force
            TwitterEngineAccount.objects.filter(username="x1").update(auth_token="new")
            pool.import_pool()
            self.assertEqual(TwitterEngineAccount.objects.get(username="x1").auth_token, "new")

    def test_pool_file_is_git_ignored(self):
        root = Path(__file__).resolve().parents[2]
        out = subprocess.run(["git", "check-ignore", "data/accounts_pool.json"], cwd=root, capture_output=True)
        self.assertEqual(out.returncode, 0)


class PoolBackupTests(TestCase):
    def test_mirror_and_history_are_written_and_rotated(self):
        from apps.discovery import accounts_pool_file as pool
        from apps.discovery.models import TwitterEngineAccount

        tmp = Path(tempfile.mkdtemp())
        env = {"ACCOUNTS_POOL_FILE": str(tmp / "pool.json"), "ACCOUNTS_POOL_BACKUP_DIR": str(tmp)}
        with mock.patch.dict(os.environ, env), mock.patch.object(pool, "HISTORY_KEEP", 3):
            acc = TwitterEngineAccount.objects.create(username="x2", auth_token="t1", ct0="c")
            for i in range(5):
                acc.auth_token = f"t{i + 2}"
                acc.save()
                pool.export_pool()
            self.assertEqual(json.loads((tmp / "accounts_pool.json").read_text())["twitter"][0]["auth_token"], "t6")
            self.assertEqual(len(list((tmp / "history").glob("accounts_pool.*.json"))), 3)
