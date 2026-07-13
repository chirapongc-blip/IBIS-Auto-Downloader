from bs4 import BeautifulSoup
from urllib.parse import parse_qsl, urljoin, urlparse
import re


def extract_invoice_links(html: str, base_url: str):
    """
    从 invoices.aspx HTML 中提取所有 DownloadARExport 链接
    """

    soup = BeautifulSoup(html, "lxml")
    links = []

    for a in soup.find_all("a", href=True):
        href = a["href"]

        if "DownloadARExport.aspx" not in href:
            continue

        url = urljoin(base_url, href)
        metadata = _extract_download_metadata(url)
        filename = _extract_filename_from_row(a)

        links.append(
            {
                "url": url,
                "title": a.get("title", ""),
                "invoice_id": metadata.get("invoice_id"),
                "billing_period": metadata.get("billing_period"),
                "filename": filename,
            }
        )

    return links


def _extract_download_metadata(url: str):
    query = dict(
        (key.strip(), value.strip())
        for key, value in parse_qsl(urlparse(url).query, keep_blank_values=True)
    )

    return {
        "invoice_id": query.get("InvoiceID") or None,
        "billing_period": query.get("BillingPeriod") or None,
    }


def _extract_filename_from_row(anchor):
    for row in anchor.find_parents("tr"):
        for candidate in row.find_all("a", href=True):
            href = candidate.get("href", "")
            text = candidate.get_text(strip=True)
            if "RetrieveInvoice.aspx" in href and text:
                return text

    return None


def get_page_info(html: str):
    """
    从 invoices HTML 中读取分页信息。
    返回：
        current_page
        total_pages
        rows_per_page
    """

    info = {}

    m = re.search(r"'pageIndex':(\d+)", html)
    if m:
        info["current_page"] = int(m.group(1)) + 1

    m = re.search(r"'pageCount':(\d+)", html)
    if m:
        info["total_pages"] = int(m.group(1))

    m = re.search(r"'pageRowCount':(\d+)", html)
    if m:
        info["rows_per_page"] = int(m.group(1))

    return info