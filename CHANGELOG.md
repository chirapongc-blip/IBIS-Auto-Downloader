# Changelog

All notable changes are documented here. Versions before the first stable
release are beta milestones.

## 3.5.0-beta1

- Centralized the application name and version in `ibis/version.py`.
- Added `--version`, `--download-dir`, and `--report-dir`.
- Kept per-run directory overrides compatible with download and reporting
  behavior.

## 3.4.0-beta1

- Added structured performance instrumentation and a performance summary.
- Optimized safe grid traversal, DOM lookup, and download-completion polling.

## 3.3.0-beta1

- Added dry-run queue previews and dry-run report output.
- Preserved read-only state and download behavior during preview runs.

## 3.2.0-beta1

- Added JSON, CSV, and HTML run reports with per-invoice detail.
- Added controlled terminal-run reporting and explicit run status fields.

## 3.1.0-beta1

- Added billing-period discovery and command-line selection.
- Preserved invoice identity as `(invoice_id, billing_period)` across resume
  and recovery workflows.

## 3.0.1-beta3

- Added structured retry classification and resilient browser/session
  AutoRecovery.
- Added controlled fault injection for recovery validation and corrected
  recovery-state consistency and download-race handling.

## 3.0.1-beta2

- Added resume verification that requeues completed invoices whose output is
  missing.

## 3.0.1-beta1

- Added rotating diagnostic logging, configured download timeouts, stable
  download-completion detection, and server-provided file-extension handling.
