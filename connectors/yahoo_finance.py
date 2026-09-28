"""Yahoo Finance — unofficial but de facto standard (`yfinance`). Supplies
prices/OHLCV, fundamentals snapshots, and news for whatever symbol list is
passed in (the scheduler job queries the current watchlist + screener
universe from the DB and passes it here — the connector itself stays DB-free,
pure I/O against the external source, per the plan's connector contract).

Per-symbol failures degrade gracefully: one bad ticker doesn't fail the batch.

Instantiated under TWO different connector_name values by scheduler/jobs.py
(job_yahoo_finance_priority / job_yahoo_finance_universe) so each gets its
own cadence/timeout in connectors.yaml — found via live use that bundling the
small, time-sensitive watchlist+sectors fetch with the full 503-symbol S&P
500 screener universe in one call meant the big one's timeout (a fetch that
genuinely takes many minutes for 500+ symbols) starved the watchlist of
updates entirely, since a timeout discards the WHOLE batch's results, not
just the slow part.
"""

from __future__ import annotations

from typing import Any, Callable

from connectors.base import BaseConnector


class YahooFinanceConnector(BaseConnector):
    name = "yahoo_finance"

    def __init__(self, symbols: list[str], connector_name: str | None = None, include_estimates: bool = False) -> None:
        if connector_name:
            self.name = connector_name  # instance override, read by BaseConnector.__init__ below
        super().__init__()
        self.symbols = symbols
        # Analyst estimate tables are 2 extra network calls per symbol — fine
        # for the ~15-symbol priority (watchlist) batch, but would meaningfully
        # slow the already-slow ~500-symbol universe batch for data the
        # screener doesn't use. See scheduler/jobs.py's two job wrappers.
        self.include_estimates = include_estimates

    def _fetch_impl(self) -> dict[str, Any]:
        import yfinance as yf

        payload: dict[str, Any] = {}
        for symbol in self.symbols:
            ticker = yf.Ticker(symbol)
            entry: dict[str, Any] = {
                "info": self._safe(lambda: ticker.info),
                # Daily history over 2y: what the candlestick chart and technicals
                # (SMA200 needs ~200 trading days) are built from.
                "history_daily": self._safe(lambda: ticker.history(period="2y", interval="1d")),
                # Short intraday window: recent-session detail only.
                "history_intraday": self._safe(lambda: ticker.history(period="5d", interval="1h")),
                "news": self._safe(lambda: ticker.news),
            }
            if self.include_estimates:
                entry["earnings_estimate"] = self._safe(lambda: ticker.earnings_estimate)
                entry["revenue_estimate"] = self._safe(lambda: ticker.revenue_estimate)
            payload[symbol] = entry
        return payload

    @staticmethod
    def _safe(fn: Callable[[], Any]) -> Any:
        try:
            return fn()
        except Exception:  # noqa: BLE001 — one field failing shouldn't drop the whole symbol
            return None
