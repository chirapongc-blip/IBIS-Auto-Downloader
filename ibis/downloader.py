from dataclasses import dataclass
from pathlib import Path

from ibis.grid import GRID_ID
from ibis.grid_walker import collect_grid_download_links


DOWNLOAD_DIR = Path("downloads")
STATUS_PENDING = "pending"


@dataclass(frozen=True)
class DownloadQueueItem:
    download_url: str
    invoice_id: str | None
    billing_period: str | None
    filename: str | None
    status: str = STATUS_PENDING


class DownloadQueue:
    def __init__(self, items=None):
        self._items = list(items or [])

    @classmethod
    def from_grid(
        cls, driver, base_url: str, grid_id: str = GRID_ID, timeout: int = 30
    ):
        links = collect_grid_download_links(
            driver, base_url, grid_id=grid_id, timeout=timeout
        )
        return cls.from_links(links)

    @classmethod
    def from_links(cls, links):
        items = [cls._build_item(link) for link in links]
        return cls(items)

    @staticmethod
    def _build_item(link):
        return DownloadQueueItem(
            download_url=link["url"],
            invoice_id=link.get("invoice_id"),
            billing_period=link.get("billing_period"),
            filename=link.get("filename"),
        )

    @property
    def items(self):
        return list(self._items)

    def __iter__(self):
        return iter(self._items)

    def __len__(self):
        return len(self._items)


def ensure_download_dir():
    """
    确保下载目录存在。
    """
    DOWNLOAD_DIR.mkdir(exist_ok=True)


def get_download_dir():
    """
    返回下载目录。
    """
    ensure_download_dir()
    return DOWNLOAD_DIR
