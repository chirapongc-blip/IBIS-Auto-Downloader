import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from ibis.downloader import DownloadQueue, STATUS_PENDING
from ibis.downloader_engine import (
    DownloaderEngine,
    STATUS_COMPLETED,
    STATUS_DOWNLOADING,
    STATUS_FAILED,
)
from ibis.scheduler import DownloadPlan


class FakeDriver:
    def __init__(self, download_dir: Path, file_by_url=None):
        self.download_dir = download_dir
        self.file_by_url = file_by_url or {}
        self.opened_urls = []

    def get(self, url):
        self.opened_urls.append(url)
        filename = self.file_by_url.get(url)
        if filename:
            (self.download_dir / filename).write_text("pdf", encoding="utf-8")


class DownloaderEngineTests(unittest.TestCase):
    def test_downloads_all_plan_items_sequentially_and_marks_completed(self):
        with TemporaryDirectory() as tmp:
            download_dir = Path(tmp)
            links = [
                {
                    "url": "https://example.com/DownloadARExport.aspx?InvoiceID=1001&BillingPeriod=202605&Format=Detailed",
                },
                {
                    "url": "https://example.com/DownloadARExport.aspx?InvoiceID=1002&BillingPeriod=202605&Format=Detailed",
                },
            ]
            queue = DownloadQueue.from_links(links)
            plan = DownloadPlan(queue)
            driver = FakeDriver(
                download_dir,
                file_by_url={
                    links[0]["url"]: "invoice-1001.pdf",
                    links[1]["url"]: "invoice-1002.pdf",
                },
            )

            engine = DownloaderEngine(
                driver,
                download_dir=download_dir,
                timeout=1,
                poll_interval=0.01,
            )
            engine.run(plan)

            self.assertEqual(driver.opened_urls, [item.download_url for item in plan.scheduled_items])
            self.assertEqual([item.download_status for item in plan.scheduled_items], [STATUS_COMPLETED, STATUS_COMPLETED])
            self.assertEqual([item.filename for item in plan.scheduled_items], ["invoice-1001.pdf", "invoice-1002.pdf"])

    def test_status_lifecycle_is_pending_to_downloading_to_completed(self):
        with TemporaryDirectory() as tmp:
            download_dir = Path(tmp)
            link = {
                "url": "https://example.com/DownloadARExport.aspx?InvoiceID=2001&BillingPeriod=202605&Format=Detailed",
            }
            queue = DownloadQueue.from_links([link])
            plan = DownloadPlan(queue)
            item = plan.scheduled_items[0]
            self.assertEqual(item.download_status, STATUS_PENDING)

            driver = FakeDriver(download_dir, file_by_url={link["url"]: "invoice-2001.pdf"})
            engine = DownloaderEngine(
                driver,
                download_dir=download_dir,
                timeout=1,
                poll_interval=0.01,
            )

            with patch.object(engine, "_set_status", wraps=engine._set_status) as mocked_set_status:
                engine.run(plan)

            statuses = [call.args[1] for call in mocked_set_status.call_args_list if call.args[0] is item]
            self.assertEqual(statuses, [STATUS_PENDING, STATUS_DOWNLOADING, STATUS_COMPLETED])

    def test_marks_item_failed_when_no_file_is_downloaded(self):
        with TemporaryDirectory() as tmp:
            download_dir = Path(tmp)
            links = [
                {
                    "url": "https://example.com/DownloadARExport.aspx?InvoiceID=3001&BillingPeriod=202605&Format=Detailed",
                },
                {
                    "url": "https://example.com/DownloadARExport.aspx?InvoiceID=3002&BillingPeriod=202605&Format=Detailed",
                },
            ]
            queue = DownloadQueue.from_links(links)
            plan = DownloadPlan(queue)

            driver = FakeDriver(
                download_dir,
                file_by_url={links[0]["url"]: "invoice-3001.pdf"},
            )
            engine = DownloaderEngine(
                driver,
                download_dir=download_dir,
                timeout=0.05,
                poll_interval=0.01,
            )
            engine.run(plan)

            items = plan.scheduled_items
            self.assertEqual(driver.opened_urls, [links[0]["url"], links[1]["url"]])
            self.assertEqual(items[0].download_status, STATUS_COMPLETED)
            self.assertEqual(items[0].filename, "invoice-3001.pdf")
            self.assertEqual(items[1].download_status, STATUS_FAILED)
            self.assertIsNone(items[1].filename)


if __name__ == "__main__":
    unittest.main()
