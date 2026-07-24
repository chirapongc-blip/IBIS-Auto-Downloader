"""Dry-run integration coverage for the read-only application path."""

import csv
import json
import unittest
from datetime import datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import MagicMock, mock_open, patch

import main
from ibis.downloader import find_downloaded_output
from ibis.reporting import RunReporter
from ibis.state_manager import StateManager


def _link(invoice_id, period, filename=None):
    return {
        "url": (
            "https://example.test/DownloadARExport.aspx?"
            f"InvoiceID={invoice_id}&BillingPeriod={period}"
        ),
        "invoice_id": invoice_id,
        "billing_period": period,
        "filename": filename,
    }


class DryRunWorkflowTests(unittest.TestCase):
    def setUp(self):
        self.links = [
            _link("100", "202605", "202605_100.xls"),
            _link("100", "202604", "202604_100.xlsx"),
            _link("101", "202604", "202604_101.xls"),
        ]
        self.driver = MagicMock()
        self.driver.page_source = ""
        self.state_manager = MagicMock()
        self.state_manager.filter_pending_links.side_effect = (
            lambda links, **_kwargs: (list(links[1:]), 1 if links else 0)
        )
        self.download_state = MagicMock()
        self.download_state.load_state.return_value = {}
        self.reporter = MagicMock()

    def _run(self, args=None):
        with patch("main.create_driver", return_value=self.driver), \
             patch("main.wait_until_logged_in") as login, \
             patch("main.open_invoice_page", return_value=""), \
             patch("main.wait_for_grid"), \
             patch("main.count_grid_rows", return_value=3), \
             patch("main.get_grid_text", return_value="grid"), \
             patch("main.get_devexpress_pager_info", return_value={}), \
             patch("main.collect_grid_download_links", return_value=self.links), \
             patch("main.StateManager", return_value=self.state_manager), \
             patch("main.DownloadState", return_value=self.download_state), \
             patch("main.PeriodTracker"), \
             patch("main.RunReporter", return_value=self.reporter), \
             patch("main.AutoRecovery") as recovery, \
             patch("main.DownloaderEngine") as engine, \
             patch("main.configure_logging", return_value="dry-run-test"), \
             patch("builtins.open", mock_open()) as opened:
            main.main(["--dry-run", *(args or [])])
        return login, recovery, engine, opened

    def _report(self):
        _, kwargs = self.reporter.generate.call_args
        return kwargs

    def test_cli_parses_dry_run_with_period_selection(self):
        args = main.parse_cli_args(["--billing-period", "202605,202604", "--dry-run"])
        self.assertTrue(args.dry_run)
        self.assertEqual(args.billing_period, ("202605", "202604"))

    def test_default_dry_run_uses_latest_and_bypasses_execution(self):
        login, recovery, engine, opened = self._run()
        report = self._report()
        login.assert_called_once_with(self.driver)
        recovery.assert_not_called()
        engine.assert_not_called()
        self.download_state.restore.assert_not_called()
        self.download_state.save_state.assert_not_called()
        self.assertEqual(report["selected_billing_periods"], ["202605"])
        self.assertTrue(report["dry_run"])
        self.assertEqual(report["retry_attempts"], 0)
        self.assertEqual(report["successful_recoveries"], 0)
        self.assertEqual(report["permanent_failures"], 0)
        self.assertFalse(opened.called, "dry runs must not write debug HTML artifacts")

    def test_period_scopes_and_preview_statuses_preserve_cross_period_identity(self):
        self._run(["--billing-period", "202605,202604"])
        report = self._report()
        self.state_manager.filter_pending_links.assert_called_once_with(
            self.links, read_only=True
        )
        self.assertEqual(report["selected_billing_periods"], ["202605", "202604"])
        self.assertEqual(report["queued"], 2)
        self.assertEqual(
            [(entry["invoice_id"], entry["billing_period"], entry["final_status"])
            for entry in report["invoices"]],
            [("100", "202605", "skipped"),
             ("100", "202604", "would_download"),
             ("101", "202604", "would_download")],
        )

    def test_single_and_all_periods_use_read_only_queue_filtering(self):
        self._run(["--billing-period", "202604"])
        report = self._report()
        self.assertEqual(report["selected_billing_periods"], ["202604"])
        self.assertEqual(report["invoices_discovered"], 2)

        self.setUp()
        self._run(["--billing-period", "all"])
        self.assertEqual(self._report()["selected_billing_periods"], ["202605", "202604"])

    def test_resume_dry_run_does_not_restore_or_save_state(self):
        state = {
            "selected_periods": ["202605"],
            "queue": [{
                "invoice_id": "100", "billing_period": "202605",
                "download_url": self.links[0]["url"], "filename": "202605_100.xls",
            }],
            "completed": [], "failed": [],
        }
        self.download_state.load_state.return_value = state
        self._run()
        self.download_state.restore.assert_not_called()
        self.download_state.save_state.assert_not_called()
        self.assertEqual(self._report()["invoices"][0]["final_status"], "would_download")

    def test_read_only_completed_filter_does_not_upgrade_or_write_state(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            downloads = root / "downloads"
            downloads.mkdir()
            output = downloads / "202605_100.xls"
            output.write_bytes(b"fixture")
            state_file = root / "completed.json"
            original = {"completed_invoices": [{
                "invoice_id": "100", "billing_period": "202605",
                "filename": output.name,
            }]}
            state_file.write_text(json.dumps(original), encoding="utf-8")
            manager = StateManager(state_file=state_file, download_dir=downloads)
            pending, completed = manager.filter_pending_links(
                [self.links[0]], read_only=True
            )
            self.assertEqual(pending, [])
            self.assertEqual(completed, 1)
            self.assertEqual(json.loads(state_file.read_text(encoding="utf-8")), original)
            self.assertEqual(output.read_bytes(), b"fixture")

    def test_missing_output_lookup_does_not_create_download_directory(self):
        with TemporaryDirectory() as directory:
            missing_directory = Path(directory) / "downloads"
            with patch("config.DOWNLOAD_DIR", missing_directory):
                self.assertIsNone(find_downloaded_output("missing.xls"))
            self.assertFalse(missing_directory.exists())


class DryRunReportingTests(unittest.TestCase):
    def test_json_csv_and_html_identify_dry_run(self):
        with TemporaryDirectory() as directory:
            now = datetime(2026, 7, 24, tzinfo=timezone.utc)
            result = RunReporter(directory, now_fn=lambda: now).generate(
                "dry-run", start_time=now, end_time=now,
                selected_billing_periods=["202605"], invoices_discovered=2,
                queued=1, completed=0, skipped=1, retry_attempts=0,
                successful_recoveries=0, permanent_failures=0, dry_run=True,
                invoices=[{"invoice_id": "100", "billing_period": "202605",
                           "filename": "202605_100.xls", "final_status": "would_download"},
                          {"invoice_id": "101", "billing_period": "202605",
                           "final_status": "skipped"}],
            )
            document = json.loads(result["json"].read_text(encoding="utf-8"))
            self.assertTrue(document["dry_run"])
            self.assertEqual(document["run_status"], "completed")
            self.assertEqual(document["invoices"][0]["final_status"], "would_download")
            with result["csv"].open(encoding="utf-8", newline="") as handle:
                self.assertEqual(next(csv.DictReader(handle))["dry_run"], "True")
            self.assertIn("Dry Run", result["html"].read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
