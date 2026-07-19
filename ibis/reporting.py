"""Structured run reporting for IBIS Auto Downloader (Build 3.1 – Task 1).

This module is intentionally self-contained and has no dependencies on
DownloaderEngine, AutoRecovery, or Scheduler.  It is integrated exclusively
from main.py.

Public API
----------
RunReport
    Dataclass that holds all statistics for a single run.
ReportWriter
    Writes both JSON and HTML reports to the ``reports/`` directory.
"""

from __future__ import annotations

import html as _html
import json
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from config import PROJECT_ROOT

REPORTS_DIR = PROJECT_ROOT / "reports"

_TIMESTAMP_FORMAT = "%Y-%m-%d_%H%M%S"


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------


@dataclass
class RunReport:
    """All statistics captured for a single IBIS run.

    Parameters
    ----------
    start_time : datetime
        UTC datetime at which the workflow began.
    end_time : datetime
        UTC datetime at which the workflow ended (success or failure).
    billing_period : str | None
        The billing period processed in this run (e.g. ``"202605"``).
    total_found : int
        Total number of invoices found (scanned from the grid).
    downloaded : int
        Number of invoices successfully downloaded.
    skipped : int
        Number of invoices skipped (already completed).
    failed : int
        Number of invoices that failed to download.
    retried : int
        Number of download items that were retried at least once.
    recovery_count : int
        Number of automatic recovery attempts that took place.
    recovery_report_location : str | None
        Path to the crash recovery report file, if recovery occurred.
    final_status : str
        One of ``"Success"``, ``"Partial"``, or ``"Failed"``.
    """

    start_time: datetime
    end_time: datetime
    billing_period: Optional[str] = None
    total_found: int = 0
    downloaded: int = 0
    skipped: int = 0
    failed: int = 0
    retried: int = 0
    recovery_count: int = 0
    recovery_report_location: Optional[str] = None
    final_status: str = "Success"

    # ------------------------------------------------------------------
    # Computed helpers
    # ------------------------------------------------------------------

    @property
    def elapsed_seconds(self) -> float:
        """Wall-clock duration of the run in seconds."""
        return (self.end_time - self.start_time).total_seconds()

    @property
    def elapsed_human(self) -> str:
        """Human-readable elapsed time (e.g. ``"1m 23s"`` or ``"45s"``)."""
        total = int(self.elapsed_seconds)
        minutes, seconds = divmod(total, 60)
        if minutes:
            return f"{minutes}m {seconds}s"
        return f"{seconds}s"

    # ------------------------------------------------------------------
    # Serialisation
    # ------------------------------------------------------------------

    def to_dict(self) -> dict:
        """Return a JSON-serialisable dictionary representation."""
        return {
            "start_time": self.start_time.isoformat(),
            "end_time": self.end_time.isoformat(),
            "elapsed_seconds": round(self.elapsed_seconds, 3),
            "elapsed_human": self.elapsed_human,
            "billing_period": self.billing_period,
            "total_found": self.total_found,
            "downloaded": self.downloaded,
            "skipped": self.skipped,
            "failed": self.failed,
            "retried": self.retried,
            "recovery_count": self.recovery_count,
            "recovery_report_location": self.recovery_report_location,
            "final_status": self.final_status,
        }

    @classmethod
    def compute_status(cls, downloaded: int, failed: int, total_found: int) -> str:
        """Derive the final status string from counters.

        Rules
        -----
        - ``"Failed"``  – nothing was downloaded and at least one failed, or
                          everything failed.
        - ``"Partial"`` – some downloaded, some failed.
        - ``"Success"`` – everything succeeded (no failures).
        """
        if failed == 0:
            return "Success"
        if downloaded == 0:
            return "Failed"
        return "Partial"


# ---------------------------------------------------------------------------
# Report writer
# ---------------------------------------------------------------------------


class ReportWriter:
    """Writes JSON and HTML run reports to ``reports/``.

    Parameters
    ----------
    reports_dir : Path | str | None
        Directory in which reports are saved.  Defaults to
        ``PROJECT_ROOT/reports``.
    """

    def __init__(self, reports_dir: Optional[Path] = None) -> None:
        self.reports_dir = Path(reports_dir) if reports_dir is not None else REPORTS_DIR

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def write(self, report: RunReport) -> tuple[Path, Path]:
        """Write JSON and HTML reports for *report*.

        Returns
        -------
        tuple[Path, Path]
            ``(json_path, html_path)`` of the created files.
        """
        self.reports_dir.mkdir(parents=True, exist_ok=True)
        stem = report.start_time.strftime(_TIMESTAMP_FORMAT)
        json_path = self.reports_dir / f"{stem}_report.json"
        html_path = self.reports_dir / f"{stem}_report.html"

        self._write_json(report, json_path)
        self._write_html(report, html_path)

        return json_path, html_path

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    @staticmethod
    def _write_json(report: RunReport, path: Path) -> None:
        with path.open("w", encoding="utf-8") as fh:
            json.dump(report.to_dict(), fh, indent=2, sort_keys=True)

    @staticmethod
    def _write_html(report: RunReport, path: Path) -> None:
        d = report.to_dict()

        def _e(v) -> str:
            return _html.escape(str(v) if v is not None else "—")

        status_colour = {
            "Success": "#2e7d32",
            "Partial": "#e65100",
            "Failed": "#c62828",
        }.get(report.final_status, "#333")

        recovery_rows = ""
        if report.recovery_count:
            loc = _e(report.recovery_report_location or "N/A")
            recovery_rows = f"""
    <tr><th>Recovery Attempts</th><td>{_e(report.recovery_count)}</td></tr>
    <tr><th>Recovery Report</th><td>{loc}</td></tr>"""

        html_content = f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>IBIS Run Report – {_e(d['start_time'])}</title>
  <style>
    body {{ font-family: Arial, sans-serif; margin: 2em; color: #333; }}
    h1 {{ color: #1a237e; }}
    table {{ border-collapse: collapse; width: 100%; max-width: 600px; }}
    th, td {{ border: 1px solid #ccc; padding: 0.5em 1em; text-align: left; }}
    th {{ background: #e8eaf6; width: 200px; }}
    .status {{ font-weight: bold; color: {status_colour}; font-size: 1.2em; }}
  </style>
</head>
<body>
  <h1>IBIS Auto Downloader – Run Report</h1>
  <table>
    <tr><th>Final Status</th><td class="status">{_e(report.final_status)}</td></tr>
    <tr><th>Start Time</th><td>{_e(d['start_time'])}</td></tr>
    <tr><th>End Time</th><td>{_e(d['end_time'])}</td></tr>
    <tr><th>Elapsed</th><td>{_e(d['elapsed_human'])}</td></tr>
    <tr><th>Billing Period</th><td>{_e(d['billing_period'])}</td></tr>
    <tr><th>Total Found</th><td>{_e(d['total_found'])}</td></tr>
    <tr><th>Downloaded</th><td>{_e(d['downloaded'])}</td></tr>
    <tr><th>Skipped</th><td>{_e(d['skipped'])}</td></tr>
    <tr><th>Failed</th><td>{_e(d['failed'])}</td></tr>
    <tr><th>Retried</th><td>{_e(d['retried'])}</td></tr>{recovery_rows}
  </table>
</body>
</html>
"""
        with path.open("w", encoding="utf-8") as fh:
            fh.write(html_content)
