"""Unit tests for the database backup helper and management command.

All tests are pure-unit: they mock subprocess, shutil, requests, and the
Telegram alert sender so PostgreSQL, GPG, and Telegram are never called.
"""
from __future__ import annotations

import os
from io import StringIO
from unittest.mock import MagicMock, patch

import pytest
import requests
from django.core.management import call_command

import apps.core.backup as mod

FAKE_ENV = {
    "POSTGRES_DB": "testdb",
    "POSTGRES_USER": "pguser",
    "POSTGRES_PASSWORD": "secret",
    "POSTGRES_HOST": "127.0.0.1",
    "POSTGRES_PORT": "5432",
    "BACKUP_DIR": "/tmp/backups",
    "BACKUP_GPG_PASSPHRASE": "gpgpass",
    "BACKUP_KEEP": "3",
    "BACKUP_MIN_FREE_MB": "200",
    "BACKUP_SPLIT_MB": "45",
    "BACKUP_TELEGRAM_CHAT_ID": "-100999",
    "CONFIG_BOT_TOKEN": "bot:TOKEN",
}


# ---------------------------------------------------------------------------
# load_backup_config
# ---------------------------------------------------------------------------


class TestLoadBackupConfig:
    def test_reads_env_vars_and_empty_fallbacks(self):
        env = {
            **FAKE_ENV,
            "BACKUP_MIN_FREE_MB": "",
            "BACKUP_KEEP": "",
            "BACKUP_SPLIT_MB": "",
        }
        with patch.dict(os.environ, env, clear=False):
            cfg = mod.load_backup_config()
        assert cfg.min_free_mb == 500
        assert cfg.keep == 7
        assert cfg.split_mb == 45
        assert cfg.telegram_chat_id == "-100999"
        assert cfg.bot_token == "bot:TOKEN"

    def test_backup_bot_token_overrides_config_bot_token(self):
        env = {**FAKE_ENV, "BACKUP_BOT_TOKEN": "backup:TOKEN"}
        with patch.dict(os.environ, env, clear=False):
            cfg = mod.load_backup_config()
        assert cfg.bot_token == "backup:TOKEN"


# ---------------------------------------------------------------------------
# free_mb_on
# ---------------------------------------------------------------------------


class TestFreeMbOn:
    def test_returns_correct_megabytes(self, tmp_path):
        usage = MagicMock()
        usage.free = 200 * 1024 * 1024
        with patch("shutil.disk_usage", return_value=usage):
            assert mod.free_mb_on(tmp_path) == 200

    def test_walks_up_to_existing_parent(self, tmp_path):
        missing = tmp_path / "does" / "not" / "exist"
        usage = MagicMock()
        usage.free = 150 * 1024 * 1024
        with patch("shutil.disk_usage", return_value=usage) as mock_du:
            assert mod.free_mb_on(missing) == 150
        mock_du.assert_called_once_with(tmp_path)

    def test_returns_zero_on_error(self, tmp_path):
        with patch("shutil.disk_usage", side_effect=OSError("no")):
            assert mod.free_mb_on(tmp_path) == 0


# ---------------------------------------------------------------------------
# rotate_old_backups
# ---------------------------------------------------------------------------


