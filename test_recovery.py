"""Comprehensive unit tests for ibis/recovery.py."""

import json
import unittest
from datetime import datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import MagicMock, patch

from selenium.common.exceptions import (
    InvalidSessionIdException,
    NoSuchWindowException,
    WebDriverException,
)

from ibis.downloader import DownloadQueueItem, STATUS_PENDING
from ibis.recovery import (
    CrashRecoveryHandler,
    RecoveryReport,
    _RECOVERY_ADVICE,
    is_browser_failure,
)
from ibis.state import DownloadState


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_item(invoice_id="INV001", billing_period="202605"):
    return DownloadQueueItem(
        download_url=f"https://example.com/dl?InvoiceID={invoice_id}&BillingPeriod={billing_period}",
        invoice_id=invoice_id,
        billing_period=billing_period,
        filename=None,
    )


class _FakeWebDriverException(Exception):
    """Simulates a WebDriverException by matching its class name."""


class _FakeInvalidSessionIdException(Exception):
    """Simulates an InvalidSessionIdException by matching its class name."""
    # Renamed to match selenium name in MRO lookup via aliasing below.


# Give the fake class the same *name* the framework looks for.
_FakeInvalidSessionIdException.__name__ = "InvalidSessionIdException"


class _FakeNoSuchWindowException(Exception):
    """Simulates a NoSuchWindowException by matching its class name."""


_FakeNoSuchWindowException.__name__ = "NoSuchWindowException"


# Subclass of selenium's WebDriverException
class _SubclassOfWebDriver(WebDriverException):
    pass


# ---------------------------------------------------------------------------
# is_browser_failure
# ---------------------------------------------------------------------------

class TestIsBrowserFailure(unittest.TestCase):

    # --- real selenium exceptions ---

    def test_webdriver_exception_is_browser_failure(self):
        self.assertTrue(is_browser_failure(WebDriverException("session error")))

    def test_invalid_session_id_exception_is_browser_failure(self):
        self.assertTrue(is_browser_failure(InvalidSessionIdException("invalid id")))

    def test_no_such_window_exception_is_browser_failure(self):
        self.assertTrue(is_browser_failure(NoSuchWindowException("no window")))

    def test_subclass_of_webdriver_exception_is_browser_failure(self):
        self.assertTrue(is_browser_failure(_SubclassOfWebDriver("sub")))

    # --- name-based fallback ---

    def test_fake_webdriver_exception_by_name_is_browser_failure(self):
        FakeWebDriverException = type("WebDriverException", (Exception,), {})
        self.assertTrue(is_browser_failure(FakeWebDriverException("fake")))

    def test_fake_invalid_session_id_by_name_is_browser_failure(self):
        self.assertTrue(is_browser_failure(_FakeInvalidSessionIdException("x")))

    def test_fake_no_such_window_by_name_is_browser_failure(self):
        self.assertTrue(is_browser_failure(_FakeNoSuchWindowException("y")))

    # --- non-browser exceptions ---

    def test_plain_exception_is_not_browser_failure(self):
        self.assertFalse(is_browser_failure(Exception("oops")))

    def test_runtime_error_is_not_browser_failure(self):
        self.assertFalse(is_browser_failure(RuntimeError("runtime")))

    def test_value_error_is_not_browser_failure(self):
        self.assertFalse(is_browser_failure(ValueError("bad value")))

    def test_os_error_is_not_browser_failure(self):
        self.assertFalse(is_browser_failure(OSError("disk error")))

    def test_timeout_error_is_not_browser_failure(self):
        self.assertFalse(is_browser_failure(TimeoutError("timeout")))

    def test_keyboard_interrupt_is_not_browser_failure(self):
        self.assertFalse(is_browser_failure(KeyboardInterrupt()))

    def test_attribute_error_is_not_browser_failure(self):
        self.assertFalse(is_browser_failure(AttributeError("attr")))

    def test_import_error_is_not_browser_failure(self):
        self.assertFalse(is_browser_failure(ImportError("import")))


