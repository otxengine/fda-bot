"""FRED (Federal Reserve Economic Data) — official free API, needs FRED_API_KEY.
Supplies both the core rates/inflation/employment/growth series used by
analysis/macro_regime.py's scoring engine (REGIME_SERIES) and a broader set
of supplementary indicators (ADDITIONAL_SERIES) for general research/chat
questions that aren't part of the regime score."""

from __future__ import annotations

import logging
from datetime import date, timedelta
from typing import Any

from tenacity import retry, stop_after_attempt, wait_exponential

from config.settings import get_settings
from connectors.base import BaseConnector

# Series feeding the regime scoring engine — macro_thresholds.yaml references
# these as "FRED:<mnemonic>"; transforms (YoY/MoM) are applied on read in
# analysis/macro_regime.py, never persisted as a separate series.
REGIME_SERIES = [
    "FEDFUNDS",   # Fed funds effective rate
    "DGS10",      # 10Y treasury yield
    "DGS2",       # 2Y treasury yield
    "T10Y2Y",     # 10Y-2Y spread (FRED publishes this directly)
    "CPIAUCSL",   # CPI, all items
    "CPILFESL",   # Core CPI
    "UNRATE",     # Unemployment rate
    "PAYEMS",     # Nonfarm payrolls
    "GDPC1",      # Real GDP
]

# Broader coverage, NOT part of regime scoring (see config/additional_indicators.yaml
# for display names/categories) — added because the original 9-series set was too
# thin for general research questions (found via live use: the chat had almost
# nothing to say about consumption, production, housing, sentiment, or trade).
# Every FRED mnemonic below was verified to exist against the live API before
# being added here (see chat history — wrong series IDs just silently fail the
# whole batch fetch, so guessing was not an option).
ADDITIONAL_SERIES = [
    "RSAFS",                   # Retail Sales (Advance, Retail & Food Services)
    "INDPRO",                  # Industrial Production Index
    "HOUST",                   # Housing Starts
    "UMCSENT",                 # Consumer Sentiment (U. Michigan)
    "PPIFIS",                  # Producer Price Index, Final Demand (headline PPI)
    "PCEPI",                   # PCE Price Index — the Fed's preferred inflation gauge
    "PCEPILFE",                # Core PCE Price Index
    "BOPGSTB",                 # Trade Balance: Goods & Services
    "GACDISA066MSFRBNY",       # Empire State Manufacturing Survey (general business conditions)
    "GACDFSA066MSFRBPHI",      # Philly Fed Manufacturing Survey (general activity) — ISM proxy, see plan
]

FRED_SERIES = REGIME_SERIES + ADDITIONAL_SERIES

logger = logging.getLogger(__name__)


class FredConnector(BaseConnector):
    name = "fred"

    def _fetch_impl(self) -> dict[str, Any]:
        from fredapi import Fred

        api_key = get_settings().fred_api_key
        if not api_key:
            raise RuntimeError("FRED_API_KEY not set — copy .env.example to .env and fill it in")
        fred = Fred(api_key=api_key)

        payload: dict[str, Any] = {}
        for series_id in FRED_SERIES:
            try:
                payload[series_id] = self._fetch_series(fred, series_id)
            except Exception:  # noqa: BLE001 — one bad series ID must not sink the whole batch
                logger.warning("fred: failed to fetch series %s, skipping it this tick", series_id, exc_info=True)
        return payload

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=1, max=10))
    def _fetch_series(self, fred: Any, series_id: str) -> Any:
        return fred.get_series(series_id)  # pandas Series indexed by date


# The economic-calendar releases we actually track elsewhere in this app
# (matches REGIME_SERIES + ADDITIONAL_SERIES above) — deliberately a curated
# subset, not FRED's full ~500-release firehose (most of which is irrelevant
# regional/niche data). FOMC is excluded here: connectors/federal_reserve.py
# already scrapes it directly from the Fed's own site.
#
# Added as a free, official replacement for an Investing.com economic
# calendar scrape: Investing.com's Terms of Service explicitly prohibit
# automated use/reproduction of their site data in writing, and the user
# asked for an alternative rather than building against that prohibition.
# Verified against the live /fred/releases endpoint before hardcoding —
# same reasoning as FRED_SERIES above, wrong IDs just silently return
# nothing, not an error, so guessing was not an option.
WATCHED_RELEASE_IDS: dict[int, str] = {
    9: "Retail Sales",
    10: "CPI",
    13: "Industrial Production",
    27: "Housing Starts",
    46: "PPI",
    50: "Employment Situation",
    51: "Trade Balance",
    53: "GDP",
    54: "PCE (Personal Income and Outlays)",
    91: "Consumer Sentiment (U. Michigan)",
    321: "Empire State Manufacturing Survey",
    351: "Philly Fed Manufacturing Survey",
}

# Deliberately the SINGULAR per-release endpoint, called once per watched
# release_id, not the plural /fred/releases/dates firehose: the plural
# endpoint's `limit` (max 1000) gets consumed by the hundreds of daily-update
# releases (FX rates, SOFR, etc.) within just the first couple weeks of any
# date window, silently truncating away the less-frequent releases (CPI,
# GDP, ...) we actually care about — confirmed live: the firehose approach
# found only 2 of our 12 watched indicators in a 97-day window.
FRED_RELEASE_DATES_URL = "https://api.stlouisfed.org/fred/release/dates"


class FredCalendarConnector(BaseConnector):
    """Forward-looking economic release calendar — a free, official
    substitute for scraping Investing.com's economic calendar (see
    WATCHED_RELEASE_IDS docstring above for why)."""

    name = "fred_calendar"

    def _fetch_impl(self) -> dict[str, Any]:
        api_key = get_settings().fred_api_key
        if not api_key:
            raise RuntimeError("FRED_API_KEY not set — copy .env.example to .env and fill it in")
        all_dates: list[dict[str, Any]] = []
        for release_id in WATCHED_RELEASE_IDS:
            all_dates.extend(self._get_one_release(api_key, release_id))
        return {"release_dates": all_dates}

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=1, max=10))
    def _get_one_release(self, api_key: str, release_id: int) -> list[dict[str, Any]]:
        import requests

        resp = requests.get(
            FRED_RELEASE_DATES_URL,
            params={
                "release_id": release_id,
                "api_key": api_key,
                "file_type": "json",
                "realtime_start": (date.today() - timedelta(days=7)).isoformat(),
                "realtime_end": (date.today() + timedelta(days=90)).isoformat(),
                # true: a future/not-yet-happened release date has no data
                # attached YET by definition — excluding "no data" dates (the
                # first thing tried, matching the parameter's name) silently
                # strips out exactly the forward-looking dates a calendar
                # needs. Confirmed live: 'false' returned 0 future CPI dates,
                # 'true' correctly returned the next 3.
                "include_release_dates_with_no_data": "true",
                "sort_order": "asc",
            },
            timeout=20,
        )
        resp.raise_for_status()
        return resp.json().get("release_dates", [])