class TestRotateOldBackups:
    def test_deletes_oldest_single_files_when_over_limit(self, tmp_path):
        for i in range(4):
            (tmp_path / f"db_2026111{i}_000000.dump.gpg").write_text("x")
        mod.rotate_old_backups(tmp_path, keep=2)
        remaining = sorted(p.name for p in tmp_path.iterdir())
        assert remaining == [
            "db_20261112_000000.dump.gpg",
            "db_20261113_000000.dump.gpg",
        ]

    def test_deletes_split_part_groups_together(self, tmp_path):
        # Set 0 (split), Set 1 (split), Set 2 (single) -> keep=2 removes Set 0 completely
        (tmp_path / "db_20261110_000000.dump.gpg.part000").write_text("x")
        (tmp_path / "db_20261110_000000.dump.gpg.part001").write_text("x")
        (tmp_path / "db_20261111_000000.dump.gpg.part000").write_text("x")
        (tmp_path / "db_20261111_000000.dump.gpg.part001").write_text("x")
        (tmp_path / "db_20261112_000000.dump.gpg").write_text("x")

        mod.rotate_old_backups(tmp_path, keep=2)
        remaining = sorted(p.name for p in tmp_path.iterdir())
        assert remaining == [
            "db_20261111_000000.dump.gpg.part000",
            "db_20261111_000000.dump.gpg.part001",
            "db_20261112_000000.dump.gpg",
        ]

    def test_no_delete_when_under_limit(self, tmp_path):
        for i in range(2):
            (tmp_path / f"db_2026111{i}_000000.dump.gpg").write_text("x")
        mod.rotate_old_backups(tmp_path, keep=3)
        assert len(list(tmp_path.iterdir())) == 2


# ---------------------------------------------------------------------------
# dump_database
# ---------------------------------------------------------------------------


class TestDumpDatabase:
    def test_calls_pg_dump_with_env_credentials(self, tmp_path):
        dest = tmp_path / "db.dump"
        with patch.dict(os.environ, FAKE_ENV, clear=False):
            with patch("subprocess.run", return_value=MagicMock(returncode=0)) as mock_run:
                mod.dump_database(dest)
        mock_run.assert_called_once()
        cmd = mock_run.call_args[0][0]
        assert cmd[0] == "pg_dump"
        assert "-Fc" in cmd
        assert str(dest) in cmd
        assert mock_run.call_args.kwargs["env"]["PGPASSWORD"] == "secret"

    def test_raises_on_nonzero_exit(self, tmp_path):
        dest = tmp_path / "db.dump"
        proc = MagicMock(returncode=1, stderr=b"connection refused")
        with patch("subprocess.run", return_value=proc):
            with pytest.raises(RuntimeError, match="pg_dump"):
                mod.dump_database(dest)

    def test_raises_clear_error_when_binary_missing(self, tmp_path):
        dest = tmp_path / "db.dump"
        with patch("subprocess.run", side_effect=FileNotFoundError("pg_dump")):
            with pytest.raises(RuntimeError, match="pg_dump"):
                mod.dump_database(dest)


# ---------------------------------------------------------------------------
# encrypt_file
# ---------------------------------------------------------------------------


class TestEncryptFile:
    def test_uses_passphrase_fd_stdin_not_cmdline(self, tmp_path):
        src = tmp_path / "db.dump"
        src.write_bytes(b"data")
        dest = tmp_path / "db.dump.gpg"
        with patch.dict(os.environ, FAKE_ENV, clear=False):
            with patch("subprocess.run", return_value=MagicMock(returncode=0)) as mock_run:
                result = mod.encrypt_file(src, dest)
        assert result == dest
        cmd = mock_run.call_args[0][0]
        assert cmd[0] == "gpg"
        assert "--symmetric" in cmd
        assert "--passphrase-fd" in cmd
        assert "--passphrase" not in cmd
        assert "gpgpass" not in cmd
        assert mock_run.call_args.kwargs["input"] == b"gpgpass"

    def test_rejects_empty_passphrase(self, tmp_path):
        src = tmp_path / "db.dump"
        src.write_bytes(b"data")
        with pytest.raises(ValueError, match="BACKUP_GPG_PASSPHRASE"):
            mod.encrypt_file(src, tmp_path / "out.gpg", passphrase="")

    def test_raises_on_gpg_failure(self, tmp_path):
        src = tmp_path / "db.dump"
        src.write_bytes(b"data")
        proc = MagicMock(returncode=2, stderr=b"gpg error")
        with patch("subprocess.run", return_value=proc):
            with pytest.raises(RuntimeError, match="gpg"):
                mod.encrypt_file(src, tmp_path / "out.gpg", passphrase="secret")


# ---------------------------------------------------------------------------
# split_file
# ---------------------------------------------------------------------------


