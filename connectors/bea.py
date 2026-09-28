"""BEA (Bureau of Economic Analysis) — official free REST API, needs BEA_API_KEY.
Pulls quarterly real GDP (NIPA Table T10106, seasonally adjusted annual rate)."""

from __future__ import annotations

from typing import Any

import requests
from tenacity import retry, stop_after_attempt, wait_exponential

from config.settings import get_settings
from connectors.base import BaseConnector

BEA_API_URL = "https://apps.bea.gov/api/data"


class BeaConnector(BaseConnector):
    name = "bea"

    def _fetch_impl(self) -> dict[str, Any]:
        api_key = get_settings().bea_api_key
        if not api_key:
            raise RuntimeError("BEA_API_KEY not set — copy .env.example to .env and fill it in")
        return self._get(api_key)

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=1, max=10))
    def _get(self, api_key: str) -> dict[str, Any]:
        params = {
            "UserID": api_key,
            "method": "GetData",
            "DataSetName": "NIPA",
            "TableName": "T10106",   # Real GDP, quarterly
            "Frequency": "Q",
            "Year": "ALL",
            "ResultFormat": "JSON",
        }
        resp = requests.get(BEA_API_URL, params=params, timeout=20)
        resp.raise_for_status()
        data = resp.json()
        results = data.get("BEAAPI", {}).get("Results", {})
        if "Error" in results:
            raise RuntimeError(f"BEA API error: {results['Error']}")
        return data
