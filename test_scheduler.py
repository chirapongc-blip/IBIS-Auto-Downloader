import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, call, patch

from ibis.scheduler import Scheduler


def _utc_now():
    return datetime.now(tz=timezone.utc)


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


if __name__ == "__main__":
    unittest.main()
