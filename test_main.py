"""
Unit tests for the main.py pipeline integration (Build 3.0 – Task 2).

These tests verify that the main() function wires the components in the
correct order: scan → state filter → DownloadQueue → DownloadPlan →
AutoRecovery.run(plan).  They do not exercise the browser or network;
all external dependencies are patched.
"""
from types import SimpleNamespace
import contextlib
import unittest
from unittest.mock import MagicMock, patch


class TestMainFlowIntegration(unittest.TestCase):
    """
    Verify that main() calls DownloadPlan and AutoRecovery.run() after
    creating the DownloadQueue, and that it does NOT print the old
    placeholder stop message.
    """

    def _run_main(
        self,
        *,
        scheduled_count=1,
        completed=1,
        failed=0,
        latest_period="202605",
        queue_size=0,
        period_file_exists=True,
    ):
        """
        Patch all browser/network dependencies and execute main().
        Returns the mock objects so tests can inspect the call history.
        """
        fake_driver = MagicMock()
        fake_driver.page_source = ""
        fake_driver.find_elements.return_value = []

        fake_links = [
            {
                "url": (
                    "https://example.com/DownloadARExport.aspx"
                    "?InvoiceID=1001&BillingPeriod=202605&Format=Detailed"
                ),
                "invoice_id": "1001",
                "billing_period": "202605",
            }
        ]
        queue = MagicMock()
        queue.__len__.return_value = queue_size
        queue_result = SimpleNamespace(
            queue=queue,
            found_count=1,
            already_completed_count=1,
            latest_billing_period=latest_period,
        )

        with patch("main.create_driver", return_value=fake_driver), \
             patch("main.wait_until_logged_in"), \
             patch("main.open_invoice_page", return_value=""), \
             patch("builtins.open", unittest.mock.mock_open()), \
             patch("main.wait_for_grid"), \
             patch("main.count_grid_rows", return_value=1), \
             patch("main.get_grid_text", return_value=""), \
             patch("main.get_devexpress_pager_info", return_value={}), \
             patch("main.collect_grid_download_links", return_value=fake_links) as mock_collect, \
             patch("main.StateManager") as MockStateManager, \
             patch("main.build_download_queue", return_value=queue_result) as mock_build_queue, \
             patch("main.DownloadPlan") as MockPlan, \
             patch("main.DownloaderEngine") as MockEngine, \
             patch("main.PeriodTracker") as MockPeriodTracker, \
             patch("main.DownloadState") as MockDownloadState:

            mock_state_manager_instance = MockStateManager.return_value
            mock_plan_instance = MockPlan.return_value
            mock_plan_instance.scheduled_count = scheduled_count
            mock_plan_instance.latest_billing_period = latest_period

            mock_engine_instance = MockEngine.return_value
            mock_engine_instance.summary = SimpleNamespace(completed=completed, failed=failed)
            mock_period_tracker_instance = MockPeriodTracker.return_value
            mock_period_tracker_instance.last_period_file_exists.return_value = period_file_exists

            mock_ds_instance = MockDownloadState.return_value
            mock_ds_instance.load_state.return_value = {}  # No interrupted session

            import main
            main.main()

            return {
                "driver": fake_driver,
                "mock_collect": mock_collect,
                "MockStateManager": MockStateManager,
                "mock_state_manager_instance": mock_state_manager_instance,
                "mock_build_queue": mock_build_queue,
                "queue_result": queue_result,
                "MockPlan": MockPlan,
                "mock_plan_instance": mock_plan_instance,
                "MockEngine": MockEngine,
                "mock_engine_instance": mock_engine_instance,
                "MockPeriodTracker": MockPeriodTracker,
                "mock_period_tracker_instance": mock_period_tracker_instance,
                "MockDownloadState": MockDownloadState,
                "mock_ds_instance": mock_ds_instance,
            }

    def test_queue_is_built_from_scanned_links_and_state_manager(self):
        """build_download_queue must receive scanned links and the state manager."""
        result = self._run_main()
        result["mock_build_queue"].assert_called_once_with(
            result["mock_collect"].return_value,
            state_manager=result["mock_state_manager_instance"],
        )

    def test_download_plan_built_from_filtered_queue(self):
        """DownloadPlan must be instantiated with the filtered DownloadQueue."""
        result = self._run_main()
        result["MockPlan"].assert_called_once_with(result["queue_result"].queue, latest_only=False)

    def test_downloader_engine_run_called_with_plan(self):
        """DownloaderEngine.run() must be called with the plan instance."""
        result = self._run_main()
        result["mock_engine_instance"].run.assert_called_once_with(
            result["mock_plan_instance"]
        )

    def test_downloader_engine_instantiated_with_driver(self):
        """DownloaderEngine must be instantiated with the Selenium driver, state manager, and download state."""
        result = self._run_main()
        result["MockEngine"].assert_called_once_with(
            result["driver"],
            state_manager=result["mock_state_manager_instance"],
            download_state=result["mock_ds_instance"],
        )

    def test_driver_quit_called_on_success(self):
        """driver.quit() must always be called (finally block)."""
        result = self._run_main()
        result["driver"].quit.assert_called_once()

    def test_no_placeholder_stop_message(self):
        """The old placeholder stop message must no longer appear in output."""
        import io
        from contextlib import redirect_stdout

        buf = io.StringIO()
        with redirect_stdout(buf):
            self._run_main()

        output = buf.getvalue()
        self.assertNotIn("当前仅排队，不执行下载", output)
        self.assertIn("Found invoices: 1", output)
        self.assertIn("Already completed: 1", output)
        self.assertIn("Download Queue: 0", output)

    def test_last_period_saved_when_all_downloads_succeed(self):
        result = self._run_main(
            scheduled_count=2,
            completed=2,
            failed=0,
            latest_period="202606",
            queue_size=2,
            period_file_exists=True,
        )
        result["mock_period_tracker_instance"].save_last_period.assert_called_once_with("202606")

    def test_last_period_not_saved_when_any_download_fails(self):
        result = self._run_main(
            scheduled_count=2,
            completed=1,
            failed=1,
            latest_period="202606",
            queue_size=2,
            period_file_exists=True,
        )
        result["mock_period_tracker_instance"].save_last_period.assert_not_called()

    def test_last_period_not_saved_when_completed_count_mismatches_plan(self):
        result = self._run_main(
            scheduled_count=2,
            completed=1,
            failed=0,
            latest_period="202606",
            queue_size=2,
            period_file_exists=True,
        )
        result["mock_period_tracker_instance"].save_last_period.assert_not_called()

    def test_last_period_initialized_once_when_queue_empty_and_file_missing(self):
        result = self._run_main(
            scheduled_count=0,
            completed=0,
            failed=0,
            latest_period="202606",
            queue_size=0,
            period_file_exists=False,
        )
        result["mock_period_tracker_instance"].save_last_period.assert_called_once_with("202606")

    def test_last_period_not_overwritten_when_queue_empty_and_file_exists(self):
        result = self._run_main(
            scheduled_count=0,
            completed=0,
            failed=0,
            latest_period="202606",
            queue_size=0,
            period_file_exists=True,
        )
        result["mock_period_tracker_instance"].save_last_period.assert_not_called()


