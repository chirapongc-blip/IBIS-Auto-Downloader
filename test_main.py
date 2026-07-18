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

    def _run_main(self):
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
        queue_result = SimpleNamespace(
            queue=MagicMock(),
            found_count=1,
            already_completed_count=1,
            latest_billing_period="202605",
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
             patch("main.PeriodTracker") as MockPeriodTracker, \
             patch("main.DownloadPlan") as MockPlan, \
             patch("main.DownloaderEngine") as MockEngine:

            mock_state_manager_instance = MockStateManager.return_value
            mock_period_tracker_instance = MockPeriodTracker.return_value
            mock_period_tracker_instance.load_last_period.return_value = None
            mock_plan_instance = MockPlan.return_value
            mock_plan_instance.scheduled_count = 1

            mock_engine_instance = MockEngine.return_value

            import main
            main.main()

            return {
                "driver": fake_driver,
                "mock_collect": mock_collect,
                "MockStateManager": MockStateManager,
                "mock_state_manager_instance": mock_state_manager_instance,
                "MockPeriodTracker": MockPeriodTracker,
                "mock_period_tracker_instance": mock_period_tracker_instance,
                "mock_build_queue": mock_build_queue,
                "queue_result": queue_result,
                "MockPlan": MockPlan,
                "mock_plan_instance": mock_plan_instance,
                "MockEngine": MockEngine,
                "mock_engine_instance": mock_engine_instance,
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


if __name__ == "__main__":
    unittest.main()
