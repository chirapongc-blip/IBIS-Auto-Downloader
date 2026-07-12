from bs4 import BeautifulSoup
from urllib.parse import urljoin


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
