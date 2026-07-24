"""Tests for the development-only controlled recovery fault hook."""

import os
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from selenium.common.exceptions import InvalidSessionIdException

from ibis.auto_recovery import AutoRecovery
from ibis.downloader import DownloadQueue
from ibis.downloader_engine import ControlledSessionFailureInjector, DownloaderEngine
from ibis.retry import ErrorCategory, classify_error
from ibis.scheduler import DownloadPlan
from ibis.state import DownloadState


class _DownloadDriver:
    def __init__(self, directory):
        self.directory = directory
        self.urls = []
        self.quit_calls = 0

    def get(self, url):
        self.urls.append(url)
        if "InvoiceID=" not in url:
            return
        invoice_id = url.split("InvoiceID=")[1].split("&", 1)[0]
        (self.directory / f"invoice-{invoice_id}.xls").write_text("export", encoding="utf-8")

    def quit(self):
        self.quit_calls += 1


def _plan(*invoice_ids):
    return DownloadPlan(
        DownloadQueue.from_links(
            [
                {
                    "url": f"https://example.test/download?InvoiceID={invoice_id}&BillingPeriod=202605",
                    "invoice_id": invoice_id,
                    "billing_period": "202605",
                }
                for invoice_id in invoice_ids
            ]
        ),
        latest_only=False,
    )


class ControlledFaultInjectionTests(unittest.TestCase):
    def test_disabled_by_default(self):
        with patch.dict(os.environ, {}, clear=True):
            injector = ControlledSessionFailureInjector.from_environment()
        self.assertFalse(injector.enabled)
        injector.raise_if_due(99)
        self.assertFalse(injector.triggered)

    def test_invalid_environment_values_disable_injection(self):
        for value in ("", "0", "-1", "not-a-number", "1.5"):
            with self.subTest(value=value), patch.dict(
                os.environ, {"IBIS_TEST_FORCE_SESSION_FAILURE_AFTER": value}, clear=True
            ):
                self.assertFalse(ControlledSessionFailureInjector.from_environment().enabled)

    def test_triggers_after_configured_completed_count_with_session_exception(self):
        with TemporaryDirectory() as tmp, patch.dict(
            os.environ, {"IBIS_TEST_FORCE_SESSION_FAILURE_AFTER": "1"}, clear=True
        ):
            directory = Path(tmp)
            driver = _DownloadDriver(directory)
            engine = DownloaderEngine(driver, download_dir=directory, timeout=1, poll_interval=0)

            with self.assertLogs("ibis.downloader_engine", level="WARNING") as logs:
                with self.assertRaises(InvalidSessionIdException) as raised:
                    engine.run(_plan("A", "B"))

        self.assertEqual(driver.urls, [
            "https://example.test/download?InvoiceID=A&BillingPeriod=202605"
        ])
        self.assertEqual(engine.summary.completed, 1)
        self.assertEqual(classify_error(raised.exception), ErrorCategory.SESSION)
        self.assertTrue(engine.fault_injector.triggered)
        self.assertTrue(any("TEST ONLY: injecting Selenium session failure" in line for line in logs.output))

    def test_triggers_only_once_and_does_not_repeat_after_recovery(self):
        with TemporaryDirectory() as tmp, patch.dict(
            os.environ, {"IBIS_TEST_FORCE_SESSION_FAILURE_AFTER": "1"}, clear=True
        ):
            directory = Path(tmp)
            state = DownloadState(state_file=directory / "download_state.json")
            original, replacement = _DownloadDriver(directory), _DownloadDriver(directory)
            engines = []

            def engine_factory(driver):
                engine = DownloaderEngine(
                    driver,
                    download_dir=directory,
                    timeout=1,
                    poll_interval=0,
                    download_state=state,
                )
                engines.append(engine)
                return engine

            recovery = AutoRecovery(
                driver_factory=iter([original, replacement]).__next__,
                login_fn=lambda _driver: None,
                open_invoice_fn=lambda _driver: None,
                download_state=state,
                engine_factory=engine_factory,
                recovery_handler=type("Handler", (), {"handle": lambda self, _exc: None})(),
            )
            def find_output(filename):
                candidate = directory / filename
                return candidate if candidate.exists() else None

            with patch("ibis.resume.find_downloaded_output", side_effect=find_output):
                summary = recovery.run(_plan("A", "B", "C"))

        self.assertEqual(original.urls, [
            "https://example.test/download?InvoiceID=A&BillingPeriod=202605"
        ])
        self.assertEqual(replacement.urls[0], "https://stationsatcom.satcomhost.com")
        self.assertEqual(
            replacement.urls[1:],
            [
                "https://example.test/download?InvoiceID=B&BillingPeriod=202605",
                "https://example.test/download?InvoiceID=C&BillingPeriod=202605",
            ],
        )
        self.assertEqual(len(engines), 2)
        self.assertIs(engines[0].fault_injector, engines[1].fault_injector)
        self.assertTrue(engines[0].fault_injector.triggered)
        self.assertEqual(summary.successful_recoveries, 1)
        self.assertEqual(summary.retry_attempts, 0)


if __name__ == "__main__":
    unittest.main()
