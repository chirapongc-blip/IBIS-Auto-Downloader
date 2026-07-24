"""Stable, run-level report generation for IBIS Auto Downloader.

The reporter is deliberately independent of browser and download execution.
It receives plain run metrics and invoice records from the orchestration layer,
then renders one canonical document as JSON, CSV, and HTML.
"""

from __future__ import annotations

import csv
import html
import json
from datetime import datetime, timezone
from pathlib import Path


REPORT_SCHEMA_VERSION = "1.0"
RUN_STATUSES = ("completed", "completed_with_failures", "failed")
INVOICE_FIELDS = (
    "billing_period",
    "invoice_id",
    "filename",
    "final_status",
    "retry_count",
    "recovered",
    "elapsed_seconds",
)


class RunReporter:
    """Create JSON, CSV, and HTML reports for one completed application run."""

    def __init__(self, reports_dir=None, *, now_fn=None):
        if reports_dir is None:
            from config import PROJECT_ROOT

            reports_dir = PROJECT_ROOT / "reports"
        self.reports_dir = Path(reports_dir)
        self.now_fn = now_fn or (lambda: datetime.now(timezone.utc))

    def generate(self, run_id, *, start_time, end_time, selected_billing_periods,
                 invoices_discovered, queued, completed, skipped, retry_attempts,
                 successful_recoveries, permanent_failures, invoices,
                 run_status="completed", failure_stage=None, error_type=None,
                 error_message=None, dry_run=False):
        """Write all report formats and return their paths plus the document.

        ``invoices`` may contain dictionaries or queue/state objects.  Missing
        values are normalised so each generated format has the same stable
        per-invoice fields.
        """
        safe_run_id = self._safe_run_id(run_id)
        document = self.build_document(
            run_id=run_id,
            start_time=start_time,
            end_time=end_time,
            selected_billing_periods=selected_billing_periods,
            invoices_discovered=invoices_discovered,
            queued=queued,
            completed=completed,
            skipped=skipped,
            retry_attempts=retry_attempts,
            successful_recoveries=successful_recoveries,
            permanent_failures=permanent_failures,
            invoices=invoices,
            run_status=run_status,
            failure_stage=failure_stage,
            error_type=error_type,
            error_message=error_message,
            dry_run=dry_run,
        )
        self.reports_dir.mkdir(parents=True, exist_ok=True)
        paths = {
            "json": self.reports_dir / f"{safe_run_id}_summary.json",
            "csv": self.reports_dir / f"{safe_run_id}_summary.csv",
            "html": self.reports_dir / f"{safe_run_id}_summary.html",
        }
        self._write_json(paths["json"], document)
        self._write_csv(paths["csv"], document)
        self._write_html(paths["html"], document)
        return {"document": document, **paths}

    def build_document(self, *, run_id, start_time, end_time,
                       selected_billing_periods, invoices_discovered, queued,
                       completed, skipped, retry_attempts, successful_recoveries,
                       permanent_failures, invoices, run_status="completed",
                       failure_stage=None, error_type=None, error_message=None,
                       dry_run=False):
        """Build the versioned canonical report document without writing files."""
        start = self._normalise_datetime(start_time)
        end = self._normalise_datetime(end_time)
        elapsed_seconds = max(0.0, round((end - start).total_seconds(), 3))
        if run_status not in RUN_STATUSES:
            raise ValueError(f"unsupported run_status: {run_status}")
        return {
            "schema_version": REPORT_SCHEMA_VERSION,
            "run_id": str(run_id),
            "generation_timestamp": self._normalise_datetime(self.now_fn()).isoformat(),
            "start_time": start.isoformat(),
            "end_time": end.isoformat(),
            "elapsed_seconds": elapsed_seconds,
            "run_status": run_status,
            "failure_stage": self._nullable_text(failure_stage),
            "error_type": self._nullable_text(error_type),
            "error_message": self._nullable_text(error_message),
            "dry_run": bool(dry_run),
            "selected_billing_periods": list(selected_billing_periods or []),
            "invoices_discovered": self._count(invoices_discovered),
            "queued": self._count(queued),
            "completed": self._count(completed),
            "skipped": self._count(skipped),
            "retry_attempts": self._count(retry_attempts),
            "successful_recoveries": self._count(successful_recoveries),
            "permanent_failures": self._count(permanent_failures),
            "invoices": [self._normalise_invoice(invoice) for invoice in invoices],
        }

    @staticmethod
    def _count(value):
        return value if isinstance(value, int) and value >= 0 else 0

    @staticmethod
    def _nullable_text(value):
        return None if value is None else str(value)

    @staticmethod
    def _safe_run_id(run_id):
        text = str(run_id)
        if not text or Path(text).name != text:
            raise ValueError("run_id must be a non-empty filename-safe value")
        return text

    @staticmethod
    def _normalise_datetime(value):
        if not isinstance(value, datetime):
            raise TypeError("report timestamps must be datetime values")
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)

    @staticmethod
    def _value(invoice, field, default=None):
        if isinstance(invoice, dict):
            return invoice.get(field, default)
        return getattr(invoice, field, default)

    def _normalise_invoice(self, invoice):
        values = {
            "billing_period": self._value(invoice, "billing_period"),
            "invoice_id": self._value(invoice, "invoice_id"),
            "filename": self._value(invoice, "filename"),
            "final_status": self._value(
                invoice, "final_status", self._value(invoice, "download_status", "pending")
            ),
            "retry_count": self._count(self._value(invoice, "retry_count", 0)),
            "recovered": bool(self._value(invoice, "recovered", False)),
            "elapsed_seconds": self._number(self._value(invoice, "elapsed_seconds", 0.0)),
        }
        return values

    @staticmethod
    def _number(value):
        if isinstance(value, (int, float)) and value >= 0:
            return round(float(value), 3)
        return 0.0

    @staticmethod
    def _write_json(path, document):
        with Path(path).open("w", encoding="utf-8") as handle:
            json.dump(document, handle, ensure_ascii=False, indent=2, sort_keys=False)
            handle.write("\n")

    @staticmethod
    def _write_csv(path, document):
        summary_fields = (
            "run_id", "start_time", "end_time", "elapsed_seconds",
            "run_status", "failure_stage", "error_type", "error_message",
            "dry_run",
            "selected_billing_periods", "invoices_discovered", "queued",
            "completed", "skipped", "retry_attempts", "successful_recoveries",
            "permanent_failures",
        )
        fieldnames = ("record_type", *summary_fields, *INVOICE_FIELDS)
        with Path(path).open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=fieldnames)
            writer.writeheader()
            summary = {field: document[field] for field in summary_fields}
            summary["selected_billing_periods"] = ",".join(document["selected_billing_periods"])
            writer.writerow({"record_type": "summary", **summary})
            for invoice in document["invoices"]:
                writer.writerow({"record_type": "invoice", **invoice})

    @staticmethod
    def _write_html(path, document):
        summary_rows = "".join(
            f"<tr><th>{html.escape(key.replace('_', ' ').title())}</th>"
            f"<td>{html.escape(str(value))}</td></tr>"
            for key, value in document.items()
            if key not in {"schema_version", "invoices"}
        )
        headers = "".join(f"<th>{html.escape(field.replace('_', ' ').title())}</th>" for field in INVOICE_FIELDS)
        invoice_rows = "".join(
            "<tr>" + "".join(
                f"<td>{html.escape(str(invoice[field]))}</td>" for field in INVOICE_FIELDS
            ) + "</tr>"
            for invoice in document["invoices"]
        )
        content = f"""<!doctype html>
<html lang=\"en\"><head><meta charset=\"utf-8\"><title>IBIS Run Summary</title>
<style>body{{font-family:system-ui,sans-serif;margin:2rem}}table{{border-collapse:collapse;margin:1rem 0}}th,td{{border:1px solid #bbb;padding:.45rem;text-align:left}}th{{background:#f2f2f2}}</style>
</head><body><h1>IBIS Run Summary</h1><p>Generated: {html.escape(document['generation_timestamp'])}</p>
<h2>Summary</h2><table>{summary_rows}</table><h2>Invoices</h2>
<table><thead><tr>{headers}</tr></thead><tbody>{invoice_rows}</tbody></table>
<p>Totals: discovered {document['invoices_discovered']}, queued {document['queued']}, completed {document['completed']}, skipped {document['skipped']}.</p>
</body></html>"""
        Path(path).write_text(content, encoding="utf-8")
