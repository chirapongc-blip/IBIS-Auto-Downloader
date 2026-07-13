import unittest
from unittest.mock import patch

from ibis.downloader import DownloadQueue, STATUS_PENDING
from ibis.grid import GRID_ID
from ibis.grid_walker import collect_grid_download_links


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


class ImmediateWait:
    def __init__(self, driver, timeout):
        self.driver = driver

    def until(self, method):
        result = method(self.driver)
        if not result:
            raise AssertionError("wait condition never became truthy")
        return result


def build_page(page_number, total_pages, rows):
    next_button = ""
    if page_number < total_pages:
        next_button = (
            f"<a id=\"{GRID_ID}_DXPagerBottom_PBN\" class=\"dxp-button dxp-bi\" "
            f"onclick=\"ASPx.GVPagerOnClick('{GRID_ID}','PBN');\">Next</a>"
        )

    row_html = "".join(
        f"""
        <tr id="{GRID_ID}_DXDataRow{index}" class="dxgvDataRow_MetropolisBlue">
          <td class="dxgvCommandColumn_MetropolisBlue">&nbsp;</td>
          <td class="dxgv"><a href="RetrieveInvoice.aspx?Format=PDF&amp;InvoiceID={row['invoice_id']}">{row['filename']}</a></td>
          <td class="dxgv" align="right">{row['invoice_id']}</td>
          <td class="dxgv">Final</td>
          <td class="dxgv">EOM</td>
          <td class="dxgv"><a href="CustomerDetails.aspx?CustomerID=1">CC-01</a></td>
          <td class="dxgv">&nbsp;</td>
          <td class="dxgv"><a href="CustomerDetails.aspx?CustomerID=1">CT-0001</a></td>
          <td class="dxgv">SSPTE</td>
          <td class="dxgv">&nbsp;</td>
          <td class="dxgv">{row['billing_period']}</td>
          <td class="dxgv">USD</td>
          <td class="dxgv" align="right">0.00</td>
          <td class="dxgv" align="right">0.00</td>
          <td class="dxgv" align="right">0.00</td>
          <td class="dxgv">&nbsp;</td>
          <td class="dxgv">&nbsp;</td>
          <td class="dxgv" align="right">&nbsp;</td>
          <td class="dxgv">
            <table>
              <tr>
                <td><a href="RetrieveInvoice.aspx?Format=PDF&amp;InvoiceID={row['invoice_id']}"><img src="images/pdf.png"></a></td>
                <td><a title="Invoice Charges" href="DownloadARExport.aspx?InvoiceID={row['invoice_id']}&amp;BillingPeriod ={row['billing_period']}&amp;Format=Detailed"><img src="images/xls.png"></a></td>
              </tr>
            </table>
          </td>
          <td class="dxgv">n/a</td>
          <td class="dxgv">Net 30</td>
        </tr>
        """
        for index, row in enumerate(rows)
    )

    return f"""
    <html>
      <body>
        <table id="{GRID_ID}">
          <tbody>
            {row_html}
          </tbody>
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


class DownloadQueueTests(unittest.TestCase):

    def test_builds_queue_items_from_links(self):
        queue = DownloadQueue.from_links(
            [
                {
                    "url": "https://stationsatcom.satcomhost.com/DownloadARExport.aspx?InvoiceID=638872&BillingPeriod=202605&Format=Detailed",
                    "title": "Invoice Charges",
                }
            ]
        )

        item = queue.items[0]
        self.assertEqual(item.download_url, "https://stationsatcom.satcomhost.com/DownloadARExport.aspx?InvoiceID=638872&BillingPeriod=202605&Format=Detailed")
        self.assertEqual(item.invoice_id, "638872")
        self.assertEqual(item.billing_period, "202605")
        self.assertIsNone(item.filename)
        self.assertEqual(item.download_status, STATUS_PENDING)

    def test_uses_metadata_from_grid_links_when_available(self):
        pages = [
            build_page(
                1,
                2,
                [{"invoice_id": "638872", "billing_period": "202605", "filename": "202605_24518"}],
            ),
            build_page(
                2,
                2,
                [{"invoice_id": "637647", "billing_period": "202605", "filename": "202605_22086"}],
            ),
        ]
        driver = FakeDriver(pages)

        with patch("ibis.grid_walker.wait_for_grid", return_value=None), \
             patch("ibis.grid_walker.WebDriverWait", ImmediateWait):
            links = collect_grid_download_links(
                driver, "https://stationsatcom.satcomhost.com"
            )

        queue = DownloadQueue.from_links(links)

        self.assertEqual(len(queue), 2)
        self.assertEqual(
            [(item.invoice_id, item.billing_period, item.filename) for item in queue],
            [
                ("638872", "202605", "202605_24518"),
                ("637647", "202605", "202605_22086"),
            ],
        )


if __name__ == "__main__":
    unittest.main()
