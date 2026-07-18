"""
Unit tests for ibis.period_tracker — Build 2.5.

Covers:
  - PeriodTracker.load_last_period()
  - PeriodTracker.save_last_period()
  - PeriodTracker.is_new_period()
  - Round-trip persistence
  - Edge cases (missing file, corrupt JSON, None values)
"""
import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from ibis.period_tracker import PeriodTracker


class TestPeriodTrackerLoad(unittest.TestCase):

    def test_returns_none_when_file_missing(self):
        with TemporaryDirectory() as tmp:
            tracker = PeriodTracker(Path(tmp) / "last_period.json")
            self.assertIsNone(tracker.load_last_period())

    def test_returns_stored_period(self):
        with TemporaryDirectory() as tmp:
            f = Path(tmp) / "last_period.json"
            f.write_text(
                json.dumps({"last_billing_period": "202605"}), encoding="utf-8"
            )
            tracker = PeriodTracker(f)
            self.assertEqual(tracker.load_last_period(), "202605")

    def test_handles_corrupt_json(self):
        with TemporaryDirectory() as tmp:
            f = Path(tmp) / "last_period.json"
            f.write_text("not valid json", encoding="utf-8")
            tracker = PeriodTracker(f)
            self.assertIsNone(tracker.load_last_period())

    def test_handles_non_dict_json(self):
        with TemporaryDirectory() as tmp:
            f = Path(tmp) / "last_period.json"
            f.write_text(json.dumps(["202605"]), encoding="utf-8")
            tracker = PeriodTracker(f)
            self.assertIsNone(tracker.load_last_period())

    def test_handles_missing_key(self):
        with TemporaryDirectory() as tmp:
            f = Path(tmp) / "last_period.json"
            f.write_text(json.dumps({"other_key": "value"}), encoding="utf-8")
            tracker = PeriodTracker(f)
            self.assertIsNone(tracker.load_last_period())

    def test_handles_empty_string_value(self):
        with TemporaryDirectory() as tmp:
            f = Path(tmp) / "last_period.json"
            f.write_text(
                json.dumps({"last_billing_period": ""}), encoding="utf-8"
            )
            tracker = PeriodTracker(f)
            self.assertIsNone(tracker.load_last_period())


class TestPeriodTrackerSave(unittest.TestCase):

    def test_creates_file_with_correct_content(self):
        with TemporaryDirectory() as tmp:
            f = Path(tmp) / "last_period.json"
            tracker = PeriodTracker(f)
            tracker.save_last_period("202606")
            self.assertTrue(f.exists())
            data = json.loads(f.read_text(encoding="utf-8"))
            self.assertEqual(data["last_billing_period"], "202606")

    def test_creates_parent_directories(self):
        with TemporaryDirectory() as tmp:
            f = Path(tmp) / "nested" / "dir" / "last_period.json"
            tracker = PeriodTracker(f)
            tracker.save_last_period("202606")
            self.assertTrue(f.exists())

    def test_overwrites_existing_period(self):
        with TemporaryDirectory() as tmp:
            f = Path(tmp) / "last_period.json"
            tracker = PeriodTracker(f)
            tracker.save_last_period("202605")
            tracker.save_last_period("202606")
            data = json.loads(f.read_text(encoding="utf-8"))
            self.assertEqual(data["last_billing_period"], "202606")

    def test_none_does_not_create_file(self):
        with TemporaryDirectory() as tmp:
            f = Path(tmp) / "last_period.json"
            tracker = PeriodTracker(f)
            tracker.save_last_period(None)
            self.assertFalse(f.exists())

    def test_none_does_not_overwrite_existing_file(self):
        with TemporaryDirectory() as tmp:
            f = Path(tmp) / "last_period.json"
            tracker = PeriodTracker(f)
            tracker.save_last_period("202605")
            tracker.save_last_period(None)
            self.assertEqual(tracker.load_last_period(), "202605")


class TestPeriodTrackerIsNewPeriod(unittest.TestCase):

    def test_is_new_when_no_stored_period(self):
        with TemporaryDirectory() as tmp:
            tracker = PeriodTracker(Path(tmp) / "last_period.json")
            self.assertTrue(tracker.is_new_period("202605"))

    def test_is_not_new_when_same_period(self):
        with TemporaryDirectory() as tmp:
            f = Path(tmp) / "last_period.json"
            tracker = PeriodTracker(f)
            tracker.save_last_period("202605")
            self.assertFalse(tracker.is_new_period("202605"))

    def test_is_new_when_different_period(self):
        with TemporaryDirectory() as tmp:
            f = Path(tmp) / "last_period.json"
            tracker = PeriodTracker(f)
            tracker.save_last_period("202605")
            self.assertTrue(tracker.is_new_period("202606"))

    def test_none_current_period_is_not_new(self):
        with TemporaryDirectory() as tmp:
            tracker = PeriodTracker(Path(tmp) / "last_period.json")
            self.assertFalse(tracker.is_new_period(None))

    def test_none_current_period_is_not_new_even_with_stored_period(self):
        with TemporaryDirectory() as tmp:
            f = Path(tmp) / "last_period.json"
            tracker = PeriodTracker(f)
            tracker.save_last_period("202605")
            self.assertFalse(tracker.is_new_period(None))


class TestPeriodTrackerRoundTrip(unittest.TestCase):

    def test_save_then_load_returns_same_value(self):
        with TemporaryDirectory() as tmp:
            f = Path(tmp) / "last_period.json"
            tracker = PeriodTracker(f)
            tracker.save_last_period("202606")
            self.assertEqual(tracker.load_last_period(), "202606")

    def test_period_advances_when_new_detected(self):
        """Simulate the Build 2.5 startup flow: detect new period, save after run."""
        with TemporaryDirectory() as tmp:
            f = Path(tmp) / "last_period.json"
            tracker = PeriodTracker(f)
            tracker.save_last_period("202605")

            current_period = "202606"
            stored_period = tracker.load_last_period()

            if current_period and current_period != stored_period:
                tracker.save_last_period(current_period)

            self.assertEqual(tracker.load_last_period(), "202606")

    def test_period_unchanged_is_not_overwritten(self):
        """Simulate unchanged period: stored value must remain."""
        with TemporaryDirectory() as tmp:
            f = Path(tmp) / "last_period.json"
            tracker = PeriodTracker(f)
            tracker.save_last_period("202605")

            current_period = "202605"
            stored_period = tracker.load_last_period()

            if current_period and current_period != stored_period:
                tracker.save_last_period(current_period)

            self.assertEqual(tracker.load_last_period(), "202605")

    def test_first_run_no_stored_period_saves_correctly(self):
        """First run: no existing file, period is saved after successful download."""
        with TemporaryDirectory() as tmp:
            f = Path(tmp) / "last_period.json"
            tracker = PeriodTracker(f)

            self.assertIsNone(tracker.load_last_period())
            self.assertTrue(tracker.is_new_period("202605"))

            tracker.save_last_period("202605")
            self.assertEqual(tracker.load_last_period(), "202605")


if __name__ == "__main__":
    unittest.main()
