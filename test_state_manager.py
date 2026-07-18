import json
import tempfile
import unittest
from pathlib import Path

from ibis.downloader import DownloadQueue
from ibis.downloader_engine import STATUS_COMPLETED, STATUS_FAILED
from ibis.state_manager import StateManager


class StateManagerTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.state_file = Path(self.temp_dir.name) / "downloaded_invoices.json"
        self.state_manager = StateManager(state_file=self.state_file)

    def test_load_returns_empty_set_when_state_file_does_not_exist(self):
        self.assertEqual(self.state_manager.load(), set())
        self.assertEqual(self.state_manager.downloaded_invoice_ids, set())

    def test_save_and_load_round_trip_downloaded_invoice_ids(self):
        self.state_manager.mark_downloaded("1002")
        self.state_manager.mark_downloaded("1001")
        self.state_manager.save()

        persisted = json.loads(self.state_file.read_text(encoding="utf-8"))
        self.assertEqual(
            persisted,
            {"downloaded_invoice_ids": ["1001", "1002"]},
        )

        reloaded = StateManager(state_file=self.state_file)
        self.assertEqual(reloaded.load(), {"1001", "1002"})

    def test_filter_pending_links_skips_downloaded_invoice_ids(self):
        self.state_manager.mark_downloaded("1002")

        pending_links = self.state_manager.filter_pending_links(
            [
                {
                    "url": "https://example.com/DownloadARExport.aspx?InvoiceID=1001&BillingPeriod=202605&Format=Detailed",
                    "invoice_id": "1001",
                },
                {
                    "url": "https://example.com/DownloadARExport.aspx?InvoiceID=1002&BillingPeriod=202605&Format=Detailed",
                    "invoice_id": "1002",
                },
                {
                    "url": "https://example.com/DownloadARExport.aspx?InvoiceID=1003&BillingPeriod=202605&Format=Detailed",
                },
            ]
        )

        self.assertEqual(
            [link["url"] for link in pending_links],
            [
                "https://example.com/DownloadARExport.aspx?InvoiceID=1001&BillingPeriod=202605&Format=Detailed",
                "https://example.com/DownloadARExport.aspx?InvoiceID=1003&BillingPeriod=202605&Format=Detailed",
            ],
        )

    def test_mark_completed_items_only_tracks_completed_invoices(self):
        queue = DownloadQueue.from_links(
            [
                {
                    "url": "https://example.com/DownloadARExport.aspx?InvoiceID=1001&BillingPeriod=202605&Format=Detailed",
                },
                {
                    "url": "https://example.com/DownloadARExport.aspx?InvoiceID=1002&BillingPeriod=202605&Format=Detailed",
                },
            ]
        )
        queue.items[0].download_status = STATUS_COMPLETED
        queue.items[1].download_status = STATUS_FAILED

        self.state_manager.mark_completed_items(queue)

        self.assertTrue(self.state_manager.is_downloaded("1001"))
        self.assertFalse(self.state_manager.is_downloaded("1002"))


if __name__ == "__main__":
    unittest.main()
