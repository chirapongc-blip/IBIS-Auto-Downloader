import time
import warnings
import logging
from dataclasses import dataclass
from pathlib import Path

from ibis.downloader import get_download_dir, STATUS_PENDING


STATUS_DOWNLOADING = "downloading"
STATUS_COMPLETED = "completed"
STATUS_FAILED = "failed"
STATUS_SKIPPED = "skipped"
_INCOMPLETE_SUFFIXES = (".crdownload", ".part", ".tmp")

MAX_RETRIES = 3
DEBUG_DOWNLOAD_DIAGNOSTICS = False
LOGGER = logging.getLogger(__name__)


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

    def _debug(self, message, **fields):
        if not DEBUG_DOWNLOAD_DIAGNOSTICS:
            return
        payload = f"{message} | {', '.join(f'{k}={v}' for k, v in fields.items())}" if fields else message
        print(f"[DEBUG] {payload}", flush=True)
        if fields:
            LOGGER.debug("%s | %s", message, ", ".join(f"{k}={v}" for k, v in fields.items()))
            return
        LOGGER.debug("%s", message)

    @staticmethod
    def _format_path_set(paths):
        return [str(path) for path in sorted(paths)]

    @staticmethod
    def _format_stats(stats):
        return {
            str(path): {"mtime": mtime, "size": size}
            for path, (mtime, size) in sorted(stats.items(), key=lambda item: str(item[0]))
        }

    def run(self, plan):
        self.summary = DownloadSummary(total_files=len(plan.scheduled_items))
        started_at = time.monotonic()
        for item in plan.scheduled_items:
            self._download_item(item)
        self._print_summary(time.monotonic() - started_at)

    def _download_item(self, item):
        self._set_status(item, STATUS_PENDING)
        self._set_status(item, STATUS_DOWNLOADING)
        success = False
        downloaded_file = None
        renamed = None

        for attempt in range(MAX_RETRIES + 1):
            existing_files = self._snapshot_files()
            existing_stats = self._snapshot_file_stats(existing_files)
            target_name = self._build_target_filename(item)
            self._debug(
                "Download attempt started",
                attempt=attempt + 1,
                max_attempts=MAX_RETRIES + 1,
                invoice_id=item.invoice_id,
                target_filename=target_name or item.filename,
                existing_files=self._format_path_set(existing_files),
                existing_stats=self._format_stats(existing_stats),
            )
            try:
                self.driver.get(item.download_url)
                downloaded_file = self._wait_for_download(existing_files, existing_stats)

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
                success = True
                break

            except Exception as exc:
                item.last_error = str(exc)
                should_retry = is_retryable(exc) and attempt < MAX_RETRIES
                if should_retry:
                    self._debug(
                        "Retry scheduled",
                        attempt=attempt + 1,
                        reason=str(exc),
                    )
                if not is_retryable(exc) or attempt == MAX_RETRIES:
                    break
                item.retry_count += 1

        if success:
            self._debug(
                "Download attempt completed",
                attempt=attempt + 1,
                selected_file=str(downloaded_file),
                final_file=str(renamed),
            )
            self._finalize_item(item, STATUS_COMPLETED)
        else:
            self._debug(
                "Download failed",
                invoice_id=item.invoice_id,
                target_filename=item.filename or self._build_target_filename(item),
                final_failure_reason=item.last_error,
            )
            self._finalize_item(item, STATUS_FAILED)

    def _wait_for_download(self, existing_files, existing_stats=None):
        deadline = time.monotonic() + self.timeout

        while time.monotonic() < deadline:
            downloaded_file = self._find_new_completed_file(existing_files)
            if downloaded_file is not None:
                self._debug(
                    "Primary detection succeeded",
                    primary_success=True,
                    secondary_entered=False,
                    selected_file=str(downloaded_file),
                )
                return downloaded_file
            time.sleep(self.poll_interval)

        # Primary 'new file' detection timed out.  Only then try the secondary
        # path: look for an existing file that was atomically overwritten.
        self._debug("Primary detection timed out", primary_success=False, secondary_entered=bool(existing_stats))
        if existing_stats:
            selected_file = self._find_overwritten_file(existing_stats)
            self._debug(
                "Secondary detection completed",
                secondary_entered=True,
                selected_file=str(selected_file) if selected_file else None,
            )
            return selected_file
        return None

    def _find_new_completed_file(self, existing_files):
        current_files = self._snapshot_files()
        new_files = current_files - existing_files
        self._debug("Primary detection scan", new_files=self._format_path_set(new_files))
        candidates = []
        for file_path in sorted(
            new_files,
            key=lambda path: path.stat().st_mtime,
            reverse=True,
        ):
            candidates.append(str(file_path))
            # Skip hidden files (e.g. Chrome temp files like .com.google.Chrome.XXXXX)
            if file_path.name.startswith("."):
                continue
            if file_path.suffix.lower() in _INCOMPLETE_SUFFIXES:
                continue
            # Skip if the matching .crdownload companion still exists — the
            # browser hasn't finished writing the file yet.
            if (file_path.parent / (file_path.name + ".crdownload")) in current_files:
                continue
            self._debug("Primary detection selected candidate", candidates=candidates, selected_file=str(file_path))
            return file_path
        self._debug("Primary detection found no candidate", candidates=candidates, selected_file=None)
        return None

    def _snapshot_file_stats(self, file_set):
        """Return ``{path: (mtime, size)}`` for every path in *file_set* that
        can be stat-ed.  Called once per download attempt so that the secondary
        overwrite-detection path has a baseline to compare against."""
        stats = {}
        for path in file_set:
            try:
                st = path.stat()
                stats[path] = (st.st_mtime, st.st_size)
            except OSError:
                pass
        return stats

    def _find_overwritten_file(self, existing_stats):
        """Secondary detection: find a pre-existing file atomically overwritten
        in-place.

        A file qualifies only when **both** its mtime *and* its size differ
        from the baseline captured in *existing_stats*.  Checking size as well
        as mtime guards against false positives from OS clock granularity or
        touch-only updates.

        This method is intentionally called **only** after the primary
        'new file' detection loop has already timed out; it must never replace
        that primary path.
        """
        candidates = []
        for path, (old_mtime, old_size) in existing_stats.items():
            candidate_data = {
                "path": str(path),
                "old_mtime": old_mtime,
                "old_size": old_size,
            }
            if path.suffix.lower() in _INCOMPLETE_SUFFIXES:
                candidate_data["skipped"] = "incomplete_suffix"
                candidates.append(candidate_data)
                continue
            try:
                st = path.stat()
            except OSError:
                candidate_data["skipped"] = "stat_error"
                candidates.append(candidate_data)
                continue
            candidate_data["new_mtime"] = st.st_mtime
            candidate_data["new_size"] = st.st_size
            candidate_data["mtime_changed"] = st.st_mtime != old_mtime
            candidate_data["size_changed"] = st.st_size != old_size
            if st.st_mtime == old_mtime or st.st_size == old_size:
                candidate_data["skipped"] = "missing_required_change"
                candidates.append(candidate_data)
                continue
            # Also skip while a .crdownload companion is present.
            if (path.parent / (path.name + ".crdownload")).exists():
                candidate_data["skipped"] = "companion_crdownload_exists"
                candidates.append(candidate_data)
                continue
            candidate_data["selected"] = True
            candidates.append(candidate_data)
            self._debug("Secondary detection selected candidate", candidates=candidates, selected_file=str(path))
            return path
        self._debug("Secondary detection found no candidate", candidates=candidates, selected_file=None)
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
        """Rename *downloaded_file* to a meaningful name; return the final path.

        Guards:
        - Never renames an incomplete file (e.g. .crdownload).
        - If the target already exists, appends a numeric suffix to avoid collision.
        - Logs a warning instead of silently swallowing rename failures.
        """
        if downloaded_file.suffix.lower() in _INCOMPLETE_SUFFIXES:
            return downloaded_file

        target_name = self._build_target_filename(item)
        if target_name is None or target_name == downloaded_file.name:
            self._debug(
                "Rename skipped",
                source_path=str(downloaded_file),
                target_path=str(downloaded_file),
            )
            return downloaded_file

        target_path = self._unique_path(downloaded_file.parent / target_name)
        self._debug(
            "Renaming downloaded file",
            source_path=str(downloaded_file),
            target_path=str(target_path),
        )
        try:
            downloaded_file.rename(target_path)
            return target_path
        except OSError as exc:
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
        elif status == STATUS_FAILED:
            self.summary.failed += 1
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
