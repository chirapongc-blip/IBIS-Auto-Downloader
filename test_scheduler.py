import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, call, patch

from ibis.scheduler import Scheduler


def _utc_now():
    return datetime.now(tz=timezone.utc)


def _dt(year, month, day, hour=0, minute=0, second=0):
    """Convenience helper: UTC datetime."""
    return datetime(year, month, day, hour, minute, second, tzinfo=timezone.utc)


class TestSchedulerShouldRunImmediately(unittest.TestCase):
    """By default the scheduler is ready to run as soon as it is created."""

    def test_should_run_returns_true_immediately(self):
        s = Scheduler(MagicMock())
        self.assertTrue(s.should_run())

    def test_next_run_is_in_the_past_or_now(self):
        before = _utc_now()
        s = Scheduler(MagicMock())
        self.assertLessEqual(s.next_run(), before + timedelta(seconds=1))


class TestSchedulerRunImmediatelyFalse(unittest.TestCase):
    """When run_immediately=False the scheduler is not ready until interval elapses."""

    def test_should_run_false_when_run_immediately_is_false_and_no_interval(self):
        # No interval + run_immediately=False → next_run is datetime.max,
        # so should_run() is False.
        s = Scheduler(MagicMock(), run_immediately=False)
        self.assertFalse(s.should_run())

    def test_should_run_false_when_interval_not_elapsed(self):
        s = Scheduler(MagicMock(), interval=timedelta(hours=24), run_immediately=False)
        self.assertFalse(s.should_run())


class TestSchedulerRunOnce(unittest.TestCase):
    """run_once() calls the workflow and updates internal state."""

    def test_run_once_calls_workflow(self):
        workflow = MagicMock()
        s = Scheduler(workflow)
        s.run_once()
        workflow.assert_called_once()

    def test_run_once_calls_workflow_multiple_times(self):
        workflow = MagicMock()
        s = Scheduler(workflow, interval=timedelta(seconds=1))
        s.run_once()
        s.run_once()
        self.assertEqual(workflow.call_count, 2)

    def test_run_once_advances_next_run_in_interval_mode(self):
        workflow = MagicMock()
        interval = timedelta(minutes=30)
        s = Scheduler(workflow, interval=interval)
        before = _utc_now()
        s.run_once()
        after = _utc_now()
        # next_run should be roughly before + interval (allow 2 s of test latency)
        self.assertGreaterEqual(s.next_run(), before + interval - timedelta(seconds=2))
        self.assertLessEqual(s.next_run(), after + interval + timedelta(seconds=2))

    def test_run_once_disables_subsequent_should_run_in_one_shot_mode(self):
        """After one run with no interval, should_run() becomes False."""
        workflow = MagicMock()
        s = Scheduler(workflow)
        self.assertTrue(s.should_run())
        s.run_once()
        self.assertFalse(s.should_run())

    def test_run_once_delegates_to_workflow_without_arguments(self):
        received_args = []

        def workflow(*args, **kwargs):
            received_args.append((args, kwargs))

        s = Scheduler(workflow)
        s.run_once()
        self.assertEqual(received_args, [((), {})])


class TestSchedulerIntervalMode(unittest.TestCase):
    """Scheduler re-enables should_run() after the interval elapses."""

    def test_should_run_true_after_interval_elapses(self):
        workflow = MagicMock()
        s = Scheduler(workflow, interval=timedelta(milliseconds=1))
        s.run_once()
        # Wait longer than the 1 ms interval
        import time
        time.sleep(0.05)
        self.assertTrue(s.should_run())

    def test_should_run_false_immediately_after_run_once_with_long_interval(self):
        workflow = MagicMock()
        s = Scheduler(workflow, interval=timedelta(hours=1))
        s.run_once()
        self.assertFalse(s.should_run())


class TestSchedulerNextRun(unittest.TestCase):
    """next_run() returns a timezone-aware UTC datetime."""

    def test_next_run_is_timezone_aware(self):
        s = Scheduler(MagicMock())
        self.assertIsNotNone(s.next_run().tzinfo)

    def test_next_run_after_run_once_in_one_shot_mode(self):
        s = Scheduler(MagicMock())
        s.run_once()
        # One-shot mode → next_run should be datetime.max (effectively never)
        self.assertEqual(
            s.next_run().replace(tzinfo=None),
            datetime.max,
        )


