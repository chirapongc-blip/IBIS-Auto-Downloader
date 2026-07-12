from bs4 import BeautifulSoup
from urllib.parse import urljoin
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

        links.append(
            {
                "url": urljoin(base_url, href),
                "title": a.get("title", ""),
            }
        )

    return links


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