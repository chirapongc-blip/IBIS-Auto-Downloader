import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import MagicMock, patch

from ibis.downloader import DownloadQueue, DownloadQueueItem, STATUS_PENDING
from ibis.downloader_engine import (
    DownloaderEngine,
    STATUS_COMPLETED,
    STATUS_FAILED,
)
from ibis.scheduler import DownloadPlan
from ibis.state import DownloadState


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_item(invoice_id="INV001", billing_period="202605", url=None):
    return DownloadQueueItem(
        download_url=url or f"https://example.com/dl?InvoiceID={invoice_id}&BillingPeriod={billing_period}",
        invoice_id=invoice_id,
        billing_period=billing_period,
        filename=None,
    )


def _make_link(invoice_id, billing_period="202605"):
    return {
        "url": (
            f"https://example.com/DownloadARExport.aspx"
            f"?InvoiceID={invoice_id}&BillingPeriod={billing_period}&Format=Detailed"
        )
    }


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


# ---------------------------------------------------------------------------
# DownloadState unit tests
# ---------------------------------------------------------------------------

class TestDownloadStateInitialization(unittest.TestCase):
    def test_default_state_file_is_in_state_dir(self):
        ds = DownloadState()
        self.assertTrue(str(ds.state_file).endswith("download_state.json"))
        self.assertIn("state", str(ds.state_file))

    def test_custom_state_file_is_used(self):
        with TemporaryDirectory() as tmp:
            custom = Path(tmp) / "custom_state.json"
            ds = DownloadState(state_file=custom)
            self.assertEqual(ds.state_file, custom)

    def test_metadata_fields_stored(self):
        ds = DownloadState(billing_period="202605", invoice_id="INV001", customer_id="CUST99")
        self.assertEqual(ds.billing_period, "202605")
        self.assertEqual(ds.invoice_id, "INV001")
        self.assertEqual(ds.customer_id, "CUST99")

    def test_metadata_fields_default_to_none(self):
        ds = DownloadState()
        self.assertIsNone(ds.billing_period)
        self.assertIsNone(ds.invoice_id)
        self.assertIsNone(ds.customer_id)


class TestDownloadStateSaveLoad(unittest.TestCase):
    def test_load_state_returns_empty_dict_when_file_missing(self):
        with TemporaryDirectory() as tmp:
            ds = DownloadState(state_file=Path(tmp) / "nonexistent.json")
            self.assertEqual(ds.load_state(), {})

    def test_load_state_returns_empty_dict_on_corrupt_json(self):
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "bad.json"
            path.write_text("NOT_JSON", encoding="utf-8")
            ds = DownloadState(state_file=path)
            self.assertEqual(ds.load_state(), {})

    def test_load_state_returns_empty_dict_for_non_dict_json(self):
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "list.json"
            path.write_text("[1, 2, 3]", encoding="utf-8")
            ds = DownloadState(state_file=path)
            self.assertEqual(ds.load_state(), {})

    def test_save_state_creates_parent_directory_automatically(self):
        with TemporaryDirectory() as tmp:
            nested = Path(tmp) / "a" / "b" / "state.json"
            ds = DownloadState(state_file=nested)
            ds.save_state()
            self.assertTrue(nested.exists())

    def test_save_and_load_round_trip_metadata(self):
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "state.json"
            ds = DownloadState(
                state_file=path,
                billing_period="202605",
                invoice_id="INV001",
                customer_id="CUST42",
            )
            ds.save_state()
            loaded = ds.load_state()

        self.assertEqual(loaded["billing_period"], "202605")
        self.assertEqual(loaded["invoice_id"], "INV001")
        self.assertEqual(loaded["customer_id"], "CUST42")

    def test_save_state_persists_timestamp(self):
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "state.json"
            ds = DownloadState(state_file=path)
            ds.save_state()
            loaded = ds.load_state()

        self.assertIn("timestamp", loaded)
        self.assertIsInstance(loaded["timestamp"], str)
        self.assertGreater(len(loaded["timestamp"]), 0)

    def test_save_state_writes_valid_json(self):
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "state.json"
            ds = DownloadState(state_file=path)
            ds.save_state()
            raw = path.read_text(encoding="utf-8")
            parsed = json.loads(raw)
        self.assertIsInstance(parsed, dict)

    def test_save_state_includes_queue_completed_failed_keys(self):
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "state.json"
            ds = DownloadState(state_file=path)
            ds.save_state()
            loaded = ds.load_state()

        for key in ("queue", "completed", "failed"):
            self.assertIn(key, loaded)
            self.assertIsInstance(loaded[key], list)