class TestSchedulerWorkflowIsolation(unittest.TestCase):
    """Scheduler must not inspect or modify DownloaderEngine/StateManager/PeriodTracker."""

    def test_scheduler_accepts_any_callable(self):
        """Workflow can be any callable; scheduler does not introspect it."""
        call_log = []

        class FakeWorkflow:
            def __call__(self):
                call_log.append("ran")

        s = Scheduler(FakeWorkflow())
        s.run_once()
        self.assertEqual(call_log, ["ran"])

    def test_workflow_exception_propagates(self):
        def bad_workflow():
            raise RuntimeError("workflow failed")

        s = Scheduler(bad_workflow)
        with self.assertRaises(RuntimeError):
            s.run_once()


# ─────────────────────────────────────────────────────────────────────────────
# Build 2.7 tests: mode-based scheduling
# ─────────────────────────────────────────────────────────────────────────────


class TestModeImmediate(unittest.TestCase):
    """mode='immediate' is one-shot: runs right away then stops."""

    def test_should_run_now_returns_true_initially(self):
        s = Scheduler(MagicMock(), mode="immediate")
        self.assertTrue(s.should_run_now())

    def test_after_run_once_should_run_now_is_false(self):
        s = Scheduler(MagicMock(), mode="immediate")
        s.run_once()
        self.assertFalse(s.should_run_now())

    def test_calculate_next_run_returns_datetime_max(self):
        s = Scheduler(MagicMock(), mode="immediate")
        s.run_once()
        self.assertEqual(s.next_run().replace(tzinfo=None), datetime.max)

    def test_calculate_next_run_method_returns_datetime_max(self):
        s = Scheduler(MagicMock(), mode="immediate")
        result = s.calculate_next_run(_dt(2025, 6, 15, 10, 30))
        self.assertEqual(result.replace(tzinfo=None), datetime.max)

    def test_wait_until_next_run_returns_immediately_in_one_shot_mode(self):
        s = Scheduler(MagicMock(), mode="immediate")
        s.run_once()
        # Should not block — datetime.max sentinel is detected
        s.wait_until_next_run()

    def test_workflow_called_once(self):
        wf = MagicMock()
        s = Scheduler(wf, mode="immediate")
        s.run_once()
        wf.assert_called_once()


class TestModeDaily(unittest.TestCase):
    """mode='daily' fires at a fixed hour:minute every day."""

    def _sched(self, hour=8, minute=30, **kw):
        return Scheduler(
            MagicMock(),
            mode="daily",
            schedule_hour=hour,
            schedule_minute=minute,
            **kw,
        )

    # calculate_next_run ---------------------------------------------------

    def test_next_run_same_day_before_schedule(self):
        s = self._sched(hour=8, minute=0)
        # 07:59 → next run is today at 08:00
        from_time = _dt(2025, 6, 15, 7, 59)
        result = s.calculate_next_run(from_time)
        self.assertEqual(result, _dt(2025, 6, 15, 8, 0))

    def test_next_run_next_day_when_past_schedule(self):
        s = self._sched(hour=8, minute=0)
        # 09:00 → already past today's 08:00, so next is tomorrow
        from_time = _dt(2025, 6, 15, 9, 0)
        result = s.calculate_next_run(from_time)
        self.assertEqual(result, _dt(2025, 6, 16, 8, 0))

    def test_next_run_advances_to_next_day_at_exact_time(self):
        # Exactly at schedule time: candidate == from_time → advance one day
        s = self._sched(hour=8, minute=0)
        from_time = _dt(2025, 6, 15, 8, 0)
        result = s.calculate_next_run(from_time)
        self.assertEqual(result, _dt(2025, 6, 16, 8, 0))

    def test_next_run_midnight_schedule(self):
        s = self._sched(hour=0, minute=0)
        from_time = _dt(2025, 6, 15, 0, 1)
        result = s.calculate_next_run(from_time)
        self.assertEqual(result, _dt(2025, 6, 16, 0, 0))

    def test_next_run_month_boundary(self):
        s = self._sched(hour=12, minute=0)
        from_time = _dt(2025, 6, 30, 13, 0)
        result = s.calculate_next_run(from_time)
        self.assertEqual(result, _dt(2025, 7, 1, 12, 0))

    def test_next_run_year_boundary(self):
        s = self._sched(hour=23, minute=59)
        from_time = _dt(2024, 12, 31, 23, 59)
        result = s.calculate_next_run(from_time)
        self.assertEqual(result, _dt(2025, 1, 1, 23, 59))

    # run_once / should_run_now --------------------------------------------

    def test_should_run_now_false_when_run_immediately_false(self):
        s = self._sched(hour=8, minute=0, run_immediately=False)
        # next_run is in the future (daily schedule), so should_run_now is False
        self.assertFalse(s.should_run_now())

    def test_run_once_advances_next_run_to_daily_slot(self):
        s = self._sched(hour=8, minute=0, run_immediately=True)
        s.run_once()
        # After run, next_run must be a future 08:00
        nr = s.next_run()
        self.assertEqual(nr.hour, 8)
        self.assertEqual(nr.minute, 0)
        self.assertEqual(nr.second, 0)
        self.assertGreater(nr, datetime.now(tz=timezone.utc))

    def test_calculate_next_run_is_timezone_aware(self):
        s = self._sched()
        result = s.calculate_next_run(_dt(2025, 3, 10, 7, 0))
        self.assertIsNotNone(result.tzinfo)

    # wait_until_next_run --------------------------------------------------

    def test_wait_until_next_run_sleeps_for_correct_duration(self):
        s = self._sched(hour=8, minute=0, run_immediately=False)
        with patch("ibis.scheduler.time.sleep") as mock_sleep:
            # Force _next_run to a known future time so sleep is positive
            future = datetime.now(tz=timezone.utc) + timedelta(seconds=60)
            s._next_run = future
            s.wait_until_next_run()
            mock_sleep.assert_called_once()
            sleep_arg = mock_sleep.call_args[0][0]
            self.assertGreater(sleep_arg, 0)
            self.assertLessEqual(sleep_arg, 60)

    def test_wait_until_next_run_no_sleep_when_overdue(self):
        s = self._sched()
        with patch("ibis.scheduler.time.sleep") as mock_sleep:
            # next_run already in the past
            s._next_run = datetime.now(tz=timezone.utc) - timedelta(seconds=10)
            s.wait_until_next_run()
            mock_sleep.assert_not_called()


