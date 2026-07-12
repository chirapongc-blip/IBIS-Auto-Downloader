from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC


GRID_ID = "ctl00_ContentPlaceHolder1_gvInvoice"


def wait_for_grid(driver, timeout=30):
    WebDriverWait(driver, timeout).until(
        EC.presence_of_element_located((By.ID, GRID_ID))
    )


def get_grid(driver):
    return driver.find_element(By.ID, GRID_ID)


def get_grid_text(driver):
    return get_grid(driver).text


def get_grid_rows(driver):
    """
    返回 Grid 中所有资料列（tr）。
    """
    grid = get_grid(driver)

    rows = grid.find_elements(By.TAG_NAME, "tr")

    return rows
def count_grid_rows(driver):
    """
    返回 Grid 中所有 tr 的数量（用于调试）。
    """
    return len(get_grid_rows(driver))

def debug_grid_rows(driver):
    """
    调试：打印每个 tr 包含多少个 td。
    """
    rows = get_grid_rows(driver)

    for i, row in enumerate(rows):
        cells = row.find_elements(By.TAG_NAME, "td")
        print(f"TR {i:03d}: {len(cells)} td")

import re

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


    ...