class TestDownloadStateInitializeMethod(unittest.TestCase):
    def test_initialize_persists_queue_items(self):
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "state.json"
            item1 = _make_item("1001")
            item2 = _make_item("1002")
            ds = DownloadState(state_file=path)
            ds.initialize([item1, item2])
            loaded = ds.load_state()

        self.assertEqual(len(loaded["queue"]), 2)
        self.assertEqual(loaded["completed"], [])
        self.assertEqual(loaded["failed"], [])

    def test_initialize_resets_completed_and_failed(self):
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "state.json"
            item = _make_item("1001")
            ds = DownloadState(state_file=path)
            ds.initialize([item])
            ds.mark_completed(item)

            # Re-initialise – completed list must be cleared
            ds.initialize([item])
            loaded = ds.load_state()

        self.assertEqual(loaded["completed"], [])
        self.assertEqual(loaded["failed"], [])

    def test_initialize_with_empty_list(self):
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "state.json"
            ds = DownloadState(state_file=path)
            ds.initialize([])
            loaded = ds.load_state()

        self.assertEqual(loaded["queue"], [])

    def test_initialize_captures_item_fields(self):
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "state.json"
            item = _make_item("9001", billing_period="202601")
            ds = DownloadState(state_file=path)
            ds.initialize([item])
            loaded = ds.load_state()

        queued = loaded["queue"][0]
        self.assertEqual(queued["invoice_id"], "9001")
        self.assertEqual(queued["billing_period"], "202601")


class TestDownloadStateMarkCompletedFailed(unittest.TestCase):
    def test_mark_completed_adds_item_to_completed_list(self):
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "state.json"
            item = _make_item("2001")
            ds = DownloadState(state_file=path)
            ds.initialize([item])
            ds.mark_completed(item)
            loaded = ds.load_state()

        self.assertEqual(len(loaded["completed"]), 1)
        self.assertEqual(loaded["completed"][0]["invoice_id"], "2001")

    def test_mark_failed_adds_item_to_failed_list(self):
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "state.json"
            item = _make_item("3001")
            ds = DownloadState(state_file=path)
            ds.initialize([item])
            ds.mark_failed(item)
            loaded = ds.load_state()

        self.assertEqual(len(loaded["failed"]), 1)
        self.assertEqual(loaded["failed"][0]["invoice_id"], "3001")

    def test_mark_completed_persists_retry_count(self):
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "state.json"
            item = _make_item("2002")
            item.retry_count = 2
            ds = DownloadState(state_file=path)
            ds.initialize([item])
            ds.mark_completed(item)
            loaded = ds.load_state()

        self.assertEqual(loaded["completed"][0]["retry_count"], 2)

    def test_mark_failed_persists_last_error(self):
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "state.json"
            item = _make_item("3002")
            item.last_error = "Connection timed out"
            ds = DownloadState(state_file=path)
            ds.initialize([item])
            ds.mark_failed(item)
            loaded = ds.load_state()

        self.assertEqual(loaded["failed"][0]["last_error"], "Connection timed out")

    def test_multiple_completed_items(self):
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "state.json"
            items = [_make_item(f"10{i}") for i in range(3)]
            ds = DownloadState(state_file=path)
            ds.initialize(items)
            for item in items:
                ds.mark_completed(item)
            loaded = ds.load_state()

        self.assertEqual(len(loaded["completed"]), 3)

    def test_mixed_completed_and_failed_items(self):
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "state.json"
            ok_item = _make_item("OK01")
            fail_item = _make_item("FAIL01")
            ds = DownloadState(state_file=path)
            ds.initialize([ok_item, fail_item])
            ds.mark_completed(ok_item)
            ds.mark_failed(fail_item)
            loaded = ds.load_state()

        self.assertEqual(len(loaded["completed"]), 1)
        self.assertEqual(len(loaded["failed"]), 1)


class TestDownloadStateItemSerialization(unittest.TestCase):
    def test_serialize_dataclass_item(self):
        item = _make_item("7001", billing_period="202612")
        serialized = DownloadState._serialize_item(item)

        self.assertEqual(serialized["invoice_id"], "7001")
        self.assertEqual(serialized["billing_period"], "202612")
        self.assertIsNone(serialized["customer_id"])
        self.assertIsNone(serialized["filename"])
        self.assertEqual(serialized["retry_count"], 0)
        self.assertIsNone(serialized["last_error"])

    def test_serialize_dict_item_passthrough(self):
        d = {"invoice_id": "8001", "billing_period": "202601", "custom_key": "val"}
        serialized = DownloadState._serialize_item(d)
        self.assertIs(serialized, d)

    def test_serialize_item_with_customer_id_attribute(self):
        item = _make_item("7002")
        item.customer_id = "CUST77"  # type: ignore[attr-defined]
        serialized = DownloadState._serialize_item(item)
        self.assertEqual(serialized["customer_id"], "CUST77")

    def test_serialize_item_without_customer_id_attribute(self):
        item = _make_item("7003")
        # DownloadQueueItem has no customer_id; should default to None
        serialized = DownloadState._serialize_item(item)
        self.assertIsNone(serialized["customer_id"])


# ---------------------------------------------------------------------------
# DownloaderEngine integration tests
# ---------------------------------------------------------------------------

