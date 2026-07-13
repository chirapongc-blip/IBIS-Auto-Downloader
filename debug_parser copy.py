from config import BASE_URL
from ibis.browser import create_driver
from ibis.login import wait_until_logged_in
from ibis.invoice import open_invoice_page
from ibis.parser import extract_invoice_links

driver = create_driver()

driver.get(BASE_URL)

input("登录完成后按 Enter...")

html = open_invoice_page(driver)

links = extract_invoice_links(html, BASE_URL)

print()

print("========== LINKS ==========")

for i, link in enumerate(links, 1):
    print(i, link.get("invoice_id"), link.get("filename"))

driver.quit()
