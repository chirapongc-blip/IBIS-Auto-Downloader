"""Comprehensive unit tests for ibis/auto_recovery.py.

Covers:
- AutoRecovery initialisation (default & custom dependencies)
- run_with_recovery: successful run, browser failure → recovery, non-browser
  exception propagation, recovery exhaustion
- recover_from_state: no interrupted session, interrupted session, recovery
  after failure, exhaustion
- _safe_quit: silent suppression of quit errors
- Integration with real DownloadState, DownloadPlan, and a stub engine
"""

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import MagicMock, call, patch

from selenium.common.exceptions import WebDriverException

from ibis.auto_recovery import AutoRecovery, _safe_quit
from ibis.downloader import DownloadQueue, DownloadQueueItem, STATUS_PENDING
from ibis.scheduler import DownloadPlan
from ibis.state import DownloadState


# ---------------------------------------------------------------------------
# Shared test helpers
# ---------------------------------------------------------------------------

def _make_queue_item(invoice_id, billing_period="202605"):
    return DownloadQueueItem(
        download_url=(
            f"https://example.com/dl"
            f"?InvoiceID={invoice_id}&BillingPeriod={billing_period}"
        ),
        invoice_id=invoice_id,
        billing_period=billing_period,
        filename=None,
    )


def _make_queue(*invoice_ids, billing_period="202605"):
    q = DownloadQueue()
    for iid in invoice_ids:
        q.add_link({
            "url": (
                f"https://example.com/dl"
                f"?InvoiceID={iid}&BillingPeriod={billing_period}"
            ),
            "invoice_id": iid,
            "billing_period": billing_period,
            "filename": None,
        })
    return q


def _make_plan(*invoice_ids, billing_period="202605"):
    return DownloadPlan(_make_queue(*invoice_ids, billing_period=billing_period), latest_only=False)


class _BrowserCrashEngine:
    """Stub engine that raises WebDriverException on run()."""

    def run(self, plan):
        raise WebDriverException("simulated browser crash")


class _SuccessEngine:
    """Stub engine whose run() succeeds silently."""

    def __init__(self):
        self.run_count = 0
        self.last_plan = None

    def run(self, plan):
        self.run_count += 1
        self.last_plan = plan


class _NonBrowserErrorEngine:
    """Stub engine that raises a non-browser error."""

    def run(self, plan):
        raise RuntimeError("not a browser failure")


class _CrashThenSucceedEngine:
    """Stub engine that crashes on first call, succeeds on subsequent calls."""

    def __init__(self):
        self.call_count = 0

    def run(self, plan):
        self.call_count += 1
        if self.call_count == 1:
            raise WebDriverException("first crash")


# ---------------------------------------------------------------------------
# AutoRecovery.__init__
# ---------------------------------------------------------------------------

class TestAutoRecoveryInit(unittest.TestCase):

    def test_download_state_stored(self):
        ds = MagicMock()
        ar = AutoRecovery(ds)
        self.assertIs(ar.download_state, ds)

    def test_default_max_attempts(self):
        ar = AutoRecovery(MagicMock())
        self.assertEqual(ar.max_attempts, 3)

    def test_custom_max_attempts(self):
        ar = AutoRecovery(MagicMock(), max_attempts=5)
        self.assertEqual(ar.max_attempts, 5)

    def test_report_file_stored(self):
        ar = AutoRecovery(MagicMock(), report_file="/tmp/r.json")
        self.assertEqual(ar.report_file, "/tmp/r.json")

    def test_default_report_file_is_none(self):
        ar = AutoRecovery(MagicMock())
        self.assertIsNone(ar.report_file)

    def test_custom_driver_factory(self):
        factory = MagicMock()
        ar = AutoRecovery(MagicMock(), driver_factory=factory)
        self.assertIs(ar.driver_factory, factory)

    def test_default_driver_factory_is_not_none(self):
        ar = AutoRecovery(MagicMock())
        self.assertIsNotNone(ar.driver_factory)

    def test_custom_login_fn(self):
        fn = MagicMock()
        ar = AutoRecovery(MagicMock(), login_fn=fn)
        self.assertIs(ar.login_fn, fn)

    def test_custom_open_invoice_fn(self):
        fn = MagicMock()
        ar = AutoRecovery(MagicMock(), open_invoice_fn=fn)
        self.assertIs(ar.open_invoice_fn, fn)

    def test_custom_engine_factory(self):
        factory = MagicMock()
        ar = AutoRecovery(MagicMock(), engine_factory=factory)
        self.assertIs(ar.engine_factory, factory)