class TestModeMonthly(unittest.TestCase):
    """mode='monthly' fires on a specific day of each month."""

    def _sched(self, day=1, hour=8, minute=0, **kw):
        return Scheduler(
            MagicMock(),
            mode="monthly",
            schedule_day=day,
            schedule_hour=hour,
            schedule_minute=minute,
            **kw,
        )

    # calculate_next_run – normal cases ------------------------------------

    def test_next_run_same_month_before_day(self):
        s = self._sched(day=15, hour=10, minute=0)
        from_time = _dt(2025, 6, 10, 9, 0)
        result = s.calculate_next_run(from_time)
        self.assertEqual(result, _dt(2025, 6, 15, 10, 0))

    def test_next_run_next_month_after_day(self):
        s = self._sched(day=10, hour=10, minute=0)
        from_time = _dt(2025, 6, 15, 9, 0)
        result = s.calculate_next_run(from_time)
        self.assertEqual(result, _dt(2025, 7, 10, 10, 0))

    def test_next_run_same_month_before_time_on_day(self):
        s = self._sched(day=15, hour=10, minute=0)
        from_time = _dt(2025, 6, 15, 9, 59)
        result = s.calculate_next_run(from_time)
        self.assertEqual(result, _dt(2025, 6, 15, 10, 0))

    def test_next_run_next_month_after_time_on_day(self):
        s = self._sched(day=15, hour=10, minute=0)
        from_time = _dt(2025, 6, 15, 10, 1)
        result = s.calculate_next_run(from_time)
        self.assertEqual(result, _dt(2025, 7, 15, 10, 0))

    def test_next_run_at_exact_schedule_time_advances_month(self):
        s = self._sched(day=15, hour=10, minute=0)
        from_time = _dt(2025, 6, 15, 10, 0)
        result = s.calculate_next_run(from_time)
        self.assertEqual(result, _dt(2025, 7, 15, 10, 0))

    def test_next_run_december_wraps_to_january(self):
        s = self._sched(day=20, hour=8, minute=0)
        from_time = _dt(2025, 12, 25, 8, 0)
        result = s.calculate_next_run(from_time)
        self.assertEqual(result, _dt(2026, 1, 20, 8, 0))

    def test_next_run_first_day_of_month(self):
        s = self._sched(day=1, hour=0, minute=0)
        from_time = _dt(2025, 6, 1, 0, 1)
        result = s.calculate_next_run(from_time)
        self.assertEqual(result, _dt(2025, 7, 1, 0, 0))

    # Edge cases: month length --------------------------------------------

    def test_day_31_in_april_clamps_to_30(self):
        # April has 30 days; schedule_day=31 → clamps to 30
        s = self._sched(day=31, hour=8, minute=0)
        from_time = _dt(2025, 4, 1, 0, 0)
        result = s.calculate_next_run(from_time)
        self.assertEqual(result, _dt(2025, 4, 30, 8, 0))

    def test_day_31_in_june_clamps_to_30(self):
        s = self._sched(day=31, hour=8, minute=0)
        from_time = _dt(2025, 6, 1, 0, 0)
        result = s.calculate_next_run(from_time)
        self.assertEqual(result, _dt(2025, 6, 30, 8, 0))

    def test_day_30_in_february_non_leap_clamps_to_28(self):
        s = self._sched(day=30, hour=8, minute=0)
        from_time = _dt(2025, 2, 1, 0, 0)  # 2025 is not a leap year
        result = s.calculate_next_run(from_time)
        self.assertEqual(result, _dt(2025, 2, 28, 8, 0))

    def test_day_31_in_february_non_leap_clamps_to_28(self):
        s = self._sched(day=31, hour=8, minute=0)
        from_time = _dt(2025, 2, 1, 0, 0)
        result = s.calculate_next_run(from_time)
        self.assertEqual(result, _dt(2025, 2, 28, 8, 0))

    # Edge cases: leap year -----------------------------------------------

    def test_day_29_in_february_leap_year(self):
        # 2024 is a leap year — schedule_day=29 is valid in February
        s = self._sched(day=29, hour=8, minute=0)
        from_time = _dt(2024, 2, 1, 0, 0)
        result = s.calculate_next_run(from_time)
        self.assertEqual(result, _dt(2024, 2, 29, 8, 0))

    def test_day_29_in_february_non_leap_year_clamps_to_28(self):
        s = self._sched(day=29, hour=8, minute=0)
        from_time = _dt(2025, 2, 1, 0, 0)
        result = s.calculate_next_run(from_time)
        self.assertEqual(result, _dt(2025, 2, 28, 8, 0))

    def test_day_29_advance_from_march_non_leap_next_month_is_march(self):
        # After clamped Feb run, next scheduled occurrence is March 29
        s = self._sched(day=29, hour=8, minute=0)
        # Already past the clamped Feb 28 slot → next is March 29
        from_time = _dt(2025, 2, 28, 9, 0)
        result = s.calculate_next_run(from_time)
        self.assertEqual(result, _dt(2025, 3, 29, 8, 0))

    # run_once / should_run_now / timezone --------------------------------

    def test_should_run_now_false_when_run_immediately_false(self):
        s = self._sched(day=1, hour=8, minute=0, run_immediately=False)
        self.assertFalse(s.should_run_now())

    def test_calculate_next_run_is_timezone_aware(self):
        s = self._sched()
        result = s.calculate_next_run(_dt(2025, 3, 10, 7, 0))
        self.assertIsNotNone(result.tzinfo)

    def test_run_once_advances_next_run_to_monthly_slot(self):
        s = self._sched(day=1, hour=8, minute=0, run_immediately=True)
        s.run_once()
        nr = s.next_run()
        self.assertEqual(nr.day, 1)
        self.assertEqual(nr.hour, 8)
        self.assertGreater(nr, datetime.now(tz=timezone.utc))


