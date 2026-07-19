"""
Unit tests for the main.py pipeline integration (Build 2.2 – Task 3).

These tests verify that the main() function wires the components in the
correct order: scan → state filter → DownloadQueue → DownloadPlan → DownloaderEngine.run(plan).
They do not exercise the browser or network; all external dependencies are
patched.
"""
from types import SimpleNamespace
import unittest
from unittest.mock import MagicMock, patch


class TestMainFlowIntegration(unittest.TestCase):
    """
    Verify that main() calls DownloadPlan and DownloaderEngine.run() after
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
        result["mock_build_resume"].assert_called_once_with(saved_state)

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
    """Verify that _download_workflow integrates with CrashRecoveryHandler
    when engine.run() raises a browser/session failure."""

    # Fake browser exception recognised by is_browser_failure via name-based lookup.
    _FakeBrowserError = type("WebDriverException", (Exception,), {})

    def _build_patches(self, engine_side_effect=None, scheduled_count=2):
        """Return a dict of all patches needed to drive _download_workflow."""
        fake_driver = MagicMock()
        fake_driver.page_source = ""
        fake_driver.find_elements.return_value = []

        from types import SimpleNamespace
        queue = MagicMock()
        queue.__len__.return_value = scheduled_count
        queue_result = SimpleNamespace(
            queue=queue,
            found_count=scheduled_count,
            already_completed_count=0,
            latest_billing_period="202605",
        )

        return {
            "create_driver": patch("main.create_driver", return_value=fake_driver),
            "wait_until_logged_in": patch("main.wait_until_logged_in"),
            "open_invoice_page": patch("main.open_invoice_page", return_value=""),
            "builtins_open": patch("builtins.open", unittest.mock.mock_open()),
            "wait_for_grid": patch("main.wait_for_grid"),
            "count_grid_rows": patch("main.count_grid_rows", return_value=1),
            "get_grid_text": patch("main.get_grid_text", return_value=""),
            "get_devexpress_pager_info": patch("main.get_devexpress_pager_info", return_value={}),
            "collect_grid_download_links": patch("main.collect_grid_download_links", return_value=[]),
            "StateManager": patch("main.StateManager"),
            "build_download_queue": patch("main.build_download_queue", return_value=queue_result),
            "DownloadPlan": patch("main.DownloadPlan"),
            "DownloaderEngine": patch("main.DownloaderEngine"),
            "PeriodTracker": patch("main.PeriodTracker"),
            "DownloadState": patch("main.DownloadState"),
            "CrashRecoveryHandler": patch("main.CrashRecoveryHandler"),
            "is_browser_failure": patch("main.is_browser_failure"),
            "_fake_driver": fake_driver,
            "_queue_result": queue_result,
            "_engine_side_effect": engine_side_effect,
        }

    def _run_with_patches(self, patches, engine_side_effect=None, scheduled_count=2):
        """Enter all patches and run main(), returning relevant mocks."""
        cms = {k: v for k, v in patches.items()
               if not k.startswith("_") and hasattr(v, "__enter__")}
        active = {k: v.__enter__() for k, v in cms.items()}

        mock_plan = active["DownloadPlan"].return_value
        mock_plan.scheduled_count = scheduled_count
        mock_plan.latest_billing_period = "202605"

        mock_engine = active["DownloaderEngine"].return_value
        if engine_side_effect is not None:
            mock_engine.run.side_effect = engine_side_effect
        else:
            mock_engine.summary = SimpleNamespace(completed=0, failed=0)

        mock_ds = active["DownloadState"].return_value
        mock_ds.load_state.return_value = {}

        active["PeriodTracker"].return_value.last_period_file_exists.return_value = True

        try:
            import main
            main.main()
        finally:
            for v in reversed(list(cms.values())):
                v.__exit__(None, None, None)

        return active

    def test_browser_failure_triggers_recovery_handler(self):
        """CrashRecoveryHandler.handle() must be called on a browser failure."""
        exc = self._FakeBrowserError("session crashed")

        fake_driver = MagicMock()
        fake_driver.page_source = ""
        fake_driver.find_elements.return_value = []

        from types import SimpleNamespace
        queue = MagicMock()
        queue.__len__.return_value = 1
        queue_result = SimpleNamespace(
            queue=queue, found_count=1, already_completed_count=0, latest_billing_period="202605"
        )

        mock_recovery_handler = MagicMock()
        mock_report = MagicMock()
        mock_report.timestamp = "2026-01-01T00:00:00+00:00"
        mock_report.exception_type = "WebDriverException"
        mock_report.exception_message = "session crashed"
        mock_report.completed_count = 0
        mock_report.pending_count = 1
        mock_report.failed_count = 0
        mock_report.state_file = "state/download_state.json"
        mock_report.recovery_advice = "Restart to resume."
        mock_recovery_handler.handle.return_value = mock_report
        mock_recovery_handler.report_file = "state/recovery_report.json"

        with patch("main.create_driver", return_value=fake_driver), \
             patch("main.wait_until_logged_in"), \
             patch("main.open_invoice_page", return_value=""), \
             patch("builtins.open", unittest.mock.mock_open()), \
             patch("main.wait_for_grid"), \
             patch("main.count_grid_rows", return_value=1), \
             patch("main.get_grid_text", return_value=""), \
             patch("main.get_devexpress_pager_info", return_value={}), \
             patch("main.collect_grid_download_links", return_value=[]), \
             patch("main.StateManager"), \
             patch("main.build_download_queue", return_value=queue_result), \
             patch("main.DownloadPlan") as MockPlan, \
             patch("main.DownloaderEngine") as MockEngine, \
             patch("main.PeriodTracker"), \
             patch("main.DownloadState") as MockDS, \
             patch("main.CrashRecoveryHandler", return_value=mock_recovery_handler), \
             patch("main.is_browser_failure", return_value=True):

            MockPlan.return_value.scheduled_count = 1
            MockPlan.return_value.latest_billing_period = "202605"
            MockEngine.return_value.run.side_effect = exc
            MockDS.return_value.load_state.return_value = {}

            import main
            main.main()

        mock_recovery_handler.handle.assert_called_once_with(exc)

    def test_browser_failure_prints_recovery_summary(self):
        """A recovery summary must be printed to stdout on browser failure."""
        import io
        from contextlib import redirect_stdout

        exc = self._FakeBrowserError("window gone")

        fake_driver = MagicMock()
        fake_driver.page_source = ""
        fake_driver.find_elements.return_value = []

        from types import SimpleNamespace
        queue = MagicMock()
        queue.__len__.return_value = 1
        queue_result = SimpleNamespace(
            queue=queue, found_count=1, already_completed_count=0, latest_billing_period="202605"
        )

        mock_recovery_handler = MagicMock()
        mock_report = MagicMock()
        mock_report.timestamp = "2026-01-01T00:00:00+00:00"
        mock_report.exception_type = "WebDriverException"
        mock_report.exception_message = "window gone"
        mock_report.completed_count = 3
        mock_report.pending_count = 5
        mock_report.failed_count = 1
        mock_report.state_file = "state/download_state.json"
        mock_report.recovery_advice = "Restart to resume."
        mock_recovery_handler.handle.return_value = mock_report
        mock_recovery_handler.report_file = "state/recovery_report.json"

        buf = io.StringIO()
        with redirect_stdout(buf), \
             patch("main.create_driver", return_value=fake_driver), \
             patch("main.wait_until_logged_in"), \
             patch("main.open_invoice_page", return_value=""), \
             patch("builtins.open", unittest.mock.mock_open()), \
             patch("main.wait_for_grid"), \
             patch("main.count_grid_rows", return_value=1), \
             patch("main.get_grid_text", return_value=""), \
             patch("main.get_devexpress_pager_info", return_value={}), \
             patch("main.collect_grid_download_links", return_value=[]), \
             patch("main.StateManager"), \
             patch("main.build_download_queue", return_value=queue_result), \
             patch("main.DownloadPlan") as MockPlan, \
             patch("main.DownloaderEngine") as MockEngine, \
             patch("main.PeriodTracker"), \
             patch("main.DownloadState") as MockDS, \
             patch("main.CrashRecoveryHandler", return_value=mock_recovery_handler), \
             patch("main.is_browser_failure", return_value=True):

            MockPlan.return_value.scheduled_count = 1
            MockPlan.return_value.latest_billing_period = "202605"
            MockEngine.return_value.run.side_effect = exc
            MockDS.return_value.load_state.return_value = {}

            import main
            main.main()

        output = buf.getvalue()
        self.assertIn("Recovery Summary", output)
        self.assertIn("WebDriverException", output)
        self.assertIn("window gone", output)
        self.assertIn("3", output)   # completed_count
        self.assertIn("5", output)   # pending_count
        self.assertIn("1", output)   # failed_count
        self.assertIn("state/recovery_report.json", output)
        self.assertIn("Restart to resume.", output)

    def test_non_browser_exception_is_reraised(self):
        """Non-browser exceptions must be re-raised without recovery handling."""
        exc = RuntimeError("disk full")

        fake_driver = MagicMock()
        fake_driver.page_source = ""
        fake_driver.find_elements.return_value = []

        from types import SimpleNamespace
        queue = MagicMock()
        queue.__len__.return_value = 1
        queue_result = SimpleNamespace(
            queue=queue, found_count=1, already_completed_count=0, latest_billing_period="202605"
        )

        mock_recovery_handler = MagicMock()

        with patch("main.create_driver", return_value=fake_driver), \
             patch("main.wait_until_logged_in"), \
             patch("main.open_invoice_page", return_value=""), \
             patch("builtins.open", unittest.mock.mock_open()), \
             patch("main.wait_for_grid"), \
             patch("main.count_grid_rows", return_value=1), \
             patch("main.get_grid_text", return_value=""), \
             patch("main.get_devexpress_pager_info", return_value={}), \
             patch("main.collect_grid_download_links", return_value=[]), \
             patch("main.StateManager"), \
             patch("main.build_download_queue", return_value=queue_result), \
             patch("main.DownloadPlan") as MockPlan, \
             patch("main.DownloaderEngine") as MockEngine, \
             patch("main.PeriodTracker"), \
             patch("main.DownloadState") as MockDS, \
             patch("main.CrashRecoveryHandler", return_value=mock_recovery_handler), \
             patch("main.is_browser_failure", return_value=False):

            MockPlan.return_value.scheduled_count = 1
            MockPlan.return_value.latest_billing_period = "202605"
            MockEngine.return_value.run.side_effect = exc
            MockDS.return_value.load_state.return_value = {}

            import main
            with self.assertRaises(RuntimeError) as ctx:
                main.main()

        self.assertIs(ctx.exception, exc)
        mock_recovery_handler.handle.assert_not_called()

    def test_browser_failure_driver_quit_still_called(self):
        """driver.quit() must still be called after a browser failure is handled."""
        exc = self._FakeBrowserError("crash")

        fake_driver = MagicMock()
        fake_driver.page_source = ""
        fake_driver.find_elements.return_value = []

        from types import SimpleNamespace
        queue = MagicMock()
        queue.__len__.return_value = 1
        queue_result = SimpleNamespace(
            queue=queue, found_count=1, already_completed_count=0, latest_billing_period="202605"
        )

        mock_recovery_handler = MagicMock()
        mock_report = MagicMock()
        mock_report.timestamp = "t"
        mock_report.exception_type = "WebDriverException"
        mock_report.exception_message = "crash"
        mock_report.completed_count = 0
        mock_report.pending_count = 0
        mock_report.failed_count = 0
        mock_report.state_file = None
        mock_report.recovery_advice = ""
        mock_recovery_handler.handle.return_value = mock_report
        mock_recovery_handler.report_file = "state/recovery_report.json"

        with patch("main.create_driver", return_value=fake_driver), \
             patch("main.wait_until_logged_in"), \
             patch("main.open_invoice_page", return_value=""), \
             patch("builtins.open", unittest.mock.mock_open()), \
             patch("main.wait_for_grid"), \
             patch("main.count_grid_rows", return_value=1), \
             patch("main.get_grid_text", return_value=""), \
             patch("main.get_devexpress_pager_info", return_value={}), \
             patch("main.collect_grid_download_links", return_value=[]), \
             patch("main.StateManager"), \
             patch("main.build_download_queue", return_value=queue_result), \
             patch("main.DownloadPlan") as MockPlan, \
             patch("main.DownloaderEngine") as MockEngine, \
             patch("main.PeriodTracker"), \
             patch("main.DownloadState") as MockDS, \
             patch("main.CrashRecoveryHandler", return_value=mock_recovery_handler), \
             patch("main.is_browser_failure", return_value=True):

            MockPlan.return_value.scheduled_count = 1
            MockPlan.return_value.latest_billing_period = "202605"
            MockEngine.return_value.run.side_effect = exc
            MockDS.return_value.load_state.return_value = {}

            import main
            main.main()

        fake_driver.quit.assert_called_once()

    def test_crash_recovery_handler_receives_download_state(self):
        """CrashRecoveryHandler must be instantiated with the DownloadState object."""
        exc = self._FakeBrowserError("session gone")

        fake_driver = MagicMock()
        fake_driver.page_source = ""
        fake_driver.find_elements.return_value = []

        from types import SimpleNamespace
        queue = MagicMock()
        queue.__len__.return_value = 1
        queue_result = SimpleNamespace(
            queue=queue, found_count=1, already_completed_count=0, latest_billing_period="202605"
        )

        mock_report = MagicMock()
        mock_report.timestamp = "t"
        mock_report.exception_type = "WebDriverException"
        mock_report.exception_message = "session gone"
        mock_report.completed_count = 0
        mock_report.pending_count = 0
        mock_report.failed_count = 0
        mock_report.state_file = None
        mock_report.recovery_advice = ""

        mock_handler_instance = MagicMock()
        mock_handler_instance.handle.return_value = mock_report
        mock_handler_instance.report_file = "state/recovery_report.json"

        with patch("main.create_driver", return_value=fake_driver), \
             patch("main.wait_until_logged_in"), \
             patch("main.open_invoice_page", return_value=""), \
             patch("builtins.open", unittest.mock.mock_open()), \
             patch("main.wait_for_grid"), \
             patch("main.count_grid_rows", return_value=1), \
             patch("main.get_grid_text", return_value=""), \
             patch("main.get_devexpress_pager_info", return_value={}), \
             patch("main.collect_grid_download_links", return_value=[]), \
             patch("main.StateManager"), \
             patch("main.build_download_queue", return_value=queue_result), \
             patch("main.DownloadPlan") as MockPlan, \
             patch("main.DownloaderEngine") as MockEngine, \
             patch("main.PeriodTracker"), \
             patch("main.DownloadState") as MockDS, \
             patch("main.CrashRecoveryHandler", return_value=mock_handler_instance) as MockCRH, \
             patch("main.is_browser_failure", return_value=True):

            mock_ds_instance = MockDS.return_value
            mock_ds_instance.load_state.return_value = {}
            MockPlan.return_value.scheduled_count = 1
            MockPlan.return_value.latest_billing_period = "202605"
            MockEngine.return_value.run.side_effect = exc

            import main
            main.main()

        MockCRH.assert_called_once_with(download_state=mock_ds_instance)

    def test_period_tracker_not_called_on_browser_failure(self):
        """PeriodTracker.save_last_period() must NOT be called when recovery handles the crash."""
        exc = self._FakeBrowserError("window closed")

        fake_driver = MagicMock()
        fake_driver.page_source = ""
        fake_driver.find_elements.return_value = []

        from types import SimpleNamespace
        queue = MagicMock()
        queue.__len__.return_value = 2
        queue_result = SimpleNamespace(
            queue=queue, found_count=2, already_completed_count=0, latest_billing_period="202605"
        )

        mock_report = MagicMock()
        mock_report.timestamp = "t"
        mock_report.exception_type = "WebDriverException"
        mock_report.exception_message = "window closed"
        mock_report.completed_count = 1
        mock_report.pending_count = 1
        mock_report.failed_count = 0
        mock_report.state_file = None
        mock_report.recovery_advice = ""

        mock_handler = MagicMock()
        mock_handler.handle.return_value = mock_report
        mock_handler.report_file = "state/recovery_report.json"

        with patch("main.create_driver", return_value=fake_driver), \
             patch("main.wait_until_logged_in"), \
             patch("main.open_invoice_page", return_value=""), \
             patch("builtins.open", unittest.mock.mock_open()), \
             patch("main.wait_for_grid"), \
             patch("main.count_grid_rows", return_value=1), \
             patch("main.get_grid_text", return_value=""), \
             patch("main.get_devexpress_pager_info", return_value={}), \
             patch("main.collect_grid_download_links", return_value=[]), \
             patch("main.StateManager"), \
             patch("main.build_download_queue", return_value=queue_result), \
             patch("main.DownloadPlan") as MockPlan, \
             patch("main.DownloaderEngine") as MockEngine, \
             patch("main.PeriodTracker") as MockPT, \
             patch("main.DownloadState") as MockDS, \
             patch("main.CrashRecoveryHandler", return_value=mock_handler), \
             patch("main.is_browser_failure", return_value=True):

            MockPlan.return_value.scheduled_count = 2
            MockPlan.return_value.latest_billing_period = "202605"
            MockEngine.return_value.run.side_effect = exc
            MockDS.return_value.load_state.return_value = {}

            import main
            main.main()

        MockPT.return_value.save_last_period.assert_not_called()

    def test_recovery_logic_separate_from_downloader_engine(self):
        """Recovery handling must occur in main, not inside DownloaderEngine.

        The engine simply raises; main.py catches it via is_browser_failure and
        delegates to CrashRecoveryHandler.  No recovery method is called on the
        engine instance itself.
        """
        exc = self._FakeBrowserError("crash mid-run")

        fake_driver = MagicMock()
        fake_driver.page_source = ""
        fake_driver.find_elements.return_value = []

        from types import SimpleNamespace
        queue = MagicMock()
        queue.__len__.return_value = 1
        queue_result = SimpleNamespace(
            queue=queue, found_count=1, already_completed_count=0, latest_billing_period="202605"
        )

        mock_report = MagicMock()
        mock_report.timestamp = "t"
        mock_report.exception_type = "WebDriverException"
        mock_report.exception_message = "crash mid-run"
        mock_report.completed_count = 0
        mock_report.pending_count = 0
        mock_report.failed_count = 0
        mock_report.state_file = None
        mock_report.recovery_advice = ""

        mock_handler = MagicMock()
        mock_handler.handle.return_value = mock_report
        mock_handler.report_file = "state/recovery_report.json"

        with patch("main.create_driver", return_value=fake_driver), \
             patch("main.wait_until_logged_in"), \
             patch("main.open_invoice_page", return_value=""), \
             patch("builtins.open", unittest.mock.mock_open()), \
             patch("main.wait_for_grid"), \
             patch("main.count_grid_rows", return_value=1), \
             patch("main.get_grid_text", return_value=""), \
             patch("main.get_devexpress_pager_info", return_value={}), \
             patch("main.collect_grid_download_links", return_value=[]), \
             patch("main.StateManager"), \
             patch("main.build_download_queue", return_value=queue_result), \
             patch("main.DownloadPlan") as MockPlan, \
             patch("main.DownloaderEngine") as MockEngine, \
             patch("main.PeriodTracker"), \
             patch("main.DownloadState") as MockDS, \
             patch("main.CrashRecoveryHandler", return_value=mock_handler), \
             patch("main.is_browser_failure", return_value=True):

            MockPlan.return_value.scheduled_count = 1
            MockPlan.return_value.latest_billing_period = "202605"
            MockEngine.return_value.run.side_effect = exc
            MockDS.return_value.load_state.return_value = {}

            import main
            main.main()

        # Recovery was handled by CrashRecoveryHandler (not DownloaderEngine).
        mock_handler.handle.assert_called_once()
        # DownloaderEngine.run() raised; no extra calls were made on it for recovery.
        engine_instance = MockEngine.return_value
        self.assertEqual(engine_instance.run.call_count, 1)


if __name__ == "__main__":
    unittest.main()
