"""Automatic recovery orchestration for IBIS Auto Downloader.

This module implements Build 3.0 – Task 1: Automatic Recovery.

After a browser/session failure it:
1. Creates a new WebDriver.
2. Waits for the user to log in again.
3. Reopens the Invoice page.
4. Loads the existing DownloadState.
5. Resumes the remaining download queue automatically.

Recovery orchestration is intentionally kept *outside* ``DownloaderEngine``
so that the download logic remains unaware of crash/restart details.
"""

from ibis.recovery import CrashRecoveryHandler, is_browser_failure
from ibis.resume import has_interrupted_session, build_resume_queue
from ibis.scheduler import DownloadPlan


class AutoRecovery:
    """Orchestrates automatic recovery after a browser/session failure.

    Parameters
    ----------
    download_state : DownloadState
        The active download-state object used to persist progress.
    driver_factory : callable
        Zero-argument callable that returns a new WebDriver instance.
        Defaults to :func:`ibis.browser.create_driver`.
    login_fn : callable
        Callable ``(driver) -> bool`` that waits for the user to log in.
        Defaults to :func:`ibis.login.wait_until_logged_in`.
    open_invoice_fn : callable
        Callable ``(driver) -> str`` that navigates to the Invoice page.
        Defaults to :func:`ibis.invoice.open_invoice_page`.
    engine_factory : callable
        Callable ``(driver, download_state) -> DownloaderEngine`` used to
        construct a fresh engine for the resumed session.
    report_file : str | Path | None
        Where to write the JSON recovery report.  Passed directly to
        :class:`~ibis.recovery.CrashRecoveryHandler`.
    max_attempts : int
        Maximum number of recovery attempts before giving up.  Defaults to 3.
    """

    def __init__(
        self,
        download_state,
        *,
        driver_factory=None,
        login_fn=None,
        open_invoice_fn=None,
        engine_factory=None,
        report_file=None,
        max_attempts=3,
    ):
        self.download_state = download_state
        self.driver_factory = driver_factory or _default_driver_factory
        self.login_fn = login_fn or _default_login_fn
        self.open_invoice_fn = open_invoice_fn or _default_open_invoice_fn
        self.engine_factory = engine_factory or _default_engine_factory
        self.report_file = report_file
        self.max_attempts = max_attempts

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def run_with_recovery(self, plan):
        """Run *plan* and automatically recover from browser failures.

        Executes the download plan.  If a browser/session failure is detected
        the state is saved, a new browser is created, the user is prompted to
        log in, the Invoice page is reopened, and the remaining items are
        resumed.  At most :attr:`max_attempts` recovery attempts are made.

        Parameters
        ----------
        plan : DownloadPlan
            The initial download plan to execute.

        Returns
        -------
        bool
            ``True`` when the session finished without a fatal browser failure
            (or after a successful recovery), ``False`` when all recovery
            attempts were exhausted.
        """
        driver = self.driver_factory()
        try:
            engine = self.engine_factory(driver, self.download_state)
            engine.run(plan)
            return True
        except Exception as exc:
            if not is_browser_failure(exc):
                raise
            return self._recover(exc)
        finally:
            _safe_quit(driver)

    def recover_from_state(self):
        """Resume from an existing persisted :attr:`download_state`.

        Loads the saved state, checks whether a previous session was
        interrupted, and if so builds a resume queue and runs it.

        Returns
        -------
        bool
            ``True`` when the resumed session completed (or there was nothing
            to resume), ``False`` when all recovery attempts were exhausted.
        """
        saved = self.download_state.load_state()
        if not has_interrupted_session(saved):
            return True

        resume_queue = build_resume_queue(saved)
        plan = DownloadPlan(resume_queue, latest_only=False)
        return self._execute_with_recovery(plan, attempt=1)

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _recover(self, exc):
        """Handle a detected browser failure and start recovery loop."""
        handler = CrashRecoveryHandler(
            download_state=self.download_state,
            report_file=self.report_file,
        )
        handler.handle(exc)
        return self._attempt_recovery(attempt=1)

    def _attempt_recovery(self, attempt):
        """Try to resume the download, retrying up to *max_attempts* times."""
        if attempt > self.max_attempts:
            return False

        driver = self.driver_factory()
        try:
            self.login_fn(driver)
            self.open_invoice_fn(driver)

            saved = self.download_state.load_state()
            if not has_interrupted_session(saved):
                return True

            resume_queue = build_resume_queue(saved)
            plan = DownloadPlan(resume_queue, latest_only=False)
            engine = self.engine_factory(driver, self.download_state)
            try:
                engine.run(plan)
                return True
            except Exception as inner_exc:
                if not is_browser_failure(inner_exc):
                    raise
                handler = CrashRecoveryHandler(
                    download_state=self.download_state,
                    report_file=self.report_file,
                )
                handler.handle(inner_exc)
                return self._attempt_recovery(attempt + 1)
        finally:
            _safe_quit(driver)

    def _execute_with_recovery(self, plan, attempt):
        """Execute *plan* with up to *max_attempts* recovery attempts."""
        if attempt > self.max_attempts:
            return False

        driver = self.driver_factory()
        try:
            self.login_fn(driver)
            self.open_invoice_fn(driver)
            engine = self.engine_factory(driver, self.download_state)
            try:
                engine.run(plan)
                return True
            except Exception as exc:
                if not is_browser_failure(exc):
                    raise
                handler = CrashRecoveryHandler(
                    download_state=self.download_state,
                    report_file=self.report_file,
                )
                handler.handle(exc)
                saved = self.download_state.load_state()
                resume_queue = build_resume_queue(saved)
                next_plan = DownloadPlan(resume_queue, latest_only=False)
                return self._execute_with_recovery(next_plan, attempt + 1)
        finally:
            _safe_quit(driver)


# ---------------------------------------------------------------------------
# Default injected dependencies (lazy imports to keep module importable
# even when Selenium is not installed in unit-test environments)
# ---------------------------------------------------------------------------

def _default_driver_factory():
    from ibis.browser import create_driver
    return create_driver()


def _default_login_fn(driver):
    from ibis.login import wait_until_logged_in
    return wait_until_logged_in(driver)


def _default_open_invoice_fn(driver):
    from ibis.invoice import open_invoice_page
    return open_invoice_page(driver)


def _default_engine_factory(driver, download_state):
    from ibis.downloader_engine import DownloaderEngine
    return DownloaderEngine(driver, download_state=download_state)


def _safe_quit(driver):
    """Quit *driver*, suppressing any errors (e.g. already-closed session)."""
    try:
        driver.quit()
    except Exception:
        pass
