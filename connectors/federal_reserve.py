"""Federal Reserve (federalreserve.gov) — official govt page, not a REST API,
but stable/structured enough to treat as low-fragility (see plan's library
table). Official interest rates are already covered via FRED (which mirrors
H.15), so this connector's unique value is the FOMC meeting calendar, which
FRED doesn't carry. Best-effort: if the page layout ever changes, the
normalizer just returns an empty list rather than raising (see BaseConnector's
"never crashes the pipeline" contract)."""

from __future__ import annotations

import requests
from tenacity import retry, stop_after_attempt, wait_exponential

from connectors.base import BaseConnector

FOMC_CALENDAR_URL = "https://www.federalreserve.gov/monetarypolicy/fomccalendars.htm"


class FederalReserveConnector(BaseConnector):
    name = "federal_reserve"

    def _fetch_impl(self) -> str:
        return self._get()

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=1, max=10))
    def _get(self) -> str:
        resp = requests.get(
            FOMC_CALENDAR_URL,
            timeout=20,
            headers={"User-Agent": "finresearch-personal-research-tool/0.1"},
        )
        resp.raise_for_status()
        return resp.text  # raw HTML — parsed in the normalizer so it's fixture-testable
