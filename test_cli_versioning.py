"""Focused Sprint 8 CLI, version, and per-run directory coverage."""

import io
from contextlib import redirect_stdout
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import MagicMock, patch

import main
from ibis.version import APPLICATION_NAME, __version__


def _link():
    return {
        "url": "https://example.test/DownloadARExport.aspx?InvoiceID=100&BillingPeriod=202605",
        "invoice_id": "100",
        "billing_period": "202605",
    }


class CliVersioningTests(unittest.TestCase):
    def test_authoritative_version_is_used_by_the_application(self):
        self.assertEqual(__version__, "3.6.0-beta1")
        self.assertEqual(APPLICATION_NAME, "IBIS Auto Downloader")

    def test_version_exits_before_logging_or_browser_initialization(self):
        output = io.StringIO()
        with patch("main.configure_logging") as configure, \
             patch("main.create_driver") as create_driver, \
             patch("main.DownloadState") as state, \
             patch("main.RunReporter") as reporter, \
             redirect_stdout(output):
            main.main(["--version"])
        self.assertEqual(output.getvalue().strip(), "IBIS Auto Downloader 3.6.0-beta1")
        configure.assert_not_called()
        create_driver.assert_not_called()
        state.assert_not_called()
        reporter.assert_not_called()

    def test_startup_log_uses_authoritative_version(self):
        scheduler = MagicMock()
        with patch("main.configure_logging", return_value="run-1"), \
             patch("main.Scheduler", return_value=scheduler), \
             patch("main.logger") as logger:
            main.main([])
        logger.info.assert_any_call(
            "Starting %s version %s (run %s).",
            APPLICATION_NAME,
            __version__,
            "run-1",
        )
        logger.info.assert_any_call(
            "Runtime metadata: python=%s, platform=%s, download_dir=%s, report_dir=%s.",
            main.sys.version.split()[0],
            main.platform.platform(),
            main.DOWNLOAD_DIR.resolve(),
            (main.PROJECT_ROOT / "reports").resolve(),
        )
        scheduler.run_once.assert_called_once()

    def test_show_config_exits_before_logging_or_browser_initialization(self):
        output = io.StringIO()
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            download_dir = root / "not-created-downloads"
            report_dir = root / "not-created-reports"
            with patch("main.configure_logging") as configure, \
                 patch("main.create_driver") as create_driver, \
                 patch("main.DownloadState") as state, \
                 patch("main.RunReporter") as reporter, \
                 patch("main.platform.platform", return_value="Test OS"), \
                 patch("main.sys.version", "3.12.0 test build"), \
                 redirect_stdout(output):
                main.main([
                    "--show-config",
                    "--download-dir", str(download_dir),
                    "--report-dir", str(report_dir),
                ])
            text = output.getvalue()
            self.assertIn("IBIS Auto Downloader 3.6.0-beta1", text)
            self.assertIn("Application version: 3.6.0-beta1", text)
            self.assertIn("Python version: 3.12.0", text)
            self.assertIn("Operating system: Test OS", text)
            self.assertIn(f"Download directory: {download_dir.resolve()}", text)
            self.assertIn(f"Report directory: {report_dir.resolve()}", text)
            self.assertIn(f"State directory: {main.STATE_DIR.resolve()}", text)
            self.assertIn("Billing Period mode: latest (default)", text)
            self.assertIn("Retry count: 3", text)
            self.assertIn("Dry Run default: false", text)
            self.assertIn("Performance instrumentation status: enabled", text)
            self.assertFalse(download_dir.exists())
            self.assertFalse(report_dir.exists())
        configure.assert_not_called()
        create_driver.assert_not_called()
        state.assert_not_called()
        reporter.assert_not_called()

    def test_help_includes_practical_examples(self):
        output = io.StringIO()
        with self.assertRaises(SystemExit) as raised, redirect_stdout(output):
            main.parse_cli_args(["--help"])
        self.assertEqual(raised.exception.code, 0)
        text = output.getvalue()
        for example in (
            "python3 main.py --version",
            "python3 main.py --show-config",
            "python3 main.py --dry-run",
            "python3 main.py --download-dir ~/Downloads",
            "python3 main.py --report-dir ~/Reports",
        ):
            self.assertIn(example, text)

    def test_directory_arguments_resolve_and_expand_home(self):
        args = main.parse_cli_args([
            "--download-dir", "~/ibis-downloads",
            "--report-dir", "~/ibis-reports",
        ])
        self.assertEqual(args.download_dir, (Path("~") / "ibis-downloads").expanduser().resolve())
        self.assertEqual(args.report_dir, (Path("~") / "ibis-reports").expanduser().resolve())

    def test_omitted_directory_options_remain_none(self):
        args = main.parse_cli_args([])
        self.assertIsNone(args.download_dir)
        self.assertIsNone(args.report_dir)


