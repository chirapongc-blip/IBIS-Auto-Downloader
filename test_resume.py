"""Comprehensive unit tests for the resume orchestration helpers in ibis/resume.py.

Tests cover:
- has_interrupted_session: all edge cases for session interruption detection
- build_resume_queue: filtering completed items, preserving pending/failed items
- Integration: end-to-end queue reconstruction from a realistic saved state
"""
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from ibis.downloader import DownloadQueue, DownloadQueueItem, STATUS_PENDING
from ibis.resume import (
    has_interrupted_session,
    build_resume_queue,
    _build_completed_keys,
    _is_completed,
)
from ibis.state import DownloadState


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_state_item(invoice_id, billing_period="202605", status="pending", url=None, filename=None):
    return {
        "invoice_id": invoice_id,
        "billing_period": billing_period,
        "download_url": url or f"https://example.com/dl?InvoiceID={invoice_id}&BillingPeriod={billing_period}",
        "filename": filename,
        "download_status": status,
        "retry_count": 0,
        "last_error": None,
        "customer_id": None,
    }


def _make_state(queue_items, completed_items=None, failed_items=None, billing_period="202605"):
    return {
        "billing_period": billing_period,
        "customer_id": None,
        "invoice_id": None,
        "queue": queue_items,
        "completed": completed_items or [],
        "failed": failed_items or [],
    }


# ---------------------------------------------------------------------------
# has_interrupted_session
# ---------------------------------------------------------------------------

class TestHasInterruptedSession(unittest.TestCase):
    def test_empty_dict_returns_false(self):
        self.assertFalse(has_interrupted_session({}))

    def test_none_like_empty_dict_returns_false(self):
        self.assertFalse(has_interrupted_session({}))

    def test_empty_queue_returns_false(self):
        state = _make_state(queue_items=[], completed_items=[])
        self.assertFalse(has_interrupted_session(state))

    def test_all_items_completed_returns_false(self):
        item = _make_state_item("1001")
        state = _make_state(queue_items=[item], completed_items=[item])
        self.assertFalse(has_interrupted_session(state))

    def test_all_items_completed_multiple_returns_false(self):
        items = [_make_state_item(f"10{i}") for i in range(3)]
        state = _make_state(queue_items=items, completed_items=items)
        self.assertFalse(has_interrupted_session(state))

    def test_no_items_completed_returns_true(self):
        item = _make_state_item("2001")
        state = _make_state(queue_items=[item], completed_items=[])
        self.assertTrue(has_interrupted_session(state))

    def test_some_items_completed_returns_true(self):
        item1 = _make_state_item("3001")
        item2 = _make_state_item("3002")
        state = _make_state(queue_items=[item1, item2], completed_items=[item1])
        self.assertTrue(has_interrupted_session(state))

    def test_failed_items_only_returns_true(self):
        """A session where all items failed is still considered interrupted (not completed)."""
        item = _make_state_item("4001", status="failed")
        state = _make_state(queue_items=[item], completed_items=[], failed_items=[item])
        self.assertTrue(has_interrupted_session(state))

    def test_missing_queue_key_returns_false(self):
        state = {"billing_period": "202605", "completed": []}
        self.assertFalse(has_interrupted_session(state))

    def test_missing_completed_key_treats_as_zero_completed(self):
        item = _make_state_item("5001")
        state = {"queue": [item], "billing_period": "202605"}
        self.assertTrue(has_interrupted_session(state))

    def test_single_item_queue_partially_complete(self):
        item1 = _make_state_item("6001")
        item2 = _make_state_item("6002")
        state = _make_state(queue_items=[item1, item2], completed_items=[item1])
        self.assertTrue(has_interrupted_session(state))

    def test_completed_count_equals_queue_count_returns_false(self):
        items = [_make_state_item(f"70{i}") for i in range(5)]
        state = _make_state(queue_items=items, completed_items=items)
        self.assertFalse(has_interrupted_session(state))


# ---------------------------------------------------------------------------
# _build_completed_keys
# ---------------------------------------------------------------------------

class TestBuildCompletedKeys(unittest.TestCase):
    def test_empty_list_returns_empty_set(self):
        self.assertEqual(_build_completed_keys([]), set())

    def test_single_item_returns_correct_key(self):
        item = _make_state_item("1001", billing_period="202605")
        keys = _build_completed_keys([item])
        self.assertIn(("1001", "202605"), keys)

    def test_item_without_invoice_id_is_excluded(self):
        item = {"invoice_id": None, "billing_period": "202605"}
        keys = _build_completed_keys([item])
        self.assertEqual(keys, set())

    def test_multiple_items_all_indexed(self):
        items = [_make_state_item(f"10{i}", billing_period="202605") for i in range(3)]
        keys = _build_completed_keys(items)
        self.assertEqual(len(keys), 3)

    def test_duplicate_items_deduplicated_by_set(self):
        item = _make_state_item("1001")
        keys = _build_completed_keys([item, item])
        self.assertEqual(len(keys), 1)