# ---------------------------------------------------------------------------
# RecoveryReport
# ---------------------------------------------------------------------------

class TestRecoveryReport(unittest.TestCase):
    def _make_report(self, **kwargs):
        defaults = dict(
            timestamp="2026-07-19T00:00:00+00:00",
            exception_type="WebDriverException",
            exception_message="browser crashed",
            state_file="/tmp/state.json",
            completed_count=3,
            pending_count=2,
            failed_count=1,
            recovery_advice=_RECOVERY_ADVICE,
        )
        defaults.update(kwargs)
        return RecoveryReport(**defaults)

    def test_fields_stored_correctly(self):
        r = self._make_report()
        self.assertEqual(r.exception_type, "WebDriverException")
        self.assertEqual(r.exception_message, "browser crashed")
        self.assertEqual(r.completed_count, 3)
        self.assertEqual(r.pending_count, 2)
        self.assertEqual(r.failed_count, 1)
        self.assertEqual(r.state_file, "/tmp/state.json")

    def test_to_dict_returns_dict(self):
        r = self._make_report()
        d = r.to_dict()
        self.assertIsInstance(d, dict)

    def test_to_dict_contains_all_fields(self):
        r = self._make_report()
        d = r.to_dict()
        for field in (
            "timestamp", "exception_type", "exception_message", "state_file",
            "completed_count", "pending_count", "failed_count", "recovery_advice",
        ):
            self.assertIn(field, d)

    def test_to_dict_values_match(self):
        r = self._make_report()
        d = r.to_dict()
        self.assertEqual(d["exception_type"], "WebDriverException")
        self.assertEqual(d["completed_count"], 3)
        self.assertEqual(d["pending_count"], 2)
        self.assertEqual(d["failed_count"], 1)

    def test_to_dict_state_file_none_when_not_provided(self):
        r = self._make_report(state_file=None)
        d = r.to_dict()
        self.assertIsNone(d["state_file"])

    def test_to_dict_is_json_serialisable(self):
        r = self._make_report()
        raw = json.dumps(r.to_dict())
        self.assertIsInstance(json.loads(raw), dict)

    def test_recovery_advice_is_not_empty(self):
        r = self._make_report()
        self.assertTrue(r.recovery_advice)


# ---------------------------------------------------------------------------
# CrashRecoveryHandler initialisation
# ---------------------------------------------------------------------------

class TestCrashRecoveryHandlerInit(unittest.TestCase):
    def test_default_report_file_in_state_dir(self):
        handler = CrashRecoveryHandler()
        self.assertIn("state", str(handler.report_file))
        self.assertTrue(str(handler.report_file).endswith(".json"))

    def test_custom_report_file_string(self):
        handler = CrashRecoveryHandler(report_file="/tmp/custom_report.json")
        self.assertEqual(handler.report_file, Path("/tmp/custom_report.json"))

    def test_custom_report_file_path(self):
        p = Path("/tmp/report.json")
        handler = CrashRecoveryHandler(report_file=p)
        self.assertEqual(handler.report_file, p)

    def test_download_state_stored(self):
        ds = MagicMock()
        handler = CrashRecoveryHandler(download_state=ds)
        self.assertIs(handler.download_state, ds)

    def test_no_download_state_by_default(self):
        handler = CrashRecoveryHandler()
        self.assertIsNone(handler.download_state)


# ---------------------------------------------------------------------------
# CrashRecoveryHandler.handle
# ---------------------------------------------------------------------------

