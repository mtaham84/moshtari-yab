import logging
from datetime import datetime, timedelta, timezone
from typing import Callable, Any, Optional
from functools import wraps

try:
    from instagrapi.exceptions import (
        FeedbackRequired,
        PleaseWaitFewMinutes,
        ChallengeRequired,
        SentryBlock,
        LoginRequired,
        ClientError,
        ClientConnectionError,
        BadPassword,
        TwoFactorRequired,
    )
except ImportError:
    class FeedbackRequired(Exception): pass
    class PleaseWaitFewMinutes(Exception): pass
    class ChallengeRequired(Exception): pass
    class SentryBlock(Exception): pass
    class LoginRequired(Exception): pass
    class ClientError(Exception): pass
    class ClientConnectionError(Exception): pass
    class BadPassword(Exception): pass
    class TwoFactorRequired(Exception): pass

from sqlalchemy.orm import Session

from instagram_engine.models import IGAccount, AccountStatus, SessionLocal

logger = logging.getLogger("instagram_engine.circuit_breaker")


class CircuitBreakerTriggered(Exception):
    """Raised when an operation is aborted by the circuit breaker due to account penalty."""
    def __init__(self, account_username: str, reason: str, cooldown_until: Optional[datetime] = None):
        self.account_username = account_username
        self.reason = reason
        self.cooldown_until = cooldown_until
        super().__init__(f"Circuit breaker tripped for {account_username}: {reason} (Cooldown until: {cooldown_until})")


def update_account_status(
    account_id: int,
    status: AccountStatus,
    cooldown_hours: Optional[float] = None,
    log_message: str = "",
) -> None:
    """Updates account status and cooldown timestamp immediately in a standalone DB session."""
    db: Session = SessionLocal()
    try:
        acc = db.query(IGAccount).filter(IGAccount.id == account_id).first()
        if not acc:
            logger.error("Failed to update status for non-existent account ID %s", account_id)
            return

        acc.status = status
        now_utc = datetime.now(timezone.utc)
        if cooldown_hours is not None and cooldown_hours > 0:
            acc.cooldown_until = now_utc + timedelta(hours=cooldown_hours)
        elif status == AccountStatus.ACTIVE:
            acc.cooldown_until = None

        acc.updated_at = now_utc
        db.commit()
        logger.warning(
            "Account @%s status updated to %s. Cooldown until: %s. Detail: %s",
            acc.username,
            status.value,
            acc.cooldown_until,
            log_message,
        )
    except Exception as exc:
        db.rollback()
        logger.exception("Error updating account %s status in DB: %s", account_id, exc)
    finally:
        db.close()


def handle_instagram_exceptions(func: Callable) -> Callable:
    """
    Decorator for wrapping Instagram operations with enterprise-grade circuit breaking:
    - FeedbackRequired: 48 hours cooldown
    - PleaseWaitFewMinutes: 2 hours cooldown
    - ChallengeRequired / SentryBlock: CHALLENGED status (halt all ops)
    - LoginRequired: Triggers re-authentication hook or signals session expiration
    """
    @wraps(func)
    def wrapper(self, *args, **kwargs) -> Any:
        # Expected to be a method on a worker or manager that holds self.account (IGAccount)
        account: Optional[IGAccount] = getattr(self, "account", None)
        username = account.username if account else "unknown"
        account_id = account.id if account else None

        try:
            return func(self, *args, **kwargs)

        except FeedbackRequired as exc:
            msg = f"Instagram FeedbackRequired (Action Blocked): {exc}"
            logger.critical("🚨 [CRITICAL ACTION BLOCK] Account @%s: %s", username, msg)
            if account_id:
                update_account_status(account_id, AccountStatus.COOLDOWN, cooldown_hours=48.0, log_message=msg)
            raise CircuitBreakerTriggered(username, "FeedbackRequired (48h cooldown)", datetime.now(timezone.utc) + timedelta(hours=48)) from exc

        except PleaseWaitFewMinutes as exc:
            msg = f"Instagram PleaseWaitFewMinutes (Soft Rate Limit): {exc}"
            logger.warning("⏳ [RATE LIMIT] Account @%s: %s", username, msg)
            if account_id:
                update_account_status(account_id, AccountStatus.COOLDOWN, cooldown_hours=2.0, log_message=msg)
            raise CircuitBreakerTriggered(username, "PleaseWaitFewMinutes (2h cooldown)", datetime.now(timezone.utc) + timedelta(hours=2)) from exc

        except (ChallengeRequired, SentryBlock) as exc:
            msg = f"Instagram Challenge/Checkpoint or SentryBlock triggered: {exc}"
            logger.critical("🛑 [CHALLENGE DETECTED] Account @%s requires verification! Halting operations: %s", username, msg)
            if account_id:
                update_account_status(account_id, AccountStatus.CHALLENGED, cooldown_hours=None, log_message=msg)
            raise CircuitBreakerTriggered(username, f"ChallengeRequired/SentryBlock: {msg}") from exc

        except (BadPassword, TwoFactorRequired) as exc:
            msg = f"Credentials or 2FA required for account @{username}: {exc}"
            logger.critical("🔑 [AUTH FAILED] %s", msg)
            if account_id:
                update_account_status(account_id, AccountStatus.CHALLENGED, cooldown_hours=None, log_message=msg)
            raise

        except LoginRequired as exc:
            logger.warning("🔑 [SESSION EXPIRED] Account @%s session expired (LoginRequired). Preserving hardware fingerprint for re-login.", username)
            raise

        except ClientConnectionError as exc:
            logger.error("🌐 [NETWORK ERROR] Connection error on account @%s via proxy: %s", username, exc)
            raise

        except ClientError as exc:
            logger.error("⚠️ [INSTAGRAPI CLIENT ERROR] Account @%s error: %s", username, exc)
            raise

    return wrapper
