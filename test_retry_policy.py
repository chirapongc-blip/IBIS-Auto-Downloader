import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from selenium.common.exceptions import WebDriverException
from ibis.auto_recovery import AutoRecovery
from ibis.downloader import DownloadQueue
from ibis.downloader_engine import (
    DownloadTimeoutError,
    DownloaderEngine,
    Http404Error,
    TemporaryBrowserError,
)
from ibis.retry import ErrorCategory, backoff_seconds, classify_error
from ibis.scheduler import DownloadPlan


class _FlakyDriver:
    def __init__(self, directory):
        self.directory = directory
        self.calls = 0

    def get(self, _url):
        self.calls += 1
        if self.calls < 4:
            raise TemporaryBrowserError("connection reset")
        (self.directory / "export.xls").write_text("export", encoding="utf-8")


class _NoopDriver:
    def get(self, _url):
        pass

    def quit(self):
        pass


class _RecoveryHandler:
    def __init__(self):
        self.calls = 0

    def handle(self, _exc):
        self.calls += 1


class _State:
    def load_state(self):
        return {}


class _Engine:
    def __init__(self, should_fail):
        self.should_fail = should_fail
        self.summary = type("Summary", (), {"retry_attempts": 0, "permanent_failures": 0})()

    def run(self, _plan):
        if self.should_fail:
            raise WebDriverException("disconnected")


class RetryPolicyTests(unittest.TestCase):
    def test_error_classification(self):
        self.assertEqual(classify_error(DownloadTimeoutError("timeout")), ErrorCategory.TEMPORARY)
        self.assertEqual(classify_error(WebDriverException("gone")), ErrorCategory.SESSION)
        self.assertEqual(classify_error(Http404Error("missing")), ErrorCategory.PERMANENT)

    def test_backoff_schedule(self):
        self.assertEqual([backoff_seconds(n) for n in (1, 2, 3)], [1, 2, 5])

    def test_temporary_error_uses_each_backoff_before_success(self):
        with TemporaryDirectory() as tmp:
            directory = Path(tmp)
            sleeps = []
            queue = DownloadQueue.from_links(
                [{"url": "https://example.test/dl?InvoiceID=1&BillingPeriod=202605"}]
            )
            engine = DownloaderEngine(
                _FlakyDriver(directory),
                download_dir=directory,
                poll_interval=0.01,
                sleep_fn=sleeps.append,
            )

            engine.run(DownloadPlan(queue))

        self.assertEqual(sleeps[:3], [1, 2, 5])
        self.assertEqual(engine.summary.retry_attempts, 3)
        self.assertEqual(engine.summary.permanent_failures, 0)

    def test_session_failure_is_recovered_and_queue_is_resumed(self):
        engines = iter([_Engine(should_fail=True), _Engine(should_fail=False)])
        handler = _RecoveryHandler()
        recovery = AutoRecovery(
            driver_factory=_NoopDriver,
            login_fn=lambda _driver: None,
            open_invoice_fn=lambda _driver: None,
            download_state=_State(),
            engine_factory=lambda _driver: next(engines),
            recovery_handler=handler,
        )

        summary = recovery.run(DownloadPlan(DownloadQueue()))

        self.assertEqual(handler.calls, 1)
        self.assertEqual(summary.successful_recoveries, 1)
        self.assertEqual(summary.permanent_failures, 0)


if __name__ == "__main__":
    unittest.main()