# ---------------------------------------------------------------------------
# _safe_quit
# ---------------------------------------------------------------------------

class TestSafeQuit(unittest.TestCase):

    def test_quit_called_on_normal_driver(self):
        driver = MagicMock()
        _safe_quit(driver)
        driver.quit.assert_called_once()

    def test_exception_during_quit_is_suppressed(self):
        driver = MagicMock()
        driver.quit.side_effect = Exception("already closed")
        # Must not raise
        _safe_quit(driver)

    def test_webdriver_exception_during_quit_suppressed(self):
        driver = MagicMock()
        driver.quit.side_effect = WebDriverException("session gone")
        _safe_quit(driver)  # no exception


# ---------------------------------------------------------------------------
# run_with_recovery – successful run
# ---------------------------------------------------------------------------

class TestRunWithRecoverySuccess(unittest.TestCase):

    def _make_ar(self, tmp, engine):
        driver = MagicMock()
        ds = MagicMock()
        ar = AutoRecovery(
            ds,
            driver_factory=lambda: driver,
            login_fn=lambda d: None,
            open_invoice_fn=lambda d: None,
            engine_factory=lambda d, s: engine,
            report_file=Path(tmp) / "r.json",
        )
        return ar, driver

    def test_returns_true_on_success(self):
        with TemporaryDirectory() as tmp:
            success_engine = _SuccessEngine()
            ar, driver = self._make_ar(tmp, success_engine)
            plan = _make_plan("1001")
            result = ar.run_with_recovery(plan)
        self.assertTrue(result)

    def test_engine_run_called_once(self):
        with TemporaryDirectory() as tmp:
            success_engine = _SuccessEngine()
            ar, driver = self._make_ar(tmp, success_engine)
            plan = _make_plan("1001")
            ar.run_with_recovery(plan)
        self.assertEqual(success_engine.run_count, 1)

    def test_driver_quit_called_on_success(self):
        with TemporaryDirectory() as tmp:
            success_engine = _SuccessEngine()
            ar, driver = self._make_ar(tmp, success_engine)
            plan = _make_plan("1001")
            ar.run_with_recovery(plan)
        driver.quit.assert_called_once()


# ---------------------------------------------------------------------------
# run_with_recovery – non-browser exception propagates
# ---------------------------------------------------------------------------

class TestRunWithRecoveryNonBrowserException(unittest.TestCase):

    def test_non_browser_exception_propagates(self):
        driver = MagicMock()
        ds = MagicMock()
        with TemporaryDirectory() as tmp:
            ar = AutoRecovery(
                ds,
                driver_factory=lambda: driver,
                login_fn=lambda d: None,
                open_invoice_fn=lambda d: None,
                engine_factory=lambda d, s: _NonBrowserErrorEngine(),
                report_file=Path(tmp) / "r.json",
            )
            plan = _make_plan("2001")
            with self.assertRaises(RuntimeError):
                ar.run_with_recovery(plan)

    def test_driver_quit_called_even_on_propagated_exception(self):
        driver = MagicMock()
        ds = MagicMock()
        with TemporaryDirectory() as tmp:
            ar = AutoRecovery(
                ds,
                driver_factory=lambda: driver,
                login_fn=lambda d: None,
                open_invoice_fn=lambda d: None,
                engine_factory=lambda d, s: _NonBrowserErrorEngine(),
                report_file=Path(tmp) / "r.json",
            )
            plan = _make_plan("2001")
            try:
                ar.run_with_recovery(plan)
            except RuntimeError:
                pass
        driver.quit.assert_called_once()