class TestDownloaderEngineDownloadStateIntegration(unittest.TestCase):
    def test_download_state_initialize_called_with_plan_items(self):
        with TemporaryDirectory() as tmp:
            download_dir = Path(tmp)
            link = _make_link("5001")
            queue = DownloadQueue.from_links([link])
            plan = DownloadPlan(queue)

            driver = FakeDriver(download_dir, file_by_url={link["url"]: "inv-5001.pdf"})
            mock_state = MagicMock()
            engine = DownloaderEngine(
                driver,
                download_dir=download_dir,
                timeout=1,
                poll_interval=0.01,
                download_state=mock_state,
            )
            engine.run(plan)

        mock_state.initialize.assert_called_once_with(plan.scheduled_items)

    def test_mark_completed_called_for_successful_download(self):
        with TemporaryDirectory() as tmp:
            download_dir = Path(tmp)
            link = _make_link("5002")
            queue = DownloadQueue.from_links([link])
            plan = DownloadPlan(queue)

            driver = FakeDriver(download_dir, file_by_url={link["url"]: "inv-5002.pdf"})
            mock_state = MagicMock()
            engine = DownloaderEngine(
                driver,
                download_dir=download_dir,
                timeout=1,
                poll_interval=0.01,
                download_state=mock_state,
            )
            engine.run(plan)

        item = plan.scheduled_items[0]
        mock_state.mark_completed.assert_called_once_with(item)
        mock_state.mark_failed.assert_not_called()

    def test_mark_failed_called_for_failed_download(self):
        with TemporaryDirectory() as tmp:
            download_dir = Path(tmp)
            link = _make_link("5003")
            queue = DownloadQueue.from_links([link])
            plan = DownloadPlan(queue)

            # Driver creates no file → timeout → failure
            driver = FakeDriver(download_dir)
            mock_state = MagicMock()
            engine = DownloaderEngine(
                driver,
                download_dir=download_dir,
                timeout=0.05,
                poll_interval=0.01,
                download_state=mock_state,
            )
            engine.run(plan)

        item = plan.scheduled_items[0]
        mock_state.mark_failed.assert_called_once_with(item)
        mock_state.mark_completed.assert_not_called()

    def test_download_state_none_does_not_affect_engine(self):
        """Engine works normally when download_state is not provided."""
        with TemporaryDirectory() as tmp:
            download_dir = Path(tmp)
            link = _make_link("5004")
            queue = DownloadQueue.from_links([link])
            plan = DownloadPlan(queue)

            driver = FakeDriver(download_dir, file_by_url={link["url"]: "inv-5004.pdf"})
            engine = DownloaderEngine(
                driver,
                download_dir=download_dir,
                timeout=1,
                poll_interval=0.01,
            )
            engine.run(plan)  # Must not raise

        self.assertEqual(plan.scheduled_items[0].download_status, STATUS_COMPLETED)

    def test_real_state_file_written_during_engine_run(self):
        with TemporaryDirectory() as tmp:
            download_dir = Path(tmp)
            state_file = Path(tmp) / "state" / "download_state.json"
            link = _make_link("6001")
            queue = DownloadQueue.from_links([link])
            plan = DownloadPlan(queue)

            driver = FakeDriver(download_dir, file_by_url={link["url"]: "inv-6001.pdf"})
            ds = DownloadState(
                state_file=state_file,
                billing_period="202605",
                customer_id="CUST01",
            )
            engine = DownloaderEngine(
                driver,
                download_dir=download_dir,
                timeout=1,
                poll_interval=0.01,
                download_state=ds,
            )
            engine.run(plan)

            self.assertTrue(state_file.exists())
            loaded = ds.load_state()

        self.assertEqual(len(loaded["completed"]), 1)
        self.assertEqual(loaded["completed"][0]["invoice_id"], "6001")
        self.assertEqual(loaded["billing_period"], "202605")
        self.assertEqual(loaded["customer_id"], "CUST01")
        self.assertIn("timestamp", loaded)

    def test_real_state_file_records_failed_with_retry_count(self):
        with TemporaryDirectory() as tmp:
            download_dir = Path(tmp)
            state_file = Path(tmp) / "state" / "download_state.json"
            link = _make_link("6002")
            queue = DownloadQueue.from_links([link])
            plan = DownloadPlan(queue)

            driver = FakeDriver(download_dir)  # No files → timeout
            ds = DownloadState(state_file=state_file)
            engine = DownloaderEngine(
                driver,
                download_dir=download_dir,
                timeout=0.05,
                poll_interval=0.01,
                download_state=ds,
            )
            from ibis.downloader_engine import MAX_RETRIES
            engine.run(plan)

            loaded = ds.load_state()

        self.assertEqual(len(loaded["failed"]), 1)
        self.assertEqual(loaded["failed"][0]["retry_count"], MAX_RETRIES)


if __name__ == "__main__":
    unittest.main()
