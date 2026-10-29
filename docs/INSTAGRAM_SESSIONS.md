# Instagram session lifecycle

**Storage.** The primary copy is `ig_accounts.session_json` (Postgres volume). A second copy is written atomically
(temp file, fsync, rename, mode 0600) to `INSTAGRAM_SESSION_DIR/account_<id>.json` on the `appdata` volume.
Paths use the numeric account id, never the username. If the DB value is empty the mirror is restored; a corrupt
mirror is left untouched and reported (`session_storage_error`).

**Startup.** The saved session is loaded; no login is performed. The first real scrape shows whether Instagram still
accepts it. After each target the live settings are saved if they changed.

**Errors** (`apps/discovery/ig_session.py`):
| Kind | Reaction |
|---|---|
| login_required | auto re-login only if `INSTAGRAM_SESSION_AUTO_REAUTH_ENABLED=true`, max `..._MAX_RECOVERY_ATTEMPTS` per incident, one retry of the read-only search; otherwise account → CHALLENGED, set a new sessionid in `/manage/` |
| challenge / 2FA / bad password | account → CHALLENGED, no automatic login |
| 429 / please wait / feedback_required | account → COOLDOWN 2h, no login |
| network / unknown | no state change, no login |

A failed re-login puts the account in COOLDOWN for 6h (persisted in `cooldown_until`, survives restarts).
A per-account `flock` in the session dir stops two containers from recovering the same account at once.

**Deploy.** `git pull && docker compose build web && docker compose up -d web ig`; verify with
`docker compose exec ig ls -l /app/data/instagram_sessions`. Back up: `docker compose exec ig tar czf - -C /app/data instagram_sessions > sessions-backup.tgz`
(treat the archive as a secret). Logs never contain cookies, tokens or passwords (`redact()`).
