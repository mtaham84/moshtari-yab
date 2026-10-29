"""Instagram session lifecycle helpers: error classification, bounded recovery policy, cross-process account lock,
and an atomic 0600 file mirror of the session next to the database copy.

The database column ``ig_accounts.session_json`` stays the primary store (it lives in the Postgres volume, so it
survives container recreation). The mirror under ``INSTAGRAM_SESSION_DIR`` (the ``appdata`` volume) is a second copy
used to restore a session if the DB value is ever empty. Nothing here logs in by itself.
"""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
import enum
import fcntl
import json
import logging
import os
from pathlib import Path
import re
import tempfile
from typing import Any, Iterator

log = logging.getLogger("instagram.session")


class ErrorKind(str, enum.Enum):
    LOGIN_REQUIRED = "login_required"      # server no longer accepts the session
    CHALLENGE = "challenge_required"       # human verification / 2FA / checkpoint
    BAD_CREDENTIALS = "bad_credentials"
    RATE_LIMIT = "rate_limited"            # 429 / please wait / feedback restriction
    NETWORK = "network_error"
    OTHER = "unclassified"


# (class-name fragments, text fragments) checked in order; challenge and restrictions win over login_required
_RULES: tuple[tuple[ErrorKind, tuple[str, ...], tuple[str, ...]], ...] = (
    (ErrorKind.CHALLENGE, ("challenge", "twofactor", "sentryblock", "consent", "checkpoint", "recaptcha"),
     ("challenge_required", "checkpoint", "two_factor", "sentryblock", "consent_required", "suspended", "disabled your account")),
    (ErrorKind.BAD_CREDENTIALS, ("badpassword", "badcredentials"), ("bad_password", "incorrect password", "invalid credentials")),
    (ErrorKind.RATE_LIMIT, ("feedbackrequired", "pleasewait", "ratelimit", "toomanyrequests"),
     ("feedback_required", "please wait", "429", "too many requests", "rate limit", "action blocked")),
    (ErrorKind.NETWORK, ("connectionerror", "timeout", "proxyerror", "sslerror", "connecttimeout", "readtimeout"),
     ("timed out", "connection reset", "connection aborted", "name resolution", "temporary failure", "tlsv1")),
    (ErrorKind.LOGIN_REQUIRED, ("loginrequired",), ("login_required", "login required")),
)


def classify(exc: BaseException | str) -> ErrorKind:
    names = " ".join(c.__name__.lower() for c in type(exc).__mro__) if isinstance(exc, BaseException) else ""
    text = str(exc).lower()
    for kind, name_bits, text_bits in _RULES:
        if any(b in names for b in name_bits) or any(b in text for b in text_bits):
            return kind
    return ErrorKind.OTHER


_SECRET = re.compile(r"(sessionid|csrftoken|authorization|password|ds_user_id|mid|ig_did)([\"'=:\s]+)([^\s\"',;&}]+)", re.I)


def redact(text: Any, limit: int = 300) -> str:
    return _SECRET.sub(r"\1\2[REDACTED]", str(text))[:limit]


@dataclass(frozen=True)
class Policy:
    session_dir: Path
    auto_reauth: bool
    max_attempts: int
    recovery_cooldown_s: int
    net_retries: int
    net_initial_s: float
    net_max_s: float

    @classmethod
    def from_env(cls) -> "Policy":
        e = os.environ.get
        return cls(
            session_dir=Path(e("INSTAGRAM_SESSION_DIR", "/app/data/instagram_sessions")),
            auto_reauth=e("INSTAGRAM_SESSION_AUTO_REAUTH_ENABLED", "false").strip().lower() in {"1", "true", "yes", "on"},
            max_attempts=int(e("INSTAGRAM_SESSION_MAX_RECOVERY_ATTEMPTS", "1")),
            recovery_cooldown_s=int(e("INSTAGRAM_SESSION_RECOVERY_COOLDOWN_SECONDS", "21600")),
            net_retries=int(e("INSTAGRAM_NETWORK_MAX_RETRIES", "3")),
            net_initial_s=float(e("INSTAGRAM_NETWORK_INITIAL_BACKOFF_SECONDS", "2")),
            net_max_s=float(e("INSTAGRAM_NETWORK_MAX_BACKOFF_SECONDS", "60")),
        )

    def backoff(self, attempt: int, jitter=None) -> float:
        import random
        jitter = jitter or random.uniform
        return min(self.net_max_s, self.net_initial_s * (2 ** attempt)) * jitter(0.5, 1.0)

    def may_recover(self, kind: ErrorKind, attempts_so_far: int) -> bool:
        """Only a genuinely rejected session may trigger a credential login, never challenges, limits or network errors."""
        return self.auto_reauth and kind is ErrorKind.LOGIN_REQUIRED and attempts_so_far < self.max_attempts


def session_path(account_id: int, policy: Policy | None = None) -> Path:
    """Path from the stable numeric id only, never from the username."""
    return (policy or Policy.from_env()).session_dir / f"account_{int(account_id)}.json"


def save_atomic(path: Path, data: dict) -> bool:
    """Write JSON via temp file + fsync + rename with 0600; the previous file survives any failure."""
    tmp = None
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        try:
            os.chmod(path.parent, 0o700)
        except OSError:
            pass
        fd, tmp = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=path.parent)
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(data, fh, ensure_ascii=False)
            fh.flush()
            os.fsync(fh.fileno())
        os.chmod(tmp, 0o600)
        os.replace(tmp, path)
        return True
    except Exception as exc:
        log.error("session_save_failed account_file=%s error=%s", path.name, redact(exc))
        if tmp:
            try:
                os.unlink(tmp)
            except OSError:
                pass
        return False


def load_file(path: Path) -> tuple[dict | None, str]:
    """(settings, state) with state in {found, missing, corrupt}. A corrupt file is left untouched."""
    if not path.exists():
        return None, "missing"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return (data, "found") if isinstance(data, dict) and data else (None, "corrupt")
    except Exception:
        return None, "corrupt"


@contextmanager
def account_lock(account_id: int, policy: Policy | None = None, blocking: bool = False) -> Iterator[bool]:
    """Exclusive per-account flock shared by every container mounting the session volume; released by the OS on crash."""
    pol = policy or Policy.from_env()
    pol.session_dir.mkdir(parents=True, exist_ok=True)
    fh = open(pol.session_dir / f".account_{int(account_id)}.lock", "a+")
    got = False
    try:
        try:
            fcntl.flock(fh, fcntl.LOCK_EX | (0 if blocking else fcntl.LOCK_NB))
            got = True
        except OSError:
            got = False
        yield got
    finally:
        if got:
            fcntl.flock(fh, fcntl.LOCK_UN)
        fh.close()
