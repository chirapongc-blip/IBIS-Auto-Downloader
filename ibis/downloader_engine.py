import logging
import time
import warnings
from dataclasses import dataclass
from pathlib import Path

from ibis.downloader import get_download_dir, STATUS_PENDING


logger = logging.getLogger(__name__)


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
    def __init__(self, driver, *, download_dir=None, timeout=None, poll_interval=0.2, state_manager=None, download_state=None):
        self.driver = driver
        self.download_dir = Path(download_dir) if download_dir is not None else get_download_dir()
        if timeout is None:
            from config import DOWNLOAD_TIMEOUT

            timeout = DOWNLOAD_TIMEOUT
        self.timeout = timeout
        self.poll_interval = poll_interval
        self.state_manager = state_manager
        self.download_state = download_state
        self.summary = DownloadSummary()

    def run(self, plan):
        self.summary = DownloadSummary(total_files=len(plan.scheduled_items))
        logger.info(
            "Download plan started: %d item(s), directory=%s, timeout=%ss, poll_interval=%ss.",
            self.summary.total_files,
            self.download_dir,
            self.timeout,
            self.poll_interval,
        )
        if self.download_state is not None:
            self.download_state.initialize(plan.scheduled_items)
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
                logger.info(
                    "Download attempt %d/%d started: invoice_id=%s, billing_period=%s, url=%s, existing_files=%d.",
                    attempt + 1,
                    MAX_RETRIES + 1,
                    item.invoice_id,
                    item.billing_period,
                    item.download_url,
                    len(existing_files),
                )
                self.driver.get(item.download_url)
                logger.info("Browser navigation completed; waiting for download file.")
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
                logger.info(
                    "Download completed: invoice_id=%s, file=%s, size=%d bytes.",
                    item.invoice_id,
                    renamed,
                    renamed.stat().st_size,
                )
                if self.state_manager is not None:
                    self.state_manager.mark_completed(item)
                self._finalize_item(item, STATUS_COMPLETED)
                return

            except Exception as exc:
                item.last_error = str(exc)
                logger.exception(
                    "Download attempt %d/%d failed: invoice_id=%s, url=%s.",
                    attempt + 1,
                    MAX_RETRIES + 1,
                    item.invoice_id,
                    item.download_url,
                )
                if not is_retryable(exc) or attempt == MAX_RETRIES:
                    logger.error(
                        "Download will not be retried: invoice_id=%s, retryable=%s.",
                        item.invoice_id,
                        is_retryable(exc),
                    )
                    break
                item.retry_count += 1
                logger.warning(
                    "Retrying download: invoice_id=%s, retry_count=%d.",
                    item.invoice_id,
                    item.retry_count,
                )

        self._finalize_item(item, STATUS_FAILED)

    def _wait_for_download(self, existing_files):
        deadline = time.monotonic() + self.timeout
        stable_file = None
        stable_size = None

        while time.monotonic() < deadline:
            downloaded_file = self._find_new_completed_file(existing_files)
            if downloaded_file is None:
                stable_file = None
                stable_size = None
            else:
                try:
                    current_size = downloaded_file.stat().st_size
                except OSError:
                    logger.debug("Candidate download disappeared before inspection: %s", downloaded_file)
                    stable_file = None
                    stable_size = None
                else:
                    if downloaded_file == stable_file and current_size == stable_size:
                        logger.info(
                            "Download file is stable and accepted: file=%s, size=%d bytes.",
                            downloaded_file,
                            current_size,
                        )
                        return downloaded_file
                    stable_file = downloaded_file
                    stable_size = current_size
                    logger.info(
                        "Download candidate found; waiting for size stability: file=%s, size=%d bytes.",
                        downloaded_file,
                        current_size,
                    )
            time.sleep(self.poll_interval)

        logger.warning("Timed out waiting for a stable completed download after %s seconds.", self.timeout)
        return None

    def _find_new_completed_file(self, existing_files):
        current_files = self._snapshot_files()
        for file_path in sorted(
            current_files - existing_files,
            key=lambda path: path.stat().st_mtime,
            reverse=True,
        ):
            if file_path.suffix.lower() in _INCOMPLETE_SUFFIXES:
                logger.debug("Ignoring temporary download file: %s", file_path)
                continue
            # Skip if the matching .crdownload companion still exists — the
            # browser hasn't finished writing the file yet.
            if (file_path.parent / (file_path.name + ".crdownload")) in current_files:
                logger.debug("Download candidate still has a .crdownload companion: %s", file_path)
                continue
            return file_path
        return None

    def _build_target_filename(self, item, downloaded_file: Path | None = None) -> str | None:
        """Return the desired final filename for *item*, or None to keep the downloaded name."""
        source_suffix = downloaded_file.suffix.lower() if downloaded_file is not None else ""
        # IBIS exports its spreadsheet payload as .xls (and may provide .xlsx
        # in future), so preserve those server-supplied extensions. The legacy
        # fallback remains only for endpoint/non-export names, where Chrome
        # did not provide a usable export extension.
        if source_suffix not in {".xls", ".xlsx"}:
            source_suffix = ".xlsx"
        pre_set = item.filename
        if pre_set:
            p = Path(pre_set)
            if not p.suffix:
                return f"{p.stem}{source_suffix}"
            return pre_set
        if item.billing_period and item.invoice_id:
            return f"{item.billing_period}_{item.invoice_id}{source_suffix}"
        return None

    def _rename_downloaded_file(self, downloaded_file: Path, item) -> Path:
        """Rename *downloaded_file* to a meaningful name; return the final path.

        Guards:
        - Never renames an incomplete file (e.g. .crdownload).
        - If the target already exists, appends a numeric suffix to avoid collision.
        - Logs a warning instead of silently swallowing rename failures.
        """
        if downloaded_file.suffix.lower() in _INCOMPLETE_SUFFIXES:
            return downloaded_file

        target_name = self._build_target_filename(item, downloaded_file)
        if target_name is None or target_name == downloaded_file.name:
            return downloaded_file

        target_path = self._unique_path(downloaded_file.parent / target_name)
        try:
            downloaded_file.rename(target_path)
            logger.info("Renamed downloaded file: %s -> %s", downloaded_file.name, target_path.name)
            return target_path
        except OSError as exc:
            logger.exception(
                "Failed to rename downloaded file '%s' to '%s'; keeping original filename.",
                downloaded_file.name,
                target_path.name,
            )
            warnings.warn(
                f"Failed to rename '{downloaded_file.name}' to '{target_path.name}': {exc}. "
                "Keeping original filename.",
                stacklevel=2,
            )
            return downloaded_file

    @staticmethod
    def _unique_path(path: Path) -> Path:
        """Return *path* unchanged if it does not exist, otherwise append _{n} before the suffix."""
        if not path.exists():
            return path
        stem = path.stem
        suffix = path.suffix
        parent = path.parent
        counter = 1
        while True:
            candidate = parent / f"{stem}_{counter}{suffix}"
            if not candidate.exists():
                return candidate
            counter += 1

    def _snapshot_files(self):
        self.download_dir.mkdir(parents=True, exist_ok=True)
        return {path for path in self.download_dir.iterdir() if path.is_file()}

    def _set_status(self, item, status):
        item.download_status = status

    def _finalize_item(self, item, status):
        self._set_status(item, status)
        if status == STATUS_COMPLETED:
            self.summary.completed += 1
            if self.download_state is not None:
                self.download_state.mark_completed(item)
        elif status == STATUS_FAILED:
            self.summary.failed += 1
            if self.download_state is not None:
                self.download_state.mark_failed(item)
        elif status == STATUS_SKIPPED:
            self.summary.skipped += 1

        if item.retry_count > 0:
            self.summary.retried += 1

        terminal_count = self.summary.completed + self.summary.failed + self.summary.skipped
        print(f"[{terminal_count}/{self.summary.total_files}] {status.title()} {self._item_label(item)}")

    def _item_label(self, item):
        return item.filename or item.invoice_id or "<unknown>"

    def _print_summary(self, elapsed_seconds):
        print("Download Summary")
        print("----------------")
        print(f"Total: {self.summary.total_files}")
        print(f"Completed: {self.summary.completed}")
        print(f"Failed: {self.summary.failed}")
        print(f"Retried: {self.summary.retried}")
        print(f"Skipped: {self.summary.skipped}")
        print(f"Elapsed: {elapsed_seconds:.1f} s")
