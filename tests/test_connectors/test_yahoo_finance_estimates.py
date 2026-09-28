"""Unit tests for the analyst consensus estimate parsing (earnings_estimate /
revenue_estimate) added after the user asked for current/next-year
revenue+earnings and growth rate — see analysis/chat_tools.py's
get_earnings_estimates and dashboard/pages/2_Watchlist.py's Analyst Estimates
section."""

from __future__ import annotations

from datetime import datetime, timezone

import pandas as pd

from connectors.base import ConnectorResult
from ingestion.models import EarningsEstimate
from ingestion.normalizers.yahoo_finance import YahooFinanceNormalizer


def _estimate_table(kind: str) -> pd.DataFrame:
    # Mirrors the real shape returned by yfinance's Ticker.earnings_estimate /
    # .revenue_estimate (verified live against the installed version).
    if kind == "earnings":
        data = {
            "avg": [1.97754, 2.90471, 8.81945, 9.58154],
            "growth": [0.0689, 0.0228, 0.1822, 0.0864],
            "numberOfAnalysts": [27, 21, 39, 40],
        }
    else:
        data = {
            "avg": [113624521680.0, 154392842060.0, 477832817030.0, 527971929389.0],
            "growth": [0.1089, 0.0740, 0.1482, 0.1049],
            "numberOfAnalysts": [27, 21, 39, 40],
        }
    return pd.DataFrame(data, index=pd.Index(["0q", "+1q", "0y", "+1y"], name="period"))


def _result(payload) -> ConnectorResult:
    return ConnectorResult(
        connector_name="yahoo_finance_priority", fetched_at=datetime.now(timezone.utc),
        status="ok", raw_payload=payload,
    )


def test_parses_earnings_and_revenue_estimates_for_all_four_periods() -> None:
    payload = {
        "AAPL": {
            "info": {}, "history_daily": None, "history_intraday": None, "news": [],
            "earnings_estimate": _estimate_table("earnings"),
            "revenue_estimate": _estimate_table("revenue"),
        }
    }
    records = YahooFinanceNormalizer().normalize(_result(payload))
    estimates = [r for r in records if isinstance(r, EarningsEstimate)]

    assert len(estimates) == 8  # 4 periods x 2 metrics
    by_key = {(e.period, e.metric): e for e in estimates}

    next_year_eps = by_key[("+1y", "earnings")]
    assert next_year_eps.avg_estimate == 9.58154
    assert round(next_year_eps.growth_yoy, 4) == 0.0864
    assert next_year_eps.number_of_analysts == 40

    this_year_revenue = by_key[("0y", "revenue")]
    assert this_year_revenue.avg_estimate == 477832817030.0
    assert round(this_year_revenue.growth_yoy, 4) == 0.1482


def test_missing_estimate_tables_produce_no_records() -> None:
    payload = {"AAPL": {"info": {}, "history_daily": None, "history_intraday": None, "news": []}}
    records = YahooFinanceNormalizer().normalize(_result(payload))
    assert not [r for r in records if isinstance(r, EarningsEstimate)]


def test_empty_estimate_table_does_not_crash() -> None:
    payload = {
        "AAPL": {
            "info": {}, "history_daily": None, "history_intraday": None, "news": [],
            "earnings_estimate": pd.DataFrame(), "revenue_estimate": pd.DataFrame(),
        }
    }
    records = YahooFinanceNormalizer().normalize(_result(payload))
    assert not [r for r in records if isinstance(r, EarningsEstimate)]