# ---------------------------------------------------------------------------
# run_with_recovery – browser failure triggers recovery
# ---------------------------------------------------------------------------

class TestRunWithRecoveryBrowserFailure(unittest.TestCase):

    def _build_ar(self, tmp, ds, engine_seq, login_fn=None, invoice_fn=None):
        """Build an AutoRecovery where engine_factory cycles through *engine_seq*."""
        engines = iter(engine_seq)
        drivers = []

        def driver_factory():
            d = MagicMock()
            drivers.append(d)
            return d

        ar = AutoRecovery(
            ds,
            driver_factory=driver_factory,
            login_fn=login_fn or (lambda d: None),
            open_invoice_fn=invoice_fn or (lambda d: None),
            engine_factory=lambda d, s: next(engines),
            report_file=Path(tmp) / "r.json",
            max_attempts=3,
        )
        return ar, drivers

    def test_returns_true_after_recovery(self):
        with TemporaryDirectory() as tmp:
            ds = DownloadState(state_file=Path(tmp) / "state.json")
            item = _make_queue_item("3001")
            ds.initialize([item])

            crash_engine = _BrowserCrashEngine()
            success_engine = _SuccessEngine()
            ar, drivers = self._build_ar(tmp, ds, [crash_engine, success_engine])
            plan = _make_plan("3001")
            result = ar.run_with_recovery(plan)
        self.assertTrue(result)

    def test_two_drivers_created_on_single_recovery(self):
        with TemporaryDirectory() as tmp:
            ds = DownloadState(state_file=Path(tmp) / "state.json")
            ds.initialize([_make_queue_item("4001")])

            ar, drivers = self._build_ar(
                tmp, ds,
                [_BrowserCrashEngine(), _SuccessEngine()],
            )
            ar.run_with_recovery(_make_plan("4001"))
        # First driver for initial run, second for recovery
        self.assertEqual(len(drivers), 2)

    def test_all_drivers_quit_after_recovery(self):
        with TemporaryDirectory() as tmp:
            ds = DownloadState(state_file=Path(tmp) / "state.json")
            ds.initialize([_make_queue_item("5001")])

            ar, drivers = self._build_ar(
                tmp, ds,
                [_BrowserCrashEngine(), _SuccessEngine()],
            )
            ar.run_with_recovery(_make_plan("5001"))
        for d in drivers:
            d.quit.assert_called()

    def test_login_fn_called_on_recovery_driver(self):
        with TemporaryDirectory() as tmp:
            ds = DownloadState(state_file=Path(tmp) / "state.json")
            ds.initialize([_make_queue_item("6001")])

            login_calls = []
            ar, drivers = self._build_ar(
                tmp, ds,
                [_BrowserCrashEngine(), _SuccessEngine()],
                login_fn=lambda d: login_calls.append(d),
            )
            ar.run_with_recovery(_make_plan("6001"))
        # login_fn should be called on the recovery driver (drivers[1])
        self.assertEqual(len(login_calls), 1)
        self.assertIs(login_calls[0], drivers[1])

    def test_invoice_fn_called_on_recovery_driver(self):
        with TemporaryDirectory() as tmp:
            ds = DownloadState(state_file=Path(tmp) / "state.json")
            ds.initialize([_make_queue_item("7001")])

            invoice_calls = []
            ar, drivers = self._build_ar(
                tmp, ds,
                [_BrowserCrashEngine(), _SuccessEngine()],
                invoice_fn=lambda d: invoice_calls.append(d),
            )
            ar.run_with_recovery(_make_plan("7001"))
        self.assertEqual(len(invoice_calls), 1)

    def test_crash_handler_saves_state_on_failure(self):
        with TemporaryDirectory() as tmp:
            state_file = Path(tmp) / "state.json"
            ds = DownloadState(state_file=state_file)
            ds.initialize([_make_queue_item("8001")])

            ar, _ = self._build_ar(
                tmp, ds,
                [_BrowserCrashEngine(), _SuccessEngine()],
            )
            ar.run_with_recovery(_make_plan("8001"))
            # State file must exist after crash (checked inside tmp scope)
            self.assertTrue(state_file.exists())

    def test_report_file_written_on_failure(self):
        with TemporaryDirectory() as tmp:
            report_file = Path(tmp) / "r.json"
            ds = DownloadState(state_file=Path(tmp) / "state.json")
            ds.initialize([_make_queue_item("9001")])

            ar, _ = self._build_ar(
                tmp, ds,
                [_BrowserCrashEngine(), _SuccessEngine()],
            )
            ar.run_with_recovery(_make_plan("9001"))
            # Report file must exist after crash (checked inside tmp scope)
            self.assertTrue(report_file.exists())


