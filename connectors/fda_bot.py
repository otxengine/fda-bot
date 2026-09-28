"""FDA-Bot — the user's own separate FDA/biopharma catalyst options-flow
scanner (github.com/otxengine/fda-bot), deployed on Render. It exposes a
public, unauthenticated /api/* HTTP surface (FastAPI). This connector pulls
a handful of those endpoints so the bot's own signal history and
alert-outcome performance are visible inside finresearch, alongside
everything else, instead of living only in the bot's separate
frontend/Telegram feed.

finresearch never writes back to fda-bot — this is read-only, one-way.

Note this is NOT a canonical macro/price data source like the other
connectors — it's a live read of a whole separate app's own analysis output,
so its normalizer (ingestion/normalizers/fda_bot.py) produces several
different record types from one fetch rather than one series per record."""

from __future__ import annotations

import requests
from tenacity import retry, stop_after_attempt, wait_exponential

from config.settings import get_settings
from connectors.base import BaseConnector


class FdaBotConnector(BaseConnector):
    name = "fda_bot"

    def _fetch_impl(self) -> dict:
        base = get_settings().fda_bot_base_url.rstrip("/")
        return {
            "status": self._get(f"{base}/api/status"),
            "performance": self._get(f"{base}/api/performance"),
            "calibration": self._get(f"{base}/api/calibration"),
            "stock_signals": self._get(f"{base}/api/stock-signals"),
        }

    @retry(stop=stop_after_attempt(2), wait=wait_exponential(multiplier=1, min=1, max=8))
    def _get(self, url: str) -> dict:
        resp = requests.get(
            url, timeout=25,
            headers={"User-Agent": "finresearch-personal-research-tool/0.1"},
        )
        resp.raise_for_status()
        return resp.json()