class TestSplitFile:
    def test_returns_single_part_for_small_file(self, tmp_path):
        src = tmp_path / "dump.gpg"
        src.write_bytes(b"x" * 100)
        parts = mod.split_file(src, split_mb=50)
        assert parts == [src]

    def test_splits_large_file(self, tmp_path):
        src = tmp_path / "dump.gpg"
        src.write_bytes(b"A" * 60 + b"B" * 50)
        parts = mod.split_file(src, split_mb=1, _chunk_bytes=50)
        assert len(parts) == 3
        assert [p.name for p in parts] == [
            "dump.gpg.part000",
            "dump.gpg.part001",
            "dump.gpg.part002",
        ]
        combined = b"".join(p.read_bytes() for p in parts)
        assert combined == src.read_bytes()

    def test_rejects_nonpositive_chunk_size(self, tmp_path):
        src = tmp_path / "dump.gpg"
        src.write_bytes(b"x")
        with pytest.raises(ValueError, match="positive"):
            mod.split_file(src, split_mb=0)


# ---------------------------------------------------------------------------
# send_to_telegram
# ---------------------------------------------------------------------------


class TestSendToTelegram:
    def test_sends_each_part_as_document_with_caption(self, tmp_path):
        parts = []
        for i in range(2):
            p = tmp_path / f"db_20261112.dump.gpg.part{i:03d}"
            p.write_bytes(b"x")
            parts.append(p)
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"ok": True}
        with patch("requests.post", return_value=mock_resp) as mock_post:
            mod.send_to_telegram(parts, chat_id="-100999", bot_token="bot:TOKEN")
        assert mock_post.call_count == 2
        assert "1/2" in mock_post.call_args_list[0].kwargs["data"]["caption"]
        assert "2/2" in mock_post.call_args_list[1].kwargs["data"]["caption"]

    def test_raises_when_telegram_rejects(self, tmp_path):
        p = tmp_path / "dump.gpg"
        p.write_bytes(b"x")
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"ok": False, "description": "Forbidden"}
        with patch("requests.post", return_value=mock_resp):
            with pytest.raises(RuntimeError, match="Telegram"):
                mod.send_to_telegram([p], chat_id="-100999", bot_token="bot:TOKEN")

    def test_retries_next_proxy_on_request_exception(self, tmp_path):
        p = tmp_path / "dump.gpg"
        p.write_bytes(b"x")
        ok_resp = MagicMock()
        ok_resp.json.return_value = {"ok": True}
        with patch("shared.telegram_alerts.proxy_candidates", return_value=["socks5h://xray:1080", None]):
            with patch(
                "requests.post",
                side_effect=[requests.exceptions.Timeout("timed out"), ok_resp],
            ) as mock_post:
                mod.send_to_telegram([p], chat_id="-100999", bot_token="bot:TOKEN")
        assert mock_post.call_count == 2


# ---------------------------------------------------------------------------
# run_backup
# ---------------------------------------------------------------------------


