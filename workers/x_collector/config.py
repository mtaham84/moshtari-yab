"""Configuration for X seller eligibility."""
import os


def seller_allowlist() -> frozenset[str]:
    return frozenset(value.strip() for value in os.getenv('X_SELLER_ALLOWLIST', '').split(',') if value.strip())