class TestMainResumeIntegration(unittest.TestCase):
    """Verify that _download_workflow skips grid scanning when an interrupted
    session is detected and uses the resume queue instead."""

    def _run_main_with_resume(self, saved_state, *, scheduled_count=1, completed=1, failed=0):
        """Run main() with a pre-configured saved state that triggers resume."""
        fake_driver = MagicMock()
        fake_driver.page_source = ""
        fake_driver.find_elements.return_value = []

        resume_queue = MagicMock()
        resume_queue.__len__.return_value = scheduled_count

        with patch("main.create_driver", return_value=fake_driver), \
             patch("main.wait_until_logged_in"), \
             patch("main.StateManager"), \
             patch("main.DownloadPlan") as MockPlan, \
             patch("main.DownloaderEngine") as MockEngine, \
             patch("main.PeriodTracker") as MockPeriodTracker, \
             patch("main.DownloadState") as MockDownloadState, \
             patch("main.has_interrupted_session", return_value=True), \
             patch("main.build_resume_queue", return_value=resume_queue) as mock_build_resume, \
             patch("main.build_download_queue") as mock_build_queue, \
             patch("main.collect_grid_download_links") as mock_collect:

            mock_ds_instance = MockDownloadState.return_value
            mock_ds_instance.load_state.return_value = saved_state

            mock_plan_instance = MockPlan.return_value
            mock_plan_instance.scheduled_count = scheduled_count
            mock_plan_instance.latest_billing_period = saved_state.get("billing_period")

            mock_engine_instance = MockEngine.return_value
            mock_engine_instance.summary = SimpleNamespace(completed=completed, failed=failed)
            mock_period_tracker_instance = MockPeriodTracker.return_value
            mock_period_tracker_instance.last_period_file_exists.return_value = True

            import main
            main.main()

            return {
                "driver": fake_driver,
                "mock_collect": mock_collect,
                "mock_build_queue": mock_build_queue,
                "mock_build_resume": mock_build_resume,
                "MockPlan": MockPlan,
                "mock_plan_instance": mock_plan_instance,
                "MockEngine": MockEngine,
                "mock_engine_instance": mock_engine_instance,
                "MockDownloadState": MockDownloadState,
                "mock_ds_instance": mock_ds_instance,
            }

    def _make_interrupted_state(self, billing_period="202605"):
        return {
            "billing_period": billing_period,
            "customer_id": None,
            "invoice_id": None,
            "queue": [
                {"invoice_id": "1001", "billing_period": billing_period,
                 "download_url": "https://example.com/dl?InvoiceID=1001",
                 "filename": None, "retry_count": 0, "last_error": None,
                 "download_status": "pending"},
                {"invoice_id": "1002", "billing_period": billing_period,
                 "download_url": "https://example.com/dl?InvoiceID=1002",
                 "filename": None, "retry_count": 0, "last_error": None,
                 "download_status": "pending"},
            ],
            "completed": [
                {"invoice_id": "1001", "billing_period": billing_period,
                 "download_url": "https://example.com/dl?InvoiceID=1001",
                 "filename": "202605_1001.xlsx", "retry_count": 0,
                 "last_error": None, "download_status": "completed"},
            ],
            "failed": [],
        }

    def test_resume_skips_grid_scanning(self):
        """When resuming, grid scanning must NOT be called."""
        saved_state = self._make_interrupted_state()
        result = self._run_main_with_resume(saved_state)
        result["mock_collect"].assert_not_called()
        result["mock_build_queue"].assert_not_called()

    def test_resume_calls_build_resume_queue_with_saved_state(self):
        """build_resume_queue must be called with the loaded state dict."""
        saved_state = self._make_interrupted_state()
        result = self._run_main_with_resume(saved_state)
        result["mock_build_resume"].assert_called_once_with(
            saved_state, periods=["202605"]
        )

    def test_resume_passes_download_state_to_engine(self):
        """DownloaderEngine must receive the DownloadState instance during resume."""
        saved_state = self._make_interrupted_state()
        result = self._run_main_with_resume(saved_state)
        _, kwargs = result["MockEngine"].call_args
        self.assertIs(kwargs.get("download_state"), result["mock_ds_instance"])

    def test_resume_engine_runs_with_plan_from_resume_queue(self):
        """DownloaderEngine.run() must be called with a plan built from the resume queue."""
        saved_state = self._make_interrupted_state()
        result = self._run_main_with_resume(saved_state)
        result["mock_engine_instance"].run.assert_called_once_with(result["mock_plan_instance"])

    def test_resume_prints_resuming_message(self):
        """The workflow must print a resuming message when an interrupted session is found."""
        import io
        from contextlib import redirect_stdout

        saved_state = self._make_interrupted_state()
        buf = io.StringIO()
        with redirect_stdout(buf):
            self._run_main_with_resume(saved_state)

        self.assertIn("Resuming interrupted download session", buf.getvalue())


