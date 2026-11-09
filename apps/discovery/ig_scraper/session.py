"""Scraper account state and Instagram session: rest/challenge state, file mirror, careful automatic re-login."""
import random
import time

from django.utils import timezone as dj_timezone

from apps.discovery.ig_session import ErrorKind, account_lock, load_file, redact, save_atomic, session_path


def set_state(account, status: str, message: str = "", hours: float | None = None, extra=()) -> None:
    from datetime import timedelta

    account.status = status
    account.status_message = message[:250]
    account.cooldown_until = dj_timezone.now() + timedelta(hours=hours) if hours else None
    account.save(update_fields=["status", "status_message", "cooldown_until", *extra])


def restore_from_mirror(run, account) -> None:
    """If the DB lost the session, recover it from the atomic file mirror (never overwrites an existing DB session)."""
    if account.session_json:
        return
    data, state = load_file(session_path(account.id, run.policy))
    if data:
        account.session_json = data
        account.save(update_fields=["session_json"])
        run.stdout.write(f"    session of @{account.username} restored from file mirror")
    elif state == "corrupt":
        run.stderr.write(f"    session_storage_error: mirror file for account {account.id} is corrupt; left untouched")


def persist_session(run, cl, account) -> None:
    """Stores rotated cookies/tokens (only when changed) in the DB and the 0600 file mirror."""
    try:
        import json as _json
        fresh = cl.get_settings()
        if _json.dumps(fresh, sort_keys=True, default=str) != _json.dumps(account.session_json, sort_keys=True, default=str):
            account.session_json = fresh
            account.last_action_at = dj_timezone.now()
            account.save(update_fields=["session_json", "last_action_at"])
            if not save_atomic(session_path(account.id, run.policy), fresh):
                run.stderr.write(f"    session_save_failed (file mirror) for account {account.id}")
    except Exception as exc:
        run.stderr.write(f"Notice: could not persist session for account {account.id}: {redact(exc)}")


def auto_renew_session(run, cl, account) -> bool:
    """
    One careful credential re-login for a session Instagram rejected, reusing the saved device/UUIDs.
    Runs only if INSTAGRAM_SESSION_AUTO_REAUTH_ENABLED is on, at most INSTAGRAM_SESSION_MAX_RECOVERY_ATTEMPTS per
    incident, under a cross-container account lock. Failure cools the account down (persisted in cooldown_until).
    """

    if cl is None or not account.password:
        return False
    attempts = run.renew_attempts.get(account.id, 0)
    if not run.policy.may_recover(ErrorKind.LOGIN_REQUIRED, attempts):
        return False
    try:
        from instagrapi.exceptions import ChallengeRequired, TwoFactorRequired, BadPassword
    except ImportError:
        return False
    run.renew_attempts = {**run.renew_attempts, account.id: attempts + 1}

    with account_lock(account.id, run.policy) as got:
        if not got:
            run.stdout.write(f"    recovery_skipped: another worker holds the lock for account {account.id}")
            return False
        old = account.session_json or {}
        if old.get("uuids"):
            cl.set_uuids(old["uuids"])

        def _no_challenge(username, choice=None):
            raise ChallengeRequired(f"Challenge code required via {choice}")
        cl.challenge_code_handler = _no_challenge
        try:
            run.stdout.write(run.style.WARNING(f"    recovery_started: account {account.id}"))
            time.sleep(random.uniform(8.0, 20.0))
            cl.login(account.username, account.password)
        except (ChallengeRequired, TwoFactorRequired) as exc:
            # Instagram wants a code (2FA / email / SMS): nobody is here to type it. The operator presses
            # «ورود» on the account page (/manage/instagram/accounts/) and types the code into that login.
            from apps.discovery.ig_login import record_login_failure
            record_login_failure(account, exc, actor="scraper")
            run.stderr.write(f"    recovery_failed: account {account.id} needs a verification code")
            return False
        except BadPassword:
            set_state(account, "CHALLENGED", "لاگین ناموفق بود: رمز عبور رد شد. رمز را بررسی کنید.")
            run.stderr.write(f"    recovery_failed: account {account.id} bad credentials")
            return False
        except Exception as exc:
            err_str = str(exc).lower()
            is_net_err = any(k in err_str for k in ["connection", "timeout", "timed out", "reset", "transport", "proxy", "eof", "ssl", "56", "connection closed"])
            if is_net_err:
                set_state(account, "COOLDOWN", f"خطای موقت ارتباط پروکسی/شبکه: {redact(exc, 120)}", hours=0.5)
                run.stderr.write(f"    recovery_skipped (network glitch): account {account.id}: {redact(exc)}")
            else:
                set_state(account, "CHALLENGED", f"لاگین مجدد با شکست مواجه شد: {redact(exc, 180)}")
                run.stderr.write(f"    recovery_failed: account {account.id}: {redact(exc)}")
            return False
        fresh = cl.get_settings()
        account.session_json = fresh
        set_state(account, "ACTIVE", "", extra=["session_json"])
        save_atomic(session_path(account.id, run.policy), fresh)
        run.renew_attempts[account.id] = 0
        run.stdout.write(run.style.SUCCESS(f"    recovery_succeeded: account {account.id}; session stored"))
        return True
