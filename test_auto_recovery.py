"""Comprehensive unit tests for ibis/auto_recovery.py."""

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import MagicMock, call, patch

from selenium.common.exceptions import (
    InvalidSessionIdException,
    NoSuchWindowException,
    WebDriverException,
)

from ibis.auto_recovery import AutoRecovery, RecoveryResult
from ibis.downloader import DownloadQueue, DownloadQueueItem, STATUS_PENDING
from ibis.recovery import RecoveryReport
from ibis.state import DownloadState


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_item(invoice_id="INV001", billing_period="202605"):
    return DownloadQueueItem(
        download_url=(
            f"https://example.com/dl?InvoiceID={invoice_id}"
            f"&BillingPeriod={billing_period}"
        ),
        invoice_id=invoice_id,
        billing_period=billing_period,
        filename=None,
    )


def _make_browser_exc():
    """Return a real WebDriverException for use in tests."""
    return WebDriverException("browser crashed")


def _fake_report():
    return RecoveryReport(
        timestamp="2026-01-01T00:00:00+00:00",
        exception_type="WebDriverException",
        exception_message="browser crashed",
        state_file=None,
        completed_count=0,
        pending_count=0,
        failed_count=0,
        recovery_advice="Restart to resume.",
    )


def _make_auto_recovery(
    download_state=None,
    create_driver_fn=None,
    wait_login_fn=None,
    open_invoice_fn=None,
    **kwargs,
):
    """Factory that provides sensible mock defaults for ``AutoRecovery``."""
    return AutoRecovery(
        download_state=download_state or MagicMock(spec=DownloadState),
        create_driver_fn=create_driver_fn or MagicMock(return_value=MagicMock()),
        wait_login_fn=wait_login_fn or MagicMock(return_value=True),
        open_invoice_fn=open_invoice_fn or MagicMock(return_value="<html/>"),
        **kwargs,
    )


# ---------------------------------------------------------------------------
# RecoveryResult dataclass
# ---------------------------------------------------------------------------

class TestRecoveryResult(unittest.TestCase):
    def test_fields_accessible(self):
        driver = MagicMock()
        queue = DownloadQueue()
        ds = MagicMock(spec=DownloadState)
        report = _fake_report()
        result = RecoveryResult(
            report=report, driver=driver, queue=queue, download_state=ds
        )
        self.assertIs(result.report, report)
        self.assertIs(result.driver, driver)
        self.assertIs(result.queue, queue)
        self.assertIs(result.download_state, ds)

    def test_is_dataclass(self):
        import dataclasses
        self.assertTrue(dataclasses.is_dataclass(RecoveryResult))


# ---------------------------------------------------------------------------
# AutoRecovery.__init__
# ---------------------------------------------------------------------------

class TestAutoRecoveryInit(unittest.TestCase):
    def test_stores_all_parameters(self):
        ds = MagicMock(spec=DownloadState)
        create_fn = MagicMock()
        login_fn = MagicMock()
        invoice_fn = MagicMock()
        ar = AutoRecovery(
            ds, create_fn, login_fn, invoice_fn,
            report_file="/tmp/report.json",
            max_retries=5,
        )
        self.assertIs(ar.download_state, ds)
        self.assertIs(ar.create_driver_fn, create_fn)
        self.assertIs(ar.wait_login_fn, login_fn)
        self.assertIs(ar.open_invoice_fn, invoice_fn)
        self.assertEqual(ar.report_file, "/tmp/report.json")
        self.assertEqual(ar.max_retries, 5)

    def test_default_report_file_is_none(self):
        ar = _make_auto_recovery()
        self.assertIsNone(ar.report_file)

    def test_default_max_retries_is_three(self):
        ar = _make_auto_recovery()
        self.assertEqual(ar.max_retries, 3)


# ---------------------------------------------------------------------------
# AutoRecovery.recover – ValueError for non-browser failures
# ---------------------------------------------------------------------------

