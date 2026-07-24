import json
from datetime import datetime, timezone
from pathlib import Path

from config import STATE_DIR

_DEFAULT_STATE_FILE = STATE_DIR / "download_state.json"


class DownloadState:
    """Persistent download-progress tracker.

    Saves a JSON snapshot of the current download session to
    ``state/download_state.json`` (or a custom path) after every
    status change so that progress survives crashes and restarts.

    Parameters
    ----------
    state_file : str | Path | None
        Path to the JSON file.  Defaults to ``STATE_DIR/download_state.json``.
    billing_period : str | None
        The billing period being processed in this session.
    invoice_id : str | None
        A single invoice ID, when the session targets a specific invoice.
    customer_id : str | None
        The customer ID associated with this download session.
    """

    def __init__(
        self,
        state_file=None,
        *,
        billing_period=None,
        invoice_id=None,
        customer_id=None,
        selected_periods=None,
    ):
        self.state_file = Path(state_file) if state_file is not None else _DEFAULT_STATE_FILE
        self.billing_period = billing_period
        self.invoice_id = invoice_id
        self.customer_id = customer_id
        self.selected_periods = list(selected_periods) if selected_periods is not None else None
        self._queue: list = []
        self._completed: list = []
        self._failed: list = []

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    def initialize(self, items):
        """Populate the queue from *items* and write an initial snapshot.

        Parameters
        ----------
        items : iterable
            ``DownloadQueueItem`` instances (or plain dicts) that make up
            the planned download set for this session.
        """
        self._queue = list(items)
        self._completed = []
        self._failed = []
        self.save_state()

    def restore(self, state: dict, *, selected_periods=None):
        """Restore a persisted session into memory for period-scoped resume.

        Legacy snapshots without ``selected_periods`` remain supported.  When
        a selection is supplied, unrelated periods are excluded from the
        in-memory session before it is saved again.
        """
        periods = selected_periods
        if periods is None:
            periods = state.get("selected_periods")
        self.selected_periods = list(periods) if periods is not None else None
        allowed = (
            set(self.selected_periods)
            if self.selected_periods is not None
            else None
        )

        def keep(items):
            if allowed is None:
                return list(items)
            return [item for item in items if item.get("billing_period") in allowed]

        self.billing_period = state.get("billing_period")
        self.customer_id = state.get("customer_id")
        self.invoice_id = state.get("invoice_id")
        self._queue = keep(state.get("queue", []))
        self._completed = keep(state.get("completed", []))
        self._failed = keep(state.get("failed", []))

    def mark_completed(self, item):
        """Record *item* as completed and reconcile prior in-session status."""
        completed_item = self._sync_queue_item(item)
        key = self._item_key(completed_item)
        if key is not None:
            self._failed = [entry for entry in self._failed if self._item_key(entry) != key]
            self._completed = [
                entry for entry in self._completed if self._item_key(entry) != key
            ]
        self._completed.append(completed_item)
        self.save_state()

    def mark_failed(self, item):
        """Record *item* as failed and persist state."""
        failed_item = self._sync_queue_item(item)
        key = self._item_key(failed_item)
        if key is not None:
            self._completed = [
                entry for entry in self._completed if self._item_key(entry) != key
            ]
            self._failed = [entry for entry in self._failed if self._item_key(entry) != key]
        self._failed.append(failed_item)
        self.save_state()

    def save_state(self):
        """Serialize current state to the JSON file.

        The ``state/`` directory is created automatically if it does not
        exist.
        """
        state = {
            "billing_period": self.billing_period,
            "customer_id": self.customer_id,
            "invoice_id": self.invoice_id,
            "selected_periods": self.selected_periods,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "queue": [self._serialize_item(item) for item in self._queue],
            "completed": [self._serialize_item(item) for item in self._completed],
            "failed": [self._serialize_item(item) for item in self._failed],
        }
        self.state_file.parent.mkdir(parents=True, exist_ok=True)
        with self.state_file.open("w", encoding="utf-8") as fh:
            json.dump(state, fh, indent=2, sort_keys=True)

    def load_state(self) -> dict:
        """Load and return the persisted state as a plain dict.

        Returns an empty dict when the file does not exist or is corrupt.
        """
        if not self.state_file.exists():
            return {}
        try:
            with self.state_file.open("r", encoding="utf-8") as fh:
                data = json.load(fh)
        except (json.JSONDecodeError, OSError):
            return {}
        if not isinstance(data, dict):
            return {}
        return data

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _item_key(item):
        """Return the stable state key for an item, or ``None`` when unavailable."""
        if isinstance(item, dict):
            key = item.get("invoice_id"), item.get("billing_period")
        else:
            key = getattr(item, "invoice_id", None), getattr(item, "billing_period", None)
        return key if key != (None, None) else None

    @staticmethod
    def _copy_item_state(target, source):
        """Update a queued entry with the latest terminal state from *source*."""
        fields = (
            "download_status",
            "filename",
            "last_error",
            "retry_count",
        )
        for field in fields:
            value = source.get(field) if isinstance(source, dict) else getattr(source, field, None)
            if isinstance(target, dict):
                target[field] = value
            else:
                setattr(target, field, value)

    def _sync_queue_item(self, item):
        """Return the canonical queued item after copying the latest item state.

        Recovery creates new queue-item objects.  Updating the original queue
        entry keeps the persisted full-session queue consistent with the
        recovered terminal result.
        """
        key = self._item_key(item)
        if key is None:
            return item
        for queued_item in self._queue:
            if self._item_key(queued_item) == key:
                self._copy_item_state(queued_item, item)
                return queued_item
        return item

    @staticmethod
    def _serialize_item(item) -> dict:
        """Convert a ``DownloadQueueItem`` (or dict) to a JSON-safe dict."""
        if isinstance(item, dict):
            return item
        return {
            "billing_period": getattr(item, "billing_period", None),
            "customer_id": getattr(item, "customer_id", None),
            "download_status": getattr(item, "download_status", None),
            "download_url": getattr(item, "download_url", None),
            "filename": getattr(item, "filename", None),
            "invoice_id": getattr(item, "invoice_id", None),
            "last_error": getattr(item, "last_error", None),
            "retry_count": getattr(item, "retry_count", 0),
        }
