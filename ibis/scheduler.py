import calendar
import time
from collections import defaultdict
from datetime import datetime, timedelta, timezone

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


class Scheduler:
    """Lightweight scheduler that wraps the Build 2.5 download workflow.

    Parameters
    ----------
    workflow : callable
        A zero-argument callable that executes the full download workflow.
        The scheduler never modifies ``DownloaderEngine``, ``StateManager``,
        or ``PeriodTracker`` — those are entirely owned by *workflow*.
    interval : timedelta | None
        Legacy interval-based scheduling.  When set it takes precedence over
        *mode*.  ``None`` (the default) delegates to the *mode* parameter.
    run_immediately : bool
        When *True* (the default) ``should_run()`` returns ``True`` as soon
        as the scheduler is created, so the first run happens without delay.
        When *False* the first run is deferred to the next calculated
        schedule time.
    mode : str
        Scheduling mode used when *interval* is ``None``.
        ``"immediate"`` – one-shot: run once then stop.
        ``"daily"``     – repeat every day at *schedule_hour*:*schedule_minute* UTC.
        ``"monthly"``   – repeat on *schedule_day* of every month at
                          *schedule_hour*:*schedule_minute* UTC.
    schedule_day : int
        Day-of-month (1–31) for ``"monthly"`` mode.  Clamped to the last
        valid day of the target month when the configured day does not exist.
    schedule_hour : int
        Hour (0–23) in UTC at which the job fires (``"daily"``/``"monthly"``).
    schedule_minute : int
        Minute (0–59) in UTC at which the job fires (``"daily"``/``"monthly"``).
    """

    def __init__(
        self,
        workflow,
        *,
        interval=None,
        run_immediately=True,
        mode="immediate",
        schedule_day=1,
        schedule_hour=0,
        schedule_minute=0,
    ):
        self._workflow = workflow
        self._interval = interval
        self._mode = mode
        self._schedule_day = schedule_day
        self._schedule_hour = schedule_hour
        self._schedule_minute = schedule_minute
        self._run_count = 0
        now = datetime.now(tz=timezone.utc)
        self._next_run: datetime = now if run_immediately else self._advance(now)

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    def should_run(self) -> bool:
        """Return *True* if it is time (or past time) to execute the workflow."""
        return datetime.now(tz=timezone.utc) >= self._next_run

    def should_run_now(self) -> bool:
        """Alias for :meth:`should_run` — return *True* if it is time to run."""
        return self.should_run()

    def next_run(self) -> datetime:
        """Return the UTC datetime at which the next run is (or was) scheduled."""
        return self._next_run

    def calculate_next_run(self, from_time: datetime = None) -> datetime:
        """Return the next scheduled run time after *from_time*.

        Parameters
        ----------
        from_time : datetime | None
            The reference point in time.  Defaults to ``datetime.now(UTC)``
            when *None*.
        """
        if from_time is None:
            from_time = datetime.now(tz=timezone.utc)
        return self._advance(from_time)

    def run_once(self):
        """Execute the workflow exactly once and update the schedule."""
        self._workflow()
        self._run_count += 1
        self._next_run = self._advance(datetime.now(tz=timezone.utc))

    def wait_until_next_run(self) -> None:
        """Sleep until the next scheduled run time.

        Returns immediately when operating in one-shot / immediate mode
        (i.e. when *next_run* is ``datetime.max``).
        """
        if self._next_run.replace(tzinfo=None) == datetime.max:
            return
        now = datetime.now(tz=timezone.utc)
        delay = (self._next_run - now).total_seconds()
        if delay > 0:
            time.sleep(delay)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _advance(self, from_time: datetime) -> datetime:
        """Return the next scheduled time relative to *from_time*."""
        if self._interval is not None:
            return from_time + self._interval

        if self._mode == "daily":
            return self._next_daily(from_time)

        if self._mode == "monthly":
            return self._next_monthly(from_time)

        # "immediate" or unknown mode → one-shot
        return datetime.max.replace(tzinfo=timezone.utc)

    def _next_daily(self, from_time: datetime) -> datetime:
        """Return the next daily occurrence of *schedule_hour*:*schedule_minute* UTC."""
        candidate = from_time.replace(
            hour=self._schedule_hour,
            minute=self._schedule_minute,
            second=0,
            microsecond=0,
        )
        if candidate <= from_time:
            candidate += timedelta(days=1)
        return candidate

    def _next_monthly(self, from_time: datetime) -> datetime:
        """Return the next monthly occurrence, clamping to valid month lengths."""
        year = from_time.year
        month = from_time.month
        day = min(self._schedule_day, calendar.monthrange(year, month)[1])
        candidate = from_time.replace(
            day=day,
            hour=self._schedule_hour,
            minute=self._schedule_minute,
            second=0,
            microsecond=0,
        )
        if candidate <= from_time:
            # Advance to next month
            if month == 12:
                year, month = year + 1, 1
            else:
                month += 1
            day = min(self._schedule_day, calendar.monthrange(year, month)[1])
            candidate = from_time.replace(
                year=year,
                month=month,
                day=day,
                hour=self._schedule_hour,
                minute=self._schedule_minute,
                second=0,
                microsecond=0,
            )
        return candidate
