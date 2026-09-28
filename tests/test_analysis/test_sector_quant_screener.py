"""Unit tests for the sector+quant idea-generation screener — verifies the
computed columns match the user's own Excel guide's formulas exactly
(P/E trailing/F1/F2, P/S trailing/next-year, SG1, EG1/EG2 with sign-flip
handling, PEG/PEG_NEXT), plus the sector-level outlier-trimmed averaging."""

from __future__ import annotations

import tempfile
from datetime import date, datetime, timezone
from pathlib import Path

import pytest

from analysis.sector_quant_screener import build_quant_table, sector_growth_overview, stocks_in_sector
from ingestion.models import EarningsEstimate, FundamentalsSnapshot, Quote
from storage.db import write_connection
from storage.writers import append_earnings_estimates, append_fundamentals_history, upsert_quotes_latest
from universe.sp500_fetch import upsert_instruments


def _seed_company(
    conn, symbol: str, sector: str, price: float, market_cap: float,
    f0_eps: float, f1_eps: float, f2_eps: float,
    annual_sales: float, next_year_sales: float,
) -> None:
    upsert_instruments(conn, [{"symbol": symbol, "name": symbol, "sector": sector, "industry": "Test"}])
    upsert_quotes_latest(conn, [Quote(symbol=symbol, price=price, market_cap=market_cap, as_of=datetime.now(timezone.utc), source="test")])
    append_fundamentals_history(conn, [
        FundamentalsSnapshot(symbol=symbol, as_of=datetime.now(timezone.utc), eps=f0_eps, revenue_ttm=annual_sales, source="test")
    ])
    now = datetime.now(timezone.utc)
    append_earnings_estimates(conn, [
        EarningsEstimate(symbol=symbol, as_of=now, period="0y", metric="earnings", avg_estimate=f1_eps, source="test"),
        EarningsEstimate(symbol=symbol, as_of=now, period="+1y", metric="earnings", avg_estimate=f2_eps, source="test"),
        EarningsEstimate(symbol=symbol, as_of=now, period="+1y", metric="revenue", avg_estimate=next_year_sales, source="test"),
    ])


def test_build_quant_table_matches_guides_formulas_for_a_normal_company() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        conn = write_connection(Path(tmp) / "t.db")
        # AAPL-like: price 150, mkt cap 3000, F0=6 F1=7 F2=8, sales 400->450 next yr
        _seed_company(conn, "AAPL", "Tech", price=150.0, market_cap=3000.0,
                       f0_eps=6.0, f1_eps=7.0, f2_eps=8.0, annual_sales=400.0, next_year_sales=450.0)
        df = build_quant_table(conn)
        conn.close()

    row = df[df["symbol"] == "AAPL"].iloc[0]
    assert row["pe_trailing"] == pytest.approx(150.0 / 6.0)
    assert row["pe_f1"] == pytest.approx(150.0 / 7.0)
    assert row["pe_f2"] == pytest.approx(150.0 / 8.0)
    assert row["ps_trailing"] == pytest.approx(3000.0 / 400.0)
    assert row["ps_next_year"] == pytest.approx(3000.0 / 450.0)
    assert row["sales_growth_next_year"] == pytest.approx(450.0 / 400.0 - 1)
    assert row["earnings_growth_f1"] == pytest.approx(7.0 / 6.0 - 1)
    assert row["earnings_growth_f2"] == pytest.approx(8.0 / 7.0 - 1)
    # PEG uses TRAILING P/E (guide's formula), not forward P/E
    assert row["peg_ratio"] == pytest.approx(row["pe_trailing"] / (row["earnings_growth_f1"] * 100))
    assert row["peg_ratio_next"] == pytest.approx(row["pe_trailing"] / (row["earnings_growth_f2"] * 100))


