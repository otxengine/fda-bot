"""BLS (Bureau of Labor Statistics) — official free API v2, needs BLS_API_KEY.
Simple JSON POST, no mature Python wrapper needed (see plan's library table)."""

from __future__ import annotations

from datetime import datetime
from typing import Any

import requests
from tenacity import retry, stop_after_attempt, wait_exponential

from config.settings import get_settings
from connectors.base import BaseConnector

BLS_API_URL = "https://api.bls.gov/publicAPI/v2/timeseries/data/"

# Headline series: CPI-U all items (NSA), unemployment rate (SA), total nonfarm payrolls (SA)
BLS_SERIES = ["CUUR0000SA0", "LNS14000000", "CES0000000001"]


class BlsConnector(BaseConnector):
    name = "bls"

    def _fetch_impl(self) -> dict[str, Any]:
        api_key = get_settings().bls_api_key
        if not api_key:
            raise RuntimeError("BLS_API_KEY not set — copy .env.example to .env and fill it in")
        current_year = datetime.now().year
        return self._post(api_key, current_year)

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=1, max=10))
    def _post(self, api_key: str, current_year: int) -> dict[str, Any]:
        body = {
            "seriesid": BLS_SERIES,
            "startyear": str(current_year - 2),
            "endyear": str(current_year),
            "registrationkey": api_key,
        }
        resp = requests.post(BLS_API_URL, json=body, timeout=15)
        resp.raise_for_status()
        data = resp.json()
        if data.get("status") != "REQUEST_SUCCEEDED":
            raise RuntimeError(f"BLS API error: {data.get('message')}")
        return data
