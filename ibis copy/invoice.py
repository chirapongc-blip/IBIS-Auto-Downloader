from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC


def open_invoice_page(driver):
    """
    打开 Invoice 页面，并等待页面加载完成。
    """

    driver.get("https://stationsatcom.satcomhost.com/invoices.aspx")

    WebDriverWait(driver, 30).until(
        EC.presence_of_element_located((By.TAG_NAME, "body"))
    )

    return driver.page_source
