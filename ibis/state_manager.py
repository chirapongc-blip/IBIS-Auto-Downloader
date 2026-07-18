import json
from pathlib import Path
from urllib.parse import parse_qsl, urlparse

from config import STATE_DIR


class StateManager:
    def __init__(self, state_file=None):
        self.state_file = Path(state_file) if state_file is not None else STATE_DIR / "downloaded_invoices.json"
        self._downloaded_invoice_ids = set()

    @property
    def downloaded_invoice_ids(self):
        return set(self._downloaded_invoice_ids)

    def load(self):
        if not self.state_file.exists():
            self._downloaded_invoice_ids = set()
            return self.downloaded_invoice_ids

        data = json.loads(self.state_file.read_text(encoding="utf-8"))
        invoice_ids = data.get("downloaded_invoice_ids", [])
        self._downloaded_invoice_ids = {
            str(invoice_id).strip()
            for invoice_id in invoice_ids
            if str(invoice_id).strip()
        }
        return self.downloaded_invoice_ids

    def save(self):
        self.state_file.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "downloaded_invoice_ids": sorted(self._downloaded_invoice_ids),
        }
        self.state_file.write_text(
            json.dumps(payload, indent=2, sort_keys=True),
            encoding="utf-8",
        )

    def is_downloaded(self, invoice_id):
        return bool(invoice_id) and str(invoice_id).strip() in self._downloaded_invoice_ids

    def filter_pending_links(self, links):
        pending_links = []
        for link in links:
            invoice_id = _get_invoice_id(link)
            if invoice_id is None or not self.is_downloaded(invoice_id):
                pending_links.append(link)
        return pending_links

    def mark_downloaded(self, invoice_id):
        if not invoice_id:
            return
        normalized_invoice_id = str(invoice_id).strip()
        if normalized_invoice_id:
            self._downloaded_invoice_ids.add(normalized_invoice_id)

    def mark_completed_items(self, items, *, completed_status="completed"):
        for item in items:
            if getattr(item, "download_status", None) != completed_status:
                continue
            self.mark_downloaded(getattr(item, "invoice_id", None))


def _get_invoice_id(link):
    invoice_id = link.get("invoice_id")
    if invoice_id:
        normalized_invoice_id = str(invoice_id).strip()
        if normalized_invoice_id:
            return normalized_invoice_id

    query = urlparse(link["url"]).query
    params = {key.strip(): value.strip() for key, value in parse_qsl(query)}
    invoice_id = params.get("InvoiceID")
    if invoice_id:
        return invoice_id.strip() or None
    return None
