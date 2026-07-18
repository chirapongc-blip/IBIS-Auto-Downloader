import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from ibis.downloader import DownloadQueue
from ibis.state_manager import StateManager


class StateManagerTests(unittest.TestCase):
    def test_mark_completed_persists_to_downloads_json(self):
        with TemporaryDirectory() as tmp:
            state_path = Path(tmp) / "state" / "downloads.json"
            manager = StateManager(state_path)

            queue = DownloadQueue.from_links([
                {
                    "url": "https://example.com/DownloadARExport.aspx?InvoiceID=8101&BillingPeriod=202605&Format=Detailed",
                }
            ])
            item = queue.items[0]

            manager.mark_completed(item, "202605_8101.xlsx")

            self.assertTrue(state_path.exists())
            payload = json.loads(state_path.read_text(encoding="utf-8"))
            self.assertIn("completed", payload)
            self.assertIn("202605_8101", payload["completed"])
            self.assertEqual(payload["completed"]["202605_8101"]["filename"], "202605_8101.xlsx")

    def test_state_round_trip_returns_completed_filename(self):
        with TemporaryDirectory() as tmp:
            state_path = Path(tmp) / "state" / "downloads.json"
            queue = DownloadQueue.from_links([
                {
                    "url": "https://example.com/DownloadARExport.aspx?InvoiceID=8102&BillingPeriod=202605&Format=Detailed",
                }
            ])
            item = queue.items[0]

            writer = StateManager(state_path)
            writer.mark_completed(item, "202605_8102.xlsx")

            reader = StateManager(state_path)
            self.assertTrue(reader.has_completed(item))
            self.assertEqual(reader.get_completed_filename(item), "202605_8102.xlsx")


if __name__ == "__main__":
    unittest.main()
