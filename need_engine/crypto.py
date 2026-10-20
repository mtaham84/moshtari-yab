"""Small helpers for values encrypted at rest with Fernet."""
from __future__ import annotations

import os

from cryptography.fernet import Fernet, InvalidToken

_PREFIX = "enc:v1:"


def _fernet() -> Fernet:
    key = os.environ.get("FIELD_ENCRYPTION_KEY", "").strip()
    if not key:
        raise RuntimeError("FIELD_ENCRYPTION_KEY is required to read or write encrypted provider API keys.")
    try:
        return Fernet(key.encode("ascii"))
    except (ValueError, UnicodeEncodeError) as exc:
        raise RuntimeError("FIELD_ENCRYPTION_KEY must be a valid Fernet key. Generate one with Fernet.generate_key().") from exc


def _fernets() -> list[Fernet]:
    keys = [os.environ.get("FIELD_ENCRYPTION_KEY", "").strip()]
    keys.extend(k.strip() for k in os.environ.get("FIELD_ENCRYPTION_OLD_KEYS", "").split(","))
    result = []
    for key in keys:
        if not key:
            continue
        try:
            result.append(Fernet(key.encode("ascii")))
        except (ValueError, UnicodeEncodeError) as exc:
            raise RuntimeError("FIELD_ENCRYPTION_KEY and FIELD_ENCRYPTION_OLD_KEYS must contain valid Fernet keys.") from exc
    if not result:
        raise RuntimeError("FIELD_ENCRYPTION_KEY is required to read or write encrypted provider API keys.")
    return result


def encrypt_secret(value: str | None) -> str:
    value = value or ""
    if not value:
        return value
    if value.startswith(_PREFIX):
        decrypt_secret(value)  # validate the token and active key before accepting a pre-encrypted value
        return value
    return _PREFIX + _fernet().encrypt(value.encode("utf-8")).decode("ascii")


def is_encrypted(value: str | None) -> bool:
    return bool(value and value.startswith(_PREFIX))


def decrypt_secret(value: str | None) -> str:
    value = value or ""
    if not value.startswith(_PREFIX):
        return value  # legacy plaintext, converted by billing migration 0003
    try:
        token = value[len(_PREFIX):].encode("ascii")
        for fernet in _fernets():
            try:
                return fernet.decrypt(token).decode("utf-8")
            except InvalidToken:
                continue
    except (UnicodeEncodeError, UnicodeDecodeError) as exc:
        raise RuntimeError("Provider API key cannot be decrypted; check FIELD_ENCRYPTION_KEY and restore the matching key.") from exc
    raise RuntimeError("Provider API key cannot be decrypted; check FIELD_ENCRYPTION_KEY and restore the matching key.")


def reencrypt_secret(value: str | None) -> str:
    value = value or ""
    if not value:
        return value
    plaintext = decrypt_secret(value)
    return _PREFIX + _fernet().encrypt(plaintext.encode("utf-8")).decode("ascii")
