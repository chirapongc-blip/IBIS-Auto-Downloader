"""
Unit tests for the main.py pipeline integration (Build 2.2 – Task 3).

These tests verify that the main() function wires the components in the
correct order: scan → state filter → DownloadQueue → DownloadPlan → DownloaderEngine.run(plan).
They do not exercise the browser or network; all external dependencies are
patched.
"""
from types import SimpleNamespace
import unittest
from unittest.mock import MagicMock, patch


class TestMainFlowIntegration(unittest.TestCase):
    """
    Verify that main() calls DownloadPlan and DownloaderEngine.run() after
    creating the DownloadQueue, and that it does NOT print the old
    placeholder stop message.
    """

    def _run_main(
        self,
        *,
        scheduled_count=1,
        completed=1,
        failed=0,
        latest_period="202605",
        queue_size=0,
        period_file_exists=True,
    ):
        """
        Patch all browser/network dependencies and execute main().
        Returns the mock objects so tests can inspect the call history.
        """
        fake_driver = MagicMock()
        fake_driver.page_source = ""
        fake_driver.find_elements.return_value = []

        fake_links = [
            {
                "url": (
                    "https://example.com/DownloadARExport.aspx"
                    "?InvoiceID=1001&BillingPeriod=202605&Format=Detailed"
                ),
                "invoice_id": "1001",
                "billing_period": "202605",
            }
        ]
        queue = MagicMock()
        queue.__len__.return_value = queue_size
        queue_result = SimpleNamespace(
            queue=queue,
            found_count=1,
            already_completed_count=1,
            latest_billing_period=latest_period,
        )

        with patch("main.create_driver", return_value=fake_driver), \
             patch("main.wait_until_logged_in"), \
             patch("main.open_invoice_page", return_value=""), \
             patch("builtins.open", unittest.mock.mock_open()), \
             patch("main.wait_for_grid"), \
             patch("main.count_grid_rows", return_value=1), \
             patch("main.get_grid_text", return_value=""), \
             patch("main.get_devexpress_pager_info", return_value={}), \
             patch("main.collect_grid_download_links", return_value=fake_links) as mock_collect, \
             patch("main.StateManager") as MockStateManager, \
             patch("main.build_download_queue", return_value=queue_result) as mock_build_queue, \
             patch("main.DownloadPlan") as MockPlan, \
             patch("main.DownloaderEngine") as MockEngine, \
             patch("main.PeriodTracker") as MockPeriodTracker:

            mock_state_manager_instance = MockStateManager.return_value
            mock_plan_instance = MockPlan.return_value
            mock_plan_instance.scheduled_count = scheduled_count
            mock_plan_instance.latest_billing_period = latest_period

            mock_engine_instance = MockEngine.return_value
            mock_engine_instance.summary = SimpleNamespace(completed=completed, failed=failed)
            mock_period_tracker_instance = MockPeriodTracker.return_value
            mock_period_tracker_instance.last_period_file_exists.return_value = period_file_exists

            import main
            main.main()

            return {
                "driver": fake_driver,
                "mock_collect": mock_collect,
                "MockStateManager": MockStateManager,
                "mock_state_manager_instance": mock_state_manager_instance,
                "mock_build_queue": mock_build_queue,
                "queue_result": queue_result,
                "MockPlan": MockPlan,
                "mock_plan_instance": mock_plan_instance,
                "MockEngine": MockEngine,
                "mock_engine_instance": mock_engine_instance,
                "MockPeriodTracker": MockPeriodTracker,
                "mock_period_tracker_instance": mock_period_tracker_instance,
            }

    def test_queue_is_built_from_scanned_links_and_state_manager(self):
        """build_download_queue must receive scanned links and the state manager."""
        result = self._run_main()
        result["mock_build_queue"].assert_called_once_with(
            result["mock_collect"].return_value,
            state_manager=result["mock_state_manager_instance"],
        )

    def test_download_plan_built_from_filtered_queue(self):
        """DownloadPlan must be instantiated with the filtered DownloadQueue."""
        result = self._run_main()
        result["MockPlan"].assert_called_once_with(result["queue_result"].queue, latest_only=False)

    def test_downloader_engine_run_called_with_plan(self):
        """DownloaderEngine.run() must be called with the plan instance."""
        result = self._run_main()
        result["mock_engine_instance"].run.assert_called_once_with(
            result["mock_plan_instance"]
        )

    def test_downloader_engine_instantiated_with_driver(self):
        """DownloaderEngine must be instantiated with the Selenium driver and state manager."""
        result = self._run_main()
        result["MockEngine"].assert_called_once_with(
            result["driver"],
            state_manager=result["mock_state_manager_instance"],
        )

    def test_driver_quit_called_on_success(self):
        """driver.quit() must always be called (finally block)."""
        result = self._run_main()
        result["driver"].quit.assert_called_once()

    def test_no_placeholder_stop_message(self):
        """The old placeholder stop message must no longer appear in output."""
        import io
        from contextlib import redirect_stdout

        buf = io.StringIO()
        with redirect_stdout(buf):
            self._run_main()

        output = buf.getvalue()
        self.assertNotIn("当前仅排队，不执行下载", output)
        self.assertIn("Found invoices: 1", output)
        self.assertIn("Already completed: 1", output)
        self.assertIn("Download Queue: 0", output)

    def test_last_period_saved_when_all_downloads_succeed(self):
        result = self._run_main(
            scheduled_count=2,
            completed=2,
            failed=0,
            latest_period="202606",
            queue_size=2,
            period_file_exists=True,
        )
        result["mock_period_tracker_instance"].save_last_period.assert_called_once_with("202606")

    def test_last_period_not_saved_when_any_download_fails(self):
        result = self._run_main(
            scheduled_count=2,
            completed=1,
            failed=1,
            latest_period="202606",
            queue_size=2,
            period_file_exists=True,
        )
        result["mock_period_tracker_instance"].save_last_period.assert_not_called()

    def test_last_period_not_saved_when_completed_count_mismatches_plan(self):
        result = self._run_main(
            scheduled_count=2,
            completed=1,
            failed=0,
            latest_period="202606",
            queue_size=2,
            period_file_exists=True,
        )
        result["mock_period_tracker_instance"].save_last_period.assert_not_called()

    def test_last_period_initialized_once_when_queue_empty_and_file_missing(self):
        result = self._run_main(
            scheduled_count=0,
            completed=0,
            failed=0,
            latest_period="202606",
            queue_size=0,
            period_file_exists=False,
        )
        result["mock_period_tracker_instance"].save_last_period.assert_called_once_with("202606")

    def test_last_period_not_overwritten_when_queue_empty_and_file_exists(self):
        result = self._run_main(
            scheduled_count=0,
            completed=0,
            failed=0,
            latest_period="202606",
            queue_size=0,
            period_file_exists=True,
        )
        result["mock_period_tracker_instance"].save_last_period.assert_not_called()