# ---------------------------------------------------------------------------
# run_with_recovery – exhaustion of max_attempts
# ---------------------------------------------------------------------------

class TestRunWithRecoveryExhaustion(unittest.TestCase):

    def test_returns_false_when_attempts_exhausted(self):
        with TemporaryDirectory() as tmp:
            ds = DownloadState(state_file=Path(tmp) / "state.json")
            ds.initialize([_make_queue_item("A001")])

            # All engines crash
            engines = [_BrowserCrashEngine() for _ in range(10)]
            engine_iter = iter(engines)

            ar = AutoRecovery(
                ds,
                driver_factory=lambda: MagicMock(),
                login_fn=lambda d: None,
                open_invoice_fn=lambda d: None,
                engine_factory=lambda d, s: next(engine_iter),
                report_file=Path(tmp) / "r.json",
                max_attempts=3,
            )
            plan = _make_plan("A001")
            result = ar.run_with_recovery(plan)
        self.assertFalse(result)

    def test_max_attempts_one_returns_false_immediately(self):
        with TemporaryDirectory() as tmp:
            ds = DownloadState(state_file=Path(tmp) / "state.json")
            ds.initialize([_make_queue_item("B001")])

            engine_iter = iter([_BrowserCrashEngine()] * 5)
            ar = AutoRecovery(
                ds,
                driver_factory=lambda: MagicMock(),
                login_fn=lambda d: None,
                open_invoice_fn=lambda d: None,
                engine_factory=lambda d, s: next(engine_iter),
                report_file=Path(tmp) / "r.json",
                max_attempts=1,
            )
            result = ar.run_with_recovery(_make_plan("B001"))
        self.assertFalse(result)


# ---------------------------------------------------------------------------
# recover_from_state – no interrupted session
# ---------------------------------------------------------------------------

class TestRecoverFromStateNoSession(unittest.TestCase):

    def test_returns_true_when_no_interrupted_session(self):
        with TemporaryDirectory() as tmp:
            ds = DownloadState(state_file=Path(tmp) / "state.json")
            # Fresh state file – no interrupted session
            ar = AutoRecovery(
                ds,
                driver_factory=lambda: MagicMock(),
                login_fn=lambda d: None,
                open_invoice_fn=lambda d: None,
                engine_factory=lambda d, s: _SuccessEngine(),
            )
            result = ar.recover_from_state()
        self.assertTrue(result)

    def test_no_engine_created_when_no_interrupted_session(self):
        with TemporaryDirectory() as tmp:
            ds = DownloadState(state_file=Path(tmp) / "state.json")
            engine_calls = []

            def engine_factory(d, s):
                engine_calls.append(1)
                return _SuccessEngine()

            ar = AutoRecovery(
                ds,
                driver_factory=lambda: MagicMock(),
                login_fn=lambda d: None,
                open_invoice_fn=lambda d: None,
                engine_factory=engine_factory,
            )
            ar.recover_from_state()
        self.assertEqual(len(engine_calls), 0)

    def test_completed_session_not_interrupted(self):
        with TemporaryDirectory() as tmp:
            ds = DownloadState(state_file=Path(tmp) / "state.json")
            item = _make_queue_item("C001")
            ds.initialize([item])
            ds.mark_completed(item)

            ar = AutoRecovery(
                ds,
                driver_factory=lambda: MagicMock(),
                login_fn=lambda d: None,
                open_invoice_fn=lambda d: None,
                engine_factory=lambda d, s: _SuccessEngine(),
            )
            result = ar.recover_from_state()
        self.assertTrue(result)


