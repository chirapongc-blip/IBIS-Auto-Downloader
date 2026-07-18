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


@dataclass(frozen=True)
class QueueBuildResult:
    queue: "DownloadQueue"
    found_count: int
    latest_billing_period: str | None
    already_completed_count: int


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
        invoice_id, billing_period, filename = extract_link_metadata(link)

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


def extract_link_metadata(link):
    params = _parse_download_params(link["url"])
    invoice_id = link.get("invoice_id") or params.get("InvoiceID")
    billing_period = link.get("billing_period") or params.get("BillingPeriod")
    filename = link.get("filename") or None
    return invoice_id, billing_period, filename


def filter_links_to_latest_billing_period(links):
    links = list(links)
    latest_billing_period = max(
        (billing_period for _, billing_period, _ in map(extract_link_metadata, links) if billing_period is not None),
        default=None,
    )
    if latest_billing_period is None:
        return links, None

    latest_links = [
        link
        for link in links
        if extract_link_metadata(link)[1] == latest_billing_period
    ]
    return latest_links, latest_billing_period


def build_download_queue(links, *, state_manager=None):
    latest_links, latest_billing_period = filter_links_to_latest_billing_period(links)
    pending_links = latest_links
    already_completed_count = 0

    if state_manager is not None:
        pending_links, already_completed_count = state_manager.filter_pending_links(latest_links)

    return QueueBuildResult(
        queue=DownloadQueue.from_links(pending_links),
        found_count=len(latest_links),
        latest_billing_period=latest_billing_period,
        already_completed_count=already_completed_count,
    )
