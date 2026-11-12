# Database Backup

## Overview

The backup job runs `manage.py backup_database` and handles the full pipeline:

1. **Free-disk check** — skips and sends a Telegram alert if free space is below
   `BACKUP_MIN_FREE_MB` (default 500 MB).
2. **pg_dump** — dumps the entire database in custom compressed format (`-Fc`).
3. **GPG encryption** — symmetric AES-256 using `BACKUP_GPG_PASSPHRASE`.
4. **Split** — splits files larger than `BACKUP_SPLIT_MB` MB (default 45 MB)
   to stay within Telegram's 50 MB document limit.
5. **Telegram upload** — sends each part to `BACKUP_TELEGRAM_CHAT_ID`.
6. **Rotation** — deletes oldest local `.gpg` files, keeping `BACKUP_KEEP` newest.

## Required environment variables

Set these in `.env` (see `.env.example` for descriptions):

```
BACKUP_DIR=/app/backups/db
BACKUP_GPG_PASSPHRASE=<strong secret>
BACKUP_TELEGRAM_CHAT_ID=-100123456789
BACKUP_BOT_TOKEN=<token>        # or leave blank to reuse CONFIG_BOT_TOKEN
BACKUP_KEEP=7
BACKUP_MIN_FREE_MB=500
BACKUP_SPLIT_MB=45
```

## Schedule (systemd)

Create `/etc/systemd/system/db-backup.service`:

```ini
[Unit]
Description=Moshtari-Yab database backup
After=docker.service

[Service]
Type=oneshot
WorkingDirectory=/root/moshtari-yab
ExecStart=docker compose --profile maintenance run --rm -T db-backup
```

Create `/etc/systemd/system/db-backup.timer`:

```ini
[Unit]
Description=Run db-backup daily at 03:00

[Timer]
OnCalendar=*-*-* 03:00:00
Persistent=true

[Install]
WantedBy=timers.target
```

Enable:

```bash
systemctl daemon-reload
systemctl enable --now db-backup.timer
systemctl list-timers db-backup.timer   # verify next run
```

## Dry run

```bash
python manage.py backup_database --dry-run
```

Checks free disk space only; prints what would happen and exits.

## Restore procedure

> **Test this once on a staging copy before you need it in a real incident.**

### 1. Collect the parts from Telegram

Download all `db_<stamp>.dump.gpg` files (and `.dump.gpg.part*` if split)
into a local directory, e.g. `/tmp/restore/`.

### 2. Reassemble split parts (if any)

```bash
cd /tmp/restore
cat db_<stamp>.dump.gpg.part* > db_<stamp>.dump.gpg
```

Skip this step if the backup was not split (single `.gpg` file).

### 3. Decrypt

```bash
gpg --decrypt --output db_<stamp>.dump db_<stamp>.dump.gpg
```

(Or non-interactively via stdin: `printf '%s' "$BACKUP_GPG_PASSPHRASE" | gpg --batch --yes --passphrase-fd 0 --decrypt --output db_<stamp>.dump db_<stamp>.dump.gpg`.)

Verify the dump is valid:

```bash
docker compose exec -T db pg_restore --list < db_<stamp>.dump | head -20
```

### 4. Restore to the database

```bash
docker compose exec -T db pg_restore \
    -U postgres -d customer_yab \
    --clean --if-exists \
    < db_<stamp>.dump
```

> **Warning:** `--clean --if-exists` drops and recreates all objects.
> This is destructive. Run against an empty or staging database first.

### 5. Run Django migrations (safety check)

```bash
docker compose run --rm web python manage.py migrate --check
```

Should report "No migrations to apply."

### 6. Verify row counts

```bash
docker compose run --rm web python manage.py shell -c "
from apps.discovery.models.core import Opportunity
print('Opportunities:', Opportunity.objects.count())
"
```

## Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| Backup skipped, Telegram alert | Free disk below `BACKUP_MIN_FREE_MB` | Free space or lower threshold |
| `pg_dump: connection refused` | DB not reachable | Check `POSTGRES_HOST`/port and that the `db` container is running |
| `gpg: bad passphrase` | Wrong `BACKUP_GPG_PASSPHRASE` | Confirm env var matches the one used at backup time |
| Telegram upload fails | Network / bot token / chat id | Check `BACKUP_BOT_TOKEN`, `BACKUP_TELEGRAM_CHAT_ID`, and proxy settings |
| Split parts out of order | Downloaded in wrong order | Re-download and sort by part number before `cat` |
