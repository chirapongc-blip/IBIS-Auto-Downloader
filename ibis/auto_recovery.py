"""Automatic recovery orchestration for IBIS Auto Downloader.

After a browser or session failure this module:

1. Creates a new WebDriver via a user-supplied factory callable.
2. Navigates to the login page and waits for the user to log in again.
3. Reopens the Invoice page.
4. Loads the existing :class:`~ibis.state.DownloadState`.
5. Rebuilds the remaining download queue from that state.
6. Runs :class:`~ibis.downloader_engine.DownloaderEngine` over the queue,
   resuming the session automatically.

Recovery orchestration is intentionally kept outside
:class:`~ibis.downloader_engine.DownloaderEngine` so that the engine
remains focused purely on downloading.
"""

from __future__ import annotations

import logging
from typing import Callable

from ibis.recovery import CrashRecoveryHandler
from ibis.retry import ErrorCategory, classify_error
from ibis.resume import build_resume_queue, has_interrupted_session
from ibis.scheduler import DownloadPlan

logger = logging.getLogger(__name__)

# Maximum number of automatic recovery attempts per session.
MAX_RECOVERY_ATTEMPTS = 3


class RecoverySummary:
    """Aggregate retry and recovery outcomes for one application run."""

    def __init__(self) -> None:
        self.retry_attempts = 0
        self.successful_recoveries = 0
        self.permanent_failures = 0


class AutoRecovery:
    """Orchestrates automatic recovery after a browser/session failure.

    Parameters
    ----------
    driver_factory : callable
        A zero-argument callable that returns a new WebDriver instance.
        Typically :func:`ibis.browser.create_driver`.
    login_fn : callable
        A one-argument callable ``login_fn(driver)`` that blocks until the
        user has successfully logged in.  Typically
        :func:`ibis.login.wait_until_logged_in`.
    open_invoice_fn : callable
        A one-argument callable ``open_invoice_fn(driver)`` that navigates
        to the Invoice page.  Typically :func:`ibis.invoice.open_invoice_page`.
    download_state : DownloadState
        The active :class:`~ibis.state.DownloadState` shared with the engine.
    engine_factory : callable
        A one-argument callable ``engine_factory(driver)`` that returns a
        configured :class:`~ibis.downloader_engine.DownloaderEngine` instance
        bound to the supplied driver.
    max_attempts : int
        Maximum number of recovery attempts before giving up.  Defaults to
        :data:`MAX_RECOVERY_ATTEMPTS`.
    recovery_handler : CrashRecoveryHandler | None
        Optional :class:`~ibis.recovery.CrashRecoveryHandler` used to
        persist crash reports.  If ``None`` a new handler is created
        automatically using *download_state*.
    """

    def __init__(
        self,
        driver_factory: Callable,
        login_fn: Callable,
        open_invoice_fn: Callable,
        download_state,
        engine_factory: Callable,
        *,
        max_attempts: int = MAX_RECOVERY_ATTEMPTS,
        recovery_handler: CrashRecoveryHandler | None = None,
    ) -> None:
        self.driver_factory = driver_factory
        self.login_fn = login_fn
        self.open_invoice_fn = open_invoice_fn
        self.download_state = download_state
        self.engine_factory = engine_factory
        self.max_attempts = max_attempts
        self.recovery_handler = recovery_handler or CrashRecoveryHandler(
            download_state=download_state
        )
        self.summary = RecoverySummary()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def run(self, plan: DownloadPlan) -> None:
        """Run *plan* with automatic recovery on browser/session failure.

        Executes the engine and, on each browser failure, performs up to
        :attr:`max_attempts` recovery cycles.  Non-browser exceptions
        propagate immediately without triggering recovery.

        Parameters
        ----------
        plan : DownloadPlan
            The initial download plan.  After the first failure, the plan is
            rebuilt from the saved :class:`~ibis.state.DownloadState`.
        """
        driver = self.driver_factory()
        current_plan = plan
        attempt = 0
        self.summary = RecoverySummary()

        while True:
            try:
                engine = self.engine_factory(driver)
                engine.run(current_plan)
                self._record_engine_summary(engine)
                # Successful completion – quit driver and return.
                self._quit_driver(driver)
                return self.summary

            except Exception as exc:
                if classify_error(exc) is not ErrorCategory.SESSION:
                    self._quit_driver(driver)
                    raise

                self._record_engine_summary(engine)

                attempt += 1
                logger.warning(
                    "Browser/session failure (attempt %d/%d): %s: %s",
                    attempt,
                    self.max_attempts,
                    type(exc).__name__,
                    exc,
                )

                # Persist crash report and state.
                self.recovery_handler.handle(exc)

                if attempt >= self.max_attempts:
                    logger.error(
                        "Maximum recovery attempts (%d) reached. Giving up.",
                        self.max_attempts,
                    )
                    self._quit_driver(driver)
                    raise

                # Attempt recovery: new driver → login → invoice page → resume.
                driver = self._recover(driver)
                current_plan = self._rebuild_plan()
                self.summary.successful_recoveries += 1
                logger.info(
                    "Automatic recovery succeeded: count=%d.",
                    self.summary.successful_recoveries,
                )

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _recover(self, old_driver) -> object:
        """Quit *old_driver*, create a new one, and wait for re-login.

        Returns
        -------
        object
            A new WebDriver instance positioned on the Invoice page.
        """
        self._quit_driver(old_driver)

        logger.info("Creating new WebDriver for recovery.")
        new_driver = self.driver_factory()

        logger.info("Waiting for user to log in again.")
        self.login_fn(new_driver)

        logger.info("Reopening Invoice page.")
        self.open_invoice_fn(new_driver)

        return new_driver

    def _rebuild_plan(self) -> DownloadPlan:
        """Reload saved state and return a :class:`DownloadPlan` for remaining items."""
        saved_state = self.download_state.load_state()
        if not has_interrupted_session(saved_state):
            logger.info("No interrupted session found; returning empty plan.")
            from ibis.downloader import DownloadQueue
            return DownloadPlan(DownloadQueue(), latest_only=False)

        queue = build_resume_queue(saved_state)
        logger.info("Rebuilt resume queue with %d item(s).", len(queue))
        return DownloadPlan(queue, latest_only=False)

    def _record_engine_summary(self, engine) -> None:
        """Accumulate metrics from an engine run, including aborted runs."""
        engine_summary = getattr(engine, "summary", None)
        self.summary.retry_attempts += getattr(engine_summary, "retry_attempts", 0)
        self.summary.permanent_failures += getattr(engine_summary, "permanent_failures", 0)

    @staticmethod
    def _quit_driver(driver) -> None:
        """Quit *driver*, silently ignoring any errors during shutdown."""
        try:
            driver.quit()
        except Exception:
            pass