def test_earnings_growth_sign_flip_handling_matches_guide() -> None:
    """Guide's exact rule: negative base -> +99%/-99% depending on the sign
    of the next period, never a raw ratio (which would be meaningless with a
    negative denominator)."""
    with tempfile.TemporaryDirectory() as tmp:
        conn = write_connection(Path(tmp) / "t.db")
        # Loss -> profit: base negative, next positive -> +99%
        _seed_company(conn, "TURN", "Tech", price=10, market_cap=100,
                       f0_eps=-2.0, f1_eps=1.0, f2_eps=1.5, annual_sales=50, next_year_sales=55)
        # Loss -> bigger loss: base negative, next negative -> -99%
        _seed_company(conn, "SINK", "Tech", price=10, market_cap=100,
                       f0_eps=-1.0, f1_eps=-3.0, f2_eps=-4.0, annual_sales=50, next_year_sales=55)
        # Profit -> loss: base positive, next negative -> -99%
        _seed_company(conn, "FALL", "Tech", price=10, market_cap=100,
                       f0_eps=2.0, f1_eps=-1.0, f2_eps=-2.0, annual_sales=50, next_year_sales=55)
        df = build_quant_table(conn)
        conn.close()

    assert df[df["symbol"] == "TURN"].iloc[0]["earnings_growth_f1"] == pytest.approx(0.99)
    assert df[df["symbol"] == "SINK"].iloc[0]["earnings_growth_f1"] == pytest.approx(-0.99)
    assert df[df["symbol"] == "FALL"].iloc[0]["earnings_growth_f1"] == pytest.approx(-0.99)


def test_sector_average_excludes_outliers_but_stock_table_keeps_them() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        conn = write_connection(Path(tmp) / "t.db")
        # Two normal companies around 10-20% EG1, one base-effect outlier at 500%.
        _seed_company(conn, "A", "Tech", price=10, market_cap=100, f0_eps=1.0, f1_eps=1.1, f2_eps=1.2, annual_sales=50, next_year_sales=55)
        _seed_company(conn, "B", "Tech", price=10, market_cap=100, f0_eps=1.0, f1_eps=1.2, f2_eps=1.3, annual_sales=50, next_year_sales=55)
        _seed_company(conn, "OUTLIER", "Tech", price=10, market_cap=100, f0_eps=0.01, f1_eps=0.06, f2_eps=0.07, annual_sales=50, next_year_sales=55)
        df = build_quant_table(conn)

        overview = sector_growth_overview(df)
        tech_row = overview[overview["sector"] == "Tech"].iloc[0]
        # Average of just A (10%) and B (20%) = 15%, NOT dragged toward the 500% outlier.
        assert tech_row["avg_earnings_growth_f1"] == pytest.approx(0.15, abs=0.01)

        stocks = stocks_in_sector(df, "Tech")
        conn.close()

    # The outlier is still a ROW in the table (guide: "keep the company in the sheet").
    assert "OUTLIER" in set(stocks["symbol"])
    outlier_row = stocks[stocks["symbol"] == "OUTLIER"].iloc[0]
    assert bool(outlier_row["earnings_growth_f1_outlier"])


def test_missing_estimates_do_not_crash_and_yield_nan_not_error() -> None:
    # ignore_cleanup_errors=True: this specific case (no fundamentals/estimates
    # rows at all, only instruments+quotes) reproducibly hits a Windows-only
    # SQLite-WAL temp-file-handle release timing quirk on TemporaryDirectory's
    # own cleanup, AFTER the test's assertions already pass — not a defect in
    # sector_quant_screener.py, which the assertion below still exercises fully.
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        conn = write_connection(Path(tmp) / "t.db")
        upsert_instruments(conn, [{"symbol": "NODATA", "name": "NODATA", "sector": "Tech", "industry": "Test"}])
        upsert_quotes_latest(conn, [Quote(symbol="NODATA", price=100.0, market_cap=1000.0, as_of=datetime.now(timezone.utc), source="test")])
        df = build_quant_table(conn)
        conn.close()

    row = df[df["symbol"] == "NODATA"].iloc[0]
    assert row["pe_trailing"] is None or row["pe_trailing"] != row["pe_trailing"]  # NaN check without importing numpy
