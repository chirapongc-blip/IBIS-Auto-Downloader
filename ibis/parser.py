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

        metadata = _extract_invoice_metadata(a)

        links.append(
            {
                "url": urljoin(base_url, href),
                "title": a.get("title", ""),
                "invoice_id": metadata["invoice_id"],
                "billing_period": metadata["billing_period"],
                "filename": metadata["filename"],
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


def _extract_invoice_metadata(anchor):
    row = _find_data_row(anchor)
    if row is None:
        return {"invoice_id": None, "billing_period": None, "filename": None}

    cells = row.find_all("td", recursive=False)
    if not cells:
        return {"invoice_id": None, "billing_period": None, "filename": None}

    filename = _get_cell_anchor_text(cells, 1)
    invoice_id = _get_cell_text(cells, 2)
    billing_period = _get_cell_text(cells, 10)

    return {
        "invoice_id": invoice_id or None,
        "billing_period": billing_period or None,
        "filename": filename or None,
    }


def _find_data_row(anchor):
    for parent in anchor.parents:
        if parent.name != "tr":
            continue

        row_id = parent.get("id", "")
        row_classes = parent.get("class", [])

        if "DXDataRow" in row_id or any("dxgvDataRow" in cls for cls in row_classes):
            return parent

    return None


def _get_cell_text(cells, index: int):
    if index >= len(cells):
        return ""
    return cells[index].get_text(strip=True)


def _get_cell_anchor_text(cells, index: int):
    if index >= len(cells):
        return ""

    anchor = cells[index].find("a")
    if anchor is None:
        return ""

    return anchor.get_text(strip=True)