from config import BASE_URL
from ibis.browser import create_driver
from ibis.login import wait_until_logged_in
from ibis.invoice import open_invoice_page
from ibis.grid import wait_for_grid

driver = create_driver()

try:
    driver.get(BASE_URL)

    print("请登录 IBIS...")
    wait_until_logged_in(driver)

    print("登录成功。")

    open_invoice_page(driver)

    print("等待 Invoice Grid...")

    wait_for_grid(driver)

    print("✓ Grid 已找到")

    input("按 Enter 结束...")

finally:
    driver.quit()
