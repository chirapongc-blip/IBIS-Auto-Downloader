"""Retry policy and exception classification for downloader operations."""

from __future__ import annotations

from enum import Enum


class ErrorCategory(str, Enum):
    TEMPORARY = "temporary"
    SESSION = "session"
    PERMANENT = "permanent"


BACKOFF_SECONDS = (1, 2, 5)

_SESSION_EXCEPTION_NAMES = frozenset(
    {"WebDriverException", "InvalidSessionIdException", "NoSuchWindowException"}
)
_TEMPORARY_EXCEPTION_NAMES = frozenset(
    {"DownloadTimeoutError", "IncompleteDownloadError", "TemporaryBrowserError"}
)


def classify_error(exc: BaseException) -> ErrorCategory:
    """Classify *exc* for retry and session-recovery decisions."""
    exception_names = {cls.__name__ for cls in type(exc).__mro__}
    if exception_names & _SESSION_EXCEPTION_NAMES:
        return ErrorCategory.SESSION
    if exception_names & _TEMPORARY_EXCEPTION_NAMES or isinstance(
        exc, (TimeoutError, ConnectionError)
    ):
        return ErrorCategory.TEMPORARY
    return ErrorCategory.PERMANENT


def backoff_seconds(retry_number: int) -> int:
    """Return the required delay for retry number 1 through 3."""
    if retry_number < 1 or retry_number > len(BACKOFF_SECONDS):
        raise ValueError(f"Unsupported retry number: {retry_number}")
    return BACKOFF_SECONDS[retry_number - 1]
