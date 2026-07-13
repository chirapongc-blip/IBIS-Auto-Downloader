from pathlib import Path

from config import BASE_URL
from ibis.parser import extract_invoice_links


html = Path("invoices.html").read_text(encoding="utf-8")

links = extract_invoice_links(html, BASE_URL)

print(f"Found {len(links)} download links")
