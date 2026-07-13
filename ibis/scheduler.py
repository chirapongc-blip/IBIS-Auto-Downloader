from collections import defaultdict

from ibis.downloader import DownloadQueue


class DownloadPlan:
    """
    Build a deduplicated, period-filtered download schedule from a DownloadQueue.

    Parameters
    ----------
    queue : DownloadQueue
        The source queue produced by Build 2.1b.
    latest_only : bool
        When *True* (the default) keep only items belonging to the highest
        (most recent) billing_period.  When *False* keep all billing periods.
    """

    def __init__(self, queue: DownloadQueue, *, latest_only: bool = True):
        self._build(queue, latest_only)

    # ------------------------------------------------------------------
    # Public read-only properties
    # ------------------------------------------------------------------

    @property
    def scheduled_items(self):
        """List of DownloadQueueItems that survived filtering."""
        return list(self._scheduled_items)

    @property
    def total_queue_items(self) -> int:
        """Total number of items in the original queue."""
        return self._total_queue_items

    @property
    def billing_periods_found(self) -> list[str]:
        """Sorted list of all billing periods found in the queue."""
        return list(self._billing_periods_found)

    @property
    def latest_billing_period(self) -> str | None:
        """The highest billing period string, or *None* if the queue was empty."""
        return self._latest_billing_period

    @property
    def duplicates_removed(self) -> int:
        """Number of duplicate invoice entries that were dropped."""
        return self._duplicates_removed

    @property
    def scheduled_count(self) -> int:
        """Number of items in the final download schedule."""
        return len(self._scheduled_items)

    def summary(self) -> dict:
        """Return all statistics as a plain dictionary."""
        return {
            "total_queue_items": self.total_queue_items,
            "billing_periods_found": self.billing_periods_found,
            "latest_billing_period": self.latest_billing_period,
            "duplicates_removed": self.duplicates_removed,
            "scheduled_count": self.scheduled_count,
        }

    # ------------------------------------------------------------------
    # Internal build logic
    # ------------------------------------------------------------------

    def _build(self, queue: DownloadQueue, latest_only: bool) -> None:
        all_items = list(queue)
        self._total_queue_items = len(all_items)

        # Group by billing_period (None sorts last via a sentinel)
        by_period: dict[str | None, list] = defaultdict(list)
        for item in all_items:
            by_period[item.billing_period].append(item)

        known_periods = sorted(
            (p for p in by_period if p is not None),
        )
        self._billing_periods_found = known_periods
        self._latest_billing_period = known_periods[-1] if known_periods else None

        # Decide which periods to keep
        if latest_only and self._latest_billing_period is not None:
            candidate_items = by_period[self._latest_billing_period]
        else:
            candidate_items = all_items

        # Deduplicate by invoice_id (keep first occurrence; None ids are kept)
        seen_ids: set[str] = set()
        scheduled = []
        duplicates = 0
        for item in candidate_items:
            if item.invoice_id is not None:
                if item.invoice_id in seen_ids:
                    duplicates += 1
                    continue
                seen_ids.add(item.invoice_id)
            scheduled.append(item)

        self._scheduled_items = scheduled
        self._duplicates_removed = duplicates