class TestAutoRecoveryNonBrowserFailure(unittest.TestCase):
    def test_plain_exception_raises_value_error(self):
        ar = _make_auto_recovery()
        with self.assertRaises(ValueError) as ctx:
            ar.recover(Exception("something else"))
        self.assertIn("non-browser failure", str(ctx.exception))

    def test_value_error_names_exception_type(self):
        ar = _make_auto_recovery()

        class MyError(Exception):
            pass

        with self.assertRaises(ValueError) as ctx:
            ar.recover(MyError("oops"))
        self.assertIn("MyError", str(ctx.exception))

    def test_runtime_error_is_not_browser_failure(self):
        ar = _make_auto_recovery()
        with self.assertRaises(ValueError):
            ar.recover(RuntimeError("runtime"))

    def test_value_error_is_not_browser_failure(self):
        ar = _make_auto_recovery()
        with self.assertRaises(ValueError):
            ar.recover(ValueError("value"))


# ---------------------------------------------------------------------------
# AutoRecovery.recover – happy path
# ---------------------------------------------------------------------------

class TestAutoRecoveryHappyPath(unittest.TestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.state_file = Path(self.tmp.name) / "state.json"

        # Build a real DownloadState with one pending item so the session
        # looks interrupted and build_resume_queue returns a non-empty queue.
        self.ds = DownloadState(state_file=self.state_file, billing_period="202605")
        item = _make_item("INV001")
        self.ds.initialize([item])
        # Item not completed → interrupted session

        self.mock_driver = MagicMock()
        self.create_driver_fn = MagicMock(return_value=self.mock_driver)
        self.wait_login_fn = MagicMock(return_value=True)
        self.open_invoice_fn = MagicMock(return_value="<html/>")

    def tearDown(self):
        self.tmp.cleanup()

    def _make_ar(self, **kwargs):
        return AutoRecovery(
            self.ds,
            self.create_driver_fn,
            self.wait_login_fn,
            self.open_invoice_fn,
            **kwargs,
        )

    def _patch_handler(self, ar):
        """Patch CrashRecoveryHandler so it doesn't write to disk."""
        mock_handler = MagicMock()
        mock_handler.handle.return_value = _fake_report()
        patcher = patch(
            "ibis.auto_recovery.CrashRecoveryHandler", return_value=mock_handler
        )
        return patcher, mock_handler

    def test_returns_recovery_result(self):
        ar = self._make_ar()
        patcher, _ = self._patch_handler(ar)
        with patcher:
            result = ar.recover(_make_browser_exc())
        self.assertIsInstance(result, RecoveryResult)

    def test_result_contains_new_driver(self):
        ar = self._make_ar()
        patcher, _ = self._patch_handler(ar)
        with patcher:
            result = ar.recover(_make_browser_exc())
        self.assertIs(result.driver, self.mock_driver)

    def test_result_contains_download_state(self):
        ar = self._make_ar()
        patcher, _ = self._patch_handler(ar)
        with patcher:
            result = ar.recover(_make_browser_exc())
        self.assertIs(result.download_state, self.ds)

    def test_result_contains_recovery_report(self):
        ar = self._make_ar()
        patcher, mock_handler = self._patch_handler(ar)
        expected_report = _fake_report()
        mock_handler.handle.return_value = expected_report
        with patcher:
            result = ar.recover(_make_browser_exc())
        self.assertIs(result.report, expected_report)

    def test_result_queue_is_download_queue(self):
        ar = self._make_ar()
        patcher, _ = self._patch_handler(ar)
        with patcher:
            result = ar.recover(_make_browser_exc())
        self.assertIsInstance(result.queue, DownloadQueue)

    def test_resume_queue_contains_pending_item(self):
        ar = self._make_ar()
        patcher, _ = self._patch_handler(ar)
        with patcher:
            result = ar.recover(_make_browser_exc())
        invoice_ids = {i.invoice_id for i in result.queue}
        self.assertIn("INV001", invoice_ids)

    def test_create_driver_called_once(self):
        ar = self._make_ar()
        patcher, _ = self._patch_handler(ar)
        with patcher:
            ar.recover(_make_browser_exc())
        self.create_driver_fn.assert_called_once()

    def test_wait_login_called_with_driver(self):
        ar = self._make_ar()
        patcher, _ = self._patch_handler(ar)
        with patcher:
            ar.recover(_make_browser_exc())
        self.wait_login_fn.assert_called_once_with(self.mock_driver)

    def test_open_invoice_called_with_driver(self):
        ar = self._make_ar()
        patcher, _ = self._patch_handler(ar)
        with patcher:
            ar.recover(_make_browser_exc())
        self.open_invoice_fn.assert_called_once_with(self.mock_driver)

    def test_call_order_driver_then_login_then_invoice(self):
        """create_driver → wait_login → open_invoice must happen in that order."""
        call_log = []
        self.create_driver_fn.side_effect = lambda: call_log.append("create") or self.mock_driver
        self.wait_login_fn.side_effect = lambda d: call_log.append("login")
        self.open_invoice_fn.side_effect = lambda d: call_log.append("invoice")

        ar = self._make_ar()
        patcher, _ = self._patch_handler(ar)
        with patcher:
            ar.recover(_make_browser_exc())

        self.assertEqual(call_log, ["create", "login", "invoice"])

    def test_crash_handler_called_with_exception(self):
        ar = self._make_ar()
        exc = _make_browser_exc()
        patcher, mock_handler = self._patch_handler(ar)
        with patcher:
            ar.recover(exc)
        mock_handler.handle.assert_called_once_with(exc)

    def test_crash_handler_instantiated_with_download_state(self):
        ar = self._make_ar()
        patcher, _ = self._patch_handler(ar)
        with patch("ibis.auto_recovery.CrashRecoveryHandler") as MockCRH:
            MockCRH.return_value.handle.return_value = _fake_report()
            ar.recover(_make_browser_exc())
        _, kwargs = MockCRH.call_args
        self.assertIs(kwargs.get("download_state"), self.ds)

    def test_report_file_forwarded_to_handler(self):
        ar = self._make_ar(report_file="/custom/report.json")
        with patch("ibis.auto_recovery.CrashRecoveryHandler") as MockCRH:
            MockCRH.return_value.handle.return_value = _fake_report()
            ar.recover(_make_browser_exc())
        _, kwargs = MockCRH.call_args
        self.assertEqual(kwargs.get("report_file"), "/custom/report.json")

    def test_different_browser_exceptions_all_trigger_recovery(self):
        for exc in [
            WebDriverException("wd"),
            InvalidSessionIdException("inv"),
            NoSuchWindowException("nsw"),
        ]:
            with self.subTest(exc_type=type(exc).__name__):
                ar = self._make_ar()
                patcher, _ = self._patch_handler(ar)
                with patcher:
                    result = ar.recover(exc)
                self.assertIsInstance(result, RecoveryResult)


# ---------------------------------------------------------------------------
# AutoRecovery.recover – empty queue when no interrupted session
# ---------------------------------------------------------------------------

class TestAutoRecoveryNoInterruptedSession(unittest.TestCase):
    def test_returns_empty_queue_when_all_items_completed(self):
        with TemporaryDirectory() as tmp:
            state_file = Path(tmp) / "state.json"
            ds = DownloadState(state_file=state_file)
            item = _make_item("INV999")
            ds.initialize([item])
            ds.mark_completed(item)
            # All completed → has_interrupted_session returns False

            ar = AutoRecovery(
                ds,
                create_driver_fn=MagicMock(return_value=MagicMock()),
                wait_login_fn=MagicMock(),
                open_invoice_fn=MagicMock(),
            )
            with patch("ibis.auto_recovery.CrashRecoveryHandler") as MockCRH:
                MockCRH.return_value.handle.return_value = _fake_report()
                result = ar.recover(_make_browser_exc())

        self.assertIsInstance(result.queue, DownloadQueue)
        self.assertEqual(len(result.queue), 0)

    def test_returns_empty_queue_when_no_state_file(self):
        with TemporaryDirectory() as tmp:
            state_file = Path(tmp) / "nonexistent.json"
            ds = DownloadState(state_file=state_file)
            # No state file at all → has_interrupted_session returns False

            ar = AutoRecovery(
                ds,
                create_driver_fn=MagicMock(return_value=MagicMock()),
                wait_login_fn=MagicMock(),
                open_invoice_fn=MagicMock(),
            )
            with patch("ibis.auto_recovery.CrashRecoveryHandler") as MockCRH:
                MockCRH.return_value.handle.return_value = _fake_report()
                result = ar.recover(_make_browser_exc())

        self.assertEqual(len(result.queue), 0)


# ---------------------------------------------------------------------------
# AutoRecovery.recover – queue contents
# ---------------------------------------------------------------------------

class TestAutoRecoveryQueueContents(unittest.TestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.state_file = Path(self.tmp.name) / "state.json"

    def tearDown(self):
        self.tmp.cleanup()

    def _recover(self, ds):
        ar = AutoRecovery(
            ds,
            create_driver_fn=MagicMock(return_value=MagicMock()),
            wait_login_fn=MagicMock(),
            open_invoice_fn=MagicMock(),
        )
        with patch("ibis.auto_recovery.CrashRecoveryHandler") as MockCRH:
            MockCRH.return_value.handle.return_value = _fake_report()
            return ar.recover(_make_browser_exc())

    def test_completed_items_excluded_from_resume_queue(self):
        ds = DownloadState(state_file=self.state_file)
        item1 = _make_item("A001")
        item2 = _make_item("A002")
        ds.initialize([item1, item2])
        ds.mark_completed(item1)

        result = self._recover(ds)
        ids = {i.invoice_id for i in result.queue}
        self.assertNotIn("A001", ids)
        self.assertIn("A002", ids)

    def test_failed_items_included_in_resume_queue(self):
        ds = DownloadState(state_file=self.state_file)
        item1 = _make_item("B001")
        item2 = _make_item("B002")
        ds.initialize([item1, item2])
        ds.mark_completed(item1)
        ds.mark_failed(item2)

        result = self._recover(ds)
        ids = {i.invoice_id for i in result.queue}
        self.assertIn("B002", ids)

    def test_all_pending_items_present_in_queue(self):
        ds = DownloadState(state_file=self.state_file)
        items = [_make_item(f"C{i:03d}") for i in range(5)]
        ds.initialize(items)
        ds.mark_completed(items[0])
        ds.mark_completed(items[1])

        result = self._recover(ds)
        self.assertEqual(len(result.queue), 3)

    def test_resumed_items_have_pending_status(self):
        ds = DownloadState(state_file=self.state_file)
        item = _make_item("D001")
        ds.initialize([item])

        result = self._recover(ds)
        for qi in result.queue:
            self.assertEqual(qi.download_status, STATUS_PENDING)

    def test_billing_period_preserved_in_resume_queue(self):
        ds = DownloadState(state_file=self.state_file, billing_period="202612")
        item = _make_item("E001", billing_period="202612")
        ds.initialize([item])

        result = self._recover(ds)
        self.assertEqual(list(result.queue)[0].billing_period, "202612")

    def test_download_url_preserved_in_resume_queue(self):
        url = "https://example.com/dl?InvoiceID=F001&BillingPeriod=202605"
        ds = DownloadState(state_file=self.state_file)
        item = DownloadQueueItem(
            download_url=url, invoice_id="F001",
            billing_period="202605", filename=None,
        )
        ds.initialize([item])

        result = self._recover(ds)
        self.assertEqual(list(result.queue)[0].download_url, url)


# ---------------------------------------------------------------------------
# AutoRecovery.recover – retry logic
# ---------------------------------------------------------------------------

class TestAutoRecoveryRetries(unittest.TestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.state_file = Path(self.tmp.name) / "state.json"
        self.ds = DownloadState(state_file=self.state_file)
        item = _make_item("R001")
        self.ds.initialize([item])

    def tearDown(self):
        self.tmp.cleanup()

    def test_succeeds_on_second_attempt(self):
        """If the first attempt fails, the second attempt should succeed."""
        good_driver = MagicMock()
        call_count = {"n": 0}

        def flaky_create():
            call_count["n"] += 1
            if call_count["n"] == 1:
                raise OSError("driver creation failed")
            return good_driver

        ar = AutoRecovery(
            self.ds,
            create_driver_fn=flaky_create,
            wait_login_fn=MagicMock(),
            open_invoice_fn=MagicMock(),
            max_retries=3,
        )
        with patch("ibis.auto_recovery.CrashRecoveryHandler") as MockCRH:
            MockCRH.return_value.handle.return_value = _fake_report()
            result = ar.recover(_make_browser_exc())

        self.assertIs(result.driver, good_driver)
        self.assertEqual(call_count["n"], 2)

    def test_raises_runtime_error_after_max_retries(self):
        always_fail = MagicMock(side_effect=OSError("always fails"))

        ar = AutoRecovery(
            self.ds,
            create_driver_fn=always_fail,
            wait_login_fn=MagicMock(),
            open_invoice_fn=MagicMock(),
            max_retries=3,
        )
        with patch("ibis.auto_recovery.CrashRecoveryHandler") as MockCRH:
            MockCRH.return_value.handle.return_value = _fake_report()
            with self.assertRaises(RuntimeError) as ctx:
                ar.recover(_make_browser_exc())

        self.assertIn("3 attempt(s)", str(ctx.exception))

    def test_runtime_error_chained_from_last_error(self):
        always_fail = MagicMock(side_effect=OSError("root cause"))

        ar = AutoRecovery(
            self.ds,
            create_driver_fn=always_fail,
            wait_login_fn=MagicMock(),
            open_invoice_fn=MagicMock(),
            max_retries=2,
        )
        with patch("ibis.auto_recovery.CrashRecoveryHandler") as MockCRH:
            MockCRH.return_value.handle.return_value = _fake_report()
            with self.assertRaises(RuntimeError) as ctx:
                ar.recover(_make_browser_exc())

        self.assertIsInstance(ctx.exception.__cause__, OSError)

    def test_create_driver_called_max_retries_times_on_failure(self):
        always_fail = MagicMock(side_effect=OSError("fail"))

        ar = AutoRecovery(
            self.ds,
            create_driver_fn=always_fail,
            wait_login_fn=MagicMock(),
            open_invoice_fn=MagicMock(),
            max_retries=4,
        )
        with patch("ibis.auto_recovery.CrashRecoveryHandler") as MockCRH:
            MockCRH.return_value.handle.return_value = _fake_report()
            with self.assertRaises(RuntimeError):
                ar.recover(_make_browser_exc())

        self.assertEqual(always_fail.call_count, 4)

    def test_max_retries_one_fails_immediately(self):
        ar = AutoRecovery(
            self.ds,
            create_driver_fn=MagicMock(side_effect=OSError("fail")),
            wait_login_fn=MagicMock(),
            open_invoice_fn=MagicMock(),
            max_retries=1,
        )
        with patch("ibis.auto_recovery.CrashRecoveryHandler") as MockCRH:
            MockCRH.return_value.handle.return_value = _fake_report()
            with self.assertRaises(RuntimeError) as ctx:
                ar.recover(_make_browser_exc())
        self.assertIn("1 attempt(s)", str(ctx.exception))

    def test_login_failure_triggers_retry(self):
        """A failure in wait_login_fn should also cause a retry."""
        good_driver = MagicMock()
        login_call_count = {"n": 0}

        def flaky_login(driver):
            login_call_count["n"] += 1
            if login_call_count["n"] == 1:
                raise TimeoutError("login timed out")

        ar = AutoRecovery(
            self.ds,
            create_driver_fn=MagicMock(return_value=good_driver),
            wait_login_fn=flaky_login,
            open_invoice_fn=MagicMock(),
            max_retries=3,
        )
        with patch("ibis.auto_recovery.CrashRecoveryHandler") as MockCRH:
            MockCRH.return_value.handle.return_value = _fake_report()
            result = ar.recover(_make_browser_exc())

        self.assertEqual(login_call_count["n"], 2)
        self.assertIsInstance(result, RecoveryResult)

    def test_open_invoice_failure_triggers_retry(self):
        """A failure in open_invoice_fn should also cause a retry."""
        invoice_call_count = {"n": 0}

        def flaky_invoice(driver):
            invoice_call_count["n"] += 1
            if invoice_call_count["n"] == 1:
                raise ConnectionError("page load failed")

        ar = AutoRecovery(
            self.ds,
            create_driver_fn=MagicMock(return_value=MagicMock()),
            wait_login_fn=MagicMock(),
            open_invoice_fn=flaky_invoice,
            max_retries=3,
        )
        with patch("ibis.auto_recovery.CrashRecoveryHandler") as MockCRH:
            MockCRH.return_value.handle.return_value = _fake_report()
            result = ar.recover(_make_browser_exc())

        self.assertEqual(invoice_call_count["n"], 2)
        self.assertIsInstance(result, RecoveryResult)

    def test_crash_handler_called_exactly_once_regardless_of_retries(self):
        """CrashRecoveryHandler.handle() must be called once, before any retries."""
        always_fail = MagicMock(side_effect=OSError("fail"))

        ar = AutoRecovery(
            self.ds,
            create_driver_fn=always_fail,
            wait_login_fn=MagicMock(),
            open_invoice_fn=MagicMock(),
            max_retries=3,
        )
        with patch("ibis.auto_recovery.CrashRecoveryHandler") as MockCRH:
            mock_instance = MockCRH.return_value
            mock_instance.handle.return_value = _fake_report()
            with self.assertRaises(RuntimeError):
                ar.recover(_make_browser_exc())

        mock_instance.handle.assert_called_once()


# ---------------------------------------------------------------------------
# Integration: real DownloadState + AutoRecovery
# ---------------------------------------------------------------------------

class TestAutoRecoveryIntegration(unittest.TestCase):
    """End-to-end tests using a real DownloadState and real DownloadQueueItems."""

    def setUp(self):
        self.tmp = TemporaryDirectory()

    def tearDown(self):
        self.tmp.cleanup()

    def _make_state(self, items, completed_items=None):
        state_file = Path(self.tmp.name) / "state.json"
        ds = DownloadState(state_file=state_file, billing_period="202605")
        ds.initialize(items)
        for c in (completed_items or []):
            ds.mark_completed(c)
        return ds

    def _recover(self, ds, report_file=None):
        ar = AutoRecovery(
            ds,
            create_driver_fn=MagicMock(return_value=MagicMock()),
            wait_login_fn=MagicMock(),
            open_invoice_fn=MagicMock(),
            report_file=report_file,
        )
        report_path = Path(self.tmp.name) / "report.json"
        with patch("ibis.auto_recovery.CrashRecoveryHandler") as MockCRH:
            MockCRH.return_value.handle.return_value = _fake_report()
            return ar.recover(_make_browser_exc())

    def test_full_interrupted_session_restored(self):
        items = [_make_item(f"I{i:03d}") for i in range(5)]
        ds = self._make_state(items, completed_items=items[:2])
        result = self._recover(ds)
        self.assertEqual(len(result.queue), 3)
        resumed_ids = {i.invoice_id for i in result.queue}
        self.assertEqual(resumed_ids, {"I002", "I003", "I004"})

    def test_all_items_pending_returns_full_queue(self):
        items = [_make_item(f"J{i:03d}") for i in range(3)]
        ds = self._make_state(items)
        result = self._recover(ds)
        self.assertEqual(len(result.queue), 3)

    def test_recovery_result_download_state_is_same_instance(self):
        items = [_make_item("K001")]
        ds = self._make_state(items)
        result = self._recover(ds)
        self.assertIs(result.download_state, ds)

    def test_recovery_result_driver_is_new_instance(self):
        items = [_make_item("L001")]
        ds = self._make_state(items)
        original_driver = MagicMock()
        new_driver = MagicMock()

        ar = AutoRecovery(
            ds,
            create_driver_fn=MagicMock(return_value=new_driver),
            wait_login_fn=MagicMock(),
            open_invoice_fn=MagicMock(),
        )
        with patch("ibis.auto_recovery.CrashRecoveryHandler") as MockCRH:
            MockCRH.return_value.handle.return_value = _fake_report()
            result = ar.recover(_make_browser_exc())

        self.assertIs(result.driver, new_driver)
        self.assertIsNot(result.driver, original_driver)

    def test_large_queue_partial_completion(self):
        items = [_make_item(f"M{i:03d}") for i in range(50)]
        ds = self._make_state(items, completed_items=items[:40])
        result = self._recover(ds)
        self.assertEqual(len(result.queue), 10)

    def test_recover_does_not_modify_downloader_engine(self):
        """AutoRecovery must not import or reference DownloaderEngine."""
        import ibis.auto_recovery as mod
        import inspect
        source = inspect.getsource(mod)
        self.assertNotIn("DownloaderEngine", source)

    def test_recover_is_outside_downloader_engine(self):
        """AutoRecovery class must not be defined inside DownloaderEngine."""
        from ibis.downloader_engine import DownloaderEngine
        self.assertFalse(hasattr(DownloaderEngine, "recover"))
        self.assertFalse(hasattr(DownloaderEngine, "auto_recover"))


if __name__ == "__main__":
    unittest.main()