class TestRunBackup:
    def test_happy_path_returns_ok_and_rotates(self, tmp_path):
        usage = MagicMock(free=2000 * 1024 * 1024)

        def fake_dump(dest, **_kw):
            dest.write_bytes(b"raw-dump")

        def fake_encrypt(src, dest, **_kw):
            dest.write_bytes(b"encrypted")
            return dest

        with patch.dict(os.environ, {**FAKE_ENV, "BACKUP_DIR": str(tmp_path)}, clear=False):
            with (
                patch("shutil.disk_usage", return_value=usage),
                patch.object(mod, "dump_database", side_effect=fake_dump),
                patch.object(mod, "encrypt_file", side_effect=fake_encrypt),
                patch("requests.post", return_value=MagicMock(**{"json.return_value": {"ok": True}})),
            ):
                result = mod.run_backup()
        assert result["status"] == "ok"
        assert result["parts"] == 1
        assert list(tmp_path.glob("*.dump")) == []
        assert len(list(tmp_path.glob("*.dump.gpg"))) == 1

    def test_skips_when_low_disk_and_alerts(self, tmp_path):
        usage = MagicMock(free=100 * 1024)
        with patch.dict(
            os.environ,
            {**FAKE_ENV, "BACKUP_DIR": str(tmp_path), "BACKUP_MIN_FREE_MB": "5000"},
            clear=False,
        ):
            with (
                patch("shutil.disk_usage", return_value=usage),
                patch("shared.tele_logging.sender.send_telegram_raw", return_value=True) as mock_alert,
            ):
                result = mod.run_backup()
        assert result["status"] == "skipped"
        mock_alert.assert_called_once()

    def test_alerts_and_cleans_split_parts_on_failure(self, tmp_path):
        usage = MagicMock(free=2000 * 1024 * 1024)

        def fake_dump(dest, **_kw):
            dest.write_bytes(b"raw")

        def fake_encrypt(src, dest, **_kw):
            dest.write_bytes(b"A" * 120)
            return dest

        with patch.dict(os.environ, {**FAKE_ENV, "BACKUP_DIR": str(tmp_path)}, clear=False):
            with (
                patch("shutil.disk_usage", return_value=usage),
                patch.object(mod, "dump_database", side_effect=fake_dump),
                patch.object(mod, "encrypt_file", side_effect=fake_encrypt),
                patch.object(mod, "send_to_telegram", side_effect=RuntimeError("upload error")),
                patch("shared.tele_logging.sender.send_telegram_raw", return_value=True) as mock_alert,
            ):
                result = mod.run_backup(_chunk_bytes=50)
        assert result["status"] == "error"
        mock_alert.assert_called_once()
        assert list(tmp_path.iterdir()) == []

    def test_fails_with_alert_when_passphrase_missing(self, tmp_path):
        usage = MagicMock(free=2000 * 1024 * 1024)
        env = {**FAKE_ENV, "BACKUP_DIR": str(tmp_path), "BACKUP_GPG_PASSPHRASE": ""}
        with patch.dict(os.environ, env, clear=False):
            with (
                patch("shutil.disk_usage", return_value=usage),
                patch("shared.tele_logging.sender.send_telegram_raw", return_value=True) as mock_alert,
            ):
                result = mod.run_backup()
        assert result["status"] == "error"
        assert "BACKUP_GPG_PASSPHRASE" in result["error"]
        mock_alert.assert_called_once()

    def test_fails_with_alert_when_telegram_not_configured(self, tmp_path):
        usage = MagicMock(free=2000 * 1024 * 1024)
        env = {**FAKE_ENV, "BACKUP_DIR": str(tmp_path), "BACKUP_TELEGRAM_CHAT_ID": ""}
        with patch.dict(os.environ, env, clear=False):
            with (
                patch("shutil.disk_usage", return_value=usage),
                patch("shared.tele_logging.sender.send_telegram_raw", return_value=True) as mock_alert,
            ):
                result = mod.run_backup()
        assert result["status"] == "error"
        assert "BACKUP_TELEGRAM_CHAT_ID" in result["error"]
        mock_alert.assert_called_once()


# ---------------------------------------------------------------------------
# Management command: backup_database
# ---------------------------------------------------------------------------


class TestBackupDatabaseCommand:
    def test_dry_run_reports_free_disk(self, tmp_path):
        out = StringIO()
        usage = MagicMock(free=1000 * 1024 * 1024)
        with patch.dict(os.environ, {**FAKE_ENV, "BACKUP_DIR": str(tmp_path)}, clear=False):
            with patch("shutil.disk_usage", return_value=usage):
                call_command("backup_database", "--dry-run", stdout=out)
        assert "1000 MB" in out.getvalue()

    def test_exits_nonzero_on_error_status(self):
        with patch("apps.core.management.commands.backup_database.run_backup", return_value={"status": "error", "error": "boom", "stamp": "x"}):
            with pytest.raises(SystemExit) as exc:
                call_command("backup_database")
        assert exc.value.code == 1
