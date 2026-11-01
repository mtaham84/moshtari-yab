"""
Core typed errors, exception mapping, and sanitized logging utilities (P08.01 / R41).

Guarantees:
1. Standard ErrorCode enumeration mapping to HTTP/Domain status codes.
2. Strict redaction of secrets, API keys, bearer tokens, passwords, and database credentials
   from logs, error representations, and user-facing messages.
3. Structured, context-aware sanitized logging with correlation/tenant/operation metadata.
"""

from __future__ import annotations

import enum
import json
import logging
import re
from typing import Any, Mapping, Optional

# Secret detection regexes
_PATTERNS = [
    # API keys (OpenAI sk-..., Google AIzaSy..., xAI, Anthropic sk-ant...)
    (re.compile(r"(sk-[a-zA-Z0-9_\-]{16,})", re.I), "[REDACTED_API_KEY]"),
    (re.compile(r"(AIzaSy[a-zA-Z0-9_\-]{30,})", re.I), "[REDACTED_API_KEY]"),
    (re.compile(r"(xai-[a-zA-Z0-9_\-]{20,})", re.I), "[REDACTED_API_KEY]"),
    (re.compile(r"(sk-ant-[a-zA-Z0-9_\-]{20,})", re.I), "[REDACTED_API_KEY]"),
    # Bearer tokens
    (re.compile(r"(Bearer\s+)([a-zA-Z0-9_\-\.]{15,})", re.I), r"\1[REDACTED_TOKEN]"),
    # Key-value secret assignments in URLs, json, query strings, headers
    (
        re.compile(
            r"(sessionid|csrftoken|authorization|password|passwd|secret|api_key|token|ds_user_id)([\"'=:\s]+)([^\s\"',;&}]+)",
            re.I,
        ),
        r"\1\2[REDACTED]",
    ),
    # Database DSN passwords (e.g. postgres://user:password@host:port/db)
    (
        re.compile(r"(postgres(?:ql)?://[^:]+:)([^@]+)(@)", re.I),
        r"\1[REDACTED_PASSWORD]\3",
    ),
]


class ErrorCode(str, enum.Enum):
    VALIDATION = "validation_error"        # 400 Bad Request
    PERMISSION = "permission_denied"      # 403 Forbidden
    NOT_FOUND = "not_found"              # 404 Not Found
    CONFLICT = "conflict"                # 409 Conflict / Unique violation
    TRANSIENT = "transient_failure"      # 503 Service Unavailable / Rate Limit
    INTERNAL = "internal_error"          # 500 Internal Server Error
    UNKNOWN = "unknown"                  # 500 Unclassified


def sanitize_text(text: Any, limit: int = 2000) -> str:
    """
    Sanitizes raw text or stringified object, replacing secrets with redacted placeholders.
    """
    if text is None:
        return ""
    s = str(text)
    for pattern, replacement in _PATTERNS:
        s = pattern.sub(replacement, s)
    return s[:limit]


def sanitize_data(data: Any, depth: int = 0) -> Any:
    """
    Recursively scrubs sensitive keys and values from dicts, lists, and tuples.
    """
    if depth > 10:
        return "[MAX_DEPTH_EXCEEDED]"

    sensitive_keys = {
        "password",
        "passwd",
        "token",
        "secret",
        "api_key",
        "apikey",
        "sessionid",
        "csrftoken",
        "authorization",
        "credentials",
    }

    if isinstance(data, Mapping):
        scrubbed = {}
        for k, v in data.items():
            k_str = str(k).lower()
            tokens = re.split(r"[_\-\s\.]+", k_str)
            if any(sk in tokens for sk in sensitive_keys) or k_str in sensitive_keys:
                scrubbed[k] = "[REDACTED]"
            else:
                scrubbed[k] = sanitize_data(v, depth + 1)
        return scrubbed
    elif isinstance(data, (list, tuple, set)):
        items = [sanitize_data(item, depth + 1) for item in data]
        return type(data)(items) if not isinstance(data, set) else items
    elif isinstance(data, (str, bytes)):
        return sanitize_text(data)
    else:
        return data


class ApplicationError(Exception):
    """
    Base domain exception providing structured code, sanitized message, and metadata.
    """

    def __init__(
        self,
        message: str,
        code: ErrorCode = ErrorCode.INTERNAL,
        user_facing_message: Optional[str] = None,
        details: Optional[Mapping[str, Any]] = None,
    ):
        clean_msg = sanitize_text(message)
        super().__init__(clean_msg)
        self.code = code
        self.message = clean_msg
        self.user_facing_message = sanitize_text(user_facing_message or clean_msg)
        self.details = sanitize_data(details or {})

    def to_dict(self) -> dict[str, Any]:
        return {
            "error_code": self.code.value,
            "message": self.user_facing_message,
            "details": self.details,
        }


class ValidationError(ApplicationError):
    def __init__(self, message: str, user_facing_message: Optional[str] = None, details: Optional[Mapping[str, Any]] = None):
        super().__init__(
            message=message,
            code=ErrorCode.VALIDATION,
            user_facing_message=user_facing_message or "داده‌های ورودی نامعتبر هستند.",
            details=details,
        )


class PermissionDeniedError(ApplicationError):
    def __init__(self, message: str, user_facing_message: Optional[str] = None, details: Optional[Mapping[str, Any]] = None):
        super().__init__(
            message=message,
            code=ErrorCode.PERMISSION,
            user_facing_message=user_facing_message or "شما دسترسی لازم برای انجام این عملیات را ندارید.",
            details=details,
        )


class ConflictError(ApplicationError):
    def __init__(self, message: str, user_facing_message: Optional[str] = None, details: Optional[Mapping[str, Any]] = None):
        super().__init__(
            message=message,
            code=ErrorCode.CONFLICT,
            user_facing_message=user_facing_message or "تداخل در انجام عملیات: داده مورد نظر تکراری یا در حال تغییر است.",
            details=details,
        )


class TransientError(ApplicationError):
    def __init__(self, message: str, user_facing_message: Optional[str] = None, details: Optional[Mapping[str, Any]] = None):
        super().__init__(
            message=message,
            code=ErrorCode.TRANSIENT,
            user_facing_message=user_facing_message or "سرویس موقتاً در دسترس نیست. لطفاً دقایقی دیگر تلاش کنید.",
            details=details,
        )


def log_sanitized(
    logger: logging.Logger,
    level: int,
    event: str,
    *,
    tenant_id: Optional[str | int] = None,
    operation_id: Optional[str] = None,
    correlation_id: Optional[str] = None,
    error: Optional[BaseException | str] = None,
    extra: Optional[Mapping[str, Any]] = None,
) -> None:
    """
    Logs structured event with tenant and operational context, strictly sanitizing all inputs.
    """
    payload: dict[str, Any] = {
        "event": event,
        "tenant_id": str(tenant_id) if tenant_id is not None else None,
        "operation_id": operation_id,
        "correlation_id": correlation_id,
    }
    if error is not None:
        payload["error"] = sanitize_text(str(error))
        payload["error_type"] = type(error).__name__ if isinstance(error, BaseException) else "str"
    if extra:
        payload["extra"] = sanitize_data(extra)

    # Format cleanly
    msg = f"[{event}] " + " ".join(f"{k}={v}" for k, v in payload.items() if v is not None and k != "event")
    logger.log(level, msg)