class TestShouldRunNowAlias(unittest.TestCase):
    """should_run_now() must behave identically to should_run()."""

    def test_both_return_true_when_due(self):
        s = Scheduler(MagicMock())
        self.assertEqual(s.should_run(), s.should_run_now())

    def test_both_return_false_after_one_shot(self):
        s = Scheduler(MagicMock())
        s.run_once()
        self.assertFalse(s.should_run())
        self.assertFalse(s.should_run_now())

    def test_consistent_in_daily_mode(self):
        s = Scheduler(MagicMock(), mode="daily", schedule_hour=8, run_immediately=False)
        self.assertEqual(s.should_run(), s.should_run_now())


class TestCalculateNextRunPublicAPI(unittest.TestCase):
    """calculate_next_run() must accept an optional from_time and default to now."""

    def test_no_arg_defaults_to_now(self):
        s = Scheduler(MagicMock(), mode="daily", schedule_hour=23, schedule_minute=59)
        before = datetime.now(tz=timezone.utc)
        result = s.calculate_next_run()
        self.assertGreater(result, before)

    def test_with_explicit_from_time(self):
        s = Scheduler(MagicMock(), mode="daily", schedule_hour=12, schedule_minute=0)
        result = s.calculate_next_run(_dt(2025, 7, 4, 6, 0))
        self.assertEqual(result, _dt(2025, 7, 4, 12, 0))

    def test_with_explicit_from_time_monthly(self):
        s = Scheduler(
            MagicMock(), mode="monthly", schedule_day=10, schedule_hour=0, schedule_minute=0
        )
        result = s.calculate_next_run(_dt(2025, 7, 5, 0, 0))
        self.assertEqual(result, _dt(2025, 7, 10, 0, 0))


if __name__ == '__main__':
    unittest.main()
