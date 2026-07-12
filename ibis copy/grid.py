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
