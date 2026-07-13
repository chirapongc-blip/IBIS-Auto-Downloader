import unittest

from ibis.downloader import DownloadQueue, STATUS_PENDING
from ibis.scheduler import DownloadPlan


def _make_queue(*rows):
    """Build a DownloadQueue from (invoice_id, billing_period) tuples."""
    links = [
        {
            "url": (
                f"https://example.com/DownloadARExport.aspx"
                f"?InvoiceID={inv}&BillingPeriod={bp}&Format=Detailed"
            ),
            "invoice_id": inv,
            "billing_period": bp,
        }
        for inv, bp in rows
    ]
    return DownloadQueue.from_links(links)


class TestDownloadPlanEmptyQueue(unittest.TestCase):

    def setUp(self):
        self.plan = DownloadPlan(DownloadQueue())

    def test_scheduled_items_is_empty(self):
        self.assertEqual(self.plan.scheduled_items, [])

    def test_total_queue_items_is_zero(self):
        self.assertEqual(self.plan.total_queue_items, 0)

    def test_billing_periods_found_is_empty(self):
        self.assertEqual(self.plan.billing_periods_found, [])

    def test_latest_billing_period_is_none(self):
        self.assertIsNone(self.plan.latest_billing_period)

    def test_duplicates_removed_is_zero(self):
        self.assertEqual(self.plan.duplicates_removed, 0)

    def test_scheduled_count_is_zero(self):
        self.assertEqual(self.plan.scheduled_count, 0)

    def test_summary_keys_and_values(self):
        s = self.plan.summary()
        self.assertEqual(
            s,
            {
                "total_queue_items": 0,
                "billing_periods_found": [],
                "latest_billing_period": None,
                "duplicates_removed": 0,
                "scheduled_count": 0,
            },
        )


class TestDownloadPlanSinglePeriod(unittest.TestCase):

    def setUp(self):
        self.queue = _make_queue(
            ("1001", "202605"),
            ("1002", "202605"),
            ("1003", "202605"),
        )
        self.plan = DownloadPlan(self.queue)

    def test_total_queue_items(self):
        self.assertEqual(self.plan.total_queue_items, 3)

    def test_billing_periods_found(self):
        self.assertEqual(self.plan.billing_periods_found, ["202605"])

    def test_latest_billing_period(self):
        self.assertEqual(self.plan.latest_billing_period, "202605")

    def test_no_duplicates_removed(self):
        self.assertEqual(self.plan.duplicates_removed, 0)

    def test_scheduled_count(self):
        self.assertEqual(self.plan.scheduled_count, 3)

    def test_scheduled_items_have_correct_invoice_ids(self):
        ids = [item.invoice_id for item in self.plan.scheduled_items]
        self.assertEqual(ids, ["1001", "1002", "1003"])

    def test_scheduled_items_preserve_download_status(self):
        for item in self.plan.scheduled_items:
            self.assertEqual(item.download_status, STATUS_PENDING)


class TestDownloadPlanLatestOnlyDefault(unittest.TestCase):
    """latest_only=True (default): only the highest billing period survives."""

    def setUp(self):
        self.queue = _make_queue(
            ("1001", "202603"),
            ("1002", "202604"),
            ("1003", "202605"),
            ("1004", "202605"),
        )
        self.plan = DownloadPlan(self.queue)

    def test_total_queue_items(self):
        self.assertEqual(self.plan.total_queue_items, 4)

    def test_billing_periods_found_contains_all_periods(self):
        self.assertEqual(self.plan.billing_periods_found, ["202603", "202604", "202605"])

    def test_latest_billing_period(self):
        self.assertEqual(self.plan.latest_billing_period, "202605")

    def test_older_periods_excluded(self):
        periods = {item.billing_period for item in self.plan.scheduled_items}
        self.assertEqual(periods, {"202605"})

    def test_scheduled_count(self):
        self.assertEqual(self.plan.scheduled_count, 2)

    def test_duplicates_removed_is_zero(self):
        self.assertEqual(self.plan.duplicates_removed, 0)


class TestDownloadPlanAllPeriods(unittest.TestCase):
    """latest_only=False: all billing periods are kept."""

    def setUp(self):
        self.queue = _make_queue(
            ("1001", "202603"),
            ("1002", "202604"),
            ("1003", "202605"),
        )
        self.plan = DownloadPlan(self.queue, latest_only=False)

    def test_scheduled_count_includes_all_periods(self):
        self.assertEqual(self.plan.scheduled_count, 3)

    def test_all_billing_periods_represented(self):
        periods = {item.billing_period for item in self.plan.scheduled_items}
        self.assertEqual(periods, {"202603", "202604", "202605"})


class TestDownloadPlanDuplicateInvoices(unittest.TestCase):
    """Duplicate invoice_id entries within the kept period are dropped."""

    def setUp(self):
        self.queue = _make_queue(
            ("1001", "202605"),
            ("1001", "202605"),  # duplicate
            ("1002", "202605"),
            ("1002", "202605"),  # duplicate
            ("1003", "202605"),
        )
        self.plan = DownloadPlan(self.queue)

    def test_total_queue_items(self):
        self.assertEqual(self.plan.total_queue_items, 5)

    def test_duplicates_removed(self):
        self.assertEqual(self.plan.duplicates_removed, 2)

    def test_scheduled_count(self):
        self.assertEqual(self.plan.scheduled_count, 3)

    def test_unique_invoice_ids_in_schedule(self):
        ids = [item.invoice_id for item in self.plan.scheduled_items]
        self.assertEqual(sorted(ids), ["1001", "1002", "1003"])

    def test_first_occurrence_is_kept(self):
        first_item = self.plan.scheduled_items[0]
        self.assertEqual(first_item.invoice_id, "1001")


