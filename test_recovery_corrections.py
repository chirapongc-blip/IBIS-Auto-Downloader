import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from selenium.common.exceptions import WebDriverException

from config import BASE_URL
from ibis.auto_recovery import AutoRecovery
from ibis.downloader import DownloadQueue
from ibis.scheduler import DownloadPlan
from ibis.state import DownloadState


class _Driver:
    def __init__(self):
        self.urls = []
        self.quit_calls = 0

    def get(self, url):
        self.urls.append(url)

    def quit(self):
        self.quit_calls += 1


class _Handler:
    def handle(self, _exc):
        pass


class _Summary:
    def __init__(self, retry_attempts=0, permanent_failures=0):
        self.retry_attempts = retry_attempts
        self.permanent_failures = permanent_failures


def _plan(*ids):
    return DownloadPlan(
        DownloadQueue.from_links(
            [
                {
                    "url": f"https://example.test/dl?InvoiceID={invoice_id}&BillingPeriod=202605",
                    "invoice_id": invoice_id,
                    "billing_period": "202605",
                }
                for invoice_id in ids
            ]
        ),
        latest_only=False,
    )


class RecoveryCorrectionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.state = DownloadState(state_file=Path(self.tmp.name) / "state.json")

    def tearDown(self):
        self.tmp.cleanup()

    def _recovery(self, drivers, engines, *, login_fn=None, open_invoice_fn=None, max_attempts=3):
        driver_iter = iter(drivers)
        engine_iter = iter(engines)
        return AutoRecovery(
            driver_factory=lambda: next(driver_iter),
            login_fn=login_fn or (lambda _driver: None),
            open_invoice_fn=open_invoice_fn or (lambda _driver: None),
            download_state=self.state,
            engine_factory=lambda _driver: next(engine_iter),
            recovery_handler=_Handler(),
            max_attempts=max_attempts,
        )

    def test_recovery_navigates_to_ibis_and_keeps_manual_login_wait(self):
        original, replacement = _Driver(), _Driver()

        class FailingEngine:
            summary = _Summary()

            def run(self, _plan):
                raise WebDriverException("disconnected")

        class SuccessfulEngine:
            summary = _Summary()

            def run(self, _plan):
                pass

        logins = []
        recovery = self._recovery(
            [original, replacement],
            [FailingEngine(), SuccessfulEngine()],
            login_fn=lambda driver: logins.append(driver),
        )
        recovery.run(_plan("A"))

        self.assertEqual(replacement.urls, [BASE_URL])
        self.assertEqual(logins, [replacement])

    def test_full_state_is_preserved_without_duplicate_completed_items(self):
        plan = _plan("A", "B", "C", "D")
        original_items = plan.scheduled_items
        self.state.initialize(original_items)
        original, replacement = _Driver(), _Driver()
        resumed_ids = []

        class FailingEngine:
            summary = _Summary(retry_attempts=2)

            def run(self, _plan):
                self_state = self_outer.state
                self_state.mark_completed(original_items[0])
                original_items[1].download_status = "downloading"
                self_state.save_state()
                raise WebDriverException("session expired")

        class ResumedEngine:
            summary = _Summary(retry_attempts=1)

            def run(self, resumed_plan):
                self_outer.assertTrue(self.preserve_existing_state)
                resumed_ids.extend(item.invoice_id for item in resumed_plan.scheduled_items)
                for item in resumed_plan.scheduled_items:
                    self_outer.state.mark_completed(item)

        self_outer = self
        recovery = self._recovery([original, replacement], [FailingEngine(), ResumedEngine()])
        summary = recovery.run(plan)
        saved = self.state.load_state()

        self.assertEqual(resumed_ids, ["B", "C", "D"])
        self.assertEqual([item["invoice_id"] for item in saved["queue"]], ["A", "B", "C", "D"])
        self.assertEqual(
            [item["invoice_id"] for item in saved["completed"]], ["A", "B", "C", "D"]
        )
        self.assertEqual(summary.retry_attempts, 3)
        self.assertEqual(summary.successful_recoveries, 1)

    def test_replacement_driver_is_closed_when_manual_login_fails(self):
        original, replacement = _Driver(), _Driver()

        class FailingEngine:
            summary = _Summary()

            def run(self, _plan):
                raise WebDriverException("crash")

        recovery = self._recovery(
            [original, replacement],
            [FailingEngine()],
            login_fn=lambda _driver: (_ for _ in ()).throw(RuntimeError("login failed")),
        )
        with self.assertRaisesRegex(RuntimeError, "login failed"):
            recovery.run(_plan("A"))
        self.assertEqual(original.quit_calls, 1)
        self.assertEqual(replacement.quit_calls, 1)

    def test_replacement_driver_is_closed_when_invoice_reopen_fails(self):
        original, replacement = _Driver(), _Driver()

        class FailingEngine:
            summary = _Summary()

            def run(self, _plan):
                raise WebDriverException("crash")

        recovery = self._recovery(
            [original, replacement],
            [FailingEngine()],
            open_invoice_fn=lambda _driver: (_ for _ in ()).throw(RuntimeError("invoice failed")),
        )
        with self.assertRaisesRegex(RuntimeError, "invoice failed"):
            recovery.run(_plan("A"))
        self.assertEqual(replacement.quit_calls, 1)

    def test_successful_recovery_is_counted_after_resumed_engine_completes(self):
        original, replacement = _Driver(), _Driver()
        recovery = None

        class FailingEngine:
            summary = _Summary()

            def run(self, _plan):
                raise WebDriverException("crash")

        class ResumedEngine:
            summary = _Summary()

            def run(self, _plan):
                self_outer.assertEqual(recovery.summary.successful_recoveries, 0)

        self_outer = self
        recovery = self._recovery([original, replacement], [FailingEngine(), ResumedEngine()])
        summary = recovery.run(_plan("A"))
        self.assertEqual(summary.successful_recoveries, 1)

    def test_engine_metrics_are_not_counted_twice_when_factory_fails(self):
        first, second, third = _Driver(), _Driver(), _Driver()

        class FailingEngine:
            summary = _Summary(retry_attempts=2, permanent_failures=1)

            def run(self, _plan):
                raise WebDriverException("crash")

        class SuccessfulEngine:
            summary = _Summary(retry_attempts=1)

            def run(self, _plan):
                pass

        engines = iter([FailingEngine(), WebDriverException("factory disconnect"), SuccessfulEngine()])
        recovery = AutoRecovery(
            driver_factory=iter([first, second, third]).__next__,
            login_fn=lambda _driver: None,
            open_invoice_fn=lambda _driver: None,
            download_state=self.state,
            engine_factory=lambda _driver: (
                (_ for _ in ()).throw(next_engine)
                if isinstance((next_engine := next(engines)), Exception)
                else next_engine
            ),
            recovery_handler=_Handler(),
        )

        summary = recovery.run(_plan("A"))
        self.assertEqual(summary.retry_attempts, 3)
        self.assertEqual(summary.permanent_failures, 1)

    def test_terminal_recovery_failure_logs_final_summary(self):
        driver = _Driver()

        class FailingEngine:
            summary = _Summary(retry_attempts=1)

            def run(self, _plan):
                raise WebDriverException("crash")

        recovery = self._recovery([driver], [FailingEngine()], max_attempts=1)
        with self.assertLogs("ibis.auto_recovery", level="ERROR") as logs:
            with self.assertRaises(WebDriverException):
                recovery.run(_plan("A"))
        self.assertTrue(any("Final retry/recovery summary" in line for line in logs.output))


if __name__ == "__main__":
    unittest.main()
