from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC


def wait_until_logged_in(driver, timeout=300):
    """
    等待用户完成登录。
    登录成功后返回 True。
    """

    WebDriverWait(driver, timeout).until(
        EC.title_contains("Welcome")
    )

    return True
