"""Encrypted PostgreSQL database backup pipeline.

Runs via ``python manage.py backup_database`` (or ``run_backup()``):

1. Validate required config (passphrase, Telegram target/token).
2. Check free disk space and skip with a Telegram alert when low.
3. Run ``pg_dump -Fc`` into a temporary dump file.
4. Encrypt symmetrically with GPG AES-256 via stdin (never on CLI args) and
   immediately delete the plaintext dump.
5. Split the encrypted file under ``BACKUP_SPLIT_MB`` MB (Telegram limit).
6. Upload every part to ``BACKUP_TELEGRAM_CHAT_ID`` (retrying fallback proxies).
7. Rotate local backup sets, keeping the newest ``BACKUP_KEEP`` sets.
"""
from __future__ import annotations

import logging
import os
import re
import shutil
import subprocess
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import requests

log = logging.getLogger(__name__)

_PART_SUFFIX_RE = re.compile(r"\.part\d+$")


@dataclass(frozen=True)
class BackupConfig:
    backup_dir: Path
    min_free_mb: int
    keep: int
    split_mb: int
    gpg_passphrase: str
    telegram_chat_id: str
    bot_token: str
    pg_host: str
    pg_port: str
    pg_user: str
    pg_db: str
    pg_password: str


def _int_env(name: str, default: int) -> int:
    raw = os.environ.get(name, "").strip()
    return int(raw) if raw else default


def load_backup_config() -> BackupConfig:
    """Read backup and PostgreSQL settings from environment variables."""
    token = os.environ.get("BACKUP_BOT_TOKEN", "").strip() or os.environ.get("CONFIG_BOT_TOKEN", "").strip()
    return BackupConfig(
        backup_dir=Path(os.environ.get("BACKUP_DIR", "").strip() or "/app/backups/db"),
        min_free_mb=_int_env("BACKUP_MIN_FREE_MB", 500),
        keep=_int_env("BACKUP_KEEP", 7),
        split_mb=_int_env("BACKUP_SPLIT_MB", 45),
        gpg_passphrase=os.environ.get("BACKUP_GPG_PASSPHRASE", ""),
        telegram_chat_id=os.environ.get("BACKUP_TELEGRAM_CHAT_ID", "").strip(),
        bot_token=token,
        pg_host=os.environ.get("POSTGRES_HOST", "").strip() or "127.0.0.1",
        pg_port=os.environ.get("POSTGRES_PORT", "").strip() or "5432",
        pg_user=os.environ.get("POSTGRES_USER", "").strip() or "postgres",
        pg_db=os.environ.get("POSTGRES_DB", "").strip() or "customer_yab",
        pg_password=os.environ.get("POSTGRES_PASSWORD", ""),
    )


