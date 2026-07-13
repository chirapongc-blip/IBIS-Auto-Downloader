import re

from bs4 import BeautifulSoup
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait

from ibis.grid import GRID_ID, get_page_info, wait_for_grid
from ibis.parser import extract_invoice_links


PAGER_SELECTOR = ".dxgvPagerBottomPanel, .dxgvPagerTopPanel"
PAGER_ACTION_RE = re.compile(r"GVPagerOnClick\('([^']+)','([^']+)'\)")
PAGER_SUMMARY_RE = re.compile(r"Page\s+(\d+)\s+of\s+(\d+)", re.IGNORECASE)


def get_devexpress_pager_info(html: str, grid_id: str = GRID_ID):
    """
    Returns a dict with:
        current_page    - 1-based page number
        total_pages     - total page count
        rows_per_page   - rows per page
        has_pager       - True when a DevExpress pager for grid_id is present
        has_next_page   - True when the Next (PBN) button is enabled
    """
    info = dict(get_page_info(html))
    soup = BeautifulSoup(html, "lxml")
    pager = _find_pager(soup, grid_id)

    info["has_pager"] = pager is not None
    info["has_next_page"] = False

    if pager is None:
        return info

    if "current_page" not in info or "total_pages" not in info:
        summary = pager.select_one(".dxp-summary")
        if summary:
            match = PAGER_SUMMARY_RE.search(summary.get_text(" ", strip=True))
            if match:
                info.setdefault("current_page", int(match.group(1)))
                info.setdefault("total_pages", int(match.group(2)))

    info["has_next_page"] = (
        pager.select_one(f"a[onclick*=\"GVPagerOnClick('{grid_id}','PBN')\"]")
        is not None
    )

    return info


def collect_grid_download_links(
    driver, base_url: str, grid_id: str = GRID_ID, timeout: int = 30
):
    """
    Walk every page of the DevExpress grid and return all DownloadARExport links.

    Detects the pager automatically.  If no pager is present (single page), the
    current page is scraped and the function returns immediately.

    Returns a list of dicts: [{"url": "...", "title": "..."}]
    """
    wait_for_grid(driver, timeout)

    links = []
    seen_urls = set()
    visited_pages = set()

    while True:
        html = driver.page_source
        pager_info = get_devexpress_pager_info(html, grid_id)
        current_page = pager_info.get("current_page", 1)

        if current_page in visited_pages:
            break

        visited_pages.add(current_page)
        _extend_unique_links(links, seen_urls, extract_invoice_links(html, base_url))

        if not pager_info.get("has_next_page"):
            break

        _go_to_next_page(driver, current_page, grid_id, timeout)

    return links


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _find_pager(soup: BeautifulSoup, grid_id: str):
    """Return the pager div that belongs to *grid_id*, or None."""
    for pager in soup.select(PAGER_SELECTOR):
        # Active pager: has a page-navigation onclick for this grid
        if pager.select_one(f"[onclick*=\"GVPagerOnClick('{grid_id}'\"]"):
            return pager
        # Last-page case: pager present but no next button; identify by child ID
        if pager.select_one(f"[id^='{grid_id}_DXPager']"):
            return pager
    return None


def _extend_unique_links(links, seen_urls, page_links):
    for link in page_links:
        url = link["url"]
        if url in seen_urls:
            continue
        seen_urls.add(url)
        links.append(link)


def _go_to_next_page(driver, current_page: int, grid_id: str, timeout: int):
    next_button = _find_next_button(driver, grid_id)
    if next_button is None:
        raise RuntimeError(
            "DevExpress pager was detected but the next-page control (PBN) "
            "was not found in the DOM."
        )

    onclick = next_button.get_attribute("onclick") or ""
    match = PAGER_ACTION_RE.search(onclick)

    if match:
        driver.execute_script(
            "ASPx.GVPagerOnClick(arguments[0], arguments[1]);",
            match.group(1),
            match.group(2),
        )
    else:
        driver.execute_script("arguments[0].click();", next_button)

    WebDriverWait(driver, timeout).until(
        lambda d: get_devexpress_pager_info(d.page_source, grid_id).get(
            "current_page", 1
        )
        > current_page
    )
    wait_for_grid(driver, timeout)


def _find_next_button(driver, grid_id: str):
    candidates = driver.find_elements(
        By.CSS_SELECTOR,
        ".dxgvPagerBottomPanel a[onclick*='GVPagerOnClick'],"
        " .dxgvPagerTopPanel a[onclick*='GVPagerOnClick']",
    )

    for candidate in candidates:
        onclick = candidate.get_attribute("onclick") or ""
        match = PAGER_ACTION_RE.search(onclick)
        if match and match.group(1) == grid_id and match.group(2) == "PBN":
            return candidate

    return None
