"""Invoice-page billing-period discovery and selection.

``BillingPeriodManager`` is deliberately read-only with respect to the
download workflow.  It discovers periods from the Invoice grid's export
links and records a validated period selection for callers that need it.
"""

from __future__ import annotations

from config import BASE_URL
from ibis.downloader import extract_link_metadata
from ibis.grid_walker import collect_grid_download_links


class BillingPeriodError(RuntimeError):
    """Base exception for BillingPeriodManager failures."""


class BillingPeriodNotFoundError(BillingPeriodError):
    """Raised when a caller selects a period that is not on the Invoice page."""


class BillingPeriodManager:
    """Discover and validate billing periods available on the Invoice page.

    The Invoice page must already be open and authenticated.  Discovery uses
    the existing grid walker, so periods on every grid page are included.
    ``select`` records the requested period only; it intentionally does not
    change the page or trigger downloads.
    """

    def __init__(self, driver, *, base_url: str = BASE_URL, links=None):
        self.driver = driver
        self.base_url = base_url
        self.selected_period: str | None = None
        self._periods: list[str] | None = None
        self._links = links

    def get_periods(self, *, refresh: bool = False) -> list[str]:
        """Return all available billing periods, newest first.

        The first call discovers periods across the complete Invoice grid.
        Pass ``refresh=True`` to discard the cached snapshot and rediscover.
        """
        if self._periods is None or refresh:
            links = self._links
            if links is None:
                links = collect_grid_download_links(self.driver, self.base_url)
            periods = {
                str(billing_period)
                for link in links
                for _, billing_period, _ in (extract_link_metadata(link),)
                if billing_period is not None
            }
            self._periods = sorted(periods, reverse=True)
        return list(self._periods)

    def latest(self) -> str | None:
        """Return the newest available billing period, or ``None`` when none exist."""
        periods = self.get_periods()
        return periods[0] if periods else None

    def exists(self, period) -> bool:
        """Return whether *period* is currently available on the Invoice page."""
        return str(period) in self.get_periods() if period is not None else False

    def select(self, period) -> str:
        """Validate and record *period* as the selected billing period.

        Raises
        ------
        BillingPeriodNotFoundError
            If the requested period is absent from the discovered Invoice-grid
            periods.
        """
        requested = str(period) if period is not None else ""
        if not self.exists(period):
            available = ", ".join(self.get_periods()) or "none"
            raise BillingPeriodNotFoundError(
                f"Billing period '{requested}' does not exist on the Invoice page. "
                f"Available periods: {available}."
            )
        self.selected_period = requested
        return requested
