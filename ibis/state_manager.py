import json
import logging
from datetime import datetime, timezone
from pathlib import Path


LOGGER = logging.getLogger(__name__)


class StateManager:
    """Persist and query download completion state."""

    def __init__(self, state_path="state/downloads.json"):
        self.state_path = Path(state_path)
        self._data = {"version": 1, "completed": {}}
        self._load()

    def has_completed(self, item):
        return self._item_key(item) in self._data["completed"]

    def get_completed_filename(self, item):
        entry = self._data["completed"].get(self._item_key(item))
        if not entry:
            return None
        return entry.get("filename")

    def mark_completed(self, item, filename):
        key = self._item_key(item)
        self._data["completed"][key] = {
            "invoice_id": item.invoice_id,
            "billing_period": item.billing_period,
            "download_url": item.download_url,
            "filename": filename,
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }
        self._persist()

    def _item_key(self, item):
        if item.billing_period and item.invoice_id:
            return f"{item.billing_period}_{item.invoice_id}"
        if item.invoice_id:
            return str(item.invoice_id)
        return item.download_url

    def _load(self):
        if not self.state_path.exists():
            return
        try:
            with self.state_path.open("r", encoding="utf-8") as fh:
                loaded = json.load(fh)
        except (OSError, json.JSONDecodeError) as exc:
            LOGGER.warning("Failed to load state file '%s': %s", self.state_path, exc)
            return

        if not isinstance(loaded, dict):
            LOGGER.warning("State file '%s' has invalid root type", self.state_path)
            return

        completed = loaded.get("completed", {})
        if not isinstance(completed, dict):
            LOGGER.warning("State file '%s' has invalid 'completed' section", self.state_path)
            return

        self._data = {
            "version": loaded.get("version", 1),
            "completed": completed,
        }

    def _persist(self):
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "version": self._data.get("version", 1),
            "completed": self._data.get("completed", {}),
        }
        with self.state_path.open("w", encoding="utf-8") as fh:
            json.dump(payload, fh, indent=2, sort_keys=True)
            fh.write("\n")
