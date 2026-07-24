"""Focused coverage for Sprint 7 performance instrumentation and optimizations."""

import unittest
from unittest.mock import MagicMock, patch

from ibis.downloader_engine import DownloaderEngine
from ibis.grid_walker import collect_grid_download_links
from ibis.performance import PerformanceTracker


class PerformanceTrackerTests(unittest.TestCase):
    def test_records_stages_and_prints_stable_summary(self):
        ticks = iter((10.0, 11.0, 12.0, 13.0, 13.5, 16.0))
        output = []
        tracker = PerformanceTracker(clock=lambda: next(ticks))

        with tracker.stage("browser_startup"):
            pass
        with tracker.stage("invoice_discovery"):
            pass

        self.assertEqual(tracker.durations, {
            "browser_startup": 1.0,
            "invoice_discovery": 0.5,
        })
        tracker.print_summary(output=output.append)
        self.assertEqual(output[0], "Performance Summary")
        self.assertIn("Browser Startup: 1.000 s", output)
        self.assertIn("Invoice Discovery: 0.500 s", output)
        self.assertEqual(output[-1], "Total: 6.000 s")

    def test_records_a_stage_when_controlled_work_raises(self):
        ticks = iter((0.0, 1.0, 3.0))
        tracker = PerformanceTracker(clock=lambda: next(ticks))
        with self.assertRaisesRegex(RuntimeError, "controlled"):
            with tracker.stage("download_execution"):
                raise RuntimeError("controlled")
        self.assertEqual(tracker.durations["download_execution"], 2.0)


class GridTraversalPerformanceTests(unittest.TestCase):
    def test_known_ready_grid_skips_duplicate_explicit_wait(self):
        driver = MagicMock()
        driver.page_source = "<html><body></body></html>"
        with patch("ibis.grid_walker.wait_for_grid") as wait:
            links = collect_grid_download_links(
                driver, "https://example.test", grid_ready=True
            )
        self.assertEqual(links, [])
        wait.assert_not_called()

    def test_default_grid_walker_keeps_existing_explicit_wait(self):
        driver = MagicMock()
        driver.page_source = "<html><body></body></html>"
        with patch("ibis.grid_walker.wait_for_grid") as wait:
            collect_grid_download_links(driver, "https://example.test")
        wait.assert_called_once_with(driver, 30)


class DownloadPollingPerformanceTests(unittest.TestCase):
    def test_snapshot_creates_directory_once_across_polling_iterations(self):
        driver = MagicMock()
        engine = DownloaderEngine(driver, download_dir="unused", sleep_fn=lambda _: None)
        directory = MagicMock()
        directory.iterdir.return_value = iter(())
        engine.download_dir = directory

        engine._snapshot_files()
        engine._snapshot_files()
        engine._snapshot_files()

        directory.mkdir.assert_called_once_with(parents=True, exist_ok=True)
        self.assertEqual(directory.iterdir.call_count, 3)


if __name__ == "__main__":
    unittest.main()
