"""Automatic recovery orchestration for IBIS Auto Downloader.

After a browser or session failure this module creates a fresh WebDriver,
waits for the user to log in, reopens the Invoice page, loads the persisted
DownloadState, builds a resume queue, and hands the result back to the caller
so that the download session can continue without manual intervention.

Responsibilities
----------------
- Validate that a failure is a browser/session failure (delegates to
  ``is_browser_failure``).
- Persist the current state (delegates to ``CrashRecoveryHandler``).
- Create a replacement WebDriver.
- Guide the user through re-login.
- Reopen the Invoice page.
- Reconstruct the resume queue from the saved DownloadState.

Intentionally *not* a responsibility of this module
----------------------------------------------------
- Running the download engine (that remains in ``DownloaderEngine``).
- Modifying ``DownloaderEngine`` in any way.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from ibis.downloader import DownloadQueue
from ibis.recovery import CrashRecoveryHandler, RecoveryReport, is_browser_failure
from ibis.resume import build_resume_queue, has_interrupted_session
from ibis.state import DownloadState


@dataclass
class RecoveryResult:
    """Outcome of a successful automatic recovery attempt.

    Attributes
    ----------
    report : RecoveryReport
        Structured record of the crash event produced by
        :class:`~ibis.recovery.CrashRecoveryHandler`.
    driver : object
        A fresh WebDriver instance ready for use.
    queue : DownloadQueue
        The remaining items to download (pending and previously failed).
    download_state : DownloadState
        The same :class:`~ibis.state.DownloadState` that was active when the
        crash occurred, ready to track progress on the resumed session.
    """

    report: RecoveryReport
    driver: object
    queue: DownloadQueue
    download_state: DownloadState


class AutoRecovery:
    """Orchestrates automatic recovery after a browser or session failure.

    This class lives *outside* :class:`~ibis.downloader_engine.DownloaderEngine`
    and does not modify the engine or its internals.

    Parameters
    ----------
    download_state : DownloadState
        The active download state to persist when a crash is detected.
    create_driver_fn : callable
        Zero-argument factory that creates and returns a new WebDriver.
    wait_login_fn : callable
        ``(driver) -> Any`` callable that blocks until the user is logged in.
    open_invoice_fn : callable
        ``(driver) -> Any`` callable that navigates to the Invoice page.
    report_file : str | Path | None
        Where to write the JSON recovery report.  ``None`` uses the default
        location configured in :class:`~ibis.recovery.CrashRecoveryHandler`.
    max_retries : int
        Maximum number of consecutive recovery attempts before giving up.
        Defaults to ``3``.
    """

    def __init__(
        self,
        download_state: DownloadState,
        create_driver_fn: Callable,
        wait_login_fn: Callable,
        open_invoice_fn: Callable,
        *,
        report_file=None,
        max_retries: int = 3,
    ):
        self.download_state = download_state
        self.create_driver_fn = create_driver_fn
        self.wait_login_fn = wait_login_fn
        self.open_invoice_fn = open_invoice_fn
        self.report_file = report_file
        self.max_retries = max_retries

    def recover(self, exc: BaseException) -> RecoveryResult:
        """Perform automatic recovery from *exc*.

        Steps
        -----
        1. Validate that *exc* is a browser/session failure.
        2. Save the current ``DownloadState`` via ``CrashRecoveryHandler`` and
           produce a :class:`~ibis.recovery.RecoveryReport`.
        3. Up to ``max_retries`` times:

           a. Create a new WebDriver via ``create_driver_fn``.
           b. Wait for the user to log in via ``wait_login_fn``.
           c. Navigate to the Invoice page via ``open_invoice_fn``.
           d. Load the persisted state and build the resume queue.
           e. Return a :class:`RecoveryResult` on success.

        4. Raise :class:`RuntimeError` if all attempts fail.

        Parameters
        ----------
        exc : BaseException
            The exception that triggered recovery.

        Returns
        -------
        RecoveryResult
            The new driver, the resume queue, the recovery report, and the
            existing ``DownloadState``.

        Raises
        ------
        ValueError
            If *exc* is not a browser/session failure.
        RuntimeError
            If all recovery attempts are exhausted.
        """
        if not is_browser_failure(exc):
            raise ValueError(
                f"Cannot automatically recover from a non-browser failure: "
                f"{type(exc).__name__}: {exc}"
            )

        handler = CrashRecoveryHandler(
            download_state=self.download_state,
            report_file=self.report_file,
        )
        report = handler.handle(exc)

        last_error: BaseException | None = None
        for attempt in range(1, self.max_retries + 1):
            try:
                driver = self.create_driver_fn()
                self.wait_login_fn(driver)
                self.open_invoice_fn(driver)

                saved_state = self.download_state.load_state()
                if has_interrupted_session(saved_state):
                    queue = build_resume_queue(saved_state)
                else:
                    queue = DownloadQueue()

                return RecoveryResult(
                    report=report,
                    driver=driver,
                    queue=queue,
                    download_state=self.download_state,
                )
            except Exception as attempt_exc:
                last_error = attempt_exc
                if attempt == self.max_retries:
                    break

        raise RuntimeError(
            f"Automatic recovery failed after {self.max_retries} attempt(s). "
            f"Last error: {last_error}"
        ) from last_error
