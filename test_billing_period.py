import unittest
from unittest.mock import MagicMock, patch

from ibis.billing_period import (
    BillingPeriodManager,
    BillingPeriodNotFoundError,
)


def _link(invoice_id, period):
    return {
        "url": (
            "https://example.test/DownloadARExport.aspx?"
            f"InvoiceID={invoice_id}&BillingPeriod={period}&Format=Detailed"
        )
    }


class BillingPeriodManagerTests(unittest.TestCase):
    def setUp(self):
        self.driver = MagicMock()

    def _manager(self, links):
        patcher = patch(
            "ibis.billing_period.collect_grid_download_links",
            return_value=links,
        )
        self.addCleanup(patcher.stop)
        self.collect_links = patcher.start()
        return BillingPeriodManager(self.driver, base_url="https://example.test")

    def test_get_periods_discovers_unique_periods_newest_first(self):
        manager = self._manager(
            [_link("1", "202604"), _link("2", "202606"), _link("3", "202605"), _link("4", "202606")]
        )

        self.assertEqual(manager.get_periods(), ["202606", "202605", "202604"])
        self.collect_links.assert_called_once_with(self.driver, "https://example.test")

    def test_get_periods_returns_a_copy_of_cached_periods(self):
        manager = self._manager([_link("1", "202605")])

        periods = manager.get_periods()
        periods.append("not-a-period")

        self.assertEqual(manager.get_periods(), ["202605"])
        self.collect_links.assert_called_once()

    def test_get_periods_refreshes_when_requested(self):
        manager = self._manager([_link("1", "202605")])
        self.assertEqual(manager.get_periods(), ["202605"])
        self.collect_links.return_value = [_link("2", "202606")]

        self.assertEqual(manager.get_periods(refresh=True), ["202606"])
        self.assertEqual(self.collect_links.call_count, 2)

    def test_get_periods_returns_empty_list_when_page_has_no_export_links(self):
        manager = self._manager([])

        self.assertEqual(manager.get_periods(), [])

    def test_latest_returns_newest_period(self):
        manager = self._manager([_link("1", "202604"), _link("2", "202606")])

        self.assertEqual(manager.latest(), "202606")

    def test_latest_returns_none_when_no_periods_exist(self):
        manager = self._manager([])

        self.assertIsNone(manager.latest())

    def test_exists_checks_discovered_periods(self):
        manager = self._manager([_link("1", "202605")])

        self.assertTrue(manager.exists("202605"))
        self.assertTrue(manager.exists(202605))
        self.assertFalse(manager.exists("202604"))
        self.assertFalse(manager.exists(None))

    def test_select_records_an_existing_period_without_browser_navigation(self):
        manager = self._manager([_link("1", "202605"), _link("2", "202604")])

        self.assertEqual(manager.select("202604"), "202604")
        self.assertEqual(manager.selected_period, "202604")
        self.driver.get.assert_not_called()

    def test_select_raises_clear_exception_for_missing_period(self):
        manager = self._manager([_link("1", "202605")])

        with self.assertRaisesRegex(
            BillingPeriodNotFoundError,
            "Billing period '202604' does not exist.*Available periods: 202605",
        ):
            manager.select("202604")

    def test_select_raises_clear_exception_when_no_periods_exist(self):
        manager = self._manager([])

        with self.assertRaisesRegex(
            BillingPeriodNotFoundError,
            "Billing period '' does not exist.*Available periods: none",
        ):
            manager.select(None)


if __name__ == "__main__":
    unittest.main()