# ---------------------------------------------------------------------------
# recover_from_state – interrupted session
# ---------------------------------------------------------------------------

class TestRecoverFromStateInterrupted(unittest.TestCase):

    def test_returns_true_after_successful_resume(self):
        with TemporaryDirectory() as tmp:
            ds = DownloadState(state_file=Path(tmp) / "state.json")
            item1 = _make_queue_item("D001")
            item2 = _make_queue_item("D002")
            ds.initialize([item1, item2])
            ds.mark_completed(item1)
            # item2 never finished – interrupted

            engine = _SuccessEngine()
            ar = AutoRecovery(
                ds,
                driver_factory=lambda: MagicMock(),
                login_fn=lambda d: None,
                open_invoice_fn=lambda d: None,
                engine_factory=lambda d, s: engine,
            )
            result = ar.recover_from_state()
        self.assertTrue(result)

    def test_engine_run_called_with_resume_plan(self):
        with TemporaryDirectory() as tmp:
            ds = DownloadState(state_file=Path(tmp) / "state.json")
            item1 = _make_queue_item("E001")
            item2 = _make_queue_item("E002")
            ds.initialize([item1, item2])
            ds.mark_completed(item1)

            engine = _SuccessEngine()
            ar = AutoRecovery(
                ds,
                driver_factory=lambda: MagicMock(),
                login_fn=lambda d: None,
                open_invoice_fn=lambda d: None,
                engine_factory=lambda d, s: engine,
            )
            ar.recover_from_state()
        # Engine should have been called once with the resume plan
        self.assertEqual(engine.run_count, 1)
        # Plan should only contain item2 (item1 already completed)
        self.assertEqual(engine.last_plan.scheduled_count, 1)

    def test_login_fn_called_before_engine(self):
        with TemporaryDirectory() as tmp:
            ds = DownloadState(state_file=Path(tmp) / "state.json")
            item = _make_queue_item("F001")
            ds.initialize([item])

            call_order = []
            engine = _SuccessEngine()

            def login_fn(d):
                call_order.append("login")

            class _TrackingEngine:
                def run(self, plan):
                    call_order.append("engine")

            ar = AutoRecovery(
                ds,
                driver_factory=lambda: MagicMock(),
                login_fn=login_fn,
                open_invoice_fn=lambda d: None,
                engine_factory=lambda d, s: _TrackingEngine(),
            )
            ar.recover_from_state()

        self.assertEqual(call_order, ["login", "engine"])

    def test_open_invoice_fn_called_before_engine(self):
        with TemporaryDirectory() as tmp:
            ds = DownloadState(state_file=Path(tmp) / "state.json")
            item = _make_queue_item("G001")
            ds.initialize([item])

            call_order = []

            class _TrackingEngine:
                def run(self, plan):
                    call_order.append("engine")

            ar = AutoRecovery(
                ds,
                driver_factory=lambda: MagicMock(),
                login_fn=lambda d: None,
                open_invoice_fn=lambda d: call_order.append("invoice"),
                engine_factory=lambda d, s: _TrackingEngine(),
            )
            ar.recover_from_state()

        self.assertIn("invoice", call_order)
        self.assertLess(call_order.index("invoice"), call_order.index("engine"))


# ---------------------------------------------------------------------------
# recover_from_state – browser failure during recovery
# ---------------------------------------------------------------------------

