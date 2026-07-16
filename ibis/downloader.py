from dataclasses import dataclass
from pathlib import Path
from urllib.parse import parse_qsl, urlparse


DOWNLOAD_DIR = Path("downloads")
STATUS_PENDING = "pending"


@dataclass
class DownloadQueueItem:
    download_url: str
    invoice_id: str | None
    billing_period: str | None
    filename: str | None
    download_status: str = STATUS_PENDING
    retry_count: int = 0
    last_error: str | None = None


class DownloadQueue:
    def __init__(self, links=None):
        self._items = []
        if links:
            self.extend(links)

    @classmethod
    def from_links(cls, links):
        return cls(links)

    @property
    def items(self):
        return list(self._items)

    def add_link(self, link):
        self._items.append(self._build_item(link))

    def extend(self, links):
        for link in links:
            self.add_link(link)

    def __iter__(self):
        return iter(self._items)

    def __len__(self):
        return len(self._items)

    def _build_item(self, link):
        params = _parse_download_params(link["url"])
        invoice_id = link.get("invoice_id") or params.get("InvoiceID")
        billing_period = link.get("billing_period") or params.get("BillingPeriod")
        filename = link.get("filename") or None

        return DownloadQueueItem(
            download_url=link["url"],
            invoice_id=invoice_id,
            billing_period=billing_period,
            filename=filename,
        )


def ensure_download_dir():
    """
    确保下载目录存在。
    """
    from config import DOWNLOAD_DIR
    DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)


def get_download_dir():
    """
    返回下载目录。
    """
    from config import DOWNLOAD_DIR
    DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)
    return DOWNLOAD_DIR


def _parse_download_params(url: str):
    query = urlparse(url).query
    return {key.strip(): value.strip() for key, value in parse_qsl(query)}
