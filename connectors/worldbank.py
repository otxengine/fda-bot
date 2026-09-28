"""World Bank Open Data — official free API via the `wbgapi` wrapper, no key
needed. Supplies the small cross-country comparison panel on the Macro page."""

from __future__ import annotations

from typing import Any

from tenacity import retry, stop_after_attempt, wait_exponential

from connectors.base import BaseConnector

WB_INDICATORS = [
    "NY.GDP.MKTP.KD.ZG",  # GDP growth (annual %)
    "FP.CPI.TOTL.ZG",     # Inflation, consumer prices (annual %)
    "SL.UEM.TOTL.ZS",     # Unemployment, total (% of labor force)
]
WB_ECONOMIES = ["USA", "CHN", "WLD", "ISR", "EUU"]  # US, China, World, Israel, EU


class WorldBankConnector(BaseConnector):
    name = "worldbank"

    def _fetch_impl(self) -> list[dict[str, Any]]:
        records: list[dict[str, Any]] = []
        for indicator in WB_INDICATORS:
            records.extend(self._fetch_indicator(indicator))
        return records

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=1, max=10))
    def _fetch_indicator(self, indicator: str) -> list[dict[str, Any]]:
        import wbgapi as wb

        return list(wb.data.fetch(indicator, economy=WB_ECONOMIES, mrv=15))
