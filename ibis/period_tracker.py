import json
from pathlib import Path

from config import STATE_DIR


class PeriodTracker:
    def __init__(self, state_file=None):
        self.state_file = Path(state_file) if state_file is not None else STATE_DIR / "last_period.json"

    def load_last_period(self):
        if not self.state_file.exists():
            return None

        try:
            with self.state_file.open("r", encoding="utf-8") as fh:
                state = json.load(fh)
        except (json.JSONDecodeError, OSError):
            return None

        if not isinstance(state, dict):
            return None

        last_period = state.get("last_period")
        return last_period if isinstance(last_period, str) else None

    def save_last_period(self, period):
        self.state_file.parent.mkdir(parents=True, exist_ok=True)
        with self.state_file.open("w", encoding="utf-8") as fh:
            json.dump({"last_period": period}, fh, indent=2, sort_keys=True)