# ---------------------------------------------------------------------------
# _is_completed
# ---------------------------------------------------------------------------

class TestIsCompleted(unittest.TestCase):
    def test_item_in_keys_returns_true(self):
        keys = {("1001", "202605")}
        item = _make_state_item("1001", billing_period="202605")
        self.assertTrue(_is_completed(item, keys))

    def test_item_not_in_keys_returns_false(self):
        keys = {("1001", "202605")}
        item = _make_state_item("9999", billing_period="202605")
        self.assertFalse(_is_completed(item, keys))

    def test_item_with_none_invoice_id_returns_false(self):
        keys = {("1001", "202605")}
        item = {"invoice_id": None, "billing_period": "202605"}
        self.assertFalse(_is_completed(item, keys))

    def test_same_invoice_id_different_billing_period_returns_false(self):
        keys = {("1001", "202605")}
        item = _make_state_item("1001", billing_period="202606")
        self.assertFalse(_is_completed(item, keys))

    def test_empty_keys_returns_false(self):
        item = _make_state_item("1001")
        self.assertFalse(_is_completed(item, set()))


# ---------------------------------------------------------------------------
# build_resume_queue
# ---------------------------------------------------------------------------

class TestBuildResumeQueue(unittest.TestCase):
    def test_empty_state_returns_empty_queue(self):
        queue = build_resume_queue({})
        self.assertEqual(len(queue), 0)

    def test_empty_queue_in_state_returns_empty_queue(self):
        state = _make_state(queue_items=[], completed_items=[])
        queue = build_resume_queue(state)
        self.assertEqual(len(queue), 0)

    def test_all_completed_returns_empty_queue(self):
        item = _make_state_item("1001")
        state = _make_state(queue_items=[item], completed_items=[item])
        queue = build_resume_queue(state)
        self.assertEqual(len(queue), 0)

    def test_no_completed_returns_all_items(self):
        items = [_make_state_item(f"20{i}") for i in range(3)]
        state = _make_state(queue_items=items, completed_items=[])
        queue = build_resume_queue(state)
        self.assertEqual(len(queue), 3)

    def test_partial_completed_returns_remaining_items(self):
        item1 = _make_state_item("3001")
        item2 = _make_state_item("3002")
        item3 = _make_state_item("3003")
        state = _make_state(
            queue_items=[item1, item2, item3],
            completed_items=[item1],
        )
        queue = build_resume_queue(state)
        self.assertEqual(len(queue), 2)

    def test_completed_items_are_excluded_by_invoice_id(self):
        item1 = _make_state_item("4001")
        item2 = _make_state_item("4002")
        state = _make_state(queue_items=[item1, item2], completed_items=[item1])
        queue = build_resume_queue(state)
        invoice_ids = {i.invoice_id for i in queue}
        self.assertNotIn("4001", invoice_ids)
        self.assertIn("4002", invoice_ids)

    def test_failed_items_are_included_in_resume(self):
        """Failed items must be retried; they should appear in the resume queue."""
        item1 = _make_state_item("5001")
        item2 = _make_state_item("5002", status="failed")
        state = _make_state(
            queue_items=[item1, item2],
            completed_items=[item1],
            failed_items=[item2],
        )
        queue = build_resume_queue(state)
        self.assertEqual(len(queue), 1)
        self.assertEqual(list(queue)[0].invoice_id, "5002")

    def test_item_without_download_url_is_skipped(self):
        item_with_url = _make_state_item("6001")
        item_without_url = {
            "invoice_id": "6002",
            "billing_period": "202605",
            "download_url": "",
            "filename": None,
        }
        state = _make_state(queue_items=[item_with_url, item_without_url], completed_items=[])
        queue = build_resume_queue(state)
        self.assertEqual(len(queue), 1)
        self.assertEqual(list(queue)[0].invoice_id, "6001")

    def test_item_without_download_url_key_is_skipped(self):
        item_with_url = _make_state_item("7001")
        item_missing_key = {"invoice_id": "7002", "billing_period": "202605"}
        state = _make_state(queue_items=[item_with_url, item_missing_key], completed_items=[])
        queue = build_resume_queue(state)
        self.assertEqual(len(queue), 1)

    def test_item_with_none_invoice_id_always_included(self):
        """Items without invoice_id can't be matched as completed; always include them."""
        item_no_id = {
            "invoice_id": None,
            "billing_period": "202605",
            "download_url": "https://example.com/dl?foo=bar",
            "filename": None,
        }
        some_completed = _make_state_item("8001")
        state = _make_state(
            queue_items=[item_no_id, some_completed],
            completed_items=[some_completed],
        )
        queue = build_resume_queue(state)
        # item_no_id is not completed → included; some_completed is excluded
        self.assertEqual(len(queue), 1)

    def test_resume_queue_contains_correct_invoice_ids(self):
        items = [_make_state_item(f"90{i}") for i in range(5)]
        completed = items[:2]
        state = _make_state(queue_items=items, completed_items=completed)
        queue = build_resume_queue(state)
        invoice_ids = {i.invoice_id for i in queue}
        self.assertEqual(invoice_ids, {"902", "903", "904"})

    def test_resume_queue_preserves_billing_period(self):
        item = _make_state_item("A001", billing_period="202612")
        state = _make_state(queue_items=[item], completed_items=[])
        queue = build_resume_queue(state)
        self.assertEqual(list(queue)[0].billing_period, "202612")

    def test_resume_queue_preserves_download_url(self):
        url = "https://example.com/dl?InvoiceID=B001&BillingPeriod=202605"
        item = _make_state_item("B001", url=url)
        state = _make_state(queue_items=[item], completed_items=[])
        queue = build_resume_queue(state)
        self.assertEqual(list(queue)[0].download_url, url)

    def test_resume_queue_preserves_filename(self):
        item = _make_state_item("C001", filename="202605_C001.xlsx")
        state = _make_state(queue_items=[item], completed_items=[])
        queue = build_resume_queue(state)
        self.assertEqual(list(queue)[0].filename, "202605_C001.xlsx")

    def test_resume_queue_items_have_pending_status(self):
        """Rebuilt queue items must start as pending regardless of saved status."""
        item = _make_state_item("D001", status="failed")
        state = _make_state(queue_items=[item], completed_items=[])
        queue = build_resume_queue(state)
        self.assertEqual(list(queue)[0].download_status, STATUS_PENDING)

    def test_returns_download_queue_instance(self):
        item = _make_state_item("E001")
        state = _make_state(queue_items=[item], completed_items=[])
        queue = build_resume_queue(state)
        self.assertIsInstance(queue, DownloadQueue)

    def test_large_queue_only_remaining_items(self):
        items = [_make_state_item(f"F{i:03d}") for i in range(100)]
        completed = items[:80]
        state = _make_state(queue_items=items, completed_items=completed)
        queue = build_resume_queue(state)
        self.assertEqual(len(queue), 20)