def run_backup(
    *,
    backup_dir: Path | None = None,
    min_free_mb: int | None = None,
    keep: int | None = None,
    split_mb: int | None = None,
    gpg_passphrase: str | None = None,
    telegram_chat_id: str | None = None,
    bot_token: str | None = None,
    _chunk_bytes: int | None = None,
) -> dict[str, Any]:
    """Execute the full backup pipeline and return a status dict."""
    cfg = load_backup_config()
    bdir = backup_dir or cfg.backup_dir
    mfree = min_free_mb if min_free_mb is not None else cfg.min_free_mb
    nkeep = keep if keep is not None else cfg.keep
    smb = split_mb if split_mb is not None else cfg.split_mb
    passphrase = gpg_passphrase if gpg_passphrase is not None else cfg.gpg_passphrase
    chat_id = telegram_chat_id if telegram_chat_id is not None else cfg.telegram_chat_id
    token = bot_token if bot_token is not None else cfg.bot_token

    bdir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(tz=timezone.utc).strftime("%Y%m%d_%H%M%S")

    free = free_mb_on(bdir)
    if free < mfree:
        msg = f"DB backup skipped: only {free} MB free on {bdir} (minimum {mfree} MB required)"
        log.warning(msg)
        _send_alert(msg)
        return {"status": "skipped", "free_mb": free, "min_free_mb": mfree}

    dump_path = bdir / f"db_{stamp}.dump"
    enc_path = bdir / f"db_{stamp}.dump.gpg"

    try:
        _validate_runtime_config(passphrase=passphrase, chat_id=chat_id, bot_token=token)
        dump_database(dump_path, config=cfg)
        encrypt_file(dump_path, enc_path, passphrase=passphrase)
        dump_path.unlink(missing_ok=True)

        parts = split_file(enc_path, split_mb=smb, _chunk_bytes=_chunk_bytes)
        send_to_telegram(parts, chat_id=chat_id, bot_token=token)

        if len(parts) > 1 and enc_path.exists():
            enc_path.unlink()

        rotate_old_backups(bdir, keep=nkeep)
        log.info("DB backup completed: %d part(s) for stamp %s", len(parts), stamp)
        return {"status": "ok", "stamp": stamp, "parts": len(parts)}

    except (RuntimeError, ValueError, OSError, subprocess.SubprocessError, requests.exceptions.RequestException) as exc:
        msg = f"DB backup FAILED ({stamp}): {exc}"
        log.warning(msg, exc_info=True)
        _send_alert(msg)
        _cleanup_stamp_files(bdir, stamp)
        return {"status": "error", "error": str(exc), "stamp": stamp}


def _validate_runtime_config(*, passphrase: str, chat_id: str, bot_token: str) -> None:
    if not passphrase:
        raise ValueError("BACKUP_GPG_PASSPHRASE must be set before running database backups.")
    if not chat_id:
        raise ValueError("BACKUP_TELEGRAM_CHAT_ID must be set to deliver database backups.")
    if not bot_token:
        raise ValueError("BACKUP_BOT_TOKEN or CONFIG_BOT_TOKEN must be set to deliver database backups.")


def _cleanup_stamp_files(backup_dir: Path, stamp: str) -> None:
    for path in backup_dir.glob(f"db_{stamp}.*"):
        path.unlink(missing_ok=True)


def free_mb_on(path: Path) -> int:
    """Return available megabytes on the filesystem containing *path* (or nearest parent)."""
    target = path
    while not target.exists() and target.parent != target:
        target = target.parent
    try:
        usage = shutil.disk_usage(target)
        return usage.free // (1024 * 1024)
    except OSError:
        return 0


def dump_database(dest: Path, *, config: BackupConfig | None = None) -> None:
    """Run ``pg_dump -Fc`` into *dest* using PostgreSQL env credentials."""
    cfg = config or load_backup_config()
    env = {**os.environ, "PGPASSWORD": cfg.pg_password}
    cmd = [
        "pg_dump",
        "-Fc",
        "-h", cfg.pg_host,
        "-p", cfg.pg_port,
        "-U", cfg.pg_user,
        "-d", cfg.pg_db,
        "-f", str(dest),
    ]
    try:
        result = subprocess.run(cmd, env=env, capture_output=True)
    except FileNotFoundError as exc:
        raise RuntimeError(f"pg_dump binary not found on PATH: {exc}") from exc
    if result.returncode != 0:
        stderr = result.stderr.decode(errors="replace") if isinstance(result.stderr, bytes) else str(result.stderr)
        raise RuntimeError(f"pg_dump exited {result.returncode}: {stderr}")


def encrypt_file(src: Path, dest: Path, *, passphrase: str | None = None) -> Path:
    """Encrypt *src* symmetrically with GPG AES-256 via stdin passphrase."""
    pw = passphrase if passphrase is not None else load_backup_config().gpg_passphrase
    if not pw:
        raise ValueError("BACKUP_GPG_PASSPHRASE must be set.")
    cmd = [
        "gpg",
        "--batch",
        "--yes",
        "--symmetric",
        "--cipher-algo", "AES256",
        "--passphrase-fd", "0",
        "--output", str(dest),
        str(src),
    ]
    try:
        result = subprocess.run(cmd, input=pw.encode("utf-8"), capture_output=True)
    except FileNotFoundError as exc:
        raise RuntimeError(f"gpg binary not found on PATH: {exc}") from exc
    if result.returncode != 0:
        stderr = result.stderr.decode(errors="replace") if isinstance(result.stderr, bytes) else str(result.stderr)
        raise RuntimeError(f"gpg exited {result.returncode}: {stderr}")
    return dest