class TestDownloadPlanDuplicatesAcrossAllPeriods(unittest.TestCase):
    """With latest_only=False duplicates are resolved across all periods."""

    def setUp(self):
        self.queue = _make_queue(
            ("1001", "202603"),
            ("1001", "202604"),  # same invoice_id, different period
            ("1002", "202605"),
        )
        self.plan = DownloadPlan(self.queue, latest_only=False)

    def test_total_queue_items(self):
        self.assertEqual(self.plan.total_queue_items, 3)

    def test_duplicates_removed(self):
        self.assertEqual(self.plan.duplicates_removed, 1)

    def test_scheduled_count(self):
        self.assertEqual(self.plan.scheduled_count, 2)


class TestDownloadPlanSummaryStatistics(unittest.TestCase):

    def test_summary_contains_all_keys(self):
        queue = _make_queue(("1001", "202604"), ("1001", "202605"), ("1002", "202605"))
        plan = DownloadPlan(queue)
        s = plan.summary()

        self.assertIn("total_queue_items", s)
        self.assertIn("billing_periods_found", s)
        self.assertIn("latest_billing_period", s)
        self.assertIn("duplicates_removed", s)
        self.assertIn("scheduled_count", s)

    def test_summary_values_are_consistent(self):
        queue = _make_queue(("1001", "202604"), ("1001", "202605"), ("1002", "202605"))
        plan = DownloadPlan(queue)
        s = plan.summary()

        self.assertEqual(s["total_queue_items"], 3)
        self.assertEqual(s["billing_periods_found"], ["202604", "202605"])
        self.assertEqual(s["latest_billing_period"], "202605")
        self.assertEqual(s["duplicates_removed"], 0)
        self.assertEqual(s["scheduled_count"], 2)


class TestDownloadPlanNoBillingPeriod(unittest.TestCase):
    """Items with no billing_period are handled gracefully."""

    def _make_no_period_queue(self):
        links = [
            {"url": "https://example.com/DownloadARExport.aspx?InvoiceID=999&Format=Detailed"}
        ]
        return DownloadQueue.from_links(links)

    def test_item_without_period_survives_when_latest_only_false(self):
        q = self._make_no_period_queue()
        plan = DownloadPlan(q, latest_only=False)
        self.assertEqual(plan.scheduled_count, 1)
        self.assertEqual(plan.billing_periods_found, [])
        self.assertIsNone(plan.latest_billing_period)

    def test_item_without_period_excluded_when_latest_only_and_no_periods(self):
        # When there are no known periods and latest_only is True the queue has
        # no "latest period" so nothing is selected from a period bucket; however
        # the implementation falls back to all items when no period exists.
        q = self._make_no_period_queue()
        plan = DownloadPlan(q, latest_only=True)
        # No known period → latest_billing_period is None → fallback: all items kept
        self.assertEqual(plan.scheduled_count, 1)

    def test_item_without_period_excluded_when_latest_only_and_period_exists(self):
        """A no-period item is excluded when latest_only drops older periods."""
        links = [
            {
                "url": "https://example.com/DownloadARExport.aspx?InvoiceID=999&Format=Detailed",
                "invoice_id": "999",
            },
            {
                "url": "https://example.com/DownloadARExport.aspx?InvoiceID=1001&BillingPeriod=202605&Format=Detailed",
                "invoice_id": "1001",
                "billing_period": "202605",
            },
        ]
        q = DownloadQueue.from_links(links)
        plan = DownloadPlan(q)
        ids = [item.invoice_id for item in plan.scheduled_items]
        self.assertNotIn("999", ids)
        self.assertIn("1001", ids)


class TestDownloadPlanDoesNotMutateQueue(unittest.TestCase):
    """Building a plan must not alter the original queue."""

    def test_original_queue_items_unchanged(self):
        queue = _make_queue(("1001", "202604"), ("1001", "202605"), ("1002", "202605"))
        original_len = len(queue)
        original_ids = [item.invoice_id for item in queue]

        DownloadPlan(queue)

        self.assertEqual(len(queue), original_len)
        self.assertEqual([item.invoice_id for item in queue], original_ids)


class TestDownloadPlanScheduledItemsIsCopy(unittest.TestCase):
    """Mutating the returned scheduled_items list must not affect the plan."""

    def test_scheduled_items_returns_new_list_each_call(self):
        queue = _make_queue(("1001", "202605"))
        plan = DownloadPlan(queue)
        items1 = plan.scheduled_items
        items2 = plan.scheduled_items
        self.assertIsNot(items1, items2)

    def test_mutating_returned_list_does_not_affect_plan(self):
        queue = _make_queue(("1001", "202605"), ("1002", "202605"))
        plan = DownloadPlan(queue)
        items = plan.scheduled_items
        items.clear()
        self.assertEqual(plan.scheduled_count, 2)


if __name__ == "__main__":
    unittest.main()
