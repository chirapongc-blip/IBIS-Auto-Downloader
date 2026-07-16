import unittest
from unittest.mock import patch

from selenium.common.exceptions import WebDriverException

from ibis.grid import GRID_ID
from ibis.grid_walker import collect_grid_download_links, get_devexpress_pager_info


class FakeElement:
    def __init__(self, onclick):
        self._onclick = onclick

    def get_attribute(self, name):
        if name == "onclick":
            return self._onclick
        return None


class FakeDriver:
    def __init__(self, pages):
        self.pages = pages
        self.page_index = 0

    @property
    def page_source(self):
        return self.pages[self.page_index]

    def find_elements(self, by, selector):
        if self.page_index >= len(self.pages) - 1:
            return []
        if "GVPagerOnClick" not in selector:
            return []
        return [FakeElement(f"ASPx.GVPagerOnClick('{GRID_ID}','PBN');")]

    def execute_script(self, script, *args):
        if "GVPagerOnClick" in script and args[1] == "PBN":
            self.page_index += 1


class JsFailureDriver(FakeDriver):
    def execute_script(self, script, *args):
        if "GVPagerOnClick" in script:
            raise WebDriverException("js pager invocation failed")
        if "arguments[0].click();" in script:
            self.page_index += 1


class ImmediateWait:
    def __init__(self, driver, timeout):
        self.driver = driver

    def until(self, method):
        result = method(self.driver)
        if not result:
            raise AssertionError("wait condition never became truthy")
        return result


def build_page(page_number, total_pages, invoice_ids):
    next_button = ""
    if page_number < total_pages:
        next_button = (
            f"<a id=\"{GRID_ID}_DXPagerBottom_PBN\" class=\"dxp-button dxp-bi\" "
            f"onclick=\"ASPx.GVPagerOnClick('{GRID_ID}','PBN');\">Next</a>"
        )

    links = "".join(
        f"<a title=\"Invoice Charges\" "
        f"href=\"DownloadARExport.aspx?InvoiceID={invoice_id}&Format=Detailed\">xls</a>"
        for invoice_id in invoice_ids
    )

    return f"""
    <html>
      <body>
        <table id="{GRID_ID}">
          <tr><td>{links}</td></tr>
        </table>
        <div class="dxgvPagerBottomPanel">
          <div class="dxpLite_MetropolisBlue" id="{GRID_ID}_DXPagerBottom">
            <b class="dxp-lead dxp-summary">Page {page_number} of {total_pages} ({total_pages * 20} items)</b>
            {next_button}
          </div>
        </div>
        <script>
          ASPx.createControl(ASPxClientGridView,'{GRID_ID}','gvInvoice',
            {{'pageRowCount':20,'pageIndex':{page_number - 1},'pageCount':{total_pages}}});
        </script>
      </body>
    </html>
    """


