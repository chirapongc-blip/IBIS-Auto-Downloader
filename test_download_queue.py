import unittest
from unittest.mock import patch

from ibis.downloader import DownloadQueue, STATUS_PENDING
from ibis.grid import GRID_ID
from ibis.parser import extract_invoice_links
from test_grid_walker import FakeDriver, ImmediateWait


def build_queue_page(page_number, total_pages, invoices):
    next_button = ""
    if page_number < total_pages:
        next_button = (
            f"<a id=\"{GRID_ID}_DXPagerBottom_PBN\" class=\"dxp-button dxp-bi\" "
            f"onclick=\"ASPx.GVPagerOnClick('{GRID_ID}','PBN');\">Next</a>"
        )

    rows = "".join(
        """
        <tr>
          <td><a href="RetrieveInvoice.aspx?Format=PDF&amp;InvoiceID={invoice_id}">{filename}</a></td>
          <td align="right">{invoice_id}</td>
          <td>{billing_period}</td>
          <td>
            <a title="Invoice Charges"
               href="DownloadARExport.aspx?InvoiceID={invoice_id}&amp;BillingPeriod ={billing_period}&amp;CustomerID=24518&amp;Format=Detailed">
               xls
            </a>
          </td>
        </tr>
        """.format(
            invoice_id=invoice["invoice_id"],
            filename=invoice["filename"],
            billing_period=invoice["billing_period"],
        )
        for invoice in invoices
    )

    return f"""
    <html>
      <body>
        <table id="{GRID_ID}">
          {rows}
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
    def test_extract_invoice_links_includes_queue_metadata(self):
        html = build_queue_page(
            1,
            1,
            [
                {
                    "invoice_id": "638872",
                    "billing_period": "202605",
                    "filename": "202605_24518",
                }
            ],
        )

        links = extract_invoice_links(html, "https://stationsatcom.satcomhost.com")

        self.assertEqual(
            links,
            [
                {
                    "url": "https://stationsatcom.satcomhost.com/DownloadARExport.aspx?InvoiceID=638872&BillingPeriod =202605&CustomerID=24518&Format=Detailed",
                    "title": "Invoice Charges",
                    "invoice_id": "638872",
                    "billing_period": "202605",
                    "filename": "202605_24518",
                }
            ],
        )

    def test_builds_queue_items_from_links(self):
        queue = DownloadQueue.from_links(
            [
                {
                    "url": "https://stationsatcom.satcomhost.com/DownloadARExport.aspx?InvoiceID=638872&BillingPeriod =202605&Format=Detailed",
                    "invoice_id": "638872",
                    "billing_period": "202605",
                    "filename": "202605_24518",
                }
            ]
        )

        self.assertEqual(len(queue), 1)
        self.assertEqual(queue.items[0].download_url, "https://stationsatcom.satcomhost.com/DownloadARExport.aspx?InvoiceID=638872&BillingPeriod =202605&Format=Detailed")
        self.assertEqual(queue.items[0].invoice_id, "638872")
        self.assertEqual(queue.items[0].billing_period, "202605")
        self.assertEqual(queue.items[0].filename, "202605_24518")
        self.assertEqual(queue.items[0].status, STATUS_PENDING)

    def test_builds_queue_from_grid_links(self):
        pages = [
            build_queue_page(
                1,
                2,
                [
                    {
                        "invoice_id": "1001",
                        "billing_period": "202601",
                        "filename": "202601_24518",
                    }
                ],
            ),
            build_queue_page(
                2,
                2,
                [
                    {
                        "invoice_id": "2001",
                        "billing_period": "202602",
                        "filename": "202602_24518",
                    }
                ],
            ),
        ]
        driver = FakeDriver(pages)

        with patch("ibis.grid_walker.wait_for_grid", return_value=None), patch(
            "ibis.grid_walker.WebDriverWait", ImmediateWait
        ):
            queue = DownloadQueue.from_grid(
                driver, "https://stationsatcom.satcomhost.com"
            )

        self.assertEqual(len(queue), 2)
        self.assertEqual(
            [item.invoice_id for item in queue],
            ["1001", "2001"],
        )
        self.assertEqual(
            [item.billing_period for item in queue],
            ["202601", "202602"],
        )
        self.assertEqual(
            [item.filename for item in queue],
            ["202601_24518", "202602_24518"],
        )
        self.assertTrue(
            all(item.status == STATUS_PENDING for item in queue)
        )


if __name__ == "__main__":
    unittest.main()
