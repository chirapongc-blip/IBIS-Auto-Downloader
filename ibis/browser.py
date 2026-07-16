from pathlib import Path

from selenium import webdriver
from selenium.webdriver.chrome.options import Options

from ibis.downloader import get_download_dir


def create_driver(*, download_dir=None):
    options = Options()
    target_download_dir = get_download_dir() if download_dir is None else Path(download_dir)

    options.add_argument("--start-maximized")
    options.add_experimental_option(
        "prefs",
        {
            "download.default_directory": str(target_download_dir.resolve()),
            "download.prompt_for_download": False,
            "download.directory_upgrade": True,
            "profile.default_content_setting_values.automatic_downloads": 1,
            "safebrowsing.enabled": True,
        },
    )

    driver = webdriver.Chrome(options=options)

    return driver