class TestRecoverFromStateBrowserFailureDuringResume(unittest.TestCase):

    def test_returns_true_after_second_attempt_succeeds(self):
        with TemporaryDirectory() as tmp:
            ds = DownloadState(state_file=Path(tmp) / "state.json")
            item = _make_queue_item("H001")
            ds.initialize([item])

            call_count = [0]

            class _CrashOnce:
                def run(self, plan):
                    call_count[0] += 1
                    if call_count[0] == 1:
                        raise WebDriverException("crash on first resume")

            ar = AutoRecovery(
                ds,
                driver_factory=lambda: MagicMock(),
                login_fn=lambda d: None,
                open_invoice_fn=lambda d: None,
                engine_factory=lambda d, s: _CrashOnce(),
                report_file=Path(tmp) / "r.json",
                max_attempts=3,
            )
            result = ar.recover_from_state()
        self.assertTrue(result)

    def test_returns_false_when_all_resume_attempts_fail(self):
        with TemporaryDirectory() as tmp:
            ds = DownloadState(state_file=Path(tmp) / "state.json")
            item = _make_queue_item("I001")
            ds.initialize([item])

            ar = AutoRecovery(
                ds,
                driver_factory=lambda: MagicMock(),
                login_fn=lambda d: None,
                open_invoice_fn=lambda d: None,
                engine_factory=lambda d, s: _BrowserCrashEngine(),
                report_file=Path(tmp) / "r.json",
                max_attempts=2,
            )
            result = ar.recover_from_state()
        self.assertFalse(result)


# ---------------------------------------------------------------------------
# Integration: DownloadState + DownloaderEngine stub
# ---------------------------------------------------------------------------

class TestAutoRecoveryIntegration(unittest.TestCase):
    """End-to-end tests using real DownloadState objects."""

    def _make_ar(self, tmp, ds, engine_factory, max_attempts=3):
        return AutoRecovery(
            ds,
            driver_factory=lambda: MagicMock(),
            login_fn=lambda d: None,
            open_invoice_fn=lambda d: None,
            engine_factory=engine_factory,
            report_file=Path(tmp) / "r.json",
            max_attempts=max_attempts,
        )

    def test_full_successful_session(self):
        with TemporaryDirectory() as tmp:
            ds = DownloadState(state_file=Path(tmp) / "state.json")
            items = [_make_queue_item(f"J{i:03d}") for i in range(5)]
            ds.initialize(items)

            engine = _SuccessEngine()
            ar = self._make_ar(tmp, ds, lambda d, s: engine)
            plan = _make_plan(*[f"J{i:03d}" for i in range(5)])
            result = ar.run_with_recovery(plan)

        self.assertTrue(result)
        self.assertEqual(engine.run_count, 1)

    def test_crash_then_resume_state_reflects_progress(self):
        """After a crash, recover_from_state should only retry remaining items."""
        with TemporaryDirectory() as tmp:
            ds = DownloadState(state_file=Path(tmp) / "state.json")
            items = [_make_queue_item(f"K{i:03d}") for i in range(4)]
            ds.initialize(items)
            # Simulate: 2 items completed, then crash
            ds.mark_completed(items[0])
            ds.mark_completed(items[1])

            engine = _SuccessEngine()
            ar = self._make_ar(tmp, ds, lambda d, s: engine)
            result = ar.recover_from_state()

        self.assertTrue(result)
        # Should only have recovered items 2 and 3
        self.assertEqual(engine.last_plan.scheduled_count, 2)

    def test_empty_queue_after_all_completed(self):
        with TemporaryDirectory() as tmp:
            ds = DownloadState(state_file=Path(tmp) / "state.json")
            items = [_make_queue_item(f"L{i:03d}") for i in range(3)]
            ds.initialize(items)
            for item in items:
                ds.mark_completed(item)

            engine_calls = []

            def engine_factory(d, s):
                engine_calls.append(1)
                return _SuccessEngine()

            ar = self._make_ar(tmp, ds, engine_factory)
            result = ar.recover_from_state()

        self.assertTrue(result)
        self.assertEqual(len(engine_calls), 0)

    def test_large_queue_partial_completion_and_recovery(self):
        with TemporaryDirectory() as tmp:
            ds = DownloadState(state_file=Path(tmp) / "state.json")
            items = [_make_queue_item(f"M{i:03d}") for i in range(20)]
            ds.initialize(items)
            # Simulate 15 completed, 5 pending
            for item in items[:15]:
                ds.mark_completed(item)

            engine = _SuccessEngine()
            ar = self._make_ar(tmp, ds, lambda d, s: engine)
            result = ar.recover_from_state()

        self.assertTrue(result)
        self.assertEqual(engine.last_plan.scheduled_count, 5)


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------