class TestMainScheduledModeResilience(unittest.TestCase):
    def test_daily_mode_continues_after_run_failure(self):
        import main

        mock_scheduler = MagicMock()
        mock_scheduler.wait_until_next_run.side_effect = [None, None, KeyboardInterrupt()]
        mock_scheduler.run_once.side_effect = [RuntimeError("boom"), None]

        with patch("main.SCHEDULER_ENABLED", True), \
             patch("main.SCHEDULER_MODE", "daily"), \
             patch("main.SCHEDULE_DAY", 1), \
             patch("main.SCHEDULE_HOUR", 8), \
             patch("main.SCHEDULE_MINUTE", 0), \
             patch("main.Scheduler", return_value=mock_scheduler):
            with self.assertLogs("main", level="ERROR") as logs:
                with self.assertRaises(KeyboardInterrupt):
                    main.main()

        self.assertEqual(mock_scheduler.run_once.call_count, 2)
        self.assertIn(
            "Scheduled workflow execution failed; waiting for next run.",
            "\n".join(logs.output),
        )

    def test_monthly_mode_continues_after_run_failure(self):
        import main

        mock_scheduler = MagicMock()
        mock_scheduler.wait_until_next_run.side_effect = [None, KeyboardInterrupt()]
        mock_scheduler.run_once.side_effect = RuntimeError("monthly boom")

        with patch("main.SCHEDULER_ENABLED", True), \
             patch("main.SCHEDULER_MODE", "monthly"), \
             patch("main.SCHEDULE_DAY", 15), \
             patch("main.SCHEDULE_HOUR", 3), \
             patch("main.SCHEDULE_MINUTE", 30), \
             patch("main.Scheduler", return_value=mock_scheduler):
            with self.assertLogs("main", level="ERROR") as logs:
                with self.assertRaises(KeyboardInterrupt):
                    main.main()

        self.assertEqual(mock_scheduler.run_once.call_count, 1)
        self.assertIn(
            "Scheduled workflow execution failed; waiting for next run.",
            "\n".join(logs.output),
        )


