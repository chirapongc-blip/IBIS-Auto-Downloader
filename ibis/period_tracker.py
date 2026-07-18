import json
from pathlib import Path

from config import STATE_DIR

PERIOD_FILE_DEFAULT = STATE_DIR / "last_period.json"


class PeriodTracker:
    """Tracks the latest Billing Period that has ever been downloaded.

    State is persisted to ``state/last_period.json`` so that consecutive
    runs can detect when a new billing period appears in the Invoice Grid.
    """

    def __init__(self, period_file=None):
        self.period_file = (
            Path(period_file) if period_file is not None else PERIOD_FILE_DEFAULT
        )

    def load_last_period(self):
        """Return the stored billing period string, or None if not recorded yet."""
        if not self.period_file.exists():
            return None
        try:
            with self.period_file.open("r", encoding="utf-8") as fh:
                data = json.load(fh)
        except (json.JSONDecodeError, OSError):
            return None
        if not isinstance(data, dict):
            return None
        return data.get("last_billing_period") or None

    def save_last_period(self, billing_period):
        """Persist *billing_period* to the state file.  No-op when None."""
        if billing_period is None:
            return
        self.period_file.parent.mkdir(parents=True, exist_ok=True)
        with self.period_file.open("w", encoding="utf-8") as fh:
            json.dump({"last_billing_period": billing_period}, fh, indent=2, sort_keys=True)

    def is_new_period(self, current_period):
        """Return True when *current_period* differs from the stored period."""
        if current_period is None:
            return False
        return current_period != self.load_last_period()