class TestCrashRecoveryHandlerHandle(unittest.TestCase):

    def test_returns_recovery_report_instance(self):
        with TemporaryDirectory() as tmp:
            handler = CrashRecoveryHandler(report_file=Path(tmp) / "r.json")
            report = handler.handle(WebDriverException("crash"))
        self.assertIsInstance(report, RecoveryReport)

    def test_exception_type_in_report(self):
        with TemporaryDirectory() as tmp:
            handler = CrashRecoveryHandler(report_file=Path(tmp) / "r.json")
            report = handler.handle(WebDriverException("crash"))
        self.assertEqual(report.exception_type, "WebDriverException")

    def test_exception_message_in_report(self):
        with TemporaryDirectory() as tmp:
            handler = CrashRecoveryHandler(report_file=Path(tmp) / "r.json")
            exc = WebDriverException("session gone")
            report = handler.handle(exc)
        self.assertIn("session gone", report.exception_message)

    def test_invalid_session_id_exception_captured(self):
        with TemporaryDirectory() as tmp:
            handler = CrashRecoveryHandler(report_file=Path(tmp) / "r.json")
            report = handler.handle(InvalidSessionIdException("bad session"))
        self.assertEqual(report.exception_type, "InvalidSessionIdException")

    def test_no_such_window_exception_captured(self):
        with TemporaryDirectory() as tmp:
            handler = CrashRecoveryHandler(report_file=Path(tmp) / "r.json")
            report = handler.handle(NoSuchWindowException("window closed"))
        self.assertEqual(report.exception_type, "NoSuchWindowException")

    def test_timestamp_is_utc_iso_string(self):
        with TemporaryDirectory() as tmp:
            handler = CrashRecoveryHandler(report_file=Path(tmp) / "r.json")
            report = handler.handle(WebDriverException("x"))
        self.assertIsInstance(report.timestamp, str)
        self.assertGreater(len(report.timestamp), 0)
        # Ensure it parses as a datetime
        datetime.fromisoformat(report.timestamp)

    def test_recovery_advice_present_in_report(self):
        with TemporaryDirectory() as tmp:
            handler = CrashRecoveryHandler(report_file=Path(tmp) / "r.json")
            report = handler.handle(WebDriverException("x"))
        self.assertEqual(report.recovery_advice, _RECOVERY_ADVICE)

    # --- state saving ---

    def test_save_state_called_on_download_state(self):
        with TemporaryDirectory() as tmp:
            mock_state = MagicMock()
            mock_state._completed = []
            mock_state._failed = []
            mock_state._queue = []
            handler = CrashRecoveryHandler(
                download_state=mock_state,
                report_file=Path(tmp) / "r.json",
            )
            handler.handle(WebDriverException("crash"))
        mock_state.save_state.assert_called_once()

    def test_save_state_not_called_when_no_download_state(self):
        with TemporaryDirectory() as tmp:
            handler = CrashRecoveryHandler(report_file=Path(tmp) / "r.json")
            # No AttributeError should be raised; no state saved
            report = handler.handle(WebDriverException("x"))
        self.assertIsNone(report.state_file)

    def test_state_file_in_report_matches_download_state(self):
        with TemporaryDirectory() as tmp:
            state_file = Path(tmp) / "state" / "download_state.json"
            ds = DownloadState(state_file=state_file)
            handler = CrashRecoveryHandler(
                download_state=ds,
                report_file=Path(tmp) / "r.json",
            )
            report = handler.handle(WebDriverException("crash"))
        self.assertEqual(report.state_file, str(state_file))

    def test_state_file_is_none_without_download_state(self):
        with TemporaryDirectory() as tmp:
            handler = CrashRecoveryHandler(report_file=Path(tmp) / "r.json")
            report = handler.handle(WebDriverException("x"))
        self.assertIsNone(report.state_file)

    # --- item counts ---

    def test_counts_zero_with_no_download_state(self):
        with TemporaryDirectory() as tmp:
            handler = CrashRecoveryHandler(report_file=Path(tmp) / "r.json")
            report = handler.handle(WebDriverException("x"))
        self.assertEqual(report.completed_count, 0)
        self.assertEqual(report.pending_count, 0)
        self.assertEqual(report.failed_count, 0)

    def test_completed_count_correct(self):
        with TemporaryDirectory() as tmp:
            state_file = Path(tmp) / "s.json"
            ds = DownloadState(state_file=state_file)
            items = [_make_item(f"10{i}") for i in range(3)]
            ds.initialize(items)
            ds.mark_completed(items[0])
            ds.mark_completed(items[1])

            handler = CrashRecoveryHandler(
                download_state=ds,
                report_file=Path(tmp) / "r.json",
            )
            report = handler.handle(WebDriverException("crash"))

        self.assertEqual(report.completed_count, 2)

    def test_failed_count_correct(self):
        with TemporaryDirectory() as tmp:
            state_file = Path(tmp) / "s.json"
            ds = DownloadState(state_file=state_file)
            items = [_make_item(f"20{i}") for i in range(3)]
            ds.initialize(items)
            ds.mark_failed(items[0])

            handler = CrashRecoveryHandler(
                download_state=ds,
                report_file=Path(tmp) / "r.json",
            )
            report = handler.handle(WebDriverException("crash"))

        self.assertEqual(report.failed_count, 1)

    def test_pending_count_correct(self):
        with TemporaryDirectory() as tmp:
            state_file = Path(tmp) / "s.json"
            ds = DownloadState(state_file=state_file)
            items = [_make_item(f"30{i}") for i in range(5)]
            ds.initialize(items)
            ds.mark_completed(items[0])
            ds.mark_failed(items[1])
            # items[2], [3], [4] are still pending

            handler = CrashRecoveryHandler(
                download_state=ds,
                report_file=Path(tmp) / "r.json",
            )
            report = handler.handle(WebDriverException("crash"))

        self.assertEqual(report.completed_count, 1)
        self.assertEqual(report.failed_count, 1)
        self.assertEqual(report.pending_count, 3)

    def test_pending_count_zero_when_all_items_handled(self):
        with TemporaryDirectory() as tmp:
            state_file = Path(tmp) / "s.json"
            ds = DownloadState(state_file=state_file)
            items = [_make_item(f"40{i}") for i in range(2)]
            ds.initialize(items)
            ds.mark_completed(items[0])
            ds.mark_failed(items[1])

            handler = CrashRecoveryHandler(
                download_state=ds,
                report_file=Path(tmp) / "r.json",
            )
            report = handler.handle(WebDriverException("crash"))

        self.assertEqual(report.pending_count, 0)

    def test_all_pending_when_none_processed(self):
        with TemporaryDirectory() as tmp:
            state_file = Path(tmp) / "s.json"
            ds = DownloadState(state_file=state_file)
            items = [_make_item(f"50{i}") for i in range(4)]
            ds.initialize(items)

            handler = CrashRecoveryHandler(
                download_state=ds,
                report_file=Path(tmp) / "r.json",
            )
            report = handler.handle(WebDriverException("crash"))

        self.assertEqual(report.completed_count, 0)
        self.assertEqual(report.failed_count, 0)
        self.assertEqual(report.pending_count, 4)

    # --- report written to disk ---

    def test_report_file_written_to_disk(self):
        with TemporaryDirectory() as tmp:
            report_file = Path(tmp) / "r.json"
            handler = CrashRecoveryHandler(report_file=report_file)
            handler.handle(WebDriverException("x"))
            self.assertTrue(report_file.exists())

    def test_report_file_contains_valid_json(self):
        with TemporaryDirectory() as tmp:
            report_file = Path(tmp) / "r.json"
            handler = CrashRecoveryHandler(report_file=report_file)
            handler.handle(WebDriverException("x"))
            raw = report_file.read_text(encoding="utf-8")
            data = json.loads(raw)
        self.assertIsInstance(data, dict)

    def test_report_file_contains_correct_exception_type(self):
        with TemporaryDirectory() as tmp:
            report_file = Path(tmp) / "r.json"
            handler = CrashRecoveryHandler(report_file=report_file)
            handler.handle(NoSuchWindowException("bye"))
            data = json.loads(report_file.read_text(encoding="utf-8"))
        self.assertEqual(data["exception_type"], "NoSuchWindowException")

    def test_report_file_parent_created_automatically(self):
        with TemporaryDirectory() as tmp:
            report_file = Path(tmp) / "a" / "b" / "c" / "r.json"
            handler = CrashRecoveryHandler(report_file=report_file)
            handler.handle(WebDriverException("x"))
            self.assertTrue(report_file.exists())

    def test_state_file_persisted_on_disk(self):
        """When a DownloadState is provided the state JSON must be written."""
        with TemporaryDirectory() as tmp:
            state_file = Path(tmp) / "state.json"
            ds = DownloadState(state_file=state_file)
            item = _make_item("6001")
            ds.initialize([item])
            ds.mark_completed(item)

            handler = CrashRecoveryHandler(
                download_state=ds,
                report_file=Path(tmp) / "r.json",
            )
            handler.handle(WebDriverException("crash mid-run"))
            self.assertTrue(state_file.exists())


