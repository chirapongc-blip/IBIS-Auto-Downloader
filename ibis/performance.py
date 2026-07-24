"""Lightweight, presentation-only workflow performance instrumentation."""

from __future__ import annotations

import time
from contextlib import contextmanager


class PerformanceTracker:
    """Accumulate monotonic durations for named workflow stages.

    This class deliberately has no dependency on application state, reporting,
    browser, or downloader code.  It is therefore safe to use for successful
    and controlled-failure runs without changing their behaviour.
    """

    def __init__(self, *, clock=None):
        self._clock = clock or time.monotonic
        self._started_at = self._clock()
        self._durations = {}

    @contextmanager
    def stage(self, name):
        """Measure one named stage, including time spent before an exception."""
        started_at = self._clock()
        try:
            yield
        finally:
            self._durations[name] = self._durations.get(name, 0.0) + max(
                0.0, self._clock() - started_at
            )

    @property
    def durations(self):
        return dict(self._durations)

    @property
    def elapsed_seconds(self):
        return max(0.0, self._clock() - self._started_at)

    def print_summary(self, *, output=print):
        """Print a stable console-only summary; no report schema is changed."""
        output("Performance Summary")
        output("-------------------")
        for name, elapsed in self._durations.items():
            output(f"{name.replace('_', ' ').title()}: {elapsed:.3f} s")
        output(f"Total: {self.elapsed_seconds:.3f} s")
