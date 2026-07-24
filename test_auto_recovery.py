"""Comprehensive unit tests for ibis/auto_recovery.py."""

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import MagicMock, call, patch

from selenium.common.exceptions import WebDriverException
from ibis.auto_recovery import AutoRecovery, MAX_RECOVERY_ATTEMPTS
from ibis.downloader import DownloadQueue, DownloadQueueItem, STATUS_PENDING
from ibis.recovery import CrashRecoveryHandler
from ibis.scheduler import DownloadPlan
from ibis.state import DownloadState


# ---------------------------------------------------------------------------
# Helpers / Stubs
# ---------------------------------------------------------------------------

_FakeWebDriverException = WebDriverException


def _make_item(invoice_id="INV001", billing_period="202605"):
    return DownloadQueueItem(
        download_url=f"https://example.com/dl?InvoiceID={invoice_id}&BillingPeriod={billing_period}",
        invoice_id=invoice_id,
        billing_period=billing_period,
        filename=None,
    )


def _make_queue(*invoice_ids):
    items = [_make_item(iid) for iid in invoice_ids]
    queue = DownloadQueue()
    for item in items:
        queue.add_link(
            {
                "url": item.download_url,
                "invoice_id": item.invoice_id,
                "billing_period": item.billing_period,
                "filename": None,
            }
        )
    return queue


def _make_plan(*invoice_ids):
    return DownloadPlan(_make_queue(*invoice_ids), latest_only=False)


def _make_auto_recovery(
    download_state,
    driver_factory=None,
    login_fn=None,
    open_invoice_fn=None,
    engine_factory=None,
    max_attempts=MAX_RECOVERY_ATTEMPTS,
    recovery_handler=None,
):
    return AutoRecovery(
        driver_factory=driver_factory or MagicMock(return_value=MagicMock()),
        login_fn=login_fn or MagicMock(),
        open_invoice_fn=open_invoice_fn or MagicMock(),
        download_state=download_state,
        engine_factory=engine_factory or MagicMock(return_value=MagicMock()),
        max_attempts=max_attempts,
        recovery_handler=recovery_handler,
    )


# ---------------------------------------------------------------------------
# MAX_RECOVERY_ATTEMPTS constant
# ---------------------------------------------------------------------------

class TestMaxRecoveryAttemptsConstant(unittest.TestCase):
    def test_default_value_is_three(self):
        self.assertEqual(MAX_RECOVERY_ATTEMPTS, 3)


# ---------------------------------------------------------------------------
# AutoRecovery.__init__
# ---------------------------------------------------------------------------