# ---------------------------------------------------------------------------
# Integration: DownloadState → has_interrupted_session → build_resume_queue
# ---------------------------------------------------------------------------

class TestResumeIntegrationWithDownloadState(unittest.TestCase):
    """End-to-end tests using a real DownloadState and real DownloadQueueItems."""

    def _make_queue_item(self, invoice_id, billing_period="202605"):
        return DownloadQueueItem(
            download_url=f"https://example.com/dl?InvoiceID={invoice_id}&BillingPeriod={billing_period}",
            invoice_id=invoice_id,
            billing_period=billing_period,
            filename=None,
        )

    def test_no_state_file_means_no_interrupted_session(self):
        with TemporaryDirectory() as tmp:
            ds = DownloadState(state_file=Path(tmp) / "no_such_file.json")
            state = ds.load_state()
        self.assertFalse(has_interrupted_session(state))

    def test_fresh_session_is_not_interrupted(self):
        """A session where all items were completed is not interrupted."""
        with TemporaryDirectory() as tmp:
            ds = DownloadState(state_file=Path(tmp) / "state.json")
            item = self._make_queue_item("1001")
            ds.initialize([item])
            ds.mark_completed(item)
            state = ds.load_state()

        self.assertFalse(has_interrupted_session(state))

    def test_partially_completed_session_is_interrupted(self):
        with TemporaryDirectory() as tmp:
            ds = DownloadState(state_file=Path(tmp) / "state.json", billing_period="202605")
            item1 = self._make_queue_item("2001")
            item2 = self._make_queue_item("2002")
            ds.initialize([item1, item2])
            ds.mark_completed(item1)
            # item2 never finished — session was interrupted
            state = ds.load_state()

        self.assertTrue(has_interrupted_session(state))

    def test_resume_queue_excludes_completed_items(self):
        with TemporaryDirectory() as tmp:
            ds = DownloadState(state_file=Path(tmp) / "state.json", billing_period="202605")
            item1 = self._make_queue_item("3001")
            item2 = self._make_queue_item("3002")
            ds.initialize([item1, item2])
            ds.mark_completed(item1)
            state = ds.load_state()

        queue = build_resume_queue(state)
        invoice_ids = {i.invoice_id for i in queue}
        self.assertNotIn("3001", invoice_ids)
        self.assertIn("3002", invoice_ids)

    def test_resume_queue_includes_failed_items(self):
        with TemporaryDirectory() as tmp:
            ds = DownloadState(state_file=Path(tmp) / "state.json", billing_period="202605")
            item1 = self._make_queue_item("4001")
            item2 = self._make_queue_item("4002")
            ds.initialize([item1, item2])
            ds.mark_completed(item1)
            ds.mark_failed(item2)
            state = ds.load_state()

        # item2 failed — must be retried
        self.assertTrue(has_interrupted_session(state))
        queue = build_resume_queue(state)
        self.assertEqual(len(queue), 1)
        self.assertEqual(list(queue)[0].invoice_id, "4002")

    def test_all_failed_session_is_interrupted_and_all_items_retried(self):
        with TemporaryDirectory() as tmp:
            ds = DownloadState(state_file=Path(tmp) / "state.json")
            items = [self._make_queue_item(f"50{i}") for i in range(3)]
            ds.initialize(items)
            for item in items:
                ds.mark_failed(item)
            state = ds.load_state()

        self.assertTrue(has_interrupted_session(state))
        queue = build_resume_queue(state)
        self.assertEqual(len(queue), 3)

    def test_resume_queue_items_are_downloadqueueitem_instances(self):
        with TemporaryDirectory() as tmp:
            ds = DownloadState(state_file=Path(tmp) / "state.json")
            item = self._make_queue_item("6001")
            ds.initialize([item])
            state = ds.load_state()

        queue = build_resume_queue(state)
        for queue_item in queue:
            self.assertIsInstance(queue_item, DownloadQueueItem)

    def test_resume_detects_crash_between_items(self):
        """Simulate a crash mid-run: some items completed, some never started."""
        with TemporaryDirectory() as tmp:
            ds = DownloadState(state_file=Path(tmp) / "state.json", billing_period="202605")
            items = [self._make_queue_item(f"70{i}") for i in range(5)]
            ds.initialize(items)
            # Only first two completed before crash
            ds.mark_completed(items[0])
            ds.mark_completed(items[1])
            state = ds.load_state()

        self.assertTrue(has_interrupted_session(state))
        queue = build_resume_queue(state)
        self.assertEqual(len(queue), 3)
        resumed_ids = {i.invoice_id for i in queue}
        self.assertEqual(resumed_ids, {"702", "703", "704"})

    def test_billing_period_preserved_in_resumed_items(self):
        with TemporaryDirectory() as tmp:
            ds = DownloadState(state_file=Path(tmp) / "state.json", billing_period="202612")
            item = self._make_queue_item("8001", billing_period="202612")
            ds.initialize([item])
            state = ds.load_state()

        queue = build_resume_queue(state)
        self.assertEqual(list(queue)[0].billing_period, "202612")

    def test_has_interrupted_session_after_initialize_only(self):
        """After initialize() with no completions the session is always interrupted."""
        with TemporaryDirectory() as tmp:
            ds = DownloadState(state_file=Path(tmp) / "state.json")
            items = [self._make_queue_item(f"90{i}") for i in range(4)]
            ds.initialize(items)
            state = ds.load_state()

        self.assertTrue(has_interrupted_session(state))

    def test_re_initialized_session_is_not_interrupted(self):
        """After re-initializing an interrupted session with one item and
        immediately completing it, the session is finished."""
        with TemporaryDirectory() as tmp:
            state_file = Path(tmp) / "state.json"
            ds = DownloadState(state_file=state_file)

            # First run: started but incomplete
            items = [self._make_queue_item(f"A00{i}") for i in range(3)]
            ds.initialize(items)
            ds.mark_completed(items[0])

            # Second run: fresh re-initialization with single item
            one_item = self._make_queue_item("A999")
            ds.initialize([one_item])
            ds.mark_completed(one_item)
            state = ds.load_state()

        self.assertFalse(has_interrupted_session(state))


if __name__ == "__main__":
    unittest.main()
