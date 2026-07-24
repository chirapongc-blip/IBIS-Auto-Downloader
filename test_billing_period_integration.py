"""Fixture-driven integration tests for Sprint 4.2 billing-period selection."""

from collections import Counter
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import MagicMock, mock_open, patch

import main
from ibis.auto_recovery import AutoRecovery, RecoverySummary
from ibis.downloader import DownloadQueue
from ibis.grid import GRID_ID
from ibis.state import DownloadState
from ibis.state_manager import StateManager


FIXTURE_DIR = Path(__file__).parent / "fixtures" / "invoice_grid_billing_period"


class ImmediateWait:
    """A synchronous WebDriverWait replacement for fixture pagination."""

    def __init__(self, driver, timeout):
        self.driver = driver

    def until(self, condition):
        result = condition(self.driver)
        if not result:
            raise AssertionError("fixture grid wait did not complete")
        return result


class FixtureGridDriver:
    """WebDriver subset which pages through captured Invoice-grid fixtures."""

    def __init__(self):
        self.pages = [
            (FIXTURE_DIR / f"page_{number}.html").read_text(encoding="utf-8")
            for number in (1, 2, 3)
        ]
        self.page_index = 0
        self.visited_urls = []
        self.quit_called = False

    @property
    def page_source(self):
        return self.pages[self.page_index]

    def get(self, url):
        self.visited_urls.append(url)

    def find_elements(self, _by, selector):
        if "GVPagerOnClick" in selector and self.page_index < len(self.pages) - 1:
            return [FixturePagerElement()]
        return []

    def execute_script(self, script, *_args):
        if "GVPagerOnClick" in script:
            self.page_index += 1

    def quit(self):
        self.quit_called = True


class FixturePagerElement:
    def get_attribute(self, name):
        if name == "onclick":
            return f"ASPx.GVPagerOnClick('{GRID_ID}','PBN');"
        return None


class CapturingAutoRecovery:
    """Keeps the production main/scheduler/discovery path while avoiding I/O."""

    instances = []

    def __init__(self, **_kwargs):
        self.plan = None
        type(self).instances.append(self)

    def run(self, plan):
        self.plan = plan
        return RecoverySummary()


def _period_counts(plan):
    return Counter(item.billing_period for item in plan.scheduled_items)


class BillingPeriodMainFlowIntegrationTests(unittest.TestCase):
    """Exercise CLI → Scheduler → main discovery → queue → DownloadPlan."""

    def setUp(self):
        self.temp_dir = TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        CapturingAutoRecovery.instances = []

    def _run_main(self, args=None):
        state_dir = Path(self.temp_dir.name)
        driver = FixtureGridDriver()
        with patch("main.create_driver", return_value=driver), \
             patch("main.wait_until_logged_in"), \
             patch("main.open_invoice_page", return_value=driver.page_source), \
             patch("main.wait_for_grid"), \
             patch("ibis.grid_walker.wait_for_grid"), \
             patch("ibis.grid_walker.WebDriverWait", ImmediateWait), \
             patch("main.count_grid_rows", return_value=5), \
             patch("main.get_grid_text", return_value="fixture grid"), \
             patch("main.get_devexpress_pager_info", return_value={}), \
             patch("builtins.open", mock_open()), \
             patch("main.StateManager", side_effect=lambda: StateManager(
                 state_file=state_dir / "completed.json",
                 download_dir=state_dir / "downloads",
             )), \
             patch("main.DownloadState", side_effect=lambda: DownloadState(
                 state_file=state_dir / "download_state.json"
             )), \
             patch("main.PeriodTracker", return_value=MagicMock()), \
             patch("main.AutoRecovery", CapturingAutoRecovery):
            main.main(args)

        self.assertTrue(CapturingAutoRecovery.instances)
        return CapturingAutoRecovery.instances[-1].plan

    def test_default_uses_only_fixture_latest_period(self):
        plan = self._run_main()
        self.assertEqual(_period_counts(plan), {"202605": 2})

    def test_explicit_latest_matches_default_queue(self):
        default_plan = self._run_main()
        explicit_plan = self._run_main(["--billing-period", "latest"])
        self.assertEqual(
            [(item.invoice_id, item.billing_period) for item in explicit_plan.scheduled_items],
            [(item.invoice_id, item.billing_period) for item in default_plan.scheduled_items],
        )

    def test_one_older_period_excludes_every_other_period(self):
        plan = self._run_main(["--billing-period", "202604"])
        self.assertEqual(_period_counts(plan), {"202604": 2})

    def test_two_selected_periods_preserve_cross_period_invoice_identity(self):
        plan = self._run_main(["--billing-period", "202605,202604"])
        self.assertEqual(_period_counts(plan), {"202605": 2, "202604": 2})
        self.assertIn(("700", "202605"), [
            (item.invoice_id, item.billing_period) for item in plan.scheduled_items
        ])
        self.assertIn(("700", "202604"), [
            (item.invoice_id, item.billing_period) for item in plan.scheduled_items
        ])

    def test_all_selects_all_fixture_periods(self):
        plan = self._run_main(["--billing-period", "all"])
        self.assertEqual(_period_counts(plan), {"202605": 2, "202604": 2, "202603": 1})

    def test_unavailable_period_reports_fixture_period_choices(self):
        with self.assertRaisesRegex(
            ValueError, "202602.*Available periods: 202605, 202604, 202603"
        ):
            self._run_main(["--billing-period", "202602"])

    def test_malformed_period_is_rejected_before_application_startup(self):
        with self.assertRaises(SystemExit):
            self._run_main(["--billing-period", "20260"])


class BillingPeriodRecoveryScopeIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)

    def test_recovery_rebuilds_only_remaining_original_selected_periods(self):
        state = DownloadState(
            Path(self.temp_dir.name) / "download_state.json",
            selected_periods=["202605", "202604"],
        )
        queue = DownloadQueue.from_links([
            {"url": "https://example.test/dl?InvoiceID=700&BillingPeriod=202605"},
            {"url": "https://example.test/dl?InvoiceID=701&BillingPeriod=202605"},
            {"url": "https://example.test/dl?InvoiceID=700&BillingPeriod=202604"},
            {"url": "https://example.test/dl?InvoiceID=702&BillingPeriod=202604"},
            {"url": "https://example.test/dl?InvoiceID=800&BillingPeriod=202603"},
        ])
        state.initialize(queue.items)
        state.mark_completed(queue.items[0])

        recovery = AutoRecovery(
            driver_factory=MagicMock(),
            login_fn=MagicMock(),
            open_invoice_fn=MagicMock(),
            download_state=state,
            engine_factory=MagicMock(),
        )
        rebuilt = recovery._rebuild_plan()

        self.assertEqual(
            [(item.invoice_id, item.billing_period) for item in rebuilt.scheduled_items],
            [("701", "202605"), ("700", "202604"), ("702", "202604")],
        )
        self.assertNotIn("202603", _period_counts(rebuilt))


if __name__ == "__main__":
    unittest.main()