# ---------------------------------------------------------------------------
# CrashRecoveryHandler.save_report
# ---------------------------------------------------------------------------

class TestCrashRecoveryHandlerSaveReport(unittest.TestCase):
    def _make_report(self):
        return RecoveryReport(
            timestamp="2026-07-19T00:00:00+00:00",
            exception_type="WebDriverException",
            exception_message="crash",
            state_file=None,
            completed_count=0,
            pending_count=1,
            failed_count=0,
            recovery_advice=_RECOVERY_ADVICE,
        )

    def test_save_report_writes_json_file(self):
        with TemporaryDirectory() as tmp:
            report_file = Path(tmp) / "report.json"
            handler = CrashRecoveryHandler(report_file=report_file)
            handler.save_report(self._make_report())
            self.assertTrue(report_file.exists())

    def test_save_report_creates_parent_dirs(self):
        with TemporaryDirectory() as tmp:
            report_file = Path(tmp) / "x" / "y" / "report.json"
            handler = CrashRecoveryHandler(report_file=report_file)
            handler.save_report(self._make_report())
            self.assertTrue(report_file.exists())

    def test_save_report_is_valid_json(self):
        with TemporaryDirectory() as tmp:
            report_file = Path(tmp) / "report.json"
            handler = CrashRecoveryHandler(report_file=report_file)
            handler.save_report(self._make_report())
            data = json.loads(report_file.read_text(encoding="utf-8"))
        self.assertIsInstance(data, dict)

    def test_save_report_contains_all_fields(self):
        with TemporaryDirectory() as tmp:
            report_file = Path(tmp) / "report.json"
            handler = CrashRecoveryHandler(report_file=report_file)
            handler.save_report(self._make_report())
            data = json.loads(report_file.read_text(encoding="utf-8"))
        for key in (
            "timestamp", "exception_type", "exception_message", "state_file",
            "completed_count", "pending_count", "failed_count", "recovery_advice",
        ):
            self.assertIn(key, data)

    def test_save_report_can_be_overwritten(self):
        with TemporaryDirectory() as tmp:
            report_file = Path(tmp) / "report.json"
            handler = CrashRecoveryHandler(report_file=report_file)
            r1 = self._make_report()
            r2 = RecoveryReport(
                timestamp="2026-07-19T12:00:00+00:00",
                exception_type="InvalidSessionIdException",
                exception_message="second crash",
                state_file=None,
                completed_count=5,
                pending_count=0,
                failed_count=0,
                recovery_advice=_RECOVERY_ADVICE,
            )
            handler.save_report(r1)
            handler.save_report(r2)
            data = json.loads(report_file.read_text(encoding="utf-8"))
        self.assertEqual(data["exception_type"], "InvalidSessionIdException")
        self.assertEqual(data["completed_count"], 5)


