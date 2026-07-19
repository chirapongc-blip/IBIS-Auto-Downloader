"""Crash recovery framework for IBIS Auto Downloader.

Detects browser/session failures and saves the current DownloadState before
exiting, then produces a structured recovery report.

This module is intentionally kept separate from DownloaderEngine so that
recovery concerns do not pollute the download logic.
"""

import json
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path

try:
    from selenium.common.exceptions import (
        InvalidSessionIdException,
        NoSuchWindowException,
        WebDriverException,
    )
    _BROWSER_EXC_TYPES = (WebDriverException, InvalidSessionIdException, NoSuchWindowException)
except ImportError:  # pragma: no cover – Selenium not installed
    _BROWSER_EXC_TYPES = ()

from config import STATE_DIR

_DEFAULT_REPORT_FILE = STATE_DIR / "recovery_report.json"

_RECOVERY_ADVICE = (
    "A browser or session failure was detected. "
    "The current download state has been saved. "
    "Restart the application to resume the interrupted session automatically."
)

# Names of browser exception classes used for name-based fallback detection.
_BROWSER_EXC_NAMES = frozenset(
    {"WebDriverException", "InvalidSessionIdException", "NoSuchWindowException"}
)


@dataclass
class RecoveryReport:
    """Structured record of a crash recovery event."""

    timestamp: str
    exception_type: str
    exception_message: str
    state_file: str | None
    completed_count: int
    pending_count: int
    failed_count: int
    recovery_advice: str

    def to_dict(self) -> dict:
        """Return a plain dict representation suitable for JSON serialisation."""
        return asdict(self)


def is_browser_failure(exc: BaseException) -> bool:
    """Return ``True`` if *exc* is a browser or session failure.

    Recognised exception types
    --------------------------
    - ``selenium.common.exceptions.WebDriverException``
    - ``selenium.common.exceptions.InvalidSessionIdException``
    - ``selenium.common.exceptions.NoSuchWindowException``

    When Selenium is available the check uses ``isinstance`` so that any
    subclass of the above is also recognised.  A name-based fallback covers
    test environments where Selenium exceptions are simulated by plain
    classes whose names match the list above.
    """
    if _BROWSER_EXC_TYPES and isinstance(exc, _BROWSER_EXC_TYPES):
        return True
    # Fallback: match by class name for simulated/subclassed exceptions.
    return any(cls.__name__ in _BROWSER_EXC_NAMES for cls in type(exc).__mro__)


class CrashRecoveryHandler:
    """Handles crash recovery for a download session.

    Parameters
    ----------
    download_state : DownloadState | None
        The active download state to persist when a crash is detected.
        If ``None`` no state is saved but a report is still generated.
    report_file : str | Path | None
        Where to write the JSON recovery report.  Defaults to
        ``STATE_DIR/recovery_report.json``.
    """

    def __init__(self, download_state=None, report_file=None):
        self.download_state = download_state
        self.report_file = (
            Path(report_file) if report_file is not None else _DEFAULT_REPORT_FILE
        )

    def handle(self, exc: BaseException) -> RecoveryReport:
        """Save state and generate a recovery report for *exc*.

        Saves the ``DownloadState`` (if one was provided), computes item
        counts from that state, builds a :class:`RecoveryReport`, writes it
        to ``self.report_file``, and returns it.

        Parameters
        ----------
        exc : BaseException
            The exception that triggered the crash.

        Returns
        -------
        RecoveryReport
            Structured record of the crash event.
        """
        if self.download_state is not None:
            self.download_state.save_state()

        completed_count = len(getattr(self.download_state, "_completed", []))
        failed_count = len(getattr(self.download_state, "_failed", []))
        total_count = len(getattr(self.download_state, "_queue", []))
        pending_count = max(0, total_count - completed_count - failed_count)

        state_file_str = (
            str(self.download_state.state_file)
            if self.download_state is not None
            else None
        )

        report = RecoveryReport(
            timestamp=datetime.now(timezone.utc).isoformat(),
            exception_type=type(exc).__name__,
            exception_message=str(exc),
            state_file=state_file_str,
            completed_count=completed_count,
            pending_count=pending_count,
            failed_count=failed_count,
            recovery_advice=_RECOVERY_ADVICE,
        )

        self.save_report(report)
        return report

    def save_report(self, report: RecoveryReport) -> None:
        """Persist *report* as JSON to ``self.report_file``.

        The parent directory is created automatically if it does not exist.
        """
        self.report_file.parent.mkdir(parents=True, exist_ok=True)
        with self.report_file.open("w", encoding="utf-8") as fh:
            json.dump(report.to_dict(), fh, indent=2, sort_keys=True)
