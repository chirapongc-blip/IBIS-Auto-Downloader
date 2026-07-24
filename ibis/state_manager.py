import json
import logging
from datetime import datetime, timezone
from pathlib import Path

from config import STATE_DIR
from ibis.downloader import extract_link_metadata, find_downloaded_output


logger = logging.getLogger(__name__)


class StateManager:
    def __init__(self, state_file=None, *, download_dir=None):
        self.state_file = Path(state_file) if state_file is not None else STATE_DIR / "completed_invoices.json"
        self.download_dir = Path(download_dir) if download_dir is not None else None

    def filter_pending_links(self, links, *, read_only=False):
        """Return pending links, optionally without migrating legacy state.

        ``read_only`` is used by the dry-run preview.  It preserves the same
        verification decisions while guaranteeing completed-state JSON is not
        written as a side effect of a legacy record upgrade.
        """
        state = self._load_state()
        completed_entries = {
            (entry.get("invoice_id"), entry.get("billing_period")): entry
            for entry in state.get("completed_invoices", [])
            if entry.get("invoice_id") is not None and entry.get("billing_period") is not None
        }
        pending_links = []
        already_completed_count = 0
        migrated = False

        for link in links:
            key = self._link_key(link)
            entry = completed_entries.get(key) if key is not None else None
            if entry is None:
                pending_links.append(link)
                continue

            _, _, link_filename = extract_link_metadata(link)
            recorded_filename = entry.get("filename") or link_filename
            if recorded_filename is None:
                # A historical record with no filename cannot be verified. Keep
                # it compatible with earlier versions until the next completed
                # download upgrades it to the richer schema.
                already_completed_count += 1
                continue

            output_file = find_downloaded_output(
                recorded_filename,
                download_dir=self.download_dir,
            )
            if output_file is None:
                logger.warning(
                    "Requeueing invoice %s for billing period %s: recorded output '%s' is missing.",
                    key[0],
                    key[1],
                    recorded_filename,
                )
                pending_links.append(link)
                continue

            expected_size = entry.get("filesize")
            actual_size = output_file.stat().st_size
            if expected_size is not None and expected_size != actual_size:
                logger.warning(
                    "Requeueing invoice %s for billing period %s: output '%s' size changed (%s != %s).",
                    key[0],
                    key[1],
                    output_file.name,
                    actual_size,
                    expected_size,
                )
                pending_links.append(link)
                continue

            if self._upgrade_entry(entry, output_file):
                migrated = True
            already_completed_count += 1

        if migrated and not read_only:
            self._save_state(state)

        return pending_links, already_completed_count

    def mark_completed(self, item_or_link):
        key = self._link_key(item_or_link)
        if key is None:
            return

        state = self._load_state()
        completed = state.setdefault("completed_invoices", [])
        entry = next(
            (
                completed_entry
                for completed_entry in completed
                if completed_entry.get("invoice_id") == key[0]
                and completed_entry.get("billing_period") == key[1]
            ),
            None,
        )
        if entry is None:
            entry = {"invoice_id": key[0], "billing_period": key[1]}
            completed.append(entry)

        filename = self._filename_from_item(item_or_link)
        output_file = find_downloaded_output(filename, download_dir=self.download_dir)
        if output_file is not None:
            self._upgrade_entry(entry, output_file, completed_at=True)
        self._save_state(state)

    def _completed_keys(self):
        state = self._load_state()
        return {
            (entry.get("invoice_id"), entry.get("billing_period"))
            for entry in state.get("completed_invoices", [])
            if entry.get("invoice_id") is not None and entry.get("billing_period") is not None
        }

    @staticmethod
    def _filename_from_item(item_or_link):
        if isinstance(item_or_link, dict):
            return item_or_link.get("filename")
        return getattr(item_or_link, "filename", None)

    @staticmethod
    def _upgrade_entry(entry, output_file, *, completed_at=False):
        """Enrich a legacy completion entry from a verified output file."""
        changed = False
        values = {
            "filename": output_file.name,
            "filesize": output_file.stat().st_size,
        }
        if completed_at and not entry.get("completed_at"):
            values["completed_at"] = datetime.now(timezone.utc).isoformat()
        elif not entry.get("completed_at"):
            values["completed_at"] = datetime.now(timezone.utc).isoformat()

        for field, value in values.items():
            if entry.get(field) != value:
                entry[field] = value
                changed = True
        return changed

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
