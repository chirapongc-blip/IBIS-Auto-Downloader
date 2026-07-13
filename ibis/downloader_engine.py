import time
from pathlib import Path

from ibis.downloader import get_download_dir, STATUS_PENDING


STATUS_DOWNLOADING = "downloading"
STATUS_COMPLETED = "completed"
STATUS_FAILED = "failed"
_INCOMPLETE_SUFFIXES = (".crdownload", ".part", ".tmp")

MAX_RETRIES = 3


class DownloadError(Exception):
    """Base class for all download-related errors."""


class DownloadTimeoutError(DownloadError):
    """Raised when a download times out waiting for a file to appear (retryable)."""


class IncompleteDownloadError(DownloadError):
    """Raised when a download completes but the expected file is missing (retryable)."""


class TemporaryBrowserError(DownloadError):
    """Raised for transient browser or network failures (retryable)."""


class Http404Error(DownloadError):
    """Raised when the server returns HTTP 404 (not retryable)."""


class InvalidUrlError(DownloadError):
    """Raised when the download URL is invalid (not retryable)."""


class DuplicateFileError(DownloadError):
    """Raised when the file has already been downloaded (not retryable)."""


def is_retryable(exc):
    """Return True if *exc* represents a transient failure that warrants a retry."""
    return isinstance(exc, (DownloadTimeoutError, IncompleteDownloadError, TemporaryBrowserError))


class DownloaderEngine:
    def __init__(self, driver, *, download_dir=None, timeout=60, poll_interval=0.2):
        self.driver = driver
        self.download_dir = Path(download_dir) if download_dir is not None else get_download_dir()
        self.timeout = timeout
        self.poll_interval = poll_interval

    def run(self, plan):
        for item in plan.scheduled_items:
            self._download_item(item)

    def _download_item(self, item):
        self._set_status(item, STATUS_PENDING)
        self._set_status(item, STATUS_DOWNLOADING)

        for attempt in range(MAX_RETRIES + 1):
            existing_files = self._snapshot_files()
            try:
                self.driver.get(item.download_url)
                downloaded_file = self._wait_for_download(existing_files)

                if downloaded_file is None:
                    raise DownloadTimeoutError(
                        f"Download timed out for URL: {item.download_url}"
                    )
                if not downloaded_file.exists():
                    raise IncompleteDownloadError(
                        f"Download file missing for URL: {item.download_url}"
                    )

                item.filename = downloaded_file.name
                self._set_status(item, STATUS_COMPLETED)
                return

            except Exception as exc:
                item.last_error = str(exc)
                if not is_retryable(exc) or attempt == MAX_RETRIES:
                    break
                item.retry_count += 1

        self._set_status(item, STATUS_FAILED)

    def _wait_for_download(self, existing_files):
        deadline = time.monotonic() + self.timeout

        while time.monotonic() < deadline:
            downloaded_file = self._find_new_completed_file(existing_files)
            if downloaded_file is not None:
                return downloaded_file
            time.sleep(self.poll_interval)

        return None

    def _find_new_completed_file(self, existing_files):
        for file_path in sorted(
            self._snapshot_files() - existing_files,
            key=lambda path: path.stat().st_mtime,
            reverse=True,
        ):
            if file_path.suffix.lower() in _INCOMPLETE_SUFFIXES:
                continue
            return file_path
        return None

    def _snapshot_files(self):
        self.download_dir.mkdir(parents=True, exist_ok=True)
        return {path for path in self.download_dir.iterdir() if path.is_file()}

    def _set_status(self, item, status):
        item.download_status = status
