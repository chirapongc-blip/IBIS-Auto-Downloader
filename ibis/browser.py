from pathlib import Path

from selenium import webdriver
from selenium.webdriver.chrome.options import Options


def create_driver(download_dir: Path | None = None):
    from config import DOWNLOAD_DIR

    target_dir = Path(download_dir) if download_dir is not None else DOWNLOAD_DIR
    target_dir.mkdir(parents=True, exist_ok=True)

    prefs = {
        "download.default_directory": str(target_dir.resolve()),
        "download.prompt_for_download": False,
        "download.directory_upgrade": True,
        "safebrowsing.enabled": False,
        "safebrowsing.disable_download_protection": True,
    }

    options = Options()
    options.add_argument("--start-maximized")
    options.add_experimental_option("prefs", prefs)

    driver = webdriver.Chrome(options=options)
    return driver
