"""Configuration for X seller eligibility."""
import os


def seller_allowlist() -> frozenset[str]:
    return frozenset(value.strip() for value in os.getenv('X_SELLER_ALLOWLIST', '').split(',') if value.strip())


def require_wallet() -> bool:
    return os.getenv('X_REQUIRE_WALLET', 'true').strip().lower() not in {'0', 'false', 'no', 'off'}


def debug_capture_path() -> str | None:
    return os.getenv('X_DEBUG_CAPTURE_PATH') or None


def thread_collection_enabled() -> bool:
    return os.getenv('X_THREAD_COLLECT', 'false').strip().lower() in {'1', 'true', 'yes', 'on'}


def query_limit() -> int:
    return max(1, int(os.getenv('X_QUERY_MAX_PER_TENANT', '150')))
