"""Retry policy and exception classification for downloader operations."""

from __future__ import annotations

from enum import Enum


class ErrorCategory(str, Enum):
    TEMPORARY = "temporary"
    SESSION = "session"
    PERMANENT = "permanent"


BACKOFF_SECONDS = (1, 2, 5)

try:  # Selenium is optional in lightweight unit-test environments.
    from selenium.common.exceptions import (
        InvalidSessionIdException,
        NoSuchWindowException,
        WebDriverException,
    )

    _SESSION_ERROR_TYPES = (
        WebDriverException,
        InvalidSessionIdException,
        NoSuchWindowException,
    )
except ImportError:  # pragma: no cover - exercised only without Selenium
    _SESSION_ERROR_TYPES = ()

_TEMPORARY_ERROR_TYPES: tuple[type[BaseException], ...] = ()


def register_temporary_error_types(*error_types: type[BaseException]) -> None:
    """Register downloader-owned temporary exception classes.

    The downloader defines these classes, so registration avoids a circular
    import while retaining explicit ``isinstance`` classification.
    """
    global _TEMPORARY_ERROR_TYPES
    _TEMPORARY_ERROR_TYPES = tuple(dict.fromkeys((*_TEMPORARY_ERROR_TYPES, *error_types)))


def classify_error(exc: BaseException) -> ErrorCategory:
    """Classify *exc* for retry and session-recovery decisions."""
    if _SESSION_ERROR_TYPES and isinstance(exc, _SESSION_ERROR_TYPES):
        return ErrorCategory.SESSION
    if isinstance(exc, _TEMPORARY_ERROR_TYPES) or isinstance(
        exc, (TimeoutError, ConnectionError)
    ):
        return ErrorCategory.TEMPORARY
    return ErrorCategory.PERMANENT


def backoff_seconds(retry_number: int) -> int:
    """Return the required delay for retry number 1 through 3."""
    if retry_number < 1 or retry_number > len(BACKOFF_SECONDS):
        raise ValueError(f"Unsupported retry number: {retry_number}")
    return BACKOFF_SECONDS[retry_number - 1]