class DirectoryPropagationTests(unittest.TestCase):
    def _patches(self, *, state, driver, reporter):
        manager = MagicMock()
        manager.filter_pending_links.return_value = ([_link()], 0)
        return (
            patch("main.create_driver", return_value=driver),
            patch("main.wait_until_logged_in"),
            patch("main.open_invoice_page", return_value=""),
            patch("main.wait_for_grid"),
            patch("main.count_grid_rows", return_value=1),
            patch("main.get_grid_text", return_value=""),
            patch("main.get_devexpress_pager_info", return_value={}),
            patch("main.collect_grid_download_links", return_value=[_link()]),
            patch("main.StateManager", return_value=manager),
            patch("main.DownloadState", return_value=state),
            patch("main.PeriodTracker"),
            patch("main.RunReporter", return_value=reporter),
        )

    def test_dry_run_with_report_dir_propagates_resolved_directories(self):
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            download_dir = (root / "downloads").resolve()
            report_dir = (root / "reports").resolve()
            driver = MagicMock(page_source="")
            state = MagicMock()
            state.load_state.return_value = {}
            reporter = MagicMock()
            patches = self._patches(state=state, driver=driver, reporter=reporter)
            with patches[0] as create_driver, patches[1], patches[2], patches[3], \
                 patches[4], patches[5], patches[6], patches[7], patches[8], \
                 patches[9], patches[10], patches[11] as reporter_type:
                main._download_workflow(
                    run_id="directory-dry-run", dry_run=True,
                    download_dir=download_dir, report_dir=report_dir,
                )
            create_driver.assert_called_once_with(download_dir)
            reporter_type.assert_called_once_with(report_dir)
            reporter.generate.assert_called_once()
            self.assertTrue(reporter.generate.call_args.kwargs["dry_run"])

    def test_download_dir_reaches_initial_and_recovery_engines(self):
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            download_dir = (root / "downloads").resolve()
            driver = MagicMock(page_source="")
            replacement = MagicMock()
            state = MagicMock()
            state.load_state.return_value = {}
            reporter = MagicMock()
            engine = MagicMock(summary=SimpleNamespace(completed=1, failed=0))

            class Recovery:
                def __init__(self, *, driver_factory, engine_factory, **_kwargs):
                    self.driver_factory = driver_factory
                    self.engine_factory = engine_factory

                def run(self, plan):
                    self.engine_factory(self.driver_factory())
                    self.engine_factory(self.driver_factory())
                    return SimpleNamespace(
                        retry_attempts=0, successful_recoveries=0, permanent_failures=0
                    )

            patches = self._patches(state=state, driver=driver, reporter=reporter)
            with patches[0] as create_driver, patches[1], patches[2], patches[3], \
                 patches[4], patches[5], patches[6], patches[7], patches[8], \
                 patches[9], patches[10], patches[11], \
                 patch("main.AutoRecovery", Recovery), \
                 patch("main.DownloaderEngine", return_value=engine) as engine_type:
                create_driver.side_effect = [driver, replacement]
                main._download_workflow(
                    run_id="directory-run", download_dir=download_dir,
                )
            self.assertEqual(create_driver.call_args_list, [
                unittest.mock.call(download_dir), unittest.mock.call(download_dir),
            ])
            self.assertEqual(engine_type.call_count, 2)
            for call in engine_type.call_args_list:
                self.assertEqual(call.kwargs["download_dir"], download_dir)

    def test_omitted_directories_preserve_default_factory_calls(self):
        driver = MagicMock(page_source="")
        state = MagicMock()
        state.load_state.return_value = {}
        reporter = MagicMock()
        engine = MagicMock(summary=SimpleNamespace(completed=1, failed=0))

        class Recovery:
            def __init__(self, *, driver_factory, engine_factory, **_kwargs):
                self.driver_factory = driver_factory
                self.engine_factory = engine_factory

            def run(self, plan):
                self.engine_factory(self.driver_factory())
                return SimpleNamespace(
                    retry_attempts=0, successful_recoveries=0, permanent_failures=0
                )

        patches = self._patches(state=state, driver=driver, reporter=reporter)
        with patches[0] as create_driver, patches[1], patches[2], patches[3], \
             patches[4], patches[5], patches[6], patches[7], patches[8], \
             patches[9], patches[10], patches[11] as reporter_type, patch("main.AutoRecovery", Recovery), \
             patch("main.DownloaderEngine", return_value=engine) as engine_type:
            main._download_workflow(run_id="default-directories")
        create_driver.assert_called_once_with()
        self.assertNotIn("download_dir", engine_type.call_args.kwargs)
        reporter_type.assert_called_once_with()


if __name__ == "__main__":
    unittest.main()
