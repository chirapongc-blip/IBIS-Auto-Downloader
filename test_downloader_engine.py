import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from ibis.downloader import DownloadQueue, STATUS_PENDING
from ibis.downloader_engine import (
    DownloaderEngine,
    DuplicateFileError,
    Http404Error,
    IncompleteDownloadError,
    InvalidUrlError,
    MAX_RETRIES,
    STATUS_COMPLETED,
    STATUS_DOWNLOADING,
    STATUS_FAILED,
    TemporaryBrowserError,
    DownloadTimeoutError,
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


class FlakeyDriver:
    """Driver that raises a sequence of exceptions per URL before creating the file.

    Parameters
    ----------
    download_dir : Path
        Directory where downloaded files are created.
    file_by_url : dict[str, str], optional
        Maps a URL to the filename to create when the download succeeds.
    exc_by_url : dict[str, list[Exception]], optional
        Maps a URL to an ordered list of exceptions to raise on successive
        calls.  Once the list is exhausted the driver behaves normally and
        creates the file (if configured).
    """

    def __init__(self, download_dir: Path, file_by_url=None, exc_by_url=None):
        self.download_dir = download_dir
        self.file_by_url = file_by_url or {}
        self._exc_by_url = {url: list(excs) for url, excs in (exc_by_url or {}).items()}
        self.opened_urls = []

    def get(self, url):
        self.opened_urls.append(url)
        url_excs = self._exc_by_url.get(url, [])
        if url_excs:
            raise url_excs.pop(0)
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
            # links[0] succeeds on first attempt (opened once).
            # links[1] times out on every attempt; with MAX_RETRIES=3 it is
            # opened 1 (initial) + 3 (retries) = 4 times total.
            expected_urls = [links[0]["url"]] + [links[1]["url"]] * (MAX_RETRIES + 1)
            self.assertEqual(driver.opened_urls, expected_urls)
            self.assertEqual(items[0].download_status, STATUS_COMPLETED)
            self.assertEqual(items[0].filename, "invoice-3001.pdf")
            self.assertEqual(items[1].download_status, STATUS_FAILED)
            self.assertIsNone(items[1].filename)
            self.assertEqual(items[1].retry_count, MAX_RETRIES)
            self.assertIn("timed out", items[1].last_error)

    # ------------------------------------------------------------------
    # Retry & error-recovery tests (Build 2.2 Task 2)
    # ------------------------------------------------------------------

    def test_retry_succeeds_after_transient_failure(self):
        """Item ends COMPLETED when the first attempt fails with a transient error
        but the second attempt succeeds."""
        with TemporaryDirectory() as tmp:
            download_dir = Path(tmp)
            url = "https://example.com/DownloadARExport.aspx?InvoiceID=4001&BillingPeriod=202605&Format=Detailed"
            queue = DownloadQueue.from_links([{"url": url}])
            plan = DownloadPlan(queue)
            item = plan.scheduled_items[0]

            driver = FlakeyDriver(
                download_dir,
                file_by_url={url: "invoice-4001.pdf"},
                exc_by_url={url: [TemporaryBrowserError("connection reset")]},
            )
            engine = DownloaderEngine(
                driver,
                download_dir=download_dir,
                timeout=1,
                poll_interval=0.01,
            )
            engine.run(plan)

            self.assertEqual(item.download_status, STATUS_COMPLETED)
            self.assertEqual(item.filename, "invoice-4001.pdf")
            self.assertEqual(item.retry_count, 1)
            self.assertIn("connection reset", item.last_error)
            # URL is opened twice: initial attempt (fails) + first retry (succeeds).
            self.assertEqual(driver.opened_urls, [url, url])

    def test_retries_exhausted_ends_in_failed(self):
        """Item ends FAILED after all MAX_RETRIES retries are consumed."""
        with TemporaryDirectory() as tmp:
            download_dir = Path(tmp)
            url = "https://example.com/DownloadARExport.aspx?InvoiceID=4002&BillingPeriod=202605&Format=Detailed"
            queue = DownloadQueue.from_links([{"url": url}])
            plan = DownloadPlan(queue)
            item = plan.scheduled_items[0]

            # Raise TemporaryBrowserError more times than MAX_RETRIES so every
            # attempt fails.
            driver = FlakeyDriver(
                download_dir,
                exc_by_url={url: [TemporaryBrowserError("flaky")] * (MAX_RETRIES + 1)},
            )
            engine = DownloaderEngine(
                driver,
                download_dir=download_dir,
                timeout=1,
                poll_interval=0.01,
            )
            engine.run(plan)

            self.assertEqual(item.download_status, STATUS_FAILED)
            self.assertIsNone(item.filename)
            self.assertEqual(item.retry_count, MAX_RETRIES)
            self.assertIn("flaky", item.last_error)
            # URL is opened once (initial) + MAX_RETRIES times.
            self.assertEqual(len(driver.opened_urls), MAX_RETRIES + 1)

    def test_no_retry_for_http404(self):
        """Http404Error is non-retryable; engine gives up immediately."""
        with TemporaryDirectory() as tmp:
            download_dir = Path(tmp)
            url = "https://example.com/DownloadARExport.aspx?InvoiceID=4003&BillingPeriod=202605&Format=Detailed"
            queue = DownloadQueue.from_links([{"url": url}])
            plan = DownloadPlan(queue)
            item = plan.scheduled_items[0]

            driver = FlakeyDriver(
                download_dir,
                exc_by_url={url: [Http404Error("404 Not Found")]},
            )
            engine = DownloaderEngine(
                driver,
                download_dir=download_dir,
                timeout=1,
                poll_interval=0.01,
            )
            engine.run(plan)

            self.assertEqual(item.download_status, STATUS_FAILED)
            self.assertEqual(item.retry_count, 0)
            self.assertIn("404", item.last_error)
            # URL is opened exactly once (no retries).
            self.assertEqual(driver.opened_urls, [url])

    def test_no_retry_for_invalid_url(self):
        """InvalidUrlError is non-retryable; engine gives up immediately."""
        with TemporaryDirectory() as tmp:
            download_dir = Path(tmp)
            url = "not-a-valid-url"
            queue = DownloadQueue.from_links([{"url": url}])
            plan = DownloadPlan(queue)
            item = plan.scheduled_items[0]

            driver = FlakeyDriver(
                download_dir,
                exc_by_url={url: [InvalidUrlError("invalid URL")]},
            )
            engine = DownloaderEngine(
                driver,
                download_dir=download_dir,
                timeout=1,
                poll_interval=0.01,
            )
            engine.run(plan)

            self.assertEqual(item.download_status, STATUS_FAILED)
            self.assertEqual(item.retry_count, 0)
            self.assertIn("invalid URL", item.last_error)
            self.assertEqual(driver.opened_urls, [url])

    def test_no_retry_for_duplicate_file(self):
        """DuplicateFileError is non-retryable; engine gives up immediately."""
        with TemporaryDirectory() as tmp:
            download_dir = Path(tmp)
            url = "https://example.com/DownloadARExport.aspx?InvoiceID=4004&BillingPeriod=202605&Format=Detailed"
            queue = DownloadQueue.from_links([{"url": url}])
            plan = DownloadPlan(queue)
            item = plan.scheduled_items[0]

            driver = FlakeyDriver(
                download_dir,
                exc_by_url={url: [DuplicateFileError("file already downloaded")]},
            )
            engine = DownloaderEngine(
                driver,
                download_dir=download_dir,
                timeout=1,
                poll_interval=0.01,
            )
            engine.run(plan)

            self.assertEqual(item.download_status, STATUS_FAILED)
            self.assertEqual(item.retry_count, 0)
            self.assertIn("already downloaded", item.last_error)
            self.assertEqual(driver.opened_urls, [url])

    def test_retry_count_and_last_error_tracking(self):
        """retry_count and last_error are updated correctly across multiple retries."""
        with TemporaryDirectory() as tmp:
            download_dir = Path(tmp)
            url = "https://example.com/DownloadARExport.aspx?InvoiceID=4005&BillingPeriod=202605&Format=Detailed"
            queue = DownloadQueue.from_links([{"url": url}])
            plan = DownloadPlan(queue)
            item = plan.scheduled_items[0]

            # Fail twice with distinct messages, then succeed.
            driver = FlakeyDriver(
                download_dir,
                file_by_url={url: "invoice-4005.pdf"},
                exc_by_url={url: [
                    DownloadTimeoutError("timeout on attempt 1"),
                    IncompleteDownloadError("incomplete on attempt 2"),
                ]},
            )
            engine = DownloaderEngine(
                driver,
                download_dir=download_dir,
                timeout=1,
                poll_interval=0.01,
            )
            engine.run(plan)

            self.assertEqual(item.download_status, STATUS_COMPLETED)
            self.assertEqual(item.retry_count, 2)
            # last_error reflects the most recent failure before success.
            self.assertIn("incomplete on attempt 2", item.last_error)
            # URL opened 3 times: initial + 2 retries.
            self.assertEqual(len(driver.opened_urls), 3)

    def test_status_transitions_compatible_after_failed_retry(self):
        """Status transitions remain pending→downloading→failed when all retries fail."""
        with TemporaryDirectory() as tmp:
            download_dir = Path(tmp)
            url = "https://example.com/DownloadARExport.aspx?InvoiceID=4006&BillingPeriod=202605&Format=Detailed"
            queue = DownloadQueue.from_links([{"url": url}])
            plan = DownloadPlan(queue)
            item = plan.scheduled_items[0]
            self.assertEqual(item.download_status, STATUS_PENDING)

            driver = FlakeyDriver(
                download_dir,
                exc_by_url={url: [TemporaryBrowserError("fail")] * (MAX_RETRIES + 1)},
            )
            engine = DownloaderEngine(
                driver,
                download_dir=download_dir,
                timeout=1,
                poll_interval=0.01,
            )

            with patch.object(engine, "_set_status", wraps=engine._set_status) as mocked:
                engine.run(plan)

            statuses = [call.args[1] for call in mocked.call_args_list if call.args[0] is item]
            self.assertEqual(statuses, [STATUS_PENDING, STATUS_DOWNLOADING, STATUS_FAILED])


if __name__ == "__main__":
    unittest.main()