class TestMainRecoveryIntegration(unittest.TestCase):
    """Verify that _download_workflow wires AutoRecovery correctly and that
    AutoRecovery.run(plan) replaces the direct DownloaderEngine.run(plan) call."""

    # Fake browser exception recognised by AutoRecovery via is_browser_failure.
    _FakeBrowserError = type("WebDriverException", (Exception,), {})

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _make_patches(self, fake_driver, queue_result, scheduled_count=1):
        """Return a list of patches common to every test in this class."""
        return [
            patch("main.create_driver", return_value=fake_driver),
            patch("main.wait_until_logged_in"),
            patch("main.open_invoice_page", return_value=""),
            patch("builtins.open", unittest.mock.mock_open()),
            patch("main.wait_for_grid"),
            patch("main.count_grid_rows", return_value=1),
            patch("main.get_grid_text", return_value=""),
            patch("main.get_devexpress_pager_info", return_value={}),
            patch("main.collect_grid_download_links", return_value=[]),
            patch("main.StateManager"),
            patch("main.build_download_queue", return_value=queue_result),
            patch("main.DownloadPlan"),
            patch("main.DownloaderEngine"),
            patch("main.PeriodTracker"),
            patch("main.DownloadState"),
            patch("main.AutoRecovery"),
        ]

    def _make_driver_and_queue(self, queue_size=1):
        fake_driver = MagicMock()
        fake_driver.page_source = ""
        fake_driver.find_elements.return_value = []
        queue = MagicMock()
        queue.__len__.return_value = queue_size
        queue_result = SimpleNamespace(
            queue=queue,
            found_count=queue_size,
            already_completed_count=0,
            latest_billing_period="202605",
        )
        return fake_driver, queue, queue_result

    # ------------------------------------------------------------------
    # Tests
    # ------------------------------------------------------------------

    def test_auto_recovery_run_called_with_plan(self):
        """AutoRecovery.run() must be called with the DownloadPlan instance."""
        fake_driver, _, queue_result = self._make_driver_and_queue()

        with contextlib.ExitStack() as stack:
            mocks = {p.attribute: stack.enter_context(p)
                     for p in self._make_patches(fake_driver, queue_result)}

            mock_plan = mocks["DownloadPlan"].return_value
            mock_plan.scheduled_count = 1
            mock_plan.latest_billing_period = "202605"
            mocks["DownloadState"].return_value.load_state.return_value = {}

            import main
            main.main()

        mock_ar_instance = mocks["AutoRecovery"].return_value
        mock_ar_instance.run.assert_called_once_with(mock_plan)

    def test_auto_recovery_receives_download_state(self):
        """AutoRecovery must be constructed with the active DownloadState instance."""
        fake_driver, _, queue_result = self._make_driver_and_queue()

        with contextlib.ExitStack() as stack:
            mocks = {p.attribute: stack.enter_context(p)
                     for p in self._make_patches(fake_driver, queue_result)}

            mock_plan = mocks["DownloadPlan"].return_value
            mock_plan.scheduled_count = 1
            mock_plan.latest_billing_period = "202605"
            mock_ds_instance = mocks["DownloadState"].return_value
            mock_ds_instance.load_state.return_value = {}

            import main
            main.main()

        _, kwargs = mocks["AutoRecovery"].call_args
        self.assertIs(kwargs.get("download_state"), mock_ds_instance)

    def test_auto_recovery_receives_correct_callables(self):
        """AutoRecovery must receive login_fn, open_invoice_fn, and an
        engine_factory that creates a DownloaderEngine bound to the given driver."""
        fake_driver, _, queue_result = self._make_driver_and_queue()

        with contextlib.ExitStack() as stack:
            mocks = {p.attribute: stack.enter_context(p)
                     for p in self._make_patches(fake_driver, queue_result)}

            mock_plan = mocks["DownloadPlan"].return_value
            mock_plan.scheduled_count = 1
            mock_plan.latest_billing_period = "202605"
            mock_ds_instance = mocks["DownloadState"].return_value
            mock_ds_instance.load_state.return_value = {}

            import main
            main.main()

            # Check callables while patches are still active so the mocks
            # in main's namespace match the values captured by AutoRecovery().
            _, kwargs = mocks["AutoRecovery"].call_args

            self.assertIs(kwargs.get("login_fn"), main.wait_until_logged_in)
            self.assertIs(kwargs.get("open_invoice_fn"), main.open_invoice_page)

            # engine_factory must produce a DownloaderEngine bound to the supplied driver.
            engine_factory = kwargs.get("engine_factory")
            self.assertIsNotNone(engine_factory, "engine_factory must be passed to AutoRecovery")
            mock_test_driver = MagicMock()
            engine_factory(mock_test_driver)
            mocks["DownloaderEngine"].assert_called_with(
                mock_test_driver,
                state_manager=unittest.mock.ANY,
                download_state=mock_ds_instance,
            )

    def test_non_browser_exception_is_reraised(self):
        """Non-browser exceptions raised by AutoRecovery.run() must propagate."""
        exc = RuntimeError("disk full")
        fake_driver, _, queue_result = self._make_driver_and_queue()

        with contextlib.ExitStack() as stack:
            mocks = {p.attribute: stack.enter_context(p)
                     for p in self._make_patches(fake_driver, queue_result)}

            mock_plan = mocks["DownloadPlan"].return_value
            mock_plan.scheduled_count = 1
            mock_plan.latest_billing_period = "202605"
            mocks["DownloadState"].return_value.load_state.return_value = {}
            mocks["AutoRecovery"].return_value.run.side_effect = exc

            import main
            with self.assertRaises(RuntimeError) as ctx:
                main.main()

        self.assertIs(ctx.exception, exc)

    def test_browser_failure_driver_quit_still_called(self):
        """driver.quit() must be called even when AutoRecovery.run() raises."""
        exc = self._FakeBrowserError("session crashed")
        fake_driver, _, queue_result = self._make_driver_and_queue()

        with contextlib.ExitStack() as stack:
            mocks = {p.attribute: stack.enter_context(p)
                     for p in self._make_patches(fake_driver, queue_result)}

            mock_plan = mocks["DownloadPlan"].return_value
            mock_plan.scheduled_count = 1
            mock_plan.latest_billing_period = "202605"
            mocks["DownloadState"].return_value.load_state.return_value = {}
            mocks["AutoRecovery"].return_value.run.side_effect = exc

            import main
            with self.assertRaises(self._FakeBrowserError):
                main.main()

        # When the mocked AutoRecovery never consumed _initial_driver, the
        # finally block in _download_workflow must quit the driver.
        fake_driver.quit.assert_called_once()

    def test_period_tracker_not_called_on_failure(self):
        """PeriodTracker.save_last_period() must NOT be called when
        AutoRecovery.run() raises (e.g. max recovery attempts exhausted)."""
        exc = self._FakeBrowserError("window closed")
        fake_driver, _, queue_result = self._make_driver_and_queue(queue_size=2)

        with contextlib.ExitStack() as stack:
            mocks = {p.attribute: stack.enter_context(p)
                     for p in self._make_patches(fake_driver, queue_result)}

            mock_plan = mocks["DownloadPlan"].return_value
            mock_plan.scheduled_count = 2
            mock_plan.latest_billing_period = "202605"
            mocks["DownloadState"].return_value.load_state.return_value = {}
            mocks["AutoRecovery"].return_value.run.side_effect = exc

            import main
            with self.assertRaises(self._FakeBrowserError):
                main.main()

        mocks["PeriodTracker"].return_value.save_last_period.assert_not_called()

    def test_engine_created_only_via_factory(self):
        """DownloaderEngine must NOT be instantiated directly by main.py;
        it must only be created through the engine_factory passed to AutoRecovery."""
        fake_driver, _, queue_result = self._make_driver_and_queue()

        with contextlib.ExitStack() as stack:
            mocks = {p.attribute: stack.enter_context(p)
                     for p in self._make_patches(fake_driver, queue_result)}

            mock_plan = mocks["DownloadPlan"].return_value
            mock_plan.scheduled_count = 1
            mock_plan.latest_billing_period = "202605"
            mocks["DownloadState"].return_value.load_state.return_value = {}

            import main
            main.main()

        # With AutoRecovery patched, engine_factory is never called, so
        # DownloaderEngine must not have been instantiated by main directly.
        mocks["DownloaderEngine"].assert_not_called()


if __name__ == "__main__":
    unittest.main()