class TestAutoRecoveryInit(unittest.TestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.ds = DownloadState(state_file=Path(self.tmp.name) / "state.json")

    def tearDown(self):
        self.tmp.cleanup()

    def test_attributes_stored(self):
        driver_factory = MagicMock()
        login_fn = MagicMock()
        open_invoice_fn = MagicMock()
        engine_factory = MagicMock()
        ar = AutoRecovery(
            driver_factory=driver_factory,
            login_fn=login_fn,
            open_invoice_fn=open_invoice_fn,
            download_state=self.ds,
            engine_factory=engine_factory,
            max_attempts=5,
        )
        self.assertIs(ar.driver_factory, driver_factory)
        self.assertIs(ar.login_fn, login_fn)
        self.assertIs(ar.open_invoice_fn, open_invoice_fn)
        self.assertIs(ar.download_state, self.ds)
        self.assertIs(ar.engine_factory, engine_factory)
        self.assertEqual(ar.max_attempts, 5)

    def test_default_max_attempts(self):
        ar = _make_auto_recovery(self.ds)
        self.assertEqual(ar.max_attempts, MAX_RECOVERY_ATTEMPTS)

    def test_auto_creates_crash_recovery_handler_when_none(self):
        ar = _make_auto_recovery(self.ds)
        self.assertIsInstance(ar.recovery_handler, CrashRecoveryHandler)
        self.assertIs(ar.recovery_handler.download_state, self.ds)

    def test_uses_supplied_recovery_handler(self):
        rh = MagicMock(spec=CrashRecoveryHandler)
        ar = _make_auto_recovery(self.ds, recovery_handler=rh)
        self.assertIs(ar.recovery_handler, rh)


# ---------------------------------------------------------------------------
# AutoRecovery.run – success path (no failure)
# ---------------------------------------------------------------------------

class TestAutoRecoveryRunSuccess(unittest.TestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.ds = DownloadState(state_file=Path(self.tmp.name) / "state.json")

    def tearDown(self):
        self.tmp.cleanup()

    def test_run_calls_engine_run_with_plan(self):
        driver = MagicMock()
        driver_factory = MagicMock(return_value=driver)
        engine = MagicMock()
        engine_factory = MagicMock(return_value=engine)

        plan = _make_plan("INV001", "INV002")
        ar = _make_auto_recovery(self.ds, driver_factory=driver_factory, engine_factory=engine_factory)
        ar.run(plan)

        engine_factory.assert_called_once_with(driver)
        engine.run.assert_called_once_with(plan)

    def test_run_quits_driver_on_success(self):
        driver = MagicMock()
        driver_factory = MagicMock(return_value=driver)
        engine = MagicMock()
        engine_factory = MagicMock(return_value=engine)

        plan = _make_plan("INV001")
        ar = _make_auto_recovery(self.ds, driver_factory=driver_factory, engine_factory=engine_factory)
        ar.run(plan)

        driver.quit.assert_called_once()

    def test_login_fn_not_called_on_success(self):
        login_fn = MagicMock()
        plan = _make_plan("INV001")
        ar = _make_auto_recovery(self.ds, login_fn=login_fn)
        ar.run(plan)
        login_fn.assert_not_called()

    def test_open_invoice_fn_not_called_on_success(self):
        open_invoice_fn = MagicMock()
        plan = _make_plan("INV001")
        ar = _make_auto_recovery(self.ds, open_invoice_fn=open_invoice_fn)
        ar.run(plan)
        open_invoice_fn.assert_not_called()

    def test_recovery_handler_not_called_on_success(self):
        rh = MagicMock(spec=CrashRecoveryHandler)
        plan = _make_plan("INV001")
        ar = _make_auto_recovery(self.ds, recovery_handler=rh)
        ar.run(plan)
        rh.handle.assert_not_called()


# ---------------------------------------------------------------------------
# AutoRecovery.run – non-browser exception propagates immediately
# ---------------------------------------------------------------------------

class TestAutoRecoveryRunNonBrowserException(unittest.TestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.ds = DownloadState(state_file=Path(self.tmp.name) / "state.json")

    def tearDown(self):
        self.tmp.cleanup()

    def test_non_browser_exception_propagates(self):
        engine = MagicMock()
        engine.run.side_effect = RuntimeError("not a browser error")
        engine_factory = MagicMock(return_value=engine)
        rh = MagicMock(spec=CrashRecoveryHandler)

        ar = _make_auto_recovery(self.ds, engine_factory=engine_factory, recovery_handler=rh)
        with self.assertRaises(RuntimeError):
            ar.run(_make_plan("INV001"))

    def test_recovery_handler_not_called_for_non_browser_exception(self):
        engine = MagicMock()
        engine.run.side_effect = ValueError("business logic error")
        rh = MagicMock(spec=CrashRecoveryHandler)

        ar = _make_auto_recovery(self.ds, engine_factory=MagicMock(return_value=engine), recovery_handler=rh)
        with self.assertRaises(ValueError):
            ar.run(_make_plan("INV001"))

        rh.handle.assert_not_called()

    def test_driver_is_quit_on_non_browser_exception(self):
        driver = MagicMock()
        driver_factory = MagicMock(return_value=driver)
        engine = MagicMock()
        engine.run.side_effect = RuntimeError("boom")

        ar = _make_auto_recovery(
            self.ds,
            driver_factory=driver_factory,
            engine_factory=MagicMock(return_value=engine),
        )
        with self.assertRaises(RuntimeError):
            ar.run(_make_plan("INV001"))

        driver.quit.assert_called_once()


# ---------------------------------------------------------------------------
# AutoRecovery.run – browser failure triggers recovery cycle
# ---------------------------------------------------------------------------

class TestAutoRecoveryRunBrowserFailure(unittest.TestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.ds = DownloadState(state_file=Path(self.tmp.name) / "state.json")
        # Pre-populate a minimal saved state so _rebuild_plan returns a queue.
        item = {
            "invoice_id": "INV001",
            "billing_period": "202605",
            "download_url": "https://example.com/dl?InvoiceID=INV001&BillingPeriod=202605",
            "filename": None,
            "download_status": "pending",
            "retry_count": 0,
            "last_error": None,
            "customer_id": None,
        }
        self.ds.initialize([item])

    def tearDown(self):
        self.tmp.cleanup()

    def _make_drivers_and_engines(self, fail_times=1):
        """Return (driver_factory, engine_factory) where the first *fail_times*
        engine.run calls raise a browser failure, then succeed."""
        drivers = [MagicMock(name=f"driver_{i}") for i in range(fail_times + 1)]
        driver_iter = iter(drivers)
        driver_factory = MagicMock(side_effect=lambda: next(driver_iter))

        engines = []
        for i in range(fail_times + 1):
            e = MagicMock(name=f"engine_{i}")
            if i < fail_times:
                e.run.side_effect = _FakeWebDriverException("browser crash")
            engines.append(e)
        engine_iter = iter(engines)
        engine_factory = MagicMock(side_effect=lambda d: next(engine_iter))

        return driver_factory, engine_factory, drivers, engines

    def test_single_failure_then_success(self):
        driver_factory, engine_factory, drivers, engines = self._make_drivers_and_engines(fail_times=1)
        rh = MagicMock(spec=CrashRecoveryHandler)
        login_fn = MagicMock()
        open_invoice_fn = MagicMock()

        ar = AutoRecovery(
            driver_factory=driver_factory,
            login_fn=login_fn,
            open_invoice_fn=open_invoice_fn,
            download_state=self.ds,
            engine_factory=engine_factory,
            max_attempts=3,
            recovery_handler=rh,
        )
        ar.run(_make_plan("INV001"))

        # CrashRecoveryHandler.handle called exactly once.
        rh.handle.assert_called_once()
        # Login and invoice page called for recovery.
        login_fn.assert_called_once()
        open_invoice_fn.assert_called_once()

    def test_two_failures_then_success(self):
        driver_factory, engine_factory, drivers, engines = self._make_drivers_and_engines(fail_times=2)
        rh = MagicMock(spec=CrashRecoveryHandler)
        login_fn = MagicMock()
        open_invoice_fn = MagicMock()

        ar = AutoRecovery(
            driver_factory=driver_factory,
            login_fn=login_fn,
            open_invoice_fn=open_invoice_fn,
            download_state=self.ds,
            engine_factory=engine_factory,
            max_attempts=3,
            recovery_handler=rh,
        )
        ar.run(_make_plan("INV001"))

        self.assertEqual(rh.handle.call_count, 2)
        self.assertEqual(login_fn.call_count, 2)
        self.assertEqual(open_invoice_fn.call_count, 2)

    def test_recovery_creates_new_driver_each_attempt(self):
        driver_factory, engine_factory, drivers, engines = self._make_drivers_and_engines(fail_times=2)
        rh = MagicMock(spec=CrashRecoveryHandler)

        ar = AutoRecovery(
            driver_factory=driver_factory,
            login_fn=MagicMock(),
            open_invoice_fn=MagicMock(),
            download_state=self.ds,
            engine_factory=engine_factory,
            max_attempts=3,
            recovery_handler=rh,
        )
        ar.run(_make_plan("INV001"))

        # driver_factory called 3 times total: initial + 2 recovery attempts.
        self.assertEqual(driver_factory.call_count, 3)

    def test_old_driver_quit_on_browser_failure(self):
        driver_factory, engine_factory, drivers, engines = self._make_drivers_and_engines(fail_times=1)
        rh = MagicMock(spec=CrashRecoveryHandler)

        ar = AutoRecovery(
            driver_factory=driver_factory,
            login_fn=MagicMock(),
            open_invoice_fn=MagicMock(),
            download_state=self.ds,
            engine_factory=engine_factory,
            max_attempts=3,
            recovery_handler=rh,
        )
        ar.run(_make_plan("INV001"))

        # First driver (failed) should have been quit.
        drivers[0].quit.assert_called()

    def test_recovery_handler_receives_exception(self):
        exc = _FakeWebDriverException("session gone")
        engine = MagicMock()
        engine.run.side_effect = exc
        engine2 = MagicMock()
        engines_iter = iter([engine, engine2])
        engine_factory = MagicMock(side_effect=lambda d: next(engines_iter))

        drivers = [MagicMock(), MagicMock()]
        drivers_iter = iter(drivers)
        driver_factory = MagicMock(side_effect=lambda: next(drivers_iter))
        rh = MagicMock(spec=CrashRecoveryHandler)

        open_invoice_fn = MagicMock()
        ar = AutoRecovery(
            driver_factory=driver_factory,
            login_fn=MagicMock(),
            open_invoice_fn=open_invoice_fn,
            download_state=self.ds,
            engine_factory=engine_factory,
            max_attempts=3,
            recovery_handler=rh,
        )
        ar.run(_make_plan("INV001"))

        rh.handle.assert_called_once_with(exc)


# ---------------------------------------------------------------------------
# AutoRecovery.run – max_attempts exceeded
# ---------------------------------------------------------------------------

class TestAutoRecoveryRunMaxAttemptsExceeded(unittest.TestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.ds = DownloadState(state_file=Path(self.tmp.name) / "state.json")
        item = {
            "invoice_id": "INV001",
            "billing_period": "202605",
            "download_url": "https://example.com/dl?InvoiceID=INV001&BillingPeriod=202605",
            "filename": None,
            "download_status": "pending",
            "retry_count": 0,
            "last_error": None,
            "customer_id": None,
        }
        self.ds.initialize([item])

    def tearDown(self):
        self.tmp.cleanup()

    def test_raises_after_max_attempts(self):
        engine = MagicMock()
        engine.run.side_effect = _FakeWebDriverException("crash")
        engine_factory = MagicMock(return_value=engine)
        driver_factory = MagicMock(return_value=MagicMock())
        rh = MagicMock(spec=CrashRecoveryHandler)

        ar = AutoRecovery(
            driver_factory=driver_factory,
            login_fn=MagicMock(),
            open_invoice_fn=MagicMock(),
            download_state=self.ds,
            engine_factory=engine_factory,
            max_attempts=2,
            recovery_handler=rh,
        )
        with self.assertRaises(_FakeWebDriverException):
            ar.run(_make_plan("INV001"))

    def test_recovery_handler_called_for_each_attempt(self):
        engine = MagicMock()
        engine.run.side_effect = _FakeWebDriverException("crash")
        engine_factory = MagicMock(return_value=engine)
        driver_factory = MagicMock(return_value=MagicMock())
        rh = MagicMock(spec=CrashRecoveryHandler)

        ar = AutoRecovery(
            driver_factory=driver_factory,
            login_fn=MagicMock(),
            open_invoice_fn=MagicMock(),
            download_state=self.ds,
            engine_factory=engine_factory,
            max_attempts=2,
            recovery_handler=rh,
        )
        with self.assertRaises(_FakeWebDriverException):
            ar.run(_make_plan("INV001"))

        # handle called for each of the 2 failures.
        self.assertEqual(rh.handle.call_count, 2)

    def test_max_attempts_one_raises_immediately(self):
        engine = MagicMock()
        engine.run.side_effect = _FakeWebDriverException("crash")
        engine_factory = MagicMock(return_value=engine)
        rh = MagicMock(spec=CrashRecoveryHandler)

        ar = AutoRecovery(
            driver_factory=MagicMock(return_value=MagicMock()),
            login_fn=MagicMock(),
            open_invoice_fn=MagicMock(),
            download_state=self.ds,
            engine_factory=engine_factory,
            max_attempts=1,
            recovery_handler=rh,
        )
        with self.assertRaises(_FakeWebDriverException):
            ar.run(_make_plan("INV001"))

        # Exactly one handle call, no recovery attempted.
        rh.handle.assert_called_once()

    def test_last_driver_quit_after_max_attempts(self):
        last_driver = MagicMock()
        drivers = [MagicMock(), last_driver]
        drivers_iter = iter(drivers)
        driver_factory = MagicMock(side_effect=lambda: next(drivers_iter))

        engine = MagicMock()
        engine.run.side_effect = _FakeWebDriverException("crash")
        rh = MagicMock(spec=CrashRecoveryHandler)

        ar = AutoRecovery(
            driver_factory=driver_factory,
            login_fn=MagicMock(),
            open_invoice_fn=MagicMock(),
            download_state=self.ds,
            engine_factory=MagicMock(return_value=engine),
            max_attempts=2,
            recovery_handler=rh,
        )
        with self.assertRaises(_FakeWebDriverException):
            ar.run(_make_plan("INV001"))

        last_driver.quit.assert_called()


# ---------------------------------------------------------------------------
# AutoRecovery._rebuild_plan
# ---------------------------------------------------------------------------

class TestAutoRecoveryRebuildPlan(unittest.TestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.ds = DownloadState(state_file=Path(self.tmp.name) / "state.json")

    def tearDown(self):
        self.tmp.cleanup()

    def _populate_state(self, invoice_ids, completed_ids=None):
        items = [
            {
                "invoice_id": iid,
                "billing_period": "202605",
                "download_url": f"https://example.com/dl?InvoiceID={iid}&BillingPeriod=202605",
                "filename": None,
                "download_status": "pending",
                "retry_count": 0,
                "last_error": None,
                "customer_id": None,
            }
            for iid in invoice_ids
        ]
        self.ds.initialize(items)
        for item in items:
            if completed_ids and item["invoice_id"] in completed_ids:
                self.ds.mark_completed(item)

    def test_rebuild_plan_returns_all_pending_items(self):
        self._populate_state(["A", "B", "C"])
        ar = _make_auto_recovery(self.ds)
        plan = ar._rebuild_plan()
        self.assertEqual(len(plan.scheduled_items), 3)

    def test_rebuild_plan_excludes_completed_items(self):
        self._populate_state(["A", "B", "C"], completed_ids=["A"])
        ar = _make_auto_recovery(self.ds)
        plan = ar._rebuild_plan()
        ids = [item.invoice_id for item in plan.scheduled_items]
        self.assertNotIn("A", ids)
        self.assertIn("B", ids)
        self.assertIn("C", ids)

    def test_rebuild_plan_with_no_state_returns_empty_plan(self):
        # No state file written → empty plan.
        ar = _make_auto_recovery(self.ds)
        plan = ar._rebuild_plan()
        self.assertEqual(len(plan.scheduled_items), 0)

    def test_rebuild_plan_with_all_completed_returns_empty_plan(self):
        self._populate_state(["A", "B"], completed_ids=["A", "B"])
        ar = _make_auto_recovery(self.ds)
        plan = ar._rebuild_plan()
        self.assertEqual(len(plan.scheduled_items), 0)


# ---------------------------------------------------------------------------
# AutoRecovery._recover
# ---------------------------------------------------------------------------

class TestAutoRecoveryRecover(unittest.TestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.ds = DownloadState(state_file=Path(self.tmp.name) / "state.json")

    def tearDown(self):
        self.tmp.cleanup()

    def test_recover_quits_old_driver(self):
        old_driver = MagicMock()
        new_driver = MagicMock()
        driver_factory = MagicMock(return_value=new_driver)

        ar = _make_auto_recovery(self.ds, driver_factory=driver_factory)
        result = ar._recover(old_driver)

        old_driver.quit.assert_called_once()

    def test_recover_creates_new_driver(self):
        old_driver = MagicMock()
        new_driver = MagicMock()
        driver_factory = MagicMock(return_value=new_driver)

        ar = _make_auto_recovery(self.ds, driver_factory=driver_factory)
        result = ar._recover(old_driver)

        driver_factory.assert_called_once()
        self.assertIs(result, new_driver)

    def test_recover_calls_login_fn_with_new_driver(self):
        old_driver = MagicMock()
        new_driver = MagicMock()
        driver_factory = MagicMock(return_value=new_driver)
        login_fn = MagicMock()

        ar = _make_auto_recovery(self.ds, driver_factory=driver_factory, login_fn=login_fn)
        ar._recover(old_driver)

        login_fn.assert_called_once_with(new_driver)

    def test_recover_calls_open_invoice_fn_with_new_driver(self):
        old_driver = MagicMock()
        new_driver = MagicMock()
        driver_factory = MagicMock(return_value=new_driver)
        open_invoice_fn = MagicMock()

        ar = _make_auto_recovery(self.ds, driver_factory=driver_factory, open_invoice_fn=open_invoice_fn)
        ar._recover(old_driver)

        open_invoice_fn.assert_called_once_with(new_driver)

    def test_recover_calls_login_before_invoice(self):
        """Verify the call order: login → invoice page."""
        call_order = []
        old_driver = MagicMock()
        new_driver = MagicMock()

        login_fn = MagicMock(side_effect=lambda d: call_order.append("login"))
        open_invoice_fn = MagicMock(side_effect=lambda d: call_order.append("invoice"))

        ar = _make_auto_recovery(
            self.ds,
            driver_factory=MagicMock(return_value=new_driver),
            login_fn=login_fn,
            open_invoice_fn=open_invoice_fn,
        )
        ar._recover(old_driver)
        self.assertEqual(call_order, ["login", "invoice"])

    def test_recover_tolerates_old_driver_quit_error(self):
        old_driver = MagicMock()
        old_driver.quit.side_effect = Exception("already gone")
        new_driver = MagicMock()

        ar = _make_auto_recovery(
            self.ds,
            driver_factory=MagicMock(return_value=new_driver),
        )
        # Should not raise even though old_driver.quit() fails.
        result = ar._recover(old_driver)
        self.assertIs(result, new_driver)


# ---------------------------------------------------------------------------
# AutoRecovery._quit_driver
# ---------------------------------------------------------------------------

class TestAutoRecoveryQuitDriver(unittest.TestCase):
    def test_quit_driver_calls_quit(self):
        driver = MagicMock()
        AutoRecovery._quit_driver(driver)
        driver.quit.assert_called_once()

    def test_quit_driver_silently_ignores_exception(self):
        driver = MagicMock()
        driver.quit.side_effect = Exception("already closed")
        # Should not raise.
        AutoRecovery._quit_driver(driver)


# ---------------------------------------------------------------------------
# Integration: full recovery cycle with real DownloadState
# ---------------------------------------------------------------------------

class TestAutoRecoveryIntegration(unittest.TestCase):
    """End-to-end test that exercises AutoRecovery with a real DownloadState."""

    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.ds = DownloadState(state_file=Path(self.tmp.name) / "state.json")

    def tearDown(self):
        self.tmp.cleanup()

    def test_full_recovery_cycle_one_failure(self):
        """Fail once on a 3-item queue, then complete successfully on recovery."""
        items = [
            {
                "invoice_id": f"INV00{i}",
                "billing_period": "202605",
                "download_url": f"https://example.com/dl?InvoiceID=INV00{i}&BillingPeriod=202605",
                "filename": None,
                "download_status": "pending",
                "retry_count": 0,
                "last_error": None,
                "customer_id": None,
            }
            for i in range(1, 4)
        ]
        self.ds.initialize(items)
        self.ds.mark_completed(items[0])  # INV001 already done

        drivers = [MagicMock(name="d0"), MagicMock(name="d1")]
        drivers_iter = iter(drivers)
        driver_factory = MagicMock(side_effect=lambda: next(drivers_iter))

        crash_engine = MagicMock(name="engine0")
        crash_engine.run.side_effect = _FakeWebDriverException("tab crashed")
        ok_engine = MagicMock(name="engine1")

        engines = [crash_engine, ok_engine]
        engines_iter = iter(engines)
        engine_factory = MagicMock(side_effect=lambda d: next(engines_iter))

        login_fn = MagicMock()
        open_invoice_fn = MagicMock()
        rh = MagicMock(spec=CrashRecoveryHandler)

        ar = AutoRecovery(
            driver_factory=driver_factory,
            login_fn=login_fn,
            open_invoice_fn=open_invoice_fn,
            download_state=self.ds,
            engine_factory=engine_factory,
            max_attempts=3,
            recovery_handler=rh,
        )

        initial_plan = _make_plan("INV002", "INV003")
        ar.run(initial_plan)

        # Recovery handler called once for the crash.
        rh.handle.assert_called_once()
        # Login and invoice page opened once for the recovery.
        login_fn.assert_called_once_with(drivers[1])
        open_invoice_fn.assert_called_once_with(drivers[1])
        # ok_engine.run called with a DownloadPlan (not the original plan).
        ok_engine.run.assert_called_once()
        resumed_plan = ok_engine.run.call_args[0][0]
        self.assertIsInstance(resumed_plan, DownloadPlan)
        # Only INV002 and INV003 should be in the resumed plan
        # (INV001 was completed; resume excludes it).
        resumed_ids = {item.invoice_id for item in resumed_plan.scheduled_items}
        self.assertNotIn("INV001", resumed_ids)

    def test_full_recovery_cycle_exceeds_max_raises(self):
        """Exhaust all retry attempts and confirm the exception propagates."""
        items = [
            {
                "invoice_id": "INV001",
                "billing_period": "202605",
                "download_url": "https://example.com/dl?InvoiceID=INV001&BillingPeriod=202605",
                "filename": None,
                "download_status": "pending",
                "retry_count": 0,
                "last_error": None,
                "customer_id": None,
            }
        ]
        self.ds.initialize(items)

        engine = MagicMock()
        engine.run.side_effect = _FakeWebDriverException("gone")
        rh = MagicMock(spec=CrashRecoveryHandler)

        ar = AutoRecovery(
            driver_factory=MagicMock(return_value=MagicMock()),
            login_fn=MagicMock(),
            open_invoice_fn=MagicMock(),
            download_state=self.ds,
            engine_factory=MagicMock(return_value=engine),
            max_attempts=2,
            recovery_handler=rh,
        )
        with self.assertRaises(_FakeWebDriverException):
            ar.run(_make_plan("INV001"))

        self.assertEqual(rh.handle.call_count, 2)


if __name__ == "__main__":
    unittest.main()
