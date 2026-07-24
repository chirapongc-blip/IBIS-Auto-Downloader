"""Focused terminal-lifecycle and recovery-attribution reporting tests."""

from types import SimpleNamespace
import unittest
from unittest.mock import MagicMock, mock_open, patch

import main
from ibis.downloader import DownloadQueue


def _link(invoice_id="100", period="202605"):
    return {
        "url": (
            "https://example.test/DownloadARExport.aspx?"
            f"InvoiceID={invoice_id}&BillingPeriod={period}"
        ),
        "invoice_id": invoice_id,
        "billing_period": period,
    }


class ReportingLifecycleTests(unittest.TestCase):
    def _run_workflow(self, *, queue=None, engine_summary=None, recovery_summary=None,
                      recovery_error=None, report_error=None):
        queue = queue if queue is not None else DownloadQueue.from_links([_link()])
        engine_summary = engine_summary or SimpleNamespace(
            completed=len(queue), failed=0, retry_attempts=0, permanent_failures=0
        )
        recovery_summary = recovery_summary or SimpleNamespace(
            retry_attempts=0, successful_recoveries=0, permanent_failures=0
        )
        driver = MagicMock()
        engine = MagicMock(summary=engine_summary)
        reporter = MagicMock()
        reporter.generate.side_effect = report_error

        class Recovery:
            def __init__(self, *, engine_factory, **_kwargs):
                self.engine_factory = engine_factory

            def run(self, plan):
                self.engine_factory(driver)
                if recovery_error is not None:
                    raise recovery_error
                return recovery_summary

        queue_result = SimpleNamespace(
            queue=queue,
            found_count=len(queue),
            already_completed_count=0,
            latest_billing_period="202605",
        )
        state = MagicMock()
        state.load_state.return_value = {}
        with patch("main.create_driver", return_value=driver), \
             patch("main.wait_until_logged_in"), \
             patch("main.open_invoice_page", return_value=""), \
             patch("builtins.open", mock_open()), \
             patch("main.wait_for_grid"), \
             patch("main.count_grid_rows", return_value=1), \
             patch("main.get_grid_text", return_value=""), \
             patch("main.get_devexpress_pager_info", return_value={}), \
             patch("main.collect_grid_download_links", return_value=[_link()]), \
             patch("main.StateManager"), \
             patch("main.build_download_queue", return_value=queue_result), \
             patch("main.DownloadState", return_value=state), \
             patch("main.DownloaderEngine", return_value=engine), \
             patch("main.AutoRecovery", Recovery), \
             patch("main.PeriodTracker"), \
             patch("main.RunReporter", return_value=reporter):
            try:
                main._download_workflow(run_id="lifecycle-run")
            except Exception as exc:  # Returned for exception-path assertions.
                return reporter, exc
        return reporter, None

    @staticmethod
    def _report_kwargs(reporter):
        _, kwargs = reporter.generate.call_args
        return kwargs

    def test_successful_report_has_completed_status(self):
        reporter, error = self._run_workflow()
        self.assertIsNone(error)
        self.assertEqual(self._report_kwargs(reporter)["run_status"], "completed")

    def test_permanent_download_failures_report_completed_with_failures(self):
        reporter, error = self._run_workflow(
            engine_summary=SimpleNamespace(completed=0, failed=1),
            recovery_summary=SimpleNamespace(
                retry_attempts=0, successful_recoveries=0, permanent_failures=1
            ),
        )
        self.assertIsNone(error)
        report = self._report_kwargs(reporter)
        self.assertEqual(report["run_status"], "completed_with_failures")
        self.assertEqual(report["permanent_failures"], 1)

    def test_workflow_exception_emits_failed_report(self):
        reporter, error = self._run_workflow(recovery_error=RuntimeError("after queue"))
        self.assertIsInstance(error, RuntimeError)
        report = self._report_kwargs(reporter)
        self.assertEqual(report["run_status"], "failed")
        self.assertEqual(report["failure_stage"], "download")
        self.assertEqual(report["error_type"], "RuntimeError")
        self.assertEqual(report["error_message"], "after queue")

    def test_recovery_failure_emits_failed_report(self):
        reporter, error = self._run_workflow(recovery_error=ConnectionError("recovery failed"))
        self.assertIsInstance(error, ConnectionError)
        self.assertEqual(self._report_kwargs(reporter)["run_status"], "failed")

    def test_empty_queue_emits_valid_completed_report(self):
        reporter, error = self._run_workflow(queue=DownloadQueue())
        self.assertIsNone(error)
        report = self._report_kwargs(reporter)
        self.assertEqual(report["run_status"], "completed")
        self.assertEqual(report["queued"], 0)

    def test_reporting_failure_does_not_replace_workflow_exception(self):
        reporter, error = self._run_workflow(
            recovery_error=RuntimeError("original workflow failure"),
            report_error=OSError("reports unavailable"),
        )
        self.assertIsInstance(error, RuntimeError)
        self.assertEqual(str(error), "original workflow failure")
        reporter.generate.assert_called_once()

    def test_recovered_flag_marks_only_rebuilt_plan_completions(self):
        before = {"invoice_id": "A", "billing_period": "202605", "download_status": "completed"}
        after = {"invoice_id": "B", "billing_period": "202605", "download_status": "completed"}
        state = {"completed": [before, after], "failed": []}
        details = main._report_invoice_details(
            [before, after], state, set(), {("B", "202605")}
        )
        self.assertFalse(details[0]["recovered"])
        self.assertTrue(details[1]["recovered"])


if __name__ == "__main__":
    unittest.main()