class TestAutoRecoveryEdgeCases(unittest.TestCase):

    def test_zero_max_attempts_run_with_recovery_crash_returns_false(self):
        """With max_attempts=0, any crash immediately returns False."""
        with TemporaryDirectory() as tmp:
            ds = DownloadState(state_file=Path(tmp) / "state.json")
            ds.initialize([_make_queue_item("N001")])

            ar = AutoRecovery(
                ds,
                driver_factory=lambda: MagicMock(),
                login_fn=lambda d: None,
                open_invoice_fn=lambda d: None,
                engine_factory=lambda d, s: _BrowserCrashEngine(),
                report_file=Path(tmp) / "r.json",
                max_attempts=0,
            )
            result = ar.run_with_recovery(_make_plan("N001"))
        self.assertFalse(result)

    def test_run_with_empty_plan_returns_true(self):
        with TemporaryDirectory() as tmp:
            ds = MagicMock()
            engine = _SuccessEngine()
            ar = AutoRecovery(
                ds,
                driver_factory=lambda: MagicMock(),
                login_fn=lambda d: None,
                open_invoice_fn=lambda d: None,
                engine_factory=lambda d, s: engine,
            )
            plan = _make_plan()  # empty queue
            result = ar.run_with_recovery(plan)
        self.assertTrue(result)
        self.assertEqual(engine.run_count, 1)

    def test_driver_always_quit_even_on_login_exception(self):
        with TemporaryDirectory() as tmp:
            ds = DownloadState(state_file=Path(tmp) / "state.json")
            ds.initialize([_make_queue_item("O001")])

            driver = MagicMock()

            def login_fn(d):
                raise RuntimeError("login page unavailable")

            ar = AutoRecovery(
                ds,
                driver_factory=lambda: driver,
                login_fn=login_fn,
                open_invoice_fn=lambda d: None,
                engine_factory=lambda d, s: _SuccessEngine(),
                report_file=Path(tmp) / "r.json",
                max_attempts=1,
            )
            # recover_from_state runs login_fn on the recovery driver
            # The RuntimeError propagates since it's not a browser failure
            try:
                ar.recover_from_state()
            except RuntimeError:
                pass
        driver.quit.assert_called()

    def test_multiple_sequential_crashes_exhaust_attempts(self):
        with TemporaryDirectory() as tmp:
            ds = DownloadState(state_file=Path(tmp) / "state.json")
            ds.initialize([_make_queue_item("P001")])

            driver_count = [0]

            def driver_factory():
                driver_count[0] += 1
                return MagicMock()

            ar = AutoRecovery(
                ds,
                driver_factory=driver_factory,
                login_fn=lambda d: None,
                open_invoice_fn=lambda d: None,
                engine_factory=lambda d, s: _BrowserCrashEngine(),
                report_file=Path(tmp) / "r.json",
                max_attempts=2,
            )
            result = ar.run_with_recovery(_make_plan("P001"))

        self.assertFalse(result)
        # Initial driver + 2 recovery attempts = 3 total
        self.assertEqual(driver_count[0], 3)


if __name__ == "__main__":
    unittest.main()
