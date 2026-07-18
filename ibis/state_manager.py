import json
from pathlib import Path

from config import STATE_DIR
from ibis.downloader import extract_link_metadata


class StateManager:
    def __init__(self, state_file=None):
        self.state_file = Path(state_file) if state_file is not None else STATE_DIR / "completed_invoices.json"

    def filter_pending_links(self, links):
        completed_keys = self._completed_keys()
        pending_links = []
        already_completed_count = 0

        for link in links:
            key = self._link_key(link)
            if key is not None and key in completed_keys:
                already_completed_count += 1
                continue
            pending_links.append(link)

        return pending_links, already_completed_count

    def mark_completed(self, item_or_link):
        key = self._link_key(item_or_link)
        if key is None:
            return

        state = self._load_state()
        completed = state.setdefault("completed_invoices", [])
        if any(
            entry.get("invoice_id") == key[0] and entry.get("billing_period") == key[1]
            for entry in completed
        ):
            return

        completed.append(
            {
                "invoice_id": key[0],
                "billing_period": key[1],
            }
        )
        self._save_state(state)

    def _completed_keys(self):
        state = self._load_state()
        return {
            (entry.get("invoice_id"), entry.get("billing_period"))
            for entry in state.get("completed_invoices", [])
            if entry.get("invoice_id") is not None and entry.get("billing_period") is not None
        }

    def _load_state(self):
        if not self.state_file.exists():
            return {"completed_invoices": []}

        try:
            with self.state_file.open("r", encoding="utf-8") as fh:
                state = json.load(fh)
        except (json.JSONDecodeError, OSError):
            return {"completed_invoices": []}

        if not isinstance(state, dict):
            return {"completed_invoices": []}

        completed = state.get("completed_invoices")
        if not isinstance(completed, list):
            state["completed_invoices"] = []

        return state

    def _save_state(self, state):
        self.state_file.parent.mkdir(parents=True, exist_ok=True)
        with self.state_file.open("w", encoding="utf-8") as fh:
            json.dump(state, fh, indent=2, sort_keys=True)

    @staticmethod
    def _link_key(item_or_link):
        if isinstance(item_or_link, dict):
            invoice_id, billing_period, _ = extract_link_metadata(item_or_link)
        else:
            invoice_id = getattr(item_or_link, "invoice_id", None)
            billing_period = getattr(item_or_link, "billing_period", None)
            if (invoice_id is None or billing_period is None) and hasattr(item_or_link, "download_url"):
                invoice_id, billing_period, _ = extract_link_metadata(
                    {
                        "url": item_or_link.download_url,
                        "invoice_id": invoice_id,
                        "billing_period": billing_period,
                        "filename": getattr(item_or_link, "filename", None),
                    }
                )

        if invoice_id is None or billing_period is None:
            return None

        return invoice_id, billing_period
