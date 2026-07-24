"""Unit tests for stable JSON, CSV, and HTML run reporting."""

import csv
import json
import unittest
from datetime import datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory

from ibis.reporting import INVOICE_FIELDS, REPORT_SCHEMA_VERSION, RunReporter


class RunReporterTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.start = datetime(2026, 7, 24, 12, 0, tzinfo=timezone.utc)
        self.end = datetime(2026, 7, 24, 12, 0, 12, 345000, tzinfo=timezone.utc)
        self.generated = datetime(2026, 7, 24, 12, 1, tzinfo=timezone.utc)
        self.reporter = RunReporter(self.temp_dir.name, now_fn=lambda: self.generated)

    def _generate(self):
        return self.reporter.generate(
            "run-001",
            start_time=self.start,
            end_time=self.end,
            selected_billing_periods=["202605", "202604"],
            invoices_discovered=3,
            queued=2,
            completed=1,
            skipped=1,
            retry_attempts=2,
            successful_recoveries=1,
            permanent_failures=0,
            invoices=[
                {
                    "billing_period": "202605",
                    "invoice_id": "100",
                    "filename": "202605_100.xls",
                    "final_status": "completed",
                    "retry_count": 2,
                    "recovered": True,
                    "elapsed_seconds": 1.2345,
                },
                {
                    "billing_period": "202604",
                    "invoice_id": "100",
                    "filename": None,
                    "download_status": "skipped",
                },
            ],
        )

    def test_creates_reports_directory_and_all_formats(self):
        result = self._generate()

        self.assertTrue(result["json"].is_file())
        self.assertTrue(result["csv"].is_file())
        self.assertTrue(result["html"].is_file())
        self.assertEqual(result["json"].parent, Path(self.temp_dir.name))

    def test_json_document_has_stable_schema_and_required_summary(self):
        result = self._generate()
        document = json.loads(result["json"].read_text(encoding="utf-8"))

        self.assertEqual(document["schema_version"], REPORT_SCHEMA_VERSION)
        self.assertEqual(document["run_id"], "run-001")
        self.assertEqual(document["elapsed_seconds"], 12.345)
        self.assertEqual(document["run_status"], "completed")
        self.assertIsNone(document["failure_stage"])
        self.assertIsNone(document["error_type"])
        self.assertIsNone(document["error_message"])
        self.assertEqual(document["selected_billing_periods"], ["202605", "202604"])
        self.assertEqual(document["invoices_discovered"], 3)
        self.assertEqual(document["queued"], 2)
        self.assertEqual(document["completed"], 1)
        self.assertEqual(document["skipped"], 1)
        self.assertEqual(document["retry_attempts"], 2)
        self.assertEqual(document["successful_recoveries"], 1)
        self.assertEqual(document["permanent_failures"], 0)
        self.assertEqual(set(document["invoices"][0]), set(INVOICE_FIELDS))
        self.assertEqual(document["invoices"][1]["final_status"], "skipped")
        self.assertEqual(document["invoices"][1]["retry_count"], 0)

    def test_csv_contains_summary_and_each_invoice_detail(self):
        result = self._generate()
        with result["csv"].open(newline="", encoding="utf-8") as handle:
            rows = list(csv.DictReader(handle))

        self.assertEqual([row["record_type"] for row in rows], ["summary", "invoice", "invoice"])
        self.assertEqual(rows[0]["selected_billing_periods"], "202605,202604")
        self.assertEqual(rows[1]["invoice_id"], "100")
        self.assertEqual(rows[1]["recovered"], "True")
        self.assertEqual(rows[0]["run_status"], "completed")

    def test_html_contains_summary_invoice_table_totals_and_timestamp(self):
        result = self._generate()
        page = result["html"].read_text(encoding="utf-8")

        self.assertIn("IBIS Run Summary", page)
        self.assertIn("2026-07-24T12:01:00+00:00", page)
        self.assertIn("Invoice Id", page)
        self.assertIn("Run Status", page)
        self.assertIn("Totals: discovered 3, queued 2, completed 1, skipped 1.", page)

    def test_failure_status_and_metadata_render_in_all_formats(self):
        result = self.reporter.generate(
            "failed-run",
            start_time=self.start,
            end_time=self.end,
            selected_billing_periods=["202605"],
            invoices_discovered=1,
            queued=1,
            completed=0,
            skipped=0,
            retry_attempts=0,
            successful_recoveries=0,
            permanent_failures=0,
            invoices=[],
            run_status="failed",
            failure_stage="download",
            error_type="RuntimeError",
            error_message="controlled failure",
        )
        document = json.loads(result["json"].read_text(encoding="utf-8"))
        csv_text = result["csv"].read_text(encoding="utf-8")
        html_text = result["html"].read_text(encoding="utf-8")

        self.assertEqual(document["run_status"], "failed")
        self.assertEqual(document["failure_stage"], "download")
        self.assertEqual(document["error_type"], "RuntimeError")
        self.assertEqual(document["error_message"], "controlled failure")
        self.assertIn("failed", csv_text)
        self.assertIn("controlled failure", html_text)

    def test_invalid_run_id_cannot_escape_reports_directory(self):
        with self.assertRaises(ValueError):
            self.reporter.generate(
                "../unsafe",
                start_time=self.start,
                end_time=self.end,
                selected_billing_periods=[],
                invoices_discovered=0,
                queued=0,
                completed=0,
                skipped=0,
                retry_attempts=0,
                successful_recoveries=0,
                permanent_failures=0,
                invoices=[],
            )


if __name__ == "__main__":
    unittest.main()