class TestMainScheduledModeResilience(unittest.TestCase):
    def test_daily_mode_continues_after_run_failure(self):
        import main

        mock_scheduler = MagicMock()
        mock_scheduler.wait_until_next_run.side_effect = [None, None, KeyboardInterrupt()]
        mock_scheduler.run_once.side_effect = [RuntimeError("boom"), None]

        with patch("main.SCHEDULER_ENABLED", True), \
             patch("main.SCHEDULER_MODE", "daily"), \
             patch("main.SCHEDULE_DAY", 1), \
             patch("main.SCHEDULE_HOUR", 8), \
             patch("main.SCHEDULE_MINUTE", 0), \
             patch("main.Scheduler", return_value=mock_scheduler):
            with self.assertLogs("main", level="ERROR") as logs:
                with self.assertRaises(KeyboardInterrupt):
                    main.main()

        self.assertEqual(mock_scheduler.run_once.call_count, 2)
        self.assertIn(
            "Scheduled workflow execution failed; waiting for next run.",
            "\n".join(logs.output),
        )

    def test_monthly_mode_continues_after_run_failure(self):
        import main

        mock_scheduler = MagicMock()
        mock_scheduler.wait_until_next_run.side_effect = [None, KeyboardInterrupt()]
        mock_scheduler.run_once.side_effect = RuntimeError("monthly boom")

        with patch("main.SCHEDULER_ENABLED", True), \
             patch("main.SCHEDULER_MODE", "monthly"), \
             patch("main.SCHEDULE_DAY", 15), \
             patch("main.SCHEDULE_HOUR", 3), \
             patch("main.SCHEDULE_MINUTE", 30), \
             patch("main.Scheduler", return_value=mock_scheduler):
            with self.assertLogs("main", level="ERROR") as logs:
                with self.assertRaises(KeyboardInterrupt):
                    main.main()

        self.assertEqual(mock_scheduler.run_once.call_count, 1)
        self.assertIn(
            "Scheduled workflow execution failed; waiting for next run.",
            "\n".join(logs.output),
        )


if __name__ == "__main__":
    unittest.main()
