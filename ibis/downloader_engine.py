import time
from dataclasses import dataclass
from pathlib import Path

from ibis.downloader import get_download_dir, STATUS_PENDING


STATUS_DOWNLOADING = "downloading"
STATUS_COMPLETED = "completed"
STATUS_FAILED = "failed"
STATUS_SKIPPED = "skipped"
_INCOMPLETE_SUFFIXES = (".crdownload", ".part", ".tmp")

MAX_RETRIES = 3


@dataclass
class DownloadSummary:
    total_files: int = 0
    completed: int = 0
    failed: int = 0
    retried: int = 0
    skipped: int = 0


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
        self.summary = DownloadSummary()

    def run(self, plan):
        self.summary = DownloadSummary(total_files=len(plan.scheduled_items))
        started_at = time.monotonic()
        for item in plan.scheduled_items:
            self._download_item(item)
        self._print_summary(time.monotonic() - started_at)

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

                renamed = self._rename_downloaded_file(downloaded_file, item)
                item.filename = renamed.name
                self._finalize_item(item, STATUS_COMPLETED)
                return

            except Exception as exc:
                item.last_error = str(exc)
                if not is_retryable(exc) or attempt == MAX_RETRIES:
                    break
                item.retry_count += 1

        self._finalize_item(item, STATUS_FAILED)

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

    def _build_target_filename(self, item) -> str | None:
        """Return the desired final filename for *item*, or None to keep the downloaded name."""
        pre_set = item.filename
        if pre_set:
            p = Path(pre_set)
            if not p.suffix:
                return f"{p.stem}.xlsx"
            return pre_set
        if item.billing_period and item.invoice_id:
            return f"{item.billing_period}_{item.invoice_id}.xlsx"
        return None

    def _rename_downloaded_file(self, downloaded_file: Path, item) -> Path:
        """Rename *downloaded_file* to a meaningful name; return the final path."""
        target_name = self._build_target_filename(item)
        if target_name is None or target_name == downloaded_file.name:
            return downloaded_file
        target_path = downloaded_file.parent / target_name
        try:
            downloaded_file.rename(target_path)
            return target_path
        except OSError:
            return downloaded_file

    def _snapshot_files(self):
        self.download_dir.mkdir(parents=True, exist_ok=True)
        return {path for path in self.download_dir.iterdir() if path.is_file()}

    def _set_status(self, item, status):
        item.download_status = status

    def _finalize_item(self, item, status):
        self._set_status(item, status)
        if status == STATUS_COMPLETED:
            self.summary.completed += 1
        elif status == STATUS_FAILED:
            self.summary.failed += 1
        elif status == STATUS_SKIPPED:
            self.summary.skipped += 1

        if item.retry_count > 0:
            self.summary.retried += 1

        terminal_count = self.summary.completed + self.summary.failed + self.summary.skipped
        print(f"[{terminal_count}/{self.summary.total_files}] {status.title()} {self._item_label(item)}")

    def _item_label(self, item):
        return item.filename or f"{item.invoice_id or item.download_url}"

    def _print_summary(self, elapsed_seconds):
        print("Download Summary")
        print("----------------")
        print(f"Total: {self.summary.total_files}")
        print(f"Completed: {self.summary.completed}")
        print(f"Failed: {self.summary.failed}")
        print(f"Retried: {self.summary.retried}")
        print(f"Skipped: {self.summary.skipped}")
        print(f"Elapsed: {elapsed_seconds:.1f} s")
