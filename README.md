# IBIS Auto Downloader

IBIS Auto Downloader collects invoice exports from the IBIS Invoice page and
stores a durable record of each download. It supports billing-period selection,
resume verification, recovery from browser-session failures, dry-run previews,
and per-run JSON, CSV, and HTML reports.

## Quick Start

```bash
python3 main.py
```

Chrome opens at the IBIS sign-in page. Complete sign-in in the browser; the
application then discovers invoices for the latest available billing period and
downloads the eligible exports.

To preview without downloading files or changing state:

```bash
python3 main.py --dry-run
```

## Installation

1. Install a supported Python version and Google Chrome.
2. Clone this repository and enter its directory.
3. Install the project dependencies using the repository's dependency setup.
4. Run `python3 main.py --help` to review the available commands.

The application intentionally uses manual IBIS login. It does not store or
submit IBIS credentials.

## Python Requirements

Use Python 3.10 or later. The project also requires the packages listed by the
repository dependency configuration, including Selenium for browser control.

## Command-Line Options

| Option | Purpose |
| --- | --- |
| `--version` | Print the application name and version, then exit. |
| `--show-config` | Print read-only runtime configuration, then exit. |
| `--billing-period latest` | Download the latest available billing period. This is the default. |
| `--billing-period YYYYMM` | Download one billing period, for example `202605`. |
| `--billing-period YYYYMM,YYYYMM` | Download the specified periods. |
| `--billing-period all` | Download all available billing periods. |
| `--dry-run` | Discover, filter, and preview the queue without downloading or changing state. |
| `--download-dir PATH` | Use a different download directory for this run. |
| `--report-dir PATH` | Use a different report directory for this run. |

Examples:

```bash
python3 main.py --version
python3 main.py --show-config
python3 main.py --dry-run
python3 main.py --billing-period 202605,202604
python3 main.py --download-dir ~/Downloads --report-dir ~/Reports
```

## Dry Run

Dry run follows the normal read-only preparation path: it opens the browser,
waits for manual login, discovers all invoice-grid pages, selects billing
periods, checks resume information, and builds the actual queue. It prints a
preview and writes reports, but does not click download controls, create Excel
files, update state files, invoke download retries, or invoke AutoRecovery.

## Resume

The state directory records interrupted sessions and completed invoices using
the stable identity `(invoice_id, billing_period)`. Before an invoice is
skipped, the application verifies its recorded output still exists. Missing,
renamed, or moved output is requeued and the reason is logged. This keeps a
completed record from masking a missing download.

## Retry and Recovery

Temporary download errors use the configured exponential retry policy. Session
and WebDriver failures enter AutoRecovery instead of being retried as ordinary
download failures. Recovery creates a fresh browser, navigates to IBIS, waits
for manual login, reopens the Invoice page, and rebuilds only the remaining
queue in the original billing-period scope. Permanent failures are not retried.

## Reports

Every controlled terminal run produces a report under `reports/` by default:

```text
<run_id>_summary.json
<run_id>_summary.csv
<run_id>_summary.html
```

Reports contain run status, selected billing periods, queue totals, retry and
recovery totals, and per-invoice results. Use `--report-dir` to write them to a
different directory for one run.

## Troubleshooting

- Run `python3 main.py --show-config` to confirm the effective directories,
  version, runtime, and defaults without opening Chrome.
- Run `python3 main.py --dry-run` to inspect the queue safely before a normal
  download.
- Review the timestamped files in `logs/` for full exception tracebacks and
  retry/recovery diagnostics.
- If a browser recovery opens a fresh window, complete IBIS login manually and
  wait for the application to reopen the Invoice page.
- If a previously completed invoice is queued again, verify whether its output
  file was moved, renamed, or removed; requeueing is intentional in that case.
