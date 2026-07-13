"""
Unit tests for the main.py pipeline integration (Build 2.2 – Task 3).

These tests verify that the main() function wires the components in the
correct order: DownloadQueue → DownloadPlan → DownloaderEngine.run(plan).
They do not exercise the browser or network; all external dependencies are
patched.
"""
import unittest
from unittest.mock import MagicMock, call, patch


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

        with patch("main.create_driver", return_value=fake_driver), \
             patch("main.wait_until_logged_in"), \
             patch("main.open_invoice_page", return_value=""), \
             patch("builtins.open", unittest.mock.mock_open()), \
             patch("main.wait_for_grid"), \
             patch("main.count_grid_rows", return_value=1), \
             patch("main.get_grid_text", return_value=""), \
             patch("main.get_devexpress_pager_info", return_value={}), \
             patch("main.collect_grid_download_links", return_value=fake_links) as mock_collect, \
             patch("main.DownloadPlan") as MockPlan, \
             patch("main.DownloaderEngine") as MockEngine:

            mock_plan_instance = MockPlan.return_value
            mock_plan_instance.scheduled_count = 1

            mock_engine_instance = MockEngine.return_value

            import main
            main.main()

            return {
                "driver": fake_driver,
                "mock_collect": mock_collect,
                "MockPlan": MockPlan,
                "mock_plan_instance": mock_plan_instance,
                "MockEngine": MockEngine,
                "mock_engine_instance": mock_engine_instance,
            }

    def test_download_plan_built_from_queue(self):
        """DownloadPlan must be instantiated with the DownloadQueue."""
        result = self._run_main()
        result["MockPlan"].assert_called_once()
        # The first positional argument should be a DownloadQueue instance
        args, _ = result["MockPlan"].call_args
        self.assertEqual(len(args), 1)

    def test_downloader_engine_run_called_with_plan(self):
        """DownloaderEngine.run() must be called with the plan instance."""
        result = self._run_main()
        result["mock_engine_instance"].run.assert_called_once_with(
            result["mock_plan_instance"]
        )

    def test_downloader_engine_instantiated_with_driver(self):
        """DownloaderEngine must be instantiated with the Selenium driver."""
        result = self._run_main()
        result["MockEngine"].assert_called_once_with(result["driver"])

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


if __name__ == "__main__":
    unittest.main()
