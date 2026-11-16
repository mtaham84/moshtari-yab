"""Configuration for X seller eligibility."""
import os


def seller_allowlist() -> frozenset[str]:
    return frozenset(value.strip() for value in os.getenv('X_SELLER_ALLOWLIST', '').split(',') if value.strip())


def require_wallet() -> bool:
    return os.getenv('X_REQUIRE_WALLET', 'true').strip().lower() not in {'0', 'false', 'no', 'off'}


def debug_capture_path() -> str | None:
    return os.getenv('X_DEBUG_CAPTURE_PATH') or None
