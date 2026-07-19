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
    ):
        self.state_file = Path(state_file) if state_file is not None else _DEFAULT_STATE_FILE
        self.billing_period = billing_period
        self.invoice_id = invoice_id
        self.customer_id = customer_id
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

    def mark_completed(self, item):
        """Record *item* as successfully completed and persist state."""
        self._completed.append(item)
        self.save_state()

    def mark_failed(self, item):
        """Record *item* as failed and persist state."""
        self._failed.append(item)
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
