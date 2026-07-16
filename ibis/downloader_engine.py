import time
from dataclasses import dataclass
from pathlib import Path
import re
from urllib.parse import urlparse

from ibis.downloader import get_download_dir, STATUS_PENDING
from selenium.common.exceptions import WebDriverException


STATUS_DOWNLOADING = "downloading"
STATUS_COMPLETED = "completed"
STATUS_FAILED = "failed"
STATUS_SKIPPED = "skipped"
_INCOMPLETE_SUFFIXES = (".crdownload", ".part", ".tmp")
_SAFE_FILENAME_RE = re.compile(r"[^A-Za-z0-9._-]+")

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
            existing_files = self._snapshot_file_state()
            try:
                self._validate_download_url(item.download_url)
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

                downloaded_file = self._rename_downloaded_file(item, downloaded_file)
                item.filename = downloaded_file.name
                self._finalize_item(item, STATUS_COMPLETED)
                return

            except Exception as exc:
                normalized_error = self._normalize_download_error(exc)
                item.last_error = str(normalized_error)
                if not is_retryable(normalized_error) or attempt == MAX_RETRIES:
                    break
                item.retry_count += 1

        self._finalize_item(item, STATUS_FAILED)

    def _wait_for_download(self, existing_files: dict[Path, tuple[int, int]]):
        deadline = time.monotonic() + self.timeout

        while time.monotonic() < deadline:
            current_files = self._snapshot_file_state()
            if self._has_active_incomplete_downloads(existing_files, current_files):
                time.sleep(self.poll_interval)
                continue

            downloaded_file = self._find_new_completed_file(existing_files, current_files)
            if downloaded_file is not None:
                return downloaded_file
            time.sleep(self.poll_interval)

        return None

    def _find_new_completed_file(
        self,
        existing_files: dict[Path, tuple[int, int]],
        current_files: dict[Path, tuple[int, int]],
    ):
        for file_path in sorted(
            self._collect_changed_files(existing_files, current_files),
            key=lambda path: current_files[path][0],
            reverse=True,
        ):
            if file_path.suffix.lower() in _INCOMPLETE_SUFFIXES:
                continue
            return file_path
        return None

    def _snapshot_file_state(self):
        self.download_dir.mkdir(parents=True, exist_ok=True)
        state = {}
        for path in self.download_dir.iterdir():
            if not path.is_file():
                continue
            try:
                stats = path.stat()
            except FileNotFoundError:
                continue
            state[path] = (stats.st_mtime_ns, stats.st_size)
        return state

    def _collect_changed_files(self, existing_files, current_files):
        changed = []
        for path, current_state in current_files.items():
            previous_state = existing_files.get(path)
            if previous_state is None or previous_state != current_state:
                changed.append(path)
        return changed

    def _has_active_incomplete_downloads(self, existing_files, current_files):
        for path in self._collect_changed_files(existing_files, current_files):
            if path.suffix.lower() in _INCOMPLETE_SUFFIXES:
                return True
        return False

    def _rename_downloaded_file(self, item, downloaded_file: Path):
        target_path = self.download_dir / self._build_target_filename(item, downloaded_file)
        if target_path == downloaded_file:
            return downloaded_file
        if target_path.exists():
            raise DuplicateFileError(f"File already exists: {target_path.name}")
        return downloaded_file.rename(target_path)

    def _build_target_filename(self, item, downloaded_file: Path):
        extension = downloaded_file.suffix or ".xlsx"
        stem = self._build_target_stem(item, downloaded_file)
        if stem.lower().endswith(extension.lower()):
            return stem
        return f"{stem}{extension}"

    def _build_target_stem(self, item, downloaded_file: Path):
        if item.filename:
            return self._sanitize_filename(Path(item.filename).stem)
        return self._sanitize_filename(downloaded_file.stem)

    def _sanitize_filename(self, value: str):
        normalized = _SAFE_FILENAME_RE.sub("_", value).strip("._-")
        return normalized or "invoice_download"

    def _validate_download_url(self, url: str):
        parsed = urlparse(url)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise InvalidUrlError(f"invalid URL: {url}")

    def _normalize_download_error(self, exc: Exception):
        if isinstance(exc, DownloadError):
            return exc
        if isinstance(exc, WebDriverException):
            message = str(exc)
            lower_message = message.lower()
            if "404" in lower_message:
                return Http404Error(message)
            if "invalid" in lower_message and "url" in lower_message:
                return InvalidUrlError(message)
            return TemporaryBrowserError(message)
        return exc

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