class GridWalkerTests(unittest.TestCase):

    def test_detects_devexpress_pager(self):
        html = build_page(1, 3, [1001, 1002])
        info = get_devexpress_pager_info(html)
        self.assertEqual(info["current_page"], 1)
        self.assertEqual(info["total_pages"], 3)
        self.assertTrue(info["has_pager"])
        self.assertTrue(info["has_next_page"])

    def test_last_page_has_no_next(self):
        html = build_page(3, 3, [3001])
        info = get_devexpress_pager_info(html)
        self.assertEqual(info["current_page"], 3)
        self.assertTrue(info["has_pager"])
        self.assertFalse(info["has_next_page"])

    def test_no_pager_single_page(self):
        html = "<html><body><table id=\"{}\"></table></body></html>".format(GRID_ID)
        info = get_devexpress_pager_info(html)
        self.assertFalse(info["has_pager"])
        self.assertFalse(info["has_next_page"])

    def test_collects_download_links_from_all_pages(self):
        pages = [
            build_page(1, 3, [1001, 1002]),
            build_page(2, 3, [2001, 2002]),
            build_page(3, 3, [3001, 3002]),
        ]
        driver = FakeDriver(pages)

        with patch("ibis.grid_walker.wait_for_grid", return_value=None), \
             patch("ibis.grid_walker.WebDriverWait", ImmediateWait):
            links = collect_grid_download_links(
                driver, "https://stationsatcom.satcomhost.com"
            )

        self.assertEqual(len(links), 6)
        self.assertEqual(
            [link["url"] for link in links],
            [
                "https://stationsatcom.satcomhost.com/DownloadARExport.aspx?InvoiceID=1001&Format=Detailed",
                "https://stationsatcom.satcomhost.com/DownloadARExport.aspx?InvoiceID=1002&Format=Detailed",
                "https://stationsatcom.satcomhost.com/DownloadARExport.aspx?InvoiceID=2001&Format=Detailed",
                "https://stationsatcom.satcomhost.com/DownloadARExport.aspx?InvoiceID=2002&Format=Detailed",
                "https://stationsatcom.satcomhost.com/DownloadARExport.aspx?InvoiceID=3001&Format=Detailed",
                "https://stationsatcom.satcomhost.com/DownloadARExport.aspx?InvoiceID=3002&Format=Detailed",
            ],
        )

    def test_deduplicates_repeated_links(self):
        same_ids = [1001, 1001]
        pages = [build_page(1, 1, same_ids)]
        driver = FakeDriver(pages)

        with patch("ibis.grid_walker.wait_for_grid", return_value=None), \
             patch("ibis.grid_walker.WebDriverWait", ImmediateWait):
            links = collect_grid_download_links(
                driver, "https://stationsatcom.satcomhost.com"
            )

        self.assertEqual(len(links), 1)

    def test_falls_back_to_click_when_js_pager_invocation_fails(self):
        pages = [
            build_page(1, 2, [1001]),
            build_page(2, 2, [2001]),
        ]
        driver = JsFailureDriver(pages)

        with patch("ibis.grid_walker.wait_for_grid", return_value=None), \
             patch("ibis.grid_walker.WebDriverWait", ImmediateWait):
            links = collect_grid_download_links(
                driver, "https://stationsatcom.satcomhost.com"
            )

        self.assertEqual(len(links), 2)

    def test_raises_when_page_navigation_loops(self):
        """collect_grid_download_links must raise if the pager returns the
        same page number twice (infinite-loop guard)."""

        class StuckDriver(FakeDriver):
            """Returns the next-button element but navigation never advances
            the page, so pager_info always reports page 1."""

            def find_elements(self, by, selector):
                if "GVPagerOnClick" in selector:
                    return [FakeElement(f"ASPx.GVPagerOnClick('{GRID_ID}','PBN');")]
                return []

            def execute_script(self, script, *args):
                pass  # navigation no-op – page index never advances

        pages = [build_page(1, 2, [1001])]
        driver = StuckDriver(pages)

        class AlwaysTrueWait:
            def __init__(self, driver, timeout):
                pass

            def until(self, method):
                return True  # pretend the page advanced

        with patch("ibis.grid_walker.wait_for_grid", return_value=None), \
             patch("ibis.grid_walker.WebDriverWait", AlwaysTrueWait):
            with self.assertRaises(RuntimeError) as ctx:
                collect_grid_download_links(
                    driver, "https://stationsatcom.satcomhost.com"
                )

        self.assertIn("repeated page", str(ctx.exception))

    def test_raises_when_next_button_not_in_dom(self):
        """RuntimeError is raised when the pager HTML indicates a next page
        but the next-page element cannot be found in the live DOM."""

        class NoDomNextDriver:
            """Page source always shows page 1 of 2, but find_elements returns
            nothing (next button missing from live DOM)."""

            @property
            def page_source(self):
                return build_page(1, 2, [1001])

            def find_elements(self, by, selector):
                return []  # next button absent from DOM

            def execute_script(self, script, *args):
                pass

        driver = NoDomNextDriver()

        with patch("ibis.grid_walker.wait_for_grid", return_value=None), \
             patch("ibis.grid_walker.WebDriverWait", ImmediateWait):
            with self.assertRaises(RuntimeError) as ctx:
                collect_grid_download_links(
                    driver, "https://stationsatcom.satcomhost.com"
                )

        self.assertIn("PBN", str(ctx.exception))

    def test_collects_single_page_with_no_pager(self):
        """When there is no pager, all links on the sole page are returned."""
        html = (
            f'<html><body>'
            f'<table id="{GRID_ID}">'
            f'<tr><td>'
            f'<a href="DownloadARExport.aspx?InvoiceID=9001&Format=Detailed">xls</a>'
            f'</td></tr>'
            f'</table></body></html>'
        )

        class SinglePageDriver:
            @property
            def page_source(self):
                return html

            def find_elements(self, by, selector):
                return []

        driver = SinglePageDriver()

        with patch("ibis.grid_walker.wait_for_grid", return_value=None), \
             patch("ibis.grid_walker.WebDriverWait", ImmediateWait):
            links = collect_grid_download_links(
                driver, "https://stationsatcom.satcomhost.com"
            )

        self.assertEqual(len(links), 1)
        self.assertIn("InvoiceID=9001", links[0]["url"])


if __name__ == "__main__":
    unittest.main()