# ---------------------------------------------------------------------------
# Integration: DownloadState → CrashRecoveryHandler
# ---------------------------------------------------------------------------

class TestCrashRecoveryIntegration(unittest.TestCase):
    """End-to-end tests: real DownloadState + CrashRecoveryHandler."""

    def test_full_crash_mid_run_scenario(self):
        """Simulate a crash after some items completed; verify report and state."""
        with TemporaryDirectory() as tmp:
            state_file = Path(tmp) / "state.json"
            report_file = Path(tmp) / "recovery_report.json"

            ds = DownloadState(state_file=state_file, billing_period="202605")
            items = [_make_item(f"70{i}") for i in range(5)]
            ds.initialize(items)
            ds.mark_completed(items[0])
            ds.mark_completed(items[1])
            ds.mark_failed(items[2])

            handler = CrashRecoveryHandler(download_state=ds, report_file=report_file)
            report = handler.handle(WebDriverException("Browser quit unexpectedly"))

            # Report fields
            self.assertEqual(report.exception_type, "WebDriverException")
            self.assertEqual(report.completed_count, 2)
            self.assertEqual(report.failed_count, 1)
            self.assertEqual(report.pending_count, 2)

            # State file was written
            self.assertTrue(state_file.exists())

            # Recovery report written
            self.assertTrue(report_file.exists())
            on_disk = json.loads(report_file.read_text(encoding="utf-8"))
            self.assertEqual(on_disk["completed_count"], 2)
            self.assertEqual(on_disk["failed_count"], 1)
            self.assertEqual(on_disk["pending_count"], 2)

    def test_crash_at_start_all_pending(self):
        with TemporaryDirectory() as tmp:
            state_file = Path(tmp) / "state.json"
            report_file = Path(tmp) / "r.json"
            ds = DownloadState(state_file=state_file)
            items = [_make_item(f"80{i}") for i in range(3)]
            ds.initialize(items)

            handler = CrashRecoveryHandler(download_state=ds, report_file=report_file)
            report = handler.handle(InvalidSessionIdException("session gone"))

        self.assertEqual(report.completed_count, 0)
        self.assertEqual(report.failed_count, 0)
        self.assertEqual(report.pending_count, 3)
        self.assertEqual(report.exception_type, "InvalidSessionIdException")

    def test_crash_with_no_prior_state(self):
        """CrashRecoveryHandler must work even without a DownloadState."""
        with TemporaryDirectory() as tmp:
            report_file = Path(tmp) / "r.json"
            handler = CrashRecoveryHandler(report_file=report_file)
            report = handler.handle(NoSuchWindowException("window gone"))

            self.assertEqual(report.exception_type, "NoSuchWindowException")
            self.assertIsNone(report.state_file)
            self.assertEqual(report.completed_count, 0)
            self.assertEqual(report.pending_count, 0)
            self.assertEqual(report.failed_count, 0)
            self.assertTrue(report_file.exists())

    def test_non_browser_exception_can_also_be_handled(self):
        """handle() works for any exception, not only browser failures."""
        with TemporaryDirectory() as tmp:
            report_file = Path(tmp) / "r.json"
            handler = CrashRecoveryHandler(report_file=report_file)
            report = handler.handle(RuntimeError("unexpected error"))

        self.assertEqual(report.exception_type, "RuntimeError")
        self.assertFalse(is_browser_failure(RuntimeError("x")))

    def test_state_contents_correct_after_crash(self):
        """State file written by handle() must reflect completed/failed items."""
        with TemporaryDirectory() as tmp:
            state_file = Path(tmp) / "state.json"
            report_file = Path(tmp) / "r.json"
            ds = DownloadState(state_file=state_file, billing_period="202612")
            items = [_make_item(f"90{i}", billing_period="202612") for i in range(3)]
            ds.initialize(items)
            ds.mark_completed(items[0])

            handler = CrashRecoveryHandler(download_state=ds, report_file=report_file)
            handler.handle(WebDriverException("crash"))

            loaded = json.loads(state_file.read_text(encoding="utf-8"))
            self.assertEqual(len(loaded["completed"]), 1)
            self.assertEqual(loaded["billing_period"], "202612")


if __name__ == "__main__":
    unittest.main()
