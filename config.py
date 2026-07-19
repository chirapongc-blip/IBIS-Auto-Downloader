from pathlib import Path

VERSION = "2.7"
# Project root
PROJECT_ROOT = Path(__file__).resolve().parent

# Download folder
DOWNLOAD_DIR = PROJECT_ROOT / "downloads"

# Log folder
LOG_DIR = PROJECT_ROOT / "logs"

# State folder
STATE_DIR = PROJECT_ROOT / "state"

# Reports folder (Build 3.1)
REPORTS_DIR = PROJECT_ROOT / "reports"

# IBIS URL
BASE_URL = "https://stationsatcom.satcomhost.com"

INVOICE_URL = f"{BASE_URL}/invoices.aspx"

# Chrome download timeout (seconds)
DOWNLOAD_TIMEOUT = 120

# ── Scheduler (Build 2.7) ────────────────────────────────────────────────────
# Set SCHEDULER_ENABLED=True to activate configurable scheduling.
# When False (default) the application runs once and exits (Build 2.6 behaviour).
SCHEDULER_ENABLED = False

# SCHEDULER_MODE controls the execution cadence.
#   "immediate" – run once and exit (same as Build 2.6).
#   "daily"     – run every day at SCHEDULE_HOUR:SCHEDULE_MINUTE UTC.
#   "monthly"   – run on SCHEDULE_DAY of every month at SCHEDULE_HOUR:SCHEDULE_MINUTE UTC.
SCHEDULER_MODE = "immediate"

# Day-of-month used only for SCHEDULER_MODE="monthly" (1–31).
# If the configured day does not exist in a given month the last valid day is used.
SCHEDULE_DAY = 1

# Hour (0–23) and minute (0–59) in UTC at which the job fires.
SCHEDULE_HOUR = 0
SCHEDULE_MINUTE = 0
