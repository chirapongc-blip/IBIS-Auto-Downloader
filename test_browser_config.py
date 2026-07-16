import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from ibis.browser import create_driver
from ibis.downloader import get_download_dir


class BrowserConfigTests(unittest.TestCase):
    def test_create_driver_sets_download_preferences_for_chrome(self):
        with patch("ibis.browser.webdriver.Chrome") as mock_chrome:
            create_driver()

        options = mock_chrome.call_args.kwargs["options"]
        prefs = options.experimental_options["prefs"]
        self.assertEqual(prefs["download.default_directory"], str(get_download_dir().resolve()))
        self.assertFalse(prefs["download.prompt_for_download"])
        self.assertTrue(prefs["download.directory_upgrade"])
        self.assertEqual(prefs["profile.default_content_setting_values.automatic_downloads"], 1)

    def test_create_driver_accepts_custom_download_directory(self):
        with TemporaryDirectory() as tmp:
            custom_dir = Path(tmp)
            with patch("ibis.browser.webdriver.Chrome") as mock_chrome:
                create_driver(download_dir=custom_dir)

            options = mock_chrome.call_args.kwargs["options"]
            prefs = options.experimental_options["prefs"]
            self.assertEqual(prefs["download.default_directory"], str(custom_dir.resolve()))


if __name__ == "__main__":
    unittest.main()
