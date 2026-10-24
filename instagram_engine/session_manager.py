import json
import logging
from pathlib import Path
from typing import Optional, Dict, Any
from urllib.parse import urlparse

try:
    from instagrapi import Client
    from instagrapi.exceptions import (
        LoginRequired,
        ChallengeRequired,
        FeedbackRequired,
        ClientError,
    )
except ImportError:
    Client = Any  # type: ignore
    class LoginRequired(Exception): pass
    class ChallengeRequired(Exception): pass
    class FeedbackRequired(Exception): pass
    class ClientError(Exception): pass

from sqlalchemy.orm import Session

from instagram_engine.models import IGAccount, AccountStatus, SessionLocal
from instagram_engine.config import settings
from instagram_engine.circuit_breaker import update_account_status

logger = logging.getLogger("instagram_engine.session_manager")


# Realistic static Android device profile templates (Samsung Galaxy S22 / S21)
DEFAULT_DEVICE_SETTINGS = {
    "app_version": "315.0.0.38.109",
    "android_version": 31,
    "android_release": "12.0",
    "dpi": "480dpi",
    "resolution": "1080x2340",
    "manufacturer": "samsung",
    "device": "SM-S901B",
    "model": "Galaxy S22",
    "cpu": "exynos2200",
    "version_code": "315003810",
}


