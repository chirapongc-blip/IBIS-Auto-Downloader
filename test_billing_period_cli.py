"""Focused unit tests for Billing Period CLI selection and scoping."""

import argparse
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import MagicMock

import main
from ibis.auto_recovery import AutoRecovery
from ibis.billing_period import BillingPeriodManager
from ibis.downloader import DownloadQueue
from ibis.resume import build_resume_queue
from ibis.scheduler import DownloadPlan
from ibis.state import DownloadState


def _link(invoice_id, period):
    return {
        "url": (
            "https://example.test/DownloadARExport.aspx?"
            f"InvoiceID={invoice_id}&BillingPeriod={period}&Format=Detailed"
        ),
        "invoice_id": invoice_id,
        "billing_period": period,
    }


def _state_item(invoice_id, period):
    return {
        "invoice_id": invoice_id,
        "billing_period": period,
        "download_url": _link(invoice_id, period)["url"],
        "filename": None,
        "download_status": "pending",
        "retry_count": 0,
        "last_error": None,
    }


class BillingPeriodCliTests(unittest.TestCase):
    def setUp(self):
        self.links = [
            _link("100", "202606"),
            _link("101", "202605"),
            _link("102", "202604"),
        ]
        self.manager = BillingPeriodManager(MagicMock(), links=self.links)

    def test_default_selection_uses_latest_period(self):
        self.assertEqual(main._resolve_available_periods(self.manager, None), ["202606"])

    def test_explicit_latest_selection_uses_latest_period(self):
        args = main.parse_cli_args(["--billing-period", "latest"])
        self.assertEqual(args.billing_period, "latest")
        self.assertEqual(
            main._resolve_available_periods(self.manager, args.billing_period), ["202606"]
        )

    def test_one_valid_period_is_selected(self):
        args = main.parse_cli_args(["--billing-period", "202605"])
        self.assertEqual(args.billing_period, ("202605",))
        self.assertEqual(
            main._resolve_available_periods(self.manager, args.billing_period), ["202605"]
        )

    def test_multiple_valid_periods_are_selected_newest_first(self):
        args = main.parse_cli_args(["--billing-period", "202604,202606"])
        self.assertEqual(
            main._resolve_available_periods(self.manager, args.billing_period),
            ["202606", "202604"],
        )

    def test_all_periods_are_selected(self):
        args = main.parse_cli_args(["--billing-period", "all"])
        self.assertEqual(
            main._resolve_available_periods(self.manager, args.billing_period),
            ["202606", "202605", "202604"],
        )

    def test_whitespace_and_duplicates_are_normalized(self):
        args = main.parse_cli_args(
            ["--billing-period", " 202605, 202604 , 202605 "]
        )
        self.assertEqual(args.billing_period, ("202605", "202604"))

    def test_malformed_period_is_rejected(self):
        for value in ("20265", "2026050", "2026AA", "202605,,202604"):
            with self.subTest(value=value):
                with self.assertRaises(argparse.ArgumentTypeError):
                    main.normalize_billing_periods(value)

    def test_unavailable_period_displays_available_periods(self):
        with self.assertRaisesRegex(
            ValueError, "202601.*Available periods: 202606, 202605, 202604"
        ):
            main._resolve_available_periods(self.manager, ("202601",))

    def test_queue_is_filtered_before_construction(self):
        selected = main._filter_links_for_periods(self.links, ["202605", "202604"])
        self.assertEqual(
            [(link["invoice_id"], link["billing_period"]) for link in selected],
            [("101", "202605"), ("102", "202604")],
        )

    def test_same_invoice_id_in_different_periods_remains_distinct(self):
        queue = DownloadQueue.from_links([_link("100", "202606"), _link("100", "202605")])
        plan = DownloadPlan(queue, latest_only=False)
        self.assertEqual(
            [(item.invoice_id, item.billing_period) for item in plan.scheduled_items],
            [("100", "202606"), ("100", "202605")],
        )


class BillingPeriodResumeAndRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = TemporaryDirectory()
        self.state_path = Path(self.temp_dir.name) / "download_state.json"
        self.items = [_state_item("100", "202605"), _state_item("101", "202604")]

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_resume_queue_is_separated_by_billing_period(self):
        state = {
            "selected_periods": ["202605"],
            "queue": self.items,
            "completed": [],
            "failed": [],
        }
        queue = build_resume_queue(state)
        self.assertEqual(
            [(item.invoice_id, item.billing_period) for item in queue],
            [("100", "202605")],
        )

    def test_recovery_rebuild_retains_original_selected_periods(self):
        state = DownloadState(self.state_path, selected_periods=["202605"])
        state.initialize(self.items)
        recovery = AutoRecovery(
            driver_factory=MagicMock(),
            login_fn=MagicMock(),
            open_invoice_fn=MagicMock(),
            download_state=state,
            engine_factory=MagicMock(),
        )

        plan = recovery._rebuild_plan()

        self.assertEqual(
            [(item.invoice_id, item.billing_period) for item in plan.scheduled_items],
            [("100", "202605")],
        )
        self.assertEqual(state.load_state()["selected_periods"], ["202605"])


if __name__ == "__main__":
    unittest.main()