def split_file(src: Path, *, split_mb: int = 45, _chunk_bytes: int | None = None) -> list[Path]:
    """Split *src* into parts of at most ``split_mb`` MiB."""
    chunk_size = _chunk_bytes if _chunk_bytes is not None else split_mb * 1024 * 1024
    if chunk_size <= 0:
        raise ValueError("split size must be positive")
    if src.stat().st_size <= chunk_size:
        return [src]

    parts: list[Path] = []
    with src.open("rb") as fh:
        idx = 0
        while True:
            chunk = fh.read(chunk_size)
            if not chunk:
                break
            part = src.parent / f"{src.name}.part{idx:03d}"
            part.write_bytes(chunk)
            parts.append(part)
            idx += 1
    return parts


def send_to_telegram(
    parts: list[Path],
    *,
    chat_id: str,
    bot_token: str,
    proxy: str | None = None,
) -> None:
    """Upload each backup part via Telegram ``sendDocument``, retrying fallback proxies."""
    from shared.telegram_alerts import proxy_candidates

    proxies_to_try = [proxy] if proxy else proxy_candidates()
    url = f"https://api.telegram.org/bot{bot_token}/sendDocument"
    total = len(parts)

    for index, part in enumerate(parts, start=1):
        sent = False
        last_error = ""
        caption = f"DB backup {part.name} ({index}/{total})"
        with part.open("rb") as fh:
            for proxy_url in proxies_to_try:
                proxies = {"http": proxy_url, "https": proxy_url} if proxy_url else None
                try:
                    resp = requests.post(
                        url,
                        data={"chat_id": chat_id, "caption": caption},
                        files={"document": (part.name, fh)},
                        proxies=proxies,
                        timeout=120,
                    )
                    data = resp.json()
                    if isinstance(data, dict) and data.get("ok"):
                        sent = True
                        break
                    last_error = str(data.get("description", data) if isinstance(data, dict) else data)
                except (requests.exceptions.RequestException, ValueError) as exc:
                    last_error = str(exc)
                finally:
                    fh.seek(0)
        if not sent:
            raise RuntimeError(f"Telegram upload failed for {part.name}: {last_error}")


def rotate_old_backups(backup_dir: Path, *, keep: int | None = None) -> None:
    """Keep the newest *keep* backup sets (``.gpg`` or ``.gpg.partNNN``) and delete older ones."""
    nkeep = keep if keep is not None else load_backup_config().keep
    groups: dict[str, list[Path]] = {}
    for path in backup_dir.iterdir():
        if not path.is_file():
            continue
        base_name = _PART_SUFFIX_RE.sub("", path.name)
        if base_name.endswith(".gpg"):
            groups.setdefault(base_name, []).append(path)

    ordered = sorted(
        groups.items(),
        key=lambda item: (max(p.stat().st_mtime for p in item[1]), item[0]),
    )
    to_remove = ordered[:-nkeep] if nkeep > 0 else ordered
    for _group_name, group_paths in to_remove:
        for old in group_paths:
            try:
                old.unlink()
                log.debug("Rotated old backup file: %s", old.name)
            except OSError as exc:
                log.warning("Could not delete old backup %s: %s", old, exc)


def _send_alert(message: str) -> None:
    """Best-effort ops alert via ``shared.tele_logging.sender``."""
    try:
        from shared.tele_logging.sender import send_telegram_raw

        send_telegram_raw(message)
    except (ImportError, OSError, ValueError, RuntimeError) as exc:
        log.warning("Could not send backup alert: %s", exc)
