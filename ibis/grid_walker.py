import re

from bs4 import BeautifulSoup
from selenium.common.exceptions import TimeoutException, WebDriverException
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait

from ibis.grid import GRID_ID, get_page_info, wait_for_grid
from ibis.parser import extract_invoice_links


PAGER_SELECTOR = (
    "[class*='dxgvPagerBottomPanel'], "
    "[class*='dxgvPagerTopPanel']"
)
PAGER_ACTION_RE = re.compile(
    r"GVPagerOnClick\(\s*['\"]([^'\"]+)['\"]\s*,\s*['\"]([^'\"]+)['\"]\s*\)"
)
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

    summary = pager.select_one(".dxp-summary")
    if summary:
        match = PAGER_SUMMARY_RE.search(summary.get_text(" ", strip=True))
        if match:
            current_from_summary = int(match.group(1))
            total_from_summary = int(match.group(2))
            if total_from_summary > 0:
                info["current_page"] = current_from_summary
                info["total_pages"] = total_from_summary

    current_page = info.get("current_page", 1)
    total_pages = info.get("total_pages", 0)
    if total_pages > 0:
        info["has_next_page"] = current_page < total_pages
    else:
        info["has_next_page"] = _pager_has_next_action(pager, grid_id, current_page)

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
            raise RuntimeError(
                f"Page navigation failed: pager repeated page {current_page}."
            )

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


def _pager_has_next_action(pager, grid_id: str, current_page: int) -> bool:
    return _find_next_action_from_anchors(
        pager.select("a[onclick*='GVPagerOnClick']"),
        grid_id,
        current_page,
    ) is not None


def _extend_unique_links(links, seen_urls, page_links):
    for link in page_links:
        url = link["url"]
        if url in seen_urls:
            continue
        seen_urls.add(url)
        links.append(link)


def _go_to_next_page(driver, current_page: int, grid_id: str, timeout: int):
    next_target = _find_next_button(driver, grid_id, current_page)
    if next_target is None:
        raise RuntimeError(
            "DevExpress pager was detected but no usable next-page control "
            "was found in the DOM."
        )

    next_button, next_action = next_target
    if _invoke_js_pager(driver, grid_id, next_action, timeout, grid_id, current_page):
        wait_for_grid(driver, timeout)
        return

    try:
        driver.execute_script("arguments[0].click();", next_button)
    except WebDriverException as exc:
        raise RuntimeError(
            f"Failed to navigate from page {current_page} using pager controls."
        ) from exc

    if not _wait_for_page_advance(driver, timeout, grid_id, current_page):
        raise RuntimeError(
            f"Page navigation failed: could not advance from page {current_page}."
        )
    wait_for_grid(driver, timeout)


def _find_next_button(driver, grid_id: str, current_page: int):
    candidates = driver.find_elements(
        By.CSS_SELECTOR,
        "[class*='dxgvPagerBottomPanel'] a[onclick*='GVPagerOnClick'],"
        " [class*='dxgvPagerTopPanel'] a[onclick*='GVPagerOnClick']",
    )
    return _find_next_action_from_anchors(candidates, grid_id, current_page)


def _find_next_action_from_anchors(anchors, grid_id: str, current_page: int):
    fallback = None
    fallback_target_page = None

    for anchor in anchors:
        if hasattr(anchor, "get_attribute"):
            onclick = anchor.get_attribute("onclick") or ""
        else:
            onclick = anchor.get("onclick") or ""
        match = PAGER_ACTION_RE.search(onclick)
        if not match or match.group(1) != grid_id:
            continue

        action = match.group(2)
        if action == "PBN":
            return anchor, action

        if not action.startswith("PN"):
            continue

        page_index_text = action[2:]
        if not page_index_text.isdigit():
            continue

        target_page = int(page_index_text) + 1
        if target_page <= current_page:
            continue

        if fallback_target_page is None or target_page < fallback_target_page:
            fallback_target_page = target_page
            fallback = (anchor, action)

    return fallback


def _invoke_js_pager(driver, grid_id: str, action: str, timeout: int, wait_grid_id: str, current_page: int) -> bool:
    try:
        driver.execute_script(
            "ASPx.GVPagerOnClick(arguments[0], arguments[1]);",
            grid_id,
            action,
        )
    except WebDriverException:
        return False

    return _wait_for_page_advance(driver, timeout, wait_grid_id, current_page)


def _wait_for_page_advance(driver, timeout: int, grid_id: str, current_page: int) -> bool:
    try:
        WebDriverWait(driver, timeout).until(
            lambda d: get_devexpress_pager_info(d.page_source, grid_id).get(
                "current_page", 1
            )
            > current_page
        )
    except TimeoutException:
        return False

    return True
