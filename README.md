# IBIS Auto Downloader

Automated invoice downloader for the IBIS/SATCOM billing portal.

## Overview

IBIS Auto Downloader opens a Chromium browser, waits for you to complete the
login, then automatically:

1. Navigates to the Invoices page.
2. Walks **every page** of the DevExpress grid (Build 2.2).
3. Collects all `DownloadARExport` links (duplicates removed).
4. Filters to the most-recent billing period (configurable).
5. Downloads every file in sequence via the existing Build 2.1 engine.

## Requirements

- Python 3.10+
- Google Chrome / Chromium
- ChromeDriver (matching your Chrome version)

```
pip install -r requirements.txt
```

## Usage

```
python main.py
```

The browser opens at `BASE_URL`. Complete the IBIS login manually. The script
takes over once it detects the post-login page.

Downloaded files are saved to `downloads/`.

## Configuration

Edit `config.py`:

| Variable          | Default                             | Description               |
|-------------------|-------------------------------------|---------------------------|
| `BASE_URL`        | `https://stationsatcom.satcomhost.com` | Portal root URL        |
| `DOWNLOAD_DIR`    | `./downloads`                       | Download destination      |
| `DOWNLOAD_TIMEOUT`| `120`                               | Per-file timeout (seconds)|

## Architecture

```
main.py
├── ibis/browser.py          – Selenium driver factory
├── ibis/login.py            – Wait for successful login
├── ibis/invoice.py          – Navigate to the Invoices page
├── ibis/grid.py             – Grid element utilities
├── ibis/grid_walker.py      – DevExpress multi-page walker (Build 2.2)
├── ibis/parser.py           – Extract DownloadARExport links from HTML
├── ibis/downloader.py       – DownloadQueue / DownloadQueueItem
├── ibis/scheduler.py        – DownloadPlan (period filter + deduplication)
└── ibis/downloader_engine.py– Download loop with retry logic (Build 2.1)
```

### Build 2.2 – Grid Pagination (`ibis/grid_walker.py`)

`collect_grid_download_links(driver, base_url)` is the main entry point.

- Reads `pageIndex` / `pageCount` from the DevExpress initialisation script.
- Falls back to the `Page N of M` pager summary when the script is absent.
- Clicks the **Next** pager button via `ASPx.GVPagerOnClick` JavaScript call;
  falls back to a DOM click if the JS call fails.
- Guards against infinite loops: raises `RuntimeError` if the same page
  number is returned twice.
- Deduplicates links by URL across all pages before returning.

### Build 2.1 – Download Engine (`ibis/downloader_engine.py`)

`DownloaderEngine.run(plan)` iterates `DownloadPlan.scheduled_items` and
downloads each file by calling `driver.get(url)`.  Each file is monitored in
`downloads/` until it appears (polling, configurable timeout).  Transient
failures are retried up to `MAX_RETRIES` (3) times.

## Running Tests

```
python -m unittest
```

All tests are pure-Python unit tests with no browser or network required.
