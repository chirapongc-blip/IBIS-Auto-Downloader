from pathlib import Path

VERSION = "2.5.0"
# Project root
PROJECT_ROOT = Path(__file__).resolve().parent

# Download folder
DOWNLOAD_DIR = PROJECT_ROOT / "downloads"

# Log folder
LOG_DIR = PROJECT_ROOT / "logs"

# State folder
STATE_DIR = PROJECT_ROOT / "state"

# IBIS URL
BASE_URL = "https://stationsatcom.satcomhost.com"

INVOICE_URL = f"{BASE_URL}/invoices.aspx"

# Chrome download timeout (seconds)
DOWNLOAD_TIMEOUT = 120