class SessionManager:
    """
    Manages Instagrapi client lifecycle, proxy binding, device fingerprint persistence,
    and anti-ban session hygiene.
    """

    def __init__(self, account: IGAccount, storage_dir: Optional[Path] = None):
        self.account = account
        self.storage_dir = storage_dir or settings.session_storage_dir
        self.storage_dir.mkdir(parents=True, exist_ok=True)
        self.local_session_path = self.storage_dir / f"session_{self.account.username}.json"

    def get_client(self) -> Client:
        """
        Builds, configures, and authenticates an instagrapi Client with persistent
        fingerprints and proxy validation.
        """
        cl = Client()
        # Set humanized request delay range globally
        cl.delay_range = [2, 5]

        # 1. Bind and Validate Proxy
        self._bind_and_validate_proxy(cl)

        # 2. Attempt to load existing session
        session_data = self._load_persisted_session()

        if session_data:
            try:
                cl.set_settings(session_data)
                # Verify session health with a lightweight call
                logger.info("Validating existing session for @%s...", self.account.username)
                cl.get_timeline_feed()
                logger.info("✅ Session is healthy and authenticated for @%s", self.account.username)
                return cl
            except LoginRequired:
                logger.warning(
                    "Session expired for @%s. Preserving hardware UUIDs and re-authenticating...",
                    self.account.username
                )
                return self._reauthenticate(cl, old_session=session_data)
            except (ChallengeRequired, FeedbackRequired) as exc:
                logger.error("Session health check failed with Instagram challenge/block for @%s: %s", self.account.username, exc)
                raise
            except Exception as exc:
                logger.warning("Session verification failed for @%s (%s). Attempting fresh login with preserved device.", self.account.username, exc)
                return self._reauthenticate(cl, old_session=session_data)
        else:
            logger.info("No existing session found for @%s. Performing initial login...", self.account.username)
            return self._initial_login(cl)

    def _bind_and_validate_proxy(self, cl: Client) -> None:
        """Binds proxy and ensures strict safety limits regarding proxy reuse across accounts."""
        if not self.account.proxy_url:
            logger.warning(
                "⚠️ Account @%s has NO PROXY configured! High risk of immediate IP ban.",
                self.account.username
            )
            return

        proxy_url = self.account.proxy_url
        cl.set_proxy(proxy_url)

        # Parse proxy host to check sharing across accounts
        try:
            parsed = urlparse(proxy_url)
            proxy_host = parsed.hostname or proxy_url
            db: Session = SessionLocal()
            try:
                shared_count = db.query(IGAccount).filter(
                    IGAccount.proxy_url.ilike(f"%{proxy_host}%"),
                    IGAccount.status != AccountStatus.BANNED,
                ).count()

                if shared_count > 3:
                    logger.warning(
                        "🚨 [PROXY OVERUSE WARNING] Proxy host '%s' is shared across %d accounts in the database! "
                        "Instagram may flag accounts sharing the same static proxy IP (recommended max: 2-3 accounts/IP).",
                        proxy_host,
                        shared_count,
                    )
                else:
                    logger.debug("Proxy host '%s' is safely assigned to %d account(s).", proxy_host, shared_count)
            finally:
                db.close()
        except Exception as exc:
            logger.debug("Proxy validation query failed: %s", exc)

    def _load_persisted_session(self) -> Optional[Dict[str, Any]]:
        """Loads session JSON prioritizing database, falling back to local file storage."""
        if self.account.session_json and isinstance(self.account.session_json, dict):
            return self.account.session_json

        if self.local_session_path.exists():
            try:
                with open(self.local_session_path, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception as exc:
                logger.warning("Failed to parse local session file %s: %s", self.local_session_path, exc)
        return None

    def _save_session(self, cl: Client) -> None:
        """Saves session settings both to local JSON backup and PostgreSQL database."""
        session_data = cl.get_settings()

        # 1. Save locally
        try:
            with open(self.local_session_path, "w", encoding="utf-8") as f:
                json.dump(session_data, f, indent=2, ensure_ascii=False)
        except Exception as exc:
            logger.error("Failed to write local session backup: %s", exc)

        # 2. Save to DB
        db: Session = SessionLocal()
        try:
            acc = db.query(IGAccount).filter(IGAccount.id == self.account.id).first()
            if acc:
                acc.session_json = session_data
                acc.device_settings = cl.device_settings or DEFAULT_DEVICE_SETTINGS
                acc.status = AccountStatus.ACTIVE
                db.commit()
                # Update in-memory reference
                self.account.session_json = session_data
        except Exception as exc:
            db.rollback()
            logger.error("Failed to save session settings to DB: %s", exc)
        finally:
            db.close()

    def _initial_login(self, cl: Client) -> Client:
        """Configures realistic Android device fingerprint and performs first-time authentication."""
        device_profile = self.account.device_settings or DEFAULT_DEVICE_SETTINGS
        cl.set_device(device_profile)

        try:
            logger.info("Logging in to Instagram as @%s...", self.account.username)
            cl.login(self.account.username, self.account.password)
            self._save_session(cl)
            logger.info("✅ First-time login successful for @%s. Session and hardware profile persisted.", self.account.username)
            return cl
        except (ChallengeRequired, FeedbackRequired):
            raise
        except Exception as exc:
            logger.critical("Login failed for @%s: %s", self.account.username, exc)
            update_account_status(self.account.id, AccountStatus.CHALLENGED, log_message=f"Initial login failed: {exc}")
            raise

    def _reauthenticate(self, cl: Client, old_session: Dict[str, Any]) -> Client:
        """
        Re-authenticates an expired session while strictly preserving the existing
        hardware UUIDs to prevent virtual device mismatch bans.
        """
        # Preserve exact virtual hardware UUIDs
        old_uuids = old_session.get("uuids", {})
        if old_uuids:
            cl.set_uuids(old_uuids)

        old_device = old_session.get("device_settings") or self.account.device_settings or DEFAULT_DEVICE_SETTINGS
        cl.set_device(old_device)

        try:
            logger.info("Re-authenticating @%s with preserved hardware fingerprint...", self.account.username)
            cl.login(self.account.username, self.account.password)
            self._save_session(cl)
            logger.info("✅ Re-authentication successful for @%s", self.account.username)
            return cl
        except (ChallengeRequired, FeedbackRequired):
            raise
        except Exception as exc:
            logger.critical("Re-authentication failed for @%s: %s", self.account.username, exc)
            update_account_status(self.account.id, AccountStatus.CHALLENGED, log_message=f"Re-authentication failed: {exc}")
            raise
