from config import BASE_URL
from selenium.webdriver.common.by import By

from ibis.browser import create_driver
from ibis.login import wait_until_logged_in
from ibis.invoice import open_invoice_page
from ibis.parser import extract_invoice_links
from ibis.grid import wait_for_grid, get_grid_text, count_grid_rows


def main():
    print("=== IBIS Auto Downloader V2.0 Beta ===")

    driver = create_driver()

    try:
        driver.get(BASE_URL)

        print("请在浏览器完成登录...")
        wait_until_logged_in(driver)

        print("登录成功。")
        print("正在打开 Invoice 页面...")

        html = open_invoice_page(driver)

        with open("invoice_page.html", "w", encoding="utf-8") as f:
            f.write(html)

            wait_for_grid(driver)

        anchors = driver.find_elements(
            By.CSS_SELECTOR,
            "a[href*='DownloadARExport.aspx']"
        )

        print("Selenium found:", len(anchors))

        for a in anchors[:5]:
            print(a.get_attribute("href"))

        html2 = driver.page_source

        with open("invoice_after_wait.html", "w", encoding="utf-8") as f:
            f.write(html2)

        print("After wait:", html2.count("DownloadARExport.aspx"))

        print(f"Grid TR 数量：{count_grid_rows(driver)}")
        print("\n========== Grid Preview ==========")
        print(get_grid_text(driver)[:1000])
        print("==================================\n")

        links = extract_invoice_links(html2, BASE_URL)

        print(f"发现 {len(links)} 个下载链接。")

        input("按 Enter 结束程序...")

    finally:
        driver.quit()


if __name__ == "__main__":
    main